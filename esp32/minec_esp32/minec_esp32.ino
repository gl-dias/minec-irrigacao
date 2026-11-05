/*
 * MineC - firmware do ESP32 para a plantação automática
 *
 * O ESP32 não guarda a lógica da fazenda: quem decide é o programa MineC,
 * que roda no servidor. Este firmware só faz duas coisas, a cada 0,5 s:
 *
 *   1. lê os sensores capacitivos de umidade pedidos pelo servidor
 *      e envia a leitura bruta (0 a 4095) por POST /api/esp32/sync;
 *   2. recebe de volta o estado que cada relé (bomba) deve ter e aplica.
 *
 * Segurança (funciona mesmo se o servidor ou o Wi-Fi caírem):
 *   - sem resposta do servidor          -> desliga todos os relés em até 5 s;
 *   - relé ligado mais que 'limite' s   -> desliga e só religa quando o
 *                                          servidor mandar desligar antes.
 *
 * Ligações (ESP32 DevKit V1 / WROOM-32):
 *   Sensor capacitivo:  VCC -> 3V3   GND -> GND   AOUT -> GPIO 34
 *   Módulo relé:        VCC -> 5V (VIN)  GND -> GND  IN -> GPIO 26
 *   Bomba/solenóide:    alimentação própria, passando pelo contato COM/NO do relé
 *
 * Placa no Arduino IDE: "ESP32 Dev Module" (pacote esp32 da Espressif).
 * Não precisa de nenhuma biblioteca extra.
 */

#include <WiFi.h>
#include <HTTPClient.h>

// ====================== CONFIGURE AQUI ======================
// Atenção: não comite este arquivo com a senha preenchida. Antes de comitar,
// volte WIFI_SSID e WIFI_SENHA para os valores de exemplo.
const char* WIFI_SSID   = "NOME_DA_SUA_REDE";
const char* WIFI_SENHA  = "SENHA_DA_SUA_REDE";

// Endereço que o servidor mostra ao iniciar ("No firmware do ESP32 use: ...")
// Use sempre o IP: resolver um nome (DNS) trava o firmware por vários segundos
// se a rede falhar, e o corte de segurança não roda nesse tempo.
const char* SERVIDOR    = "http://192.168.0.10:8000";

const char* ID_PLACA    = "minec-esp32-01";

// A maioria dos módulos relé com optoacoplador liga com nível BAIXO (LOW).
// Se o seu relé ligar ao contrário, troque para false.
const bool RELE_ATIVO_EM_BAIXO = true;
// ============================================================

const char* VERSAO_FW = "1.0";

// Prazos do HTTP, em ms nas séries 2.x e 3.x. A 2.x arredonda o prazo de leitura do
// socket para segundos inteiros: 500 vira 1 s e abaixo de 500 vira 0, então não reduza.
const unsigned long HTTP_CONEXAO_MS  = 1000;
const unsigned long HTTP_RESPOSTA_MS = 500;

// A segurança não roda durante o HTTP: a checagem pode passar logo antes de vencer o
// prazo e o sync seguinte travar inteiro. Pior caso até o relé desligar:
//   1800 (FAILSAFE_MS) + 3000 (bloqueio) + 200 (folga: sensores, delay, http.end) = 5000 ms
//   bloqueio = conexão 1000 + resposta 500 + linha cortada 1000 (500 na 3.x) + silêncio 500
const unsigned long FAILSAFE_MS = 1800;   // sem servidor -> desliga tudo
const int AMOSTRAS_ADC = 16;             // média para filtrar ruído
const int MAX_PINOS = 8;

// Pinos aceitos (mesmas regras do analisador semântico do MineC)
const int PINOS_SENSOR_OK[] = {32, 33, 34, 35, 36, 39};                 // ADC1
const int PINOS_RELE_OK[]   = {2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19,
                               21, 22, 23, 25, 26, 27, 32, 33};

int pinosLeitura[MAX_PINOS];
int qtdLeitura = 0;

struct Saida {
  int pino;
  bool desejado;          // o que o servidor pediu
  bool ligado;            // estado real do relé
  bool bloqueado;         // desligado pelo limite de segurança
  unsigned long ligadoDesde;
};
Saida saidas[MAX_PINOS];
int qtdSaidas = 0;

bool pinoLiberadoComPull[40] = {false};   // saída liberada, ainda com pull ligado

// Recusas da resposta atual; só vão para o Serial quando mudam em relação às já avisadas.
String recusasAtuais;
String recusasAvisadas;

unsigned long periodoMs = 500;
unsigned long limiteMs = 120000;
unsigned long ultimoSyncOk = 0;
unsigned long ultimoCiclo = 0;
bool emFailsafe = true;

void verificarSeguranca();

// ------------------------------------------------------------
// utilidades
// ------------------------------------------------------------

bool contem(const int* lista, int tamanho, int valor) {
  for (int i = 0; i < tamanho; i++) if (lista[i] == valor) return true;
  return false;
}

bool pinoSensorValido(int p) {
  return contem(PINOS_SENSOR_OK, sizeof(PINOS_SENSOR_OK) / sizeof(int), p);
}

bool pinoReleValido(int p) {
  return contem(PINOS_RELE_OK, sizeof(PINOS_RELE_OK) / sizeof(int), p);
}

void recusarPino(int pino, const char* uso, const char* motivo) {
  recusasAtuais += String("[pinos] GPIO ") + String(pino) + " recusado como " + uso + ": " + motivo + "\n";
}

void avisarRecusas() {
  if (recusasAtuais == recusasAvisadas) return;
  if (recusasAtuais.length() > 0) Serial.print(recusasAtuais);
  else Serial.println("[pinos] nenhum pino recusado agora");
  recusasAvisadas = recusasAtuais;
}

void escreverRele(int pino, bool ligado) {
  bool nivelAlto = RELE_ATIVO_EM_BAIXO ? !ligado : ligado;
  digitalWrite(pino, nivelAlto ? HIGH : LOW);
}

Saida* buscarSaida(int pino) {
  for (int i = 0; i < qtdSaidas; i++) if (saidas[i].pino == pino) return &saidas[i];
  return nullptr;
}

Saida* registrarSaida(int pino) {
  Saida* s = buscarSaida(pino);
  if (s) return s;
  if (!pinoReleValido(pino)) {
    recusarPino(pino, "saída", "pino não permitido");
    return nullptr;
  }
  if (contem(pinosLeitura, qtdLeitura, pino)) {
    recusarPino(pino, "saída", "está em uso como sensor");
    return nullptr;
  }
  if (qtdSaidas >= MAX_PINOS) {
    recusarPino(pino, "saída", "lista de saídas cheia");
    return nullptr;
  }
  // escreve o nível "desligado" ANTES de virar saída, para o relé não dar um pulso
  escreverRele(pino, false);
  pinMode(pino, OUTPUT);
  escreverRele(pino, false);
  pinoLiberadoComPull[pino] = false;
  saidas[qtdSaidas] = {pino, false, false, false, 0};
  Serial.printf("[relé] GPIO %d configurado\n", pino);
  return &saidas[qtdSaidas++];
}

void aplicar(Saida& s, bool ligar) {
  if (s.ligado == ligar) return;
  s.ligado = ligar;
  if (ligar) s.ligadoDesde = millis();
  escreverRele(s.pino, ligar);
  Serial.printf("[relé] GPIO %d %s\n", s.pino, ligar ? "LIGADO" : "desligado");
}

void desligarTudo() {
  for (int i = 0; i < qtdSaidas; i++) aplicar(saidas[i], false);
}

int lerSensor(int pino) {
  long soma = 0;
  for (int i = 0; i < AMOSTRAS_ADC; i++) {
    soma += analogRead(pino);
    delayMicroseconds(200);
  }
  return (int)(soma / AMOSTRAS_ADC);
}

// "34,35" -> pinosLeitura
void lerListaSensores(const String& valor) {
  qtdLeitura = 0;
  int inicio = 0;
  while (inicio < (int)valor.length()) {
    int fim = valor.indexOf(',', inicio);
    if (fim < 0) fim = valor.length();
    int p = valor.substring(inicio, fim).toInt();
    if (fim > inicio) {
      if (!pinoSensorValido(p)) {
        recusarPino(p, "sensor", "pino não permitido");
      } else if (buscarSaida(p)) {
        recusarPino(p, "sensor", "está em uso como saída");
      } else if (qtdLeitura >= MAX_PINOS) {
        recusarPino(p, "sensor", "lista de sensores cheia");
      } else {
        // pull ligado distorceria a leitura do sensor
        if (pinoLiberadoComPull[p]) {
          pinMode(p, INPUT);
          pinoLiberadoComPull[p] = false;
        }
        pinosLeitura[qtdLeitura++] = p;
        analogSetPinAttenuation(p, ADC_11db);  // faixa completa de 0 a ~3,3 V
      }
    }
    inicio = fim + 1;
  }
}

// o pino aparece no começo do item: "26:1".toInt() dá 26
bool listaSaidasTemPino(const String& valor, int pino) {
  int inicio = 0;
  while (inicio < (int)valor.length()) {
    int fim = valor.indexOf(',', inicio);
    if (fim < 0) fim = valor.length();
    if (valor.substring(inicio, fim).toInt() == pino) return true;
    inicio = fim + 1;
  }
  return false;
}

// Saída fora da lista do servidor: desliga, devolve a vaga e volta a ser entrada.
// Fica com pull no nível "desligado": sem pull o IN do módulo ficaria solto e poderia
// acionar o relé sozinho. O pino nunca é levado ao nível "ligado" nessa troca.
void liberarSaidasForaDaLista(const String& valor) {
  for (int i = qtdSaidas - 1; i >= 0; i--) {
    int pino = saidas[i].pino;
    if (listaSaidasTemPino(valor, pino)) continue;
    aplicar(saidas[i], false);
    pinMode(pino, RELE_ATIVO_EM_BAIXO ? INPUT_PULLUP : INPUT_PULLDOWN);
    pinoLiberadoComPull[pino] = true;
    saidas[i] = saidas[qtdSaidas - 1];
    qtdSaidas--;
    Serial.printf("[relé] GPIO %d liberado, voltou a ser entrada\n", pino);
  }
}

// "26:1,27:0" -> estado desejado de cada relé
void lerListaSaidas(const String& valor) {
  int inicio = 0;
  while (inicio < (int)valor.length()) {
    int fim = valor.indexOf(',', inicio);
    if (fim < 0) fim = valor.length();
    String item = valor.substring(inicio, fim);
    int sep = item.indexOf(':');
    if (sep > 0) {
      int p = item.substring(0, sep).toInt();
      bool ligar = item.substring(sep + 1).toInt() == 1;
      Saida* s = registrarSaida(p);
      if (s) {
        if (!ligar) s->bloqueado = false;   // servidor reconheceu o desligamento
        s->desejado = ligar;
      }
    }
    inicio = fim + 1;
  }
}

void interpretarResposta(const String& corpo) {
  String valorLer, valorSaidas;
  bool temLer = false, temSaidas = false;
  recusasAtuais = "";
  int inicio = 0;
  while (inicio < (int)corpo.length()) {
    int fim = corpo.indexOf('\n', inicio);
    if (fim < 0) fim = corpo.length();
    String linha = corpo.substring(inicio, fim);
    linha.trim();
    int igual = linha.indexOf('=');
    if (igual > 0) {
      String chave = linha.substring(0, igual);
      String valor = linha.substring(igual + 1);
      if (chave == "ler") { valorLer = valor; temLer = true; }
      else if (chave == "saidas") { valorSaidas = valor; temSaidas = true; }
      // teto de 1000: período perto de FAILSAFE_MS cortaria o relé mesmo com o servidor no ar
      else if (chave == "periodo") periodoMs = constrain(valor.toInt(), 200, 1000);
      else if (chave == "limite") limiteMs = (unsigned long)constrain(valor.toInt(), 5, 1800) * 1000UL;
    }
    inicio = fim + 1;
  }

  // Esta ordem faz a troca de função do pino valer no mesmo sync, nos dois sentidos:
  // saída que vira sensor já foi liberada ao ler "ler", e sensor que vira saída já
  // saiu da lista de leitura ao registrar a saída. Sem a linha, a lista não muda.
  if (temSaidas) liberarSaidasForaDaLista(valorSaidas);
  if (temLer) lerListaSensores(valorLer);
  if (temSaidas) lerListaSaidas(valorSaidas);
  avisarRecusas();
}

// ------------------------------------------------------------
// rede
// ------------------------------------------------------------

void conectarWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("[wifi] conectando em %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
  unsigned long inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 15000) {
    delay(300);
    Serial.print(".");
    verificarSeguranca();
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[wifi] conectado! IP do ESP32: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[wifi] falhou, tentando de novo em instantes");
  }
}

// http.getString() não tem prazo: se o corpo não chega e a conexão não cai, ela fica
// presa para sempre e a segurança não roda. Só pode ser chamada com o corpo já no buffer.
bool corpoChegou(HTTPClient& http) {
  int tamanho = http.getSize();
  Stream* fluxo = http.getStreamPtr();
  if (tamanho <= 0 || !fluxo) return false;
  unsigned long inicio = millis();
  while (fluxo->available() < tamanho) {
    if (millis() - inicio >= HTTP_RESPOSTA_MS) return false;
    delay(5);
  }
  return true;
}

bool sincronizar() {
  String corpo = "id=" + String(ID_PLACA) + "\nfw=" + VERSAO_FW + "\nrssi=" + String(WiFi.RSSI()) + "\n";
  for (int i = 0; i < qtdLeitura; i++) {
    corpo += "a" + String(pinosLeitura[i]) + "=" + String(lerSensor(pinosLeitura[i])) + "\n";
  }

  HTTPClient http;
  http.setConnectTimeout(HTTP_CONEXAO_MS);
  http.setTimeout(HTTP_RESPOSTA_MS);
  http.begin(String(SERVIDOR) + "/api/esp32/sync");
  http.addHeader("Content-Type", "text/plain");
  int status = http.POST(corpo);
  bool ok = status == 200 && corpoChegou(http);
  if (ok) {
    interpretarResposta(http.getString());
  } else {
    String erro = status == 200 ? String("corpo da resposta não chegou") : http.errorToString(status);
    Serial.printf("[sync] falhou (%d): %s\n", status, erro.c_str());
  }
  http.end();
  return ok;
}

// ------------------------------------------------------------
// segurança e aplicação dos relés
// ------------------------------------------------------------

void verificarSeguranca() {
  bool semServidor = millis() - ultimoSyncOk > FAILSAFE_MS;
  if (semServidor && !emFailsafe) {
    Serial.println("[segurança] sem resposta do servidor: relés desligados");
  }
  emFailsafe = semServidor;

  for (int i = 0; i < qtdSaidas; i++) {
    Saida& s = saidas[i];
    if (s.ligado && millis() - s.ligadoDesde > limiteMs) {
      s.bloqueado = true;
      Serial.printf("[segurança] GPIO %d passou do limite de tempo ligado\n", s.pino);
    }
    aplicar(s, s.desejado && !s.bloqueado && !emFailsafe);
  }
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n=== MineC ESP32 ===");
  analogReadResolution(12);
  conectarWiFi();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    conectarWiFi();
  }

  if (WiFi.status() == WL_CONNECTED && millis() - ultimoCiclo >= periodoMs) {
    ultimoCiclo = millis();
    if (sincronizar()) {
      if (emFailsafe) Serial.println("[sync] servidor conectado");
      ultimoSyncOk = millis();
      emFailsafe = false;
    }
  }

  verificarSeguranca();
  delay(20);
}
