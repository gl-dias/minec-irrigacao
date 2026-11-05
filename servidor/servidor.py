"""
Servidor do protótipo MineC (back-end).

    python servidor/servidor.py            # porta 8000
    python servidor/servidor.py --porta 9000

Só usa a biblioteca padrão do Python. Ele:
  * serve o painel web (pasta web);
  * guarda os canteiros e ajustes em servidor/dados/config.json;
  * gera o programa MineC, compila e executa num thread;
  * conversa com o ESP32 pelo endpoint POST /api/esp32/sync.
"""

import argparse
import collections
import copy
import ipaddress
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, urlsplit

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from compilador import compilar, problema_pino_atuador, problema_pino_sensor  # noqa: E402
from gerador import gerar_programa, nome_de_variavel  # noqa: E402
from hardware import Hardware  # noqa: E402
from interpretador import ErroExecucao, Interpretador, ProgramaParado  # noqa: E402

PASTA_WEB = os.path.abspath(os.path.join(AQUI, "..", "web"))
PASTA_DADOS = os.path.join(AQUI, "dados")
ARQUIVO_CONFIG = os.path.join(PASTA_DADOS, "config.json")

CONFIG_PADRAO = {
    "modo": "simulacao",            # simulacao | esp32
    "intervalo": 120,               # segundos entre verificações
    "tempo_rega": 10,               # segundos de bomba ligada por rega
    "fator_tempo": 1,               # só na simulação: 10 = 10x mais rápido
    "limite_bomba": 120,            # segurança: tempo máximo de bomba ligada
    "rodar_ao_iniciar": True,
    "calibracao_padrao": {"seco": 3000, "molhado": 1300},
    "canteiros": [
        {"id": "c1", "nome": "Canteiro de cenouras", "variavel": "cenouras",
         "pino_sensor": 34, "pino_bomba": 26, "minimo": 30, "seco": None, "molhado": None},
    ],
    "programa_personalizado": None,
}


MAX_BOMBAS = 8  # igual ao MAX_PINOS do firmware


class ErroRequisicao(Exception):
    def __init__(self, mensagem, status=400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


# =====================================================================
# Estado da aplicação
# =====================================================================

class App:
    def __init__(self):
        self.lock = threading.RLock()
        self.lock_console = threading.Lock()
        self.console = collections.deque(maxlen=400)
        self.seq = 0
        self.sessao = uuid.uuid4().hex  # muda a cada subida; o painel usa para limpar o console

        self.config = self._carregar_config()

        self.thread = None
        self.parar_evento = threading.Event()
        self.rodando = False
        self.erro = None
        self.terminou_sozinho = False
        self.pulso = None  # (evento, pino) do teste manual de bomba em andamento
        self.iniciado_em = None
        self.espera = (0.0, 0.0)
        self.codigo_rodando = ""
        self.compilacao = None
        self.ultimas_leituras = {}  # pino -> {"valor", "t"} lidos pelo programa (mine)

        self.hw = Hardware(lambda: self.config, self.registrar, self.pinos_leitura, self.pinos_saida)
        self.registrar("sistema", "Servidor MineC iniciado.")

    # ---------------- configuração ----------------

    def _carregar_config(self):
        cfg = copy.deepcopy(CONFIG_PADRAO)
        if os.path.exists(ARQUIVO_CONFIG):
            try:
                with open(ARQUIVO_CONFIG, encoding="utf-8") as f:
                    lido = json.load(f)
                if not isinstance(lido, dict):
                    raise ValueError("o arquivo não é um objeto JSON")
                cfg.update(self._config_valida(lido))
            except (OSError, ValueError) as exc:
                print(f"Aviso: não consegui ler {ARQUIVO_CONFIG} ({exc}); usando o padrão.")
        return cfg

    @staticmethod
    def _config_valida(lido):
        """Descarta o que não presta num config.json antigo ou editado à mão, avisando.
        O arquivo só é sobrescrito no próximo salvamento feito pelo painel."""
        cfg = dict(lido)

        def descartar(chave, motivo):
            print(f"Aviso: ignorando '{chave}' de {ARQUIVO_CONFIG}: {motivo}.")
            cfg.pop(chave, None)

        def numero(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")

        for chave in ("intervalo", "tempo_rega", "fator_tempo", "limite_bomba"):
            if chave in cfg and not numero(cfg[chave]):
                descartar(chave, "precisa ser um número")
        if "modo" in cfg and cfg["modo"] not in ("simulacao", "esp32"):
            descartar("modo", "modo desconhecido")
        cp = cfg.get("calibracao_padrao")
        if "calibracao_padrao" in cfg and not (isinstance(cp, dict) and numero(cp.get("seco")) and numero(cp.get("molhado"))):
            descartar("calibracao_padrao", "precisa ter 'seco' e 'molhado'")
        if not isinstance(cfg.get("programa_personalizado"), (str, type(None))):
            descartar("programa_personalizado", "precisa ser texto")
        if "canteiros" in cfg:
            if not isinstance(cfg["canteiros"], list):
                descartar("canteiros", "precisa ser uma lista")
            else:
                def canteiro_ok(c):
                    return (isinstance(c, dict) and all(isinstance(c.get(k), str) and c[k] for k in ("id", "nome", "variavel"))
                            and all(numero(c.get(k)) for k in ("pino_sensor", "pino_bomba", "minimo")))
                bons = [c for c in cfg["canteiros"] if canteiro_ok(c)]
                if len(bons) != len(cfg["canteiros"]):
                    print(f"Aviso: ignorando {len(cfg['canteiros']) - len(bons)} canteiro(s) incompleto(s) de {ARQUIVO_CONFIG}.")
                cfg["canteiros"] = bons
        return cfg

    def _salvar(self, novo):
        """Troca a config inteira de uma vez (quem lê sem lock vê a velha ou a nova)."""
        self.config = novo
        os.makedirs(PASTA_DADOS, exist_ok=True)
        tmp = ARQUIVO_CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(novo, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ARQUIVO_CONFIG)

    def registrar(self, tipo, texto, linha=None):
        with self.lock_console:
            self.seq += 1
            self.console.append({"seq": self.seq, "t": time.time(), "tipo": tipo, "texto": texto, "linha": linha})
        print(f"[{time.strftime('%H:%M:%S')}] {tipo.upper():8} {texto}", flush=True)

    # ---------------- pinos em uso ----------------

    def pinos_leitura(self):
        pinos = {c["pino_sensor"] for c in self.config["canteiros"]}
        if self.rodando and self.compilacao:
            pinos |= set(self.compilacao.sensores.values())
        return pinos

    def pinos_saida(self):
        pinos = {c["pino_bomba"] for c in self.config["canteiros"]}
        if self.rodando and self.compilacao:
            pinos |= set(self.compilacao.atuadores.values())
        return pinos

    # ---------------- programa ----------------

    def codigo_atual(self):
        personalizado = self.config.get("programa_personalizado")
        if personalizado:
            return personalizado, "personalizado"
        return gerar_programa(self.config), "gerado"

    def compilar(self, codigo):
        return compilar(codigo, checar_pinos=True, pinos_como_erro=self.config["modo"] == "esp32")

    def rodar(self, codigo=None):
        with self.lock:
            if codigo is not None and not isinstance(codigo, str):
                raise ErroRequisicao("O código precisa ser um texto.")
            enviado = codigo is not None
            if codigo:
                origem = "personalizado"
            elif enviado:
                codigo, origem = gerar_programa(self.config), "gerado"  # editor vazio
            else:
                codigo, origem = self.codigo_atual()
            resultado = self.compilar(codigo)
            if not resultado.ok:
                self.registrar("erro", f"O programa não compilou: {resultado.erros[0]}", resultado.erros[0].linha)
                # código recém-enviado com erro não troca nada: o programa salvo segue valendo
                if not enviado:
                    self.erro = str(resultado.erros[0])
                return resultado
            if enviado:
                novo = copy.deepcopy(self.config)
                novo["programa_personalizado"] = codigo if origem == "personalizado" else None
                self._salvar(novo)
            self._parar_thread()
            for aviso in resultado.avisos:
                self.registrar("aviso", str(aviso), aviso.linha)

            self.compilacao = resultado
            self.codigo_rodando = codigo
            self.erro = None
            self.terminou_sozinho = False
            self.ultimas_leituras = {}
            self.parar_evento = threading.Event()
            self.rodando = True
            self.iniciado_em = time.time()
            self.registrar("sistema", f"Programa {origem} compilado e em execução.")
            self.thread = threading.Thread(target=self._executar, args=(resultado, self.parar_evento),
                                           daemon=True, name="minec")
            self.thread.start()
            return resultado

    def _executar(self, resultado, parar):
        def ao_ler(nome, pino, valor):
            self.ultimas_leituras[pino] = {"valor": valor, "t": time.time(), "nome": nome}

        def ao_esperar(inicio, fim):
            self.espera = (inicio, fim)

        interp = Interpretador(resultado.ast, self.hw, parar,
                               ao_dizer=lambda texto, linha: self.registrar("say", texto, linha),
                               ao_ler=ao_ler, ao_esperar=ao_esperar)
        try:
            interp.executar()
            if not parar.is_set():
                self.terminou_sozinho = True
                self.registrar("sistema", "Programa terminou.")
        except ProgramaParado:
            pass
        except ErroExecucao as exc:
            self.erro = str(exc)
            self.registrar("erro", f"Erro de execução: {exc}", exc.linha)
        except Exception as exc:  # erro inesperado no próprio interpretador
            self.erro = f"Erro interno: {exc}"
            self.registrar("erro", self.erro)
        finally:
            if self.parar_evento is parar:
                self.rodando = False
                self.espera = (0.0, 0.0)
                self.hw.desligar_tudo()

    def _parar_thread(self):
        self._cancelar_pulso()
        if self.thread and self.thread.is_alive():
            self.parar_evento.set()
            self.thread.join(timeout=6)
        self.rodando = False
        self.espera = (0.0, 0.0)
        self.hw.desligar_tudo()

    def parar(self):
        with self.lock:
            estava = self.rodando
            self.terminou_sozinho = False
            self._parar_thread()
            if estava:
                self.registrar("sistema", "Programa parado. Bombas desligadas.")

    def reiniciar_se_preciso(self, motivo):
        """Depois de mudar canteiros/ajustes, roda de novo o programa gerado."""
        if self.config.get("programa_personalizado"):
            if self.rodando:
                self.registrar("aviso", f"{motivo}. Você está com um programa personalizado, que não foi alterado.")
            return
        # sem canteiros o programa gerado termina sozinho; o próximo canteiro precisa religá-lo
        if self.rodando or self.erro or self.terminou_sozinho:
            self.registrar("sistema", f"{motivo}. Programa gerado de novo.")
            self.rodar()

    # ---------------- canteiros ----------------

    def _validar_canteiro(self, dados, atual=None):
        c = dict(atual or {})
        nome = dados.get("nome", c.get("nome", ""))
        nome = "" if nome is None else str(nome).strip()
        if not nome:
            raise ErroRequisicao("Dê um nome ao canteiro.")
        if len(nome) > 40:
            raise ErroRequisicao("Nome muito longo (máximo 40 caracteres).")
        try:
            pino_sensor = int(dados.get("pino_sensor", c.get("pino_sensor", 34)))
            pino_bomba = int(dados.get("pino_bomba", c.get("pino_bomba", 26)))
            minimo = int(round(float(dados.get("minimo", c.get("minimo", 30)))))
        except (TypeError, ValueError, OverflowError):
            raise ErroRequisicao("Pinos e mínimo precisam ser números.")
        if not 0 <= minimo <= 100:
            raise ErroRequisicao("O mínimo recomendado precisa estar entre 0 e 100%.")
        for problema in (problema_pino_sensor(pino_sensor), problema_pino_atuador(pino_bomba)):
            if problema:
                raise ErroRequisicao(problema)
        bombas = {x["pino_bomba"] for x in self.config["canteiros"] if x.get("id") != c.get("id")}
        sensores = {x["pino_sensor"] for x in self.config["canteiros"] if x.get("id") != c.get("id")}
        if pino_sensor in bombas or pino_bomba in sensores or pino_sensor == pino_bomba:
            raise ErroRequisicao("Um mesmo GPIO não pode ser sensor e bomba ao mesmo tempo.")
        if len(bombas | {pino_bomba}) > MAX_BOMBAS:
            raise ErroRequisicao(f"O ESP32 aciona no máximo {MAX_BOMBAS} bombas diferentes.")

        def calib(chave):
            v = dados.get(chave, c.get(chave))
            if v in (None, ""):
                return None
            try:
                v = int(float(v))
            except (TypeError, ValueError, OverflowError):
                raise ErroRequisicao("A calibração precisa ser um número de 0 a 4095.")
            if not 0 <= v <= 4095:
                raise ErroRequisicao("A calibração usa a leitura bruta do ESP32 (0 a 4095).")
            return v

        seco, molhado = calib("seco"), calib("molhado")
        self._conferir_calibracao(seco, molhado, self.config["calibracao_padrao"])

        c.update(nome=nome, pino_sensor=pino_sensor, pino_bomba=pino_bomba, minimo=minimo, seco=seco, molhado=molhado)
        return c

    @staticmethod
    def _conferir_calibracao(seco, molhado, padrao):
        """Campo em branco herda o padrão, então a comparação vale para o par efetivo."""
        herdou = seco is None or molhado is None
        seco = padrao["seco"] if seco is None else seco
        molhado = padrao["molhado"] if molhado is None else molhado
        if seco <= molhado:
            detalhe = f" Com o padrão herdado, ficaria seco {seco} e molhado {molhado}." if herdou else ""
            raise ErroRequisicao(f"No sensor capacitivo, a leitura 'seco' é maior que a 'molhado'. Confira a calibração.{detalhe}")

    def criar_canteiro(self, dados):
        with self.lock:
            c = self._validar_canteiro(dados)
            existentes = {x["variavel"] for x in self.config["canteiros"]}
            c["id"] = uuid.uuid4().hex[:8]
            c["variavel"] = nome_de_variavel(c["nome"], existentes)
            novo = copy.deepcopy(self.config)
            novo["canteiros"].append(c)
            self._salvar(novo)
            self.reiniciar_se_preciso(f"Canteiro '{c['nome']}' adicionado")
            return c

    def editar_canteiro(self, cid, dados):
        with self.lock:
            novo = copy.deepcopy(self.config)
            for i, atual in enumerate(novo["canteiros"]):
                if atual["id"] == cid:
                    c = self._validar_canteiro(dados, atual)
                    if c["nome"] != atual["nome"]:
                        outros = {x["variavel"] for x in novo["canteiros"] if x["id"] != cid}
                        c["variavel"] = nome_de_variavel(c["nome"], outros)
                    novo["canteiros"][i] = c
                    self._salvar(novo)
                    self.reiniciar_se_preciso(f"Canteiro '{c['nome']}' alterado")
                    return c
            raise ErroRequisicao("Canteiro não encontrado.", 404)

    def remover_canteiro(self, cid):
        with self.lock:
            novo = copy.deepcopy(self.config)
            antes = len(novo["canteiros"])
            removido = next((c for c in novo["canteiros"] if c["id"] == cid), None)
            novo["canteiros"] = [c for c in novo["canteiros"] if c["id"] != cid]
            if len(novo["canteiros"]) == antes:
                raise ErroRequisicao("Canteiro não encontrado.", 404)
            self._salvar(novo)
            self.reiniciar_se_preciso(f"Canteiro '{removido['nome']}' removido")

    def ajustar(self, dados):
        with self.lock:
            novo = copy.deepcopy(self.config)
            mudou_modo = False
            try:
                if "intervalo" in dados:
                    novo["intervalo"] = int(dados["intervalo"])
                    if not 5 <= novo["intervalo"] <= 86400:
                        raise ErroRequisicao("O intervalo precisa estar entre 5 segundos e 24 horas.")
                if "tempo_rega" in dados:
                    novo["tempo_rega"] = int(dados["tempo_rega"])
                    if not 1 <= novo["tempo_rega"] <= 600:
                        raise ErroRequisicao("O tempo de rega precisa estar entre 1 e 600 segundos.")
                if "limite_bomba" in dados:
                    novo["limite_bomba"] = int(dados["limite_bomba"])
                    if not 5 <= novo["limite_bomba"] <= 1800:
                        raise ErroRequisicao("O limite de segurança precisa estar entre 5 e 1800 segundos.")
                if "fator_tempo" in dados:
                    novo["fator_tempo"] = float(dados["fator_tempo"])
                    if not 1 <= novo["fator_tempo"] <= 120:
                        raise ErroRequisicao("A aceleração da simulação vai de 1x a 120x.")
                if "rodar_ao_iniciar" in dados:
                    novo["rodar_ao_iniciar"] = bool(dados["rodar_ao_iniciar"])
                if "modo" in dados:
                    if dados["modo"] not in ("simulacao", "esp32"):
                        raise ErroRequisicao("Modo inválido.")
                    mudou_modo = dados["modo"] != novo["modo"]
                    novo["modo"] = dados["modo"]
                if "calibracao_padrao" in dados:
                    cp = dados["calibracao_padrao"]
                    novo["calibracao_padrao"] = {"seco": int(cp["seco"]), "molhado": int(cp["molhado"])}
                    if not all(0 <= v <= 4095 for v in novo["calibracao_padrao"].values()):
                        raise ErroRequisicao("A calibração usa a leitura bruta do ESP32 (0 a 4095).")
                    self._conferir_calibracao(None, None, novo["calibracao_padrao"])
                    for c in novo["canteiros"]:
                        self._conferir_calibracao(c.get("seco"), c.get("molhado"), novo["calibracao_padrao"])
            except (TypeError, ValueError, KeyError, OverflowError):
                raise ErroRequisicao("Valor inválido nos ajustes.")
            if novo["tempo_rega"] > novo["limite_bomba"]:
                raise ErroRequisicao("O tempo de rega não pode passar do limite de segurança da bomba.")

            programa_muda = any(novo[k] != self.config[k] for k in ("intervalo", "tempo_rega")) or mudou_modo
            self._salvar(novo)
            if mudou_modo:
                self.registrar("sistema", "Modo ESP32 ativado." if novo["modo"] == "esp32" else "Modo simulação ativado.")
                if novo["modo"] == "esp32" and self.rodando:
                    self._parar_thread()
                    self.rodar()
            if programa_muda and not mudou_modo:
                self.reiniciar_se_preciso("Ajustes alterados")
            return novo

    def voltar_ao_gerado(self):
        with self.lock:
            novo = copy.deepcopy(self.config)
            novo["programa_personalizado"] = None
            self._salvar(novo)
            self.registrar("sistema", "Voltando ao programa gerado pelos canteiros.")
            return self.rodar()

    def _cancelar_pulso(self):
        if self.pulso:
            evento, pino = self.pulso
            self.pulso = None
            evento.set()
            self.hw.acionar("teste", pino, False)

    def testar_bomba(self, pino, segundos):
        with self.lock:
            if self.rodando:
                raise ErroRequisicao("Pare o programa antes de testar a bomba manualmente.")
            if problema_pino_atuador(pino):
                raise ErroRequisicao(problema_pino_atuador(pino))
            segundos = max(1, min(int(segundos), 15))
            self._cancelar_pulso()
            evento = threading.Event()
            self.pulso = (evento, pino)
            self.hw.acionar("teste", pino, True, manual=True)

        def fim_do_pulso():
            # quem cancela o pulso (programa novo, outro teste) já cuidou da bomba
            if not evento.wait(segundos):
                with self.lock:
                    if self.pulso and self.pulso[0] is evento:
                        self._cancelar_pulso()

        threading.Thread(target=fim_do_pulso, daemon=True).start()

    # ---------------- estado para o painel ----------------

    def estado(self, desde=0):
        cfg = self.config
        agora = time.time()
        canteiros = []
        for c in cfg["canteiros"]:
            leitura = self.hw.leitura(c["pino_sensor"])
            programa = self.ultimas_leituras.get(c["pino_sensor"])
            canteiros.append({
                **c,
                **leitura,
                "ultima_verificacao": programa["t"] if programa else None,
                "valor_verificado": programa["valor"] if programa else None,
                "irrigando": self.hw.saida(c["pino_bomba"]),
                "usado_pelo_programa": bool(self.compilacao and c["pino_sensor"] in self.compilacao.sensores.values()),
            })
        with self.lock_console:
            mensagens = [m for m in self.console if m["seq"] > desde]
        espera_ini, espera_fim = self.espera
        _, origem = self.codigo_atual()
        return {
            "sessao": self.sessao,
            "agora": agora,
            "modo": cfg["modo"],
            "esp32": {**self.hw.esp, "online": self.hw.online()},
            "ajustes": {k: cfg[k] for k in ("intervalo", "tempo_rega", "fator_tempo", "limite_bomba",
                                            "rodar_ao_iniciar", "calibracao_padrao")},
            "programa": {
                "rodando": self.rodando,
                "erro": self.erro,
                "origem": origem,
                "iniciado_em": self.iniciado_em,
                "proxima_verificacao": espera_fim if self.rodando and espera_fim > agora else None,
            },
            "canteiros": canteiros,
            "console": mensagens,
            "seq": self.seq,
        }


# =====================================================================
# HTTP
# =====================================================================

app: App = None


def host_confiavel(host):
    """Aceita localhost, IP e nome de rede local. Barra DNS rebinding: um domínio de
    fora apontando para este servidor chega aqui com o próprio nome no cabeçalho Host."""
    try:
        nome = urlsplit("//" + (host or "")).hostname
    except ValueError:
        return False
    if not nome:
        return False
    try:
        ipaddress.ip_address(nome)
        return True
    except ValueError:
        return "." not in nome or nome.endswith(".local")


class Handler(BaseHTTPRequestHandler):
    server_version = "MineC/1.0"

    def log_message(self, fmt, *args):
        pass  # o console da aplicação já registra o que importa

    # ---------------- respostas ----------------

    def _enviar(self, status, corpo: bytes, tipo):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dados, status=200):
        self._enviar(status, json.dumps(dados, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _corpo(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho < 0:
            raise ErroRequisicao("Content-Length inválido.")
        if tamanho > 200_000:
            raise ErroRequisicao("Requisição grande demais.", 413)
        return self.rfile.read(tamanho) if tamanho else b""

    def _corpo_json(self):
        bruto = self._corpo()
        if not bruto:
            return {}
        if "application/json" not in (self.headers.get("Content-Type") or "").lower():
            raise ErroRequisicao("Envie o corpo como application/json.", 415)
        try:
            dados = json.loads(bruto.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ErroRequisicao("JSON inválido.")
        if not isinstance(dados, dict):
            raise ErroRequisicao("O corpo precisa ser um objeto JSON.")
        return dados

    def _conferir_origem(self, metodo):
        """Impede que uma página de outro site comande a bomba pelo navegador de quem
        está na rede. O ESP32 e o curl não mandam Origin; navegador sempre manda em POST."""
        host = self.headers.get("Host")
        if not host_confiavel(host):
            raise ErroRequisicao(f"Host não permitido: {host}. Abra o painel pelo IP ou por localhost.", 403)
        origem = self.headers.get("Origin")
        if metodo != "GET" and origem and urlsplit(origem).netloc != host:
            raise ErroRequisicao("Pedido de outra origem recusado.", 403)

    def _arquivo(self, caminho):
        if caminho in ("", "/"):
            caminho = "/index.html"
        alvo = os.path.abspath(os.path.join(PASTA_WEB, caminho.lstrip("/")))
        if not alvo.startswith(PASTA_WEB + os.sep) or not os.path.isfile(alvo):
            return self._json({"erro": "Não encontrado."}, 404)
        tipo = mimetypes.guess_type(alvo)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo in ("application/javascript",):
            tipo += "; charset=utf-8"
        with open(alvo, "rb") as f:
            self._enviar(200, f.read(), tipo)

    # ---------------- roteamento ----------------

    def _rotear(self, metodo):
        url = urlparse(self.path)
        rota = url.path.rstrip("/") or "/"
        try:
            self._conferir_origem(metodo)
            if metodo == "GET" and not rota.startswith("/api/"):
                return self._arquivo(url.path)

            if metodo == "GET" and rota == "/api/estado":
                desde = int(parse_qs(url.query).get("desde", ["0"])[0] or 0)
                return self._json(app.estado(desde))

            if metodo == "GET" and rota == "/api/programa":
                codigo, origem = app.codigo_atual()
                return self._json({"codigo": codigo, "origem": origem,
                                   "compilacao": app.compilar(codigo).para_dict()})

            if metodo == "POST" and rota == "/api/programa/compilar":
                codigo = str(self._corpo_json().get("codigo", ""))
                return self._json(app.compilar(codigo).para_dict())

            if metodo == "POST" and rota == "/api/programa/rodar":
                dados = self._corpo_json()
                resultado = app.rodar(dados.get("codigo"))
                return self._json(resultado.para_dict(), 200 if resultado.ok else 422)

            if metodo == "POST" and rota == "/api/programa/parar":
                app.parar()
                return self._json({"ok": True})

            if metodo == "POST" and rota == "/api/programa/gerado":
                resultado = app.voltar_ao_gerado()
                return self._json(resultado.para_dict(), 200 if resultado.ok else 422)

            if metodo == "POST" and rota == "/api/canteiros":
                return self._json(app.criar_canteiro(self._corpo_json()), 201)

            if rota.startswith("/api/canteiros/"):
                cid = rota.split("/")[-1]
                if metodo == "PUT":
                    return self._json(app.editar_canteiro(cid, self._corpo_json()))
                if metodo == "DELETE":
                    app.remover_canteiro(cid)
                    return self._json({"ok": True})

            if metodo == "PUT" and rota == "/api/ajustes":
                app.ajustar(self._corpo_json())
                return self._json({"ok": True})

            if metodo == "POST" and rota == "/api/simulacao":
                dados = self._corpo_json()
                app.hw.definir_simulado(int(dados["pino"]), float(dados["umidade"]))
                return self._json({"ok": True})

            if metodo == "POST" and rota == "/api/bomba/testar":
                dados = self._corpo_json()
                app.testar_bomba(int(dados["pino"]), int(dados.get("segundos", 3)))
                return self._json({"ok": True})

            if metodo == "POST" and rota == "/api/esp32/sync":
                texto = self._corpo().decode("utf-8", "replace")
                campos = {}
                for parte in texto.replace("\n", ";").split(";"):
                    if "=" in parte:
                        k, v = parte.split("=", 1)
                        campos[k.strip()] = v.strip()
                resposta = app.hw.sincronizar(campos, self.client_address[0])
                return self._enviar(200, resposta.encode("utf-8"), "text/plain; charset=utf-8")

            return self._json({"erro": "Rota não encontrada."}, 404)
        except ErroRequisicao as exc:
            return self._json({"erro": exc.mensagem}, exc.status)
        except (KeyError, ValueError, TypeError, OverflowError):
            return self._json({"erro": "Dados inválidos na requisição."}, 400)
        except Exception as exc:
            app.registrar("erro", f"Erro no servidor: {exc}")
            return self._json({"erro": "Erro interno no servidor."}, 500)

    def do_GET(self):
        self._rotear("GET")

    def do_POST(self):
        self._rotear("POST")

    def do_PUT(self):
        self._rotear("PUT")

    def do_DELETE(self):
        self._rotear("DELETE")


class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    # No Windows, reaproveitar o endereço deixaria dois servidores na mesma porta sem erro
    allow_reuse_address = os.name != "nt"


def ip_da_rede():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    global app, PASTA_DADOS, ARQUIVO_CONFIG
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="Servidor do protótipo MineC")
    parser.add_argument("--porta", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--dados", default=PASTA_DADOS, help="pasta do config.json (os testes usam uma temporária)")
    args = parser.parse_args()

    PASTA_DADOS = os.path.abspath(args.dados)
    ARQUIVO_CONFIG = os.path.join(PASTA_DADOS, "config.json")
    # a porta vem antes do programa: sem ela, nada pode acionar bomba
    try:
        servidor = Servidor((args.host, args.porta), Handler)
    except OSError as exc:
        print(f"Não consegui usar a porta {args.porta} ({exc}).\n"
              "Talvez o servidor já esteja rodando em outro terminal. Feche-o ou use --porta.")
        sys.exit(1)

    app = App()
    if app.config.get("rodar_ao_iniciar"):
        try:
            app.rodar()
        except Exception as exc:  # o painel precisa subir para o problema poder ser corrigido
            app.erro = f"Não consegui iniciar o programa salvo: {exc}"
            app.registrar("erro", app.erro)

    ip = ip_da_rede()
    print("")
    print("  MineC rodando!")
    print(f"  Painel neste computador:  http://localhost:{args.porta}")
    print(f"  Painel no celular (mesma rede Wi-Fi): http://{ip}:{args.porta}")
    print(f"  No firmware do ESP32 use: SERVIDOR = \"http://{ip}:{args.porta}\"")
    print("")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nDesligando: parando o programa e as bombas...")
        app.parar()


if __name__ == "__main__":
    main()
