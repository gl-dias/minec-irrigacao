#!/usr/bin/env python3
"""
ESP32 virtual: substitui a parte física do protótipo numa demonstração.

Fala com o servidor pelo mesmo protocolo do firmware (POST /api/esp32/sync) e repete
a lógica de minec_esp32.ino: validação de pinos, corte sem servidor e limite do relé.
O servidor roda no modo "ESP32 (hardware)" e não sabe que a placa é simulada.

O que é simulado aqui: a placa, o sensor (leitura bruta de 0 a 4095), o relé, a bomba
e o solo. O solo seca e molha bem mais rápido que na vida real, para caber na apresentação.

    python3 esp32/simulador_esp32.py --demo     # sobe o servidor, o painel e a placa
    python3 esp32/simulador_esp32.py            # só a placa, com o servidor já rodando

Sem dependências além do Python 3.9+.
"""

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from urllib.parse import urlsplit

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
PASTA_DEMO = os.path.join(RAIZ, "servidor", "dados", "demo")

# mesmos valores de minec_esp32.ino
ID_PLACA = "minec-esp32-virtual"
VERSAO_FW = "1.0-sim"
FAILSAFE_S = 1.8
HTTP_PRAZO_S = 1.5
MAX_PINOS = 8
PINOS_SENSOR_OK = {32, 33, 34, 35, 36, 39}
PINOS_RELE_OK = {2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33}

# leitura do sensor no ar e na água: os mesmos números da calibração padrão do painel
ADC_SECO = 3000
ADC_MOLHADO = 1300

UMIDADE_INICIAL = 38.0

AJUDA = """Comandos (digite e aperte Enter):
  umidade N [pino]  coloca o solo em N% (ex.: umidade 15)
  fio               solta/reconecta o fio do sensor (painel: Sem sinal)
  wifi              derruba/volta o Wi-Fi (corte de segurança dos relés)
  ar | agua | solo  põe o sensor no ar, num copo d'água ou de volta na terra (calibração)
  status            mostra o estado da bancada
  sair              encerra"""

# sem proxy: a conversa é sempre direta com o servidor
_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def serial(texto):
    print(texto, flush=True)


class Bancada:
    """A parte física: solo e sensor."""

    def __init__(self, secagem, rega):
        self.secagem = secagem
        self.rega = rega
        self.umidade = {}          # pino do sensor -> % de água no solo
        self.bombas_do_sensor = {} # pino do sensor -> pinos das bombas que o molham
        self.fio_solto = False
        self.posicao = "solo"      # solo | ar | agua

    def adc(self, pino):
        self.umidade.setdefault(pino, UMIDADE_INICIAL)
        if self.fio_solto:
            return 0
        pct = {"ar": 0.0, "agua": 100.0}.get(self.posicao, self.umidade[pino])
        ruido = random.randint(-6, 6)
        return int(round(ADC_SECO - pct / 100.0 * (ADC_SECO - ADC_MOLHADO))) + ruido

    def avancar(self, dt, bombas_ligadas):
        for pino, valor in list(self.umidade.items()):
            # sensor sem canteiro conhecido: qualquer bomba ligada conta, como no servidor
            bombas = self.bombas_do_sensor.get(pino)
            regando = bool(bombas_ligadas & bombas) if bombas else bool(bombas_ligadas)
            valor += (self.rega if regando else -self.secagem) * dt
            self.umidade[pino] = max(0.0, min(100.0, valor))


class Saida:
    def __init__(self, pino):
        self.pino = pino
        self.desejado = False  # o que o servidor pediu
        self.ligado = False    # estado real do relé
        self.bloqueado = False # desligado pelo limite de segurança
        self.ligado_desde = 0.0


class Placa:
    """Tradução de minec_esp32.ino, mantendo os nomes e as mensagens do Monitor Serial."""

    def __init__(self, servidor, bancada):
        self.servidor = servidor.rstrip("/")
        self.bancada = bancada
        self.leitura = []
        self.saidas = {}
        self.periodo = 0.5
        self.limite = 120.0
        self.ultimo_sync_ok = 0.0
        self.ultimo_ciclo = 0.0
        self.em_failsafe = True
        self.wifi = True
        self.rodando = True
        self.falha_avisada = False
        self.recusas_atuais = []
        self.recusas_avisadas = []

    # ---------------- relés ----------------

    def aplicar(self, s, ligar):
        if s.ligado == ligar:
            return
        s.ligado = ligar
        if ligar:
            s.ligado_desde = time.monotonic()
        serial(f"[relé] GPIO {s.pino} {'LIGADO' if ligar else 'desligado'}")

    def recusar(self, pino, uso, motivo):
        self.recusas_atuais.append(f"[pinos] GPIO {pino} recusado como {uso}: {motivo}")

    def registrar_saida(self, pino):
        if pino in self.saidas:
            return self.saidas[pino]
        if pino not in PINOS_RELE_OK:
            return self.recusar(pino, "saída", "pino não permitido")
        if pino in self.leitura:
            return self.recusar(pino, "saída", "está em uso como sensor")
        if len(self.saidas) >= MAX_PINOS:
            return self.recusar(pino, "saída", "lista de saídas cheia")
        self.saidas[pino] = Saida(pino)
        serial(f"[relé] GPIO {pino} configurado")
        return self.saidas[pino]

    # ---------------- protocolo ----------------

    def interpretar(self, corpo):
        campos = dict(l.strip().split("=", 1) for l in corpo.splitlines() if "=" in l)
        self.recusas_atuais = []
        if "periodo" in campos:
            self.periodo = min(1000, max(200, int(campos["periodo"]))) / 1000.0
        if "limite" in campos:
            self.limite = float(min(1800, max(5, int(campos["limite"]))))

        # mesma ordem do firmware: a troca de função de um pino vale no mesmo sync
        itens = []
        if "saidas" in campos:
            itens = [i.split(":") for i in campos["saidas"].split(",") if ":" in i]
            na_lista = {int(p) for p, _ in itens}
            for pino in [p for p in self.saidas if p not in na_lista]:
                self.aplicar(self.saidas.pop(pino), False)
                serial(f"[relé] GPIO {pino} liberado, voltou a ser entrada")
        if "ler" in campos:
            self.leitura = []
            for pino in (int(p) for p in campos["ler"].split(",") if p):
                if pino not in PINOS_SENSOR_OK:
                    self.recusar(pino, "sensor", "pino não permitido")
                elif pino in self.saidas:
                    self.recusar(pino, "sensor", "está em uso como saída")
                elif len(self.leitura) >= MAX_PINOS:
                    self.recusar(pino, "sensor", "lista de sensores cheia")
                else:
                    self.leitura.append(pino)
        for pino, valor in itens:
            s = self.registrar_saida(int(pino))
            if s:
                ligar = int(valor) == 1
                if not ligar:
                    s.bloqueado = False  # servidor reconheceu o desligamento
                s.desejado = ligar

        if self.recusas_atuais != self.recusas_avisadas:
            for linha in self.recusas_atuais or ["[pinos] nenhum pino recusado agora"]:
                serial(linha)
            self.recusas_avisadas = self.recusas_atuais

    def sincronizar(self):
        linhas = [f"id={ID_PLACA}", f"fw={VERSAO_FW}", f"rssi={random.randint(-62, -54)}"]
        linhas += [f"a{p}={self.bancada.adc(p)}" for p in self.leitura]
        pedido = urllib.request.Request(self.servidor + "/api/esp32/sync", method="POST",
                                        data=("\n".join(linhas) + "\n").encode(),
                                        headers={"Content-Type": "text/plain"})
        try:
            with _http.open(pedido, timeout=HTTP_PRAZO_S) as resposta:
                self.interpretar(resposta.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if not self.falha_avisada:
                serial(f"[sync] falhou: {exc}")
                self.falha_avisada = True
            return False
        self.falha_avisada = False
        return True

    # ---------------- segurança ----------------

    def verificar_seguranca(self):
        agora = time.monotonic()
        sem_servidor = agora - self.ultimo_sync_ok > FAILSAFE_S
        if sem_servidor and not self.em_failsafe:
            serial("[segurança] sem resposta do servidor: relés desligados")
        self.em_failsafe = sem_servidor
        for s in self.saidas.values():
            if s.ligado and agora - s.ligado_desde > self.limite:
                s.bloqueado = True
                serial(f"[segurança] GPIO {s.pino} passou do limite de tempo ligado")
            self.aplicar(s, s.desejado and not s.bloqueado and not self.em_failsafe)

    # ---------------- laços ----------------

    def status(self):
        b = self.bancada
        sensores = ", ".join(f"GPIO {p}: {b.umidade.get(p, 0):.0f}% (ADC {b.adc(p)})" for p in self.leitura) or "nenhum"
        bombas = ", ".join(f"GPIO {s.pino}: {'LIGADA' if s.ligado else 'desligada'}" for s in self.saidas.values()) or "nenhuma"
        extras = []
        if b.fio_solto:
            extras.append("FIO SOLTO")
        if b.posicao != "solo":
            extras.append(f"sensor no(a) {b.posicao}")
        if not self.wifi:
            extras.append("SEM WI-FI")
        serial(f"[bancada] solo {sensores} | bomba {bombas}" + (f" | {', '.join(extras)}" if extras else ""))

    def laco(self):
        anterior = time.monotonic()
        proximo_status = anterior + 5
        while self.rodando:
            agora = time.monotonic()
            ligadas = {s.pino for s in self.saidas.values() if s.ligado}
            self.bancada.avancar(agora - anterior, ligadas)
            anterior = agora

            if self.wifi and agora - self.ultimo_ciclo >= self.periodo:
                self.ultimo_ciclo = agora
                if self.sincronizar():
                    if self.em_failsafe:
                        serial("[sync] servidor conectado")
                    self.ultimo_sync_ok = time.monotonic()
                    self.em_failsafe = False

            self.verificar_seguranca()
            if agora >= proximo_status:
                proximo_status = agora + 5
                self.status()
            time.sleep(0.02)

    def laco_canteiros(self):
        """Descobre qual bomba molha qual sensor: na bancada real, isso é a mangueira."""
        while self.rodando:
            if self.wifi:
                try:
                    with _http.open(self.servidor + "/api/estado?desde=999999999", timeout=2) as r:
                        estado = json.load(r)
                    mapa = {}
                    for c in estado.get("canteiros", []):
                        mapa.setdefault(c["pino_sensor"], set()).add(c["pino_bomba"])
                    self.bancada.bombas_do_sensor = mapa
                except (urllib.error.URLError, OSError, ValueError, KeyError):
                    pass
            time.sleep(2)

    def executar(self, linha):
        partes = linha.lower().split()
        if not partes:
            return
        b = self.bancada
        comando = partes[0]
        if comando == "fio":
            b.fio_solto = not b.fio_solto
            serial(f"[bancada] fio do sensor {'SOLTO' if b.fio_solto else 'reconectado'}")
        elif comando == "wifi":
            self.wifi = not self.wifi
            serial(f"[wifi] {'conectado' if self.wifi else 'sinal perdido'}")
        elif comando in ("ar", "agua", "água", "solo"):
            b.posicao = comando.replace("á", "a")
            serial(f"[bancada] sensor em: {b.posicao}")
        elif comando == "umidade" and len(partes) >= 2:
            try:
                valor = max(0.0, min(100.0, float(partes[1].replace(",", "."))))
                pinos = [int(partes[2])] if len(partes) > 2 else (list(b.umidade) or self.leitura)
            except ValueError:
                return serial("Use: umidade N [pino]")
            for pino in pinos:
                b.umidade[pino] = valor
            serial(f"[bancada] solo em {valor:.0f}%")
        elif comando == "status":
            self.status()
        elif comando in ("sair", "exit", "quit"):
            self.rodando = False
        else:
            serial(AJUDA)

    def laco_teclado(self):
        try:
            for linha in sys.stdin:
                self.executar(linha)
                if not self.rodando:
                    return
        except (OSError, ValueError):
            pass


# ---------------- modo --demo ----------------

def porta_ocupada(porta):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def subir_servidor(porta):
    if porta_ocupada(porta):
        sys.exit(f"A porta {porta} já está em uso. Feche o outro servidor ou use --porta.")

    # pasta própria, recriada a cada demo: a configuração normal do projeto não é tocada
    os.makedirs(PASTA_DEMO, exist_ok=True)
    with open(os.path.join(PASTA_DEMO, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"modo": "esp32", "intervalo": 10, "tempo_rega": 3}, f)

    processo = subprocess.Popen(
        [sys.executable, os.path.join(RAIZ, "servidor", "servidor.py"),
         "--porta", str(porta), "--host", "127.0.0.1", "--dados", PASTA_DEMO],
        stdout=subprocess.DEVNULL)
    for _ in range(100):
        if processo.poll() is not None:
            sys.exit("O servidor não conseguiu iniciar.")
        if porta_ocupada(porta):
            return processo
        time.sleep(0.1)
    processo.terminate()
    sys.exit("O servidor demorou demais para responder.")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="ESP32 virtual do protótipo MineC")
    parser.add_argument("--demo", action="store_true", help="sobe também o servidor (já no modo ESP32) e abre o painel")
    parser.add_argument("--servidor", help="endereço do servidor (padrão: http://127.0.0.1:PORTA)")
    parser.add_argument("--porta", type=int, default=8000)
    parser.add_argument("--secagem", type=float, default=0.6, help="%% de umidade perdida por segundo")
    parser.add_argument("--rega", type=float, default=5.0, help="%% de umidade ganha por segundo com a bomba ligada")
    parser.add_argument("--sem-navegador", action="store_true", help="no --demo, não abre o painel sozinho")
    args = parser.parse_args()

    endereco = args.servidor or f"http://127.0.0.1:{args.porta}"
    if urlsplit(endereco).scheme != "http":
        sys.exit("Use um endereço http://, como no firmware.")

    processo = subir_servidor(args.porta) if args.demo else None
    placa = Placa(endereco, Bancada(args.secagem, args.rega))

    serial("\n=== MineC ESP32 VIRTUAL (nenhum hardware real) ===")
    serial(f"Painel: http://localhost:{args.porta}" if args.demo else f"Servidor: {endereco}")
    serial(AJUDA + "\n")

    threading.Thread(target=placa.laco_canteiros, daemon=True).start()
    threading.Thread(target=placa.laco_teclado, daemon=True).start()
    if args.demo and not args.sem_navegador:
        webbrowser.open(f"http://localhost:{args.porta}")

    try:
        placa.laco()
    except KeyboardInterrupt:
        pass
    finally:
        placa.rodando = False
        if processo:
            processo.terminate()
            processo.wait(timeout=5)
        serial("\nDemonstração encerrada.")


if __name__ == "__main__":
    main()
