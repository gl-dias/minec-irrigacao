"""
Camada de hardware do protótipo.

Dois modos, trocados pelo painel:

  * esp32      : o ESP32 conversa com o servidor a cada ~0,5 s (POST /api/esp32/sync),
                 mandando as leituras brutas do ADC e recebendo o estado que cada
                 relé deve ter. Se o ESP32 ficar sem resposta, ele mesmo desliga
                 os relés (failsafe no firmware).
  * simulacao  : nada de hardware; a umidade de cada sensor é simulada e sobe
                 enquanto a bomba do canteiro está ligada.

O interpretador MineC só enxerga ler_sensor() e acionar().
"""

import threading
import time
from typing import Callable, Dict, Optional

TEMPO_OFFLINE = 5.0        # s sem sync = ESP32 desconectado
ESPERA_LEITURA_NOVA = 4.0  # s esperando uma leitura fresca para o 'mine'
PERIODO_SYNC_MS = 500
SEM_LEITURA = -1           # valor do 'mine' quando não há leitura confiável
SECAGEM_POR_SEG = 0.05     # % por segundo (simulação)
REGA_POR_SEG = 2.0         # % por segundo com a bomba ligada (simulação)


def bruto_para_umidade(bruto: float, seco: float, molhado: float) -> int:
    """Sensor capacitivo: quanto mais água, MENOR a tensão (leitura bruta)."""
    if seco == molhado:
        return 0
    pct = (seco - bruto) / (seco - molhado) * 100.0
    return int(round(max(0.0, min(100.0, pct))))


def leitura_plausivel(bruto: float) -> bool:
    """Sensor solto costuma ler 0 (fio aberto) ou 4095 (saturado)."""
    return 50 < bruto < 4090


def umidade_para_bruto(pct: float, seco: float, molhado: float) -> int:
    return int(round(seco - (pct / 100.0) * (seco - molhado)))


class Hardware:
    def __init__(self, obter_config: Callable[[], dict], registrar: Callable[[str, str], None],
                 pinos_leitura: Callable[[], set], pinos_saida: Callable[[], set]):
        self.cfg = obter_config
        self.registrar = registrar
        self._pinos_leitura = pinos_leitura
        self._pinos_saida = pinos_saida

        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.leituras: Dict[int, dict] = {}       # pino -> {"bruto", "t"}
        self.saidas: Dict[int, bool] = {}         # pino -> ligado?
        self.ligado_desde: Dict[int, float] = {}
        self.sim: Dict[int, float] = {}           # pino -> umidade simulada (%)
        self._ultima_simulacao = time.time()
        self.travadas: set = set()                # pinos cortados pelo limite de segurança
        self._recusas: set = set()                # (pino, motivo) já avisados no console
        self.esp = {"ultimo": 0.0, "ip": None, "rssi": None, "id": None, "fw": None}
        self._estava_online = False

        threading.Thread(target=self._laco, daemon=True, name="hardware").start()

    # ------------------------------------------------------------------
    # consultas
    # ------------------------------------------------------------------

    @property
    def modo(self) -> str:
        return self.cfg().get("modo", "simulacao")

    def fator_tempo(self) -> float:
        if self.modo != "simulacao":
            return 1.0
        return float(self.cfg().get("fator_tempo", 1) or 1)

    def online(self) -> bool:
        return time.time() - self.esp["ultimo"] < TEMPO_OFFLINE

    def calibracao(self, pino: int):
        cfg = self.cfg()
        padrao = cfg.get("calibracao_padrao", {"seco": 3000, "molhado": 1300})
        for c in cfg.get("canteiros", []):
            if c.get("pino_sensor") == pino:
                seco, molhado = c.get("seco"), c.get("molhado")
                return (padrao["seco"] if seco is None else seco, padrao["molhado"] if molhado is None else molhado)
        return padrao["seco"], padrao["molhado"]

    def leitura(self, pino: int) -> dict:
        """Situação atual do sensor de um pino, para o painel."""
        with self.lock:
            seco, molhado = self.calibracao(pino)
            if self.modo == "simulacao":
                self._integrar_simulacao()
                pct = self._sim_valor(pino)
                return {"umidade": int(round(pct)), "bruto": umidade_para_bruto(pct, seco, molhado),
                        "t": time.time(), "ok": True, "situacao": "simulado"}
            l = self.leituras.get(pino)
            if not l:
                return {"umidade": None, "bruto": None, "t": None, "ok": False,
                        "situacao": "sem_esp32" if not self.online() else "aguardando"}
            fresca = time.time() - l["t"] < TEMPO_OFFLINE
            situacao = "ok" if fresca and leitura_plausivel(l["bruto"]) else ("sem_sinal" if fresca else "sem_esp32")
            return {"umidade": bruto_para_umidade(l["bruto"], seco, molhado), "bruto": l["bruto"],
                    "t": l["t"], "ok": situacao == "ok", "situacao": situacao}

    def saida(self, pino: int) -> bool:
        with self.lock:
            return self.saidas.get(pino, False)

    # ------------------------------------------------------------------
    # interface usada pelo interpretador
    # ------------------------------------------------------------------

    def ler_sensor(self, nome: str, pino: int, parar: threading.Event):
        if self.modo == "simulacao":
            with self.lock:
                self._integrar_simulacao()
                return int(round(self._sim_valor(pino)))

        pedido = time.time()
        avisou = False
        with self.cond:
            while not parar.is_set():
                l = self.leituras.get(pino)
                if l and l["t"] >= pedido:
                    if not leitura_plausivel(l["bruto"]):
                        self.registrar("aviso", f"Sensor '{nome}' (GPIO {pino}) sem sinal: leitura bruta {l['bruto']}. Confira os fios.")
                        return SEM_LEITURA
                    seco, molhado = self.calibracao(pino)
                    return bruto_para_umidade(l["bruto"], seco, molhado)
                if time.time() - pedido > ESPERA_LEITURA_NOVA:
                    if l:
                        self.registrar("aviso", f"ESP32 sem resposta: não há leitura nova de '{nome}'.")
                        return SEM_LEITURA
                    if not avisou:
                        self.registrar("aviso", f"Aguardando o ESP32 enviar a leitura do sensor '{nome}' (GPIO {pino})...")
                        avisou = True
                self.cond.wait(0.25)
        return 0

    def _motivo_de_recusa(self, pino: int) -> Optional[str]:
        """Por que esta bomba não pode ligar agora (None = pode)."""
        if pino in self.travadas:
            return "passou do limite de segurança e só volta a ligar depois de um 'unpower'"
        if self.modo != "esp32":
            return None
        cfg = self.cfg()
        personalizado = cfg.get("programa_personalizado")
        agora = time.time()
        for sensor in self._pinos_leitura():
            l = self.leituras.get(sensor)
            if not l or agora - l["t"] >= TEMPO_OFFLINE or leitura_plausivel(l["bruto"]):
                continue
            # no programa gerado cada sensor decide a própria bomba; num programa
            # personalizado essa ligação é desconhecida, então vale para todas
            bombas = {c.get("pino_bomba") for c in cfg.get("canteiros", []) if c.get("pino_sensor") == sensor}
            if personalizado or not bombas or pino in bombas:
                return f"o sensor do GPIO {sensor} está sem sinal"
        return None

    def acionar(self, nome: str, pino: int, ligado: bool, manual: bool = False):
        """`manual` é o teste de bomba do painel, que só existe com o programa parado."""
        with self.lock:
            self._integrar_simulacao()
            antes = self.saidas.get(pino, False)
            if ligado and not antes and not manual:
                motivo = self._motivo_de_recusa(pino)
                if motivo:
                    if (pino, motivo) not in self._recusas:
                        self._recusas.add((pino, motivo))
                        self.registrar("aviso", f"'{nome}' (GPIO {pino}) não ligou: {motivo}.")
                    return
            self.saidas[pino] = ligado
            if ligado and not antes:
                self.ligado_desde[pino] = time.time()
            if not ligado:
                self.ligado_desde.pop(pino, None)
                self.travadas.discard(pino)
            self._recusas = {r for r in self._recusas if r[0] != pino}
        if ligado != antes:
            self.registrar("bomba", f"{'Ligou' if ligado else 'Desligou'} '{nome}' (GPIO {pino}).")
        if ligado and self.modo == "esp32" and not self.online():
            self.registrar("aviso", "O ESP32 está desconectado: o relé só vai ligar quando ele voltar.")

    def desligar_tudo(self):
        with self.lock:
            self._integrar_simulacao()
            for pino in list(self.saidas):
                self.saidas[pino] = False
            self.ligado_desde.clear()
            self.travadas.clear()
            self._recusas.clear()

    # ------------------------------------------------------------------
    # ESP32
    # ------------------------------------------------------------------

    def sincronizar(self, campos: Dict[str, str], ip: Optional[str]) -> str:
        """Recebe as leituras do ESP32 e devolve o que ele deve fazer."""
        agora = time.time()
        with self.cond:
            self.esp.update(ultimo=agora, ip=ip, rssi=campos.get("rssi"),
                            id=campos.get("id"), fw=campos.get("fw"))
            for chave, valor in campos.items():
                if chave.startswith("a") and chave[1:].isdigit():
                    try:
                        self.leituras[int(chave[1:])] = {"bruto": int(float(valor)), "t": agora}
                    except (ValueError, OverflowError):
                        pass
            self.cond.notify_all()

            ler = sorted(self._pinos_leitura())
            # só o que está em uso ou ainda ligado: o firmware libera os outros pinos
            saidas = sorted(set(self._pinos_saida()) | {p for p, ligada in self.saidas.items() if ligada})
            # no modo simulação o ESP32 conectado não aciona nada
            ativo = self.modo == "esp32"
            estados = [f"{p}:{1 if (ativo and self.saidas.get(p)) else 0}" for p in saidas]
            limite = int(self.cfg().get("limite_bomba", 120))

        return (f"ler={','.join(map(str, ler))}\n"
                f"saidas={','.join(estados)}\n"
                f"periodo={PERIODO_SYNC_MS}\n"
                f"limite={limite}\n")

    # ------------------------------------------------------------------
    # simulação e segurança
    # ------------------------------------------------------------------

    def _sim_valor(self, pino: int) -> float:
        if pino not in self.sim:
            self.sim[pino] = 28.0 + 14.0 * (len(self.sim) % 3)
        return self.sim[pino]

    def definir_simulado(self, pino: int, umidade: float):
        with self.lock:
            self._integrar_simulacao()
            self.sim[pino] = max(0.0, min(100.0, float(umidade)))

    def _integrar_simulacao(self):
        """Aplica o tempo passado com o estado atual das bombas. Roda também a cada
        liga/desliga, para uma rega curta na simulação acelerada não se perder entre dois passos."""
        agora = time.time()
        dt = agora - self._ultima_simulacao
        self._ultima_simulacao = agora
        if self.modo != "simulacao":
            return
        fator = self.fator_tempo()
        for pino in set(self._pinos_leitura()):
            regando = any(self.saidas.get(b) for b in self._bombas_do_sensor(pino))
            valor = self._sim_valor(pino) + (REGA_POR_SEG if regando else -SECAGEM_POR_SEG) * dt * fator
            self.sim[pino] = max(0.0, min(100.0, valor))

    def _bombas_do_sensor(self, pino_sensor: int):
        bombas = {c.get("pino_bomba") for c in self.cfg().get("canteiros", []) if c.get("pino_sensor") == pino_sensor}
        return bombas or set(self.saidas)

    def _laco(self):
        while True:
            time.sleep(0.5)
            try:
                self._passo(time.time())
            except Exception as exc:  # o laço de segurança nunca pode morrer
                self.registrar("erro", f"Falha no laço de hardware: {exc}")

    def _passo(self, agora: float):
        cfg = self.cfg()
        fator = self.fator_tempo()
        limite = float(cfg.get("limite_bomba", 120))

        with self.lock:
            # segurança: nenhuma bomba fica ligada além do limite
            for pino, desde in list(self.ligado_desde.items()):
                if (agora - desde) * fator > limite:
                    self.saidas[pino] = False
                    self.ligado_desde.pop(pino, None)
                    self.travadas.add(pino)
                    self.registrar("aviso", f"Segurança: bomba do GPIO {pino} desligada após {int(limite)} s ligada sem parar. Só volta a ligar depois de um 'unpower'.")

            self._integrar_simulacao()

        online = self.online()
        if self.modo == "esp32" and online != self._estava_online:
            if online:
                self.registrar("sistema", f"ESP32 conectado ({self.esp.get('ip')}).")
            else:
                self.registrar("aviso", "ESP32 desconectado. Os relés foram desligados por segurança.")
        self._estava_online = online
