"""
Testes do servidor de verdade: sobe servidor.py numa porta livre, com pasta de dados
temporária, e conversa por HTTP (o "ESP32" aqui é só um cliente que imita o protocolo).

    python servidor/testes_api.py
"""

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from hardware import Hardware, leitura_plausivel  # noqa: E402

SERVIDOR = os.path.join(AQUI, "servidor.py")


def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp(prefix="minec-teste-")
        self.addCleanup(shutil.rmtree, self.pasta, ignore_errors=True)
        self.porta = porta_livre()
        self.processo = None
        self.addCleanup(self.derrubar)

    def subir(self, config=None, esperar=True):
        self.derrubar()  # um subTest que falhou no meio não pode deixar servidor vivo
        if config is not None:
            with open(os.path.join(self.pasta, "config.json"), "w", encoding="utf-8") as f:
                f.write(config if isinstance(config, str) else json.dumps(config))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        self.saida = open(os.path.join(self.pasta, "saida.log"), "w", encoding="utf-8")
        self.processo = subprocess.Popen(
            [sys.executable, SERVIDOR, "--porta", str(self.porta), "--host", "127.0.0.1", "--dados", self.pasta],
            stdout=self.saida, stderr=subprocess.STDOUT, env=env)
        if esperar:
            self.esperar(lambda: self.req("GET", "/api/estado")[0] == 200, "o servidor não subiu", tolerar=OSError)

    def derrubar(self):
        if self.processo:
            self.processo.kill()
            self.processo.wait()
            self.saida.close()
            self.processo = None

    def req(self, metodo, rota, corpo=None, cru=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
        h = dict(headers or {})
        dados = cru
        if corpo is not None:
            dados = json.dumps(corpo).encode()
            h.setdefault("Content-Type", "application/json")
        try:
            c.request(metodo, rota, body=dados, headers=h)
            r = c.getresponse()
            b = r.read()
        finally:
            c.close()
        if "json" in (r.getheader("Content-Type") or ""):
            return r.status, json.loads(b)
        return r.status, b.decode("utf-8", "replace")

    def estado(self):
        return self.req("GET", "/api/estado")[1]

    def esperar(self, condicao, mensagem, tempo=8.0, tolerar=()):
        limite = time.time() + tempo
        while time.time() < limite:
            try:
                if condicao():
                    return
            except tolerar:
                pass
            time.sleep(0.05)
        self.fail(mensagem)

    def sync(self, corpo=b""):
        return self.req("POST", "/api/esp32/sync", cru=corpo)[1]

    def console(self, *tipos):
        return [m["texto"] for m in self.estado()["console"] if m["tipo"] in tipos]

    def config_salva(self):
        with open(os.path.join(self.pasta, "config.json"), encoding="utf-8") as f:
            return json.load(f)


class TesteProgramaSalvo(Base):
    def test_codigo_com_erro_nao_troca_o_programa_salvo(self):
        self.subir()
        antes = self.req("GET", "/api/programa")[1]["codigo"]
        st, d = self.req("POST", "/api/programa/rodar", {"codigo": "craft x = "})
        self.assertEqual(st, 422)
        self.assertFalse(d["ok"])
        pg = self.req("GET", "/api/programa")[1]
        self.assertEqual(pg["codigo"], antes)
        self.assertEqual(pg["origem"], "gerado")
        self.assertTrue(self.estado()["programa"]["rodando"])
        self.derrubar()
        self.subir()
        self.assertTrue(self.estado()["programa"]["rodando"])

    def test_codigo_valido_e_salvo_e_sobrevive_ao_reinicio(self):
        self.subir()
        st, _ = self.req("POST", "/api/programa/rodar", {"codigo": 'say "meu"\nwait 3600'})
        self.assertEqual(st, 200)
        self.assertEqual(self.config_salva()["programa_personalizado"], 'say "meu"\nwait 3600')
        self.derrubar()
        self.subir()
        p = self.estado()["programa"]
        self.assertTrue(p["rodando"])
        self.assertEqual(p["origem"], "personalizado")

    def test_entradas_extremas_nao_derrubam_a_proxima_subida(self):
        extremos = [
            "craft x = " + "9" * 5000,
            "say " + "+".join(["1"] * 2000),
            "say " + "(" * 500 + "1" + ")" * 500,
            "if true {" * 300 + "}" * 300,
        ]
        self.subir()
        for codigo in extremos:
            with self.subTest(codigo=codigo[:20]):
                st, d = self.req("POST", "/api/programa/rodar", {"codigo": codigo})
                self.assertEqual(st, 422, d)
                self.assertTrue(d["erros"])
        self.assertEqual(self.req("GET", "/api/programa")[0], 200)
        self.derrubar()
        self.subir()
        self.assertTrue(self.estado()["programa"]["rodando"])

    def test_codigo_que_nao_e_texto_e_recusado_sem_gravar(self):
        self.subir()
        self.assertEqual(self.req("POST", "/api/programa/rodar", {"codigo": 123})[0], 400)
        self.assertEqual(self.req("GET", "/api/programa")[0], 200)

    def test_config_antiga_envenenada_ainda_sobe(self):
        for config in ({"programa_personalizado": 123},
                       {"programa_personalizado": "craft x = " + "9" * 5000},
                       {"canteiros": [{"nome": "a"}]},
                       {"canteiros": None},
                       {"intervalo": "abc"},
                       "[1, 2]",
                       "{quebrado"):
            with self.subTest(config=str(config)[:30]):
                self.subir(config)
                try:
                    self.assertEqual(self.req("GET", "/api/programa")[0], 200)
                finally:
                    self.derrubar()

    def test_programa_salvo_com_erro_aparece_no_estado(self):
        self.subir({"programa_personalizado": "craft x = "})
        p = self.estado()["programa"]
        self.assertFalse(p["rodando"])
        self.assertIn("Sintática", p["erro"])


class TesteSensorInvalido(Base):
    CONFIG = {"modo": "esp32", "intervalo": 5, "tempo_rega": 2}

    def rele_nunca_liga(self, corpo, segundos=3.0):
        ligou = False
        fim = time.time() + segundos
        while time.time() < fim:
            ligou = ligou or "26:1" in self.sync(corpo)
            time.sleep(0.2)
        self.assertFalse(ligou, "o relé ligou com sensor sem sinal")

    def test_saturado_nao_rega(self):
        self.subir(self.CONFIG)
        self.rele_nunca_liga(b"a34=4095")
        self.assertEqual(self.estado()["canteiros"][0]["situacao"], "sem_sinal")
        self.assertTrue(any("sem leitura válida" in t for t in self.console("say")), self.console("say"))

    def test_fio_aberto_nao_finge_solo_molhado(self):
        self.subir(self.CONFIG)
        self.rele_nunca_liga(b"a34=0")
        self.assertFalse(any("umidade ok" in t for t in self.console("say")), self.console("say"))

    def test_leitura_boa_seca_continua_regando(self):
        self.subir(self.CONFIG)
        self.esperar(lambda: "26:1" in self.sync(b"a34=3000"), "solo seco de verdade precisa regar")

    def test_programa_personalizado_tambem_e_barrado(self):
        codigo = 'observer u = 34\nlever b = 27\nmine u\nif u < 30 {\n power b\n wait 60\n}'
        self.subir({**self.CONFIG, "programa_personalizado": codigo})
        fim = time.time() + 2.5
        while time.time() < fim:
            self.assertNotIn("27:1", self.sync(b"a34=4095"))
            time.sleep(0.2)
        self.assertTrue(any("não ligou" in t for t in self.console("aviso")), self.console("aviso"))

    def test_outro_canteiro_com_sensor_bom_continua_regando(self):
        canteiros = [
            {"id": "a", "nome": "A", "variavel": "a", "pino_sensor": 34, "pino_bomba": 26, "minimo": 30},
            {"id": "b", "nome": "B", "variavel": "b", "pino_sensor": 35, "pino_bomba": 27, "minimo": 30},
        ]
        self.subir({**self.CONFIG, "canteiros": canteiros})
        self.esperar(lambda: "27:1" in self.sync(b"a34=4095\na35=3000"), "a bomba do sensor bom deveria ligar")

    def test_esp32_some_nao_decide_com_leitura_velha(self):
        h = _hardware({"modo": "esp32"})
        h.sincronizar({"a34": "3000"}, "x")
        time.sleep(0.05)  # o relógio do Windows tem passo de cerca de 15 ms
        parar = threading.Event()
        # sem sync novo: a espera de 4 s acaba em SEM_LEITURA, nunca na leitura velha
        self.assertEqual(h.ler_sensor("u", 34, parar), -1)


class TesteLimiteDaBomba(Base):
    def test_power_em_laco_nao_religa_depois_do_corte(self):
        self.subir({"modo": "esp32", "limite_bomba": 5, "rodar_ao_iniciar": False})
        self.req("POST", "/api/programa/rodar", {"codigo": "lever b = 26\nwhile true {\n power b\n wait 1\n}"})
        self.esperar(lambda: "26:1" in self.sync(), "a bomba deveria ligar")
        self.esperar(lambda: "26:0" in self.sync(), "o limite deveria cortar a bomba", tempo=8)
        fim = time.time() + 2.5  # bem mais que o 'wait 1' do laço
        while time.time() < fim:
            self.assertIn("26:0", self.sync())
            time.sleep(0.2)

    def test_unpower_libera_depois_do_corte(self):
        h = _hardware({"modo": "simulacao", "limite_bomba": 5})
        h.acionar("b", 26, True)
        h.ligado_desde[26] -= 10
        h._passo(time.time())
        self.assertFalse(h.saida(26))
        h.acionar("b", 26, True)
        self.assertFalse(h.saida(26), "travada até o unpower")
        h.acionar("b", 26, False)
        h.acionar("b", 26, True)
        self.assertTrue(h.saida(26))


class TesteOrigem(Base):
    CODIGO = {"codigo": "lever b = 26\npower b\nwait 60"}

    def setUp(self):
        super().setUp()
        self.subir({"modo": "esp32", "rodar_ao_iniciar": False})

    def assertRecusado(self, status, resposta):
        self.assertEqual(resposta[0], status, resposta)
        self.assertIn("26:0", self.sync())

    def test_texto_simples_recusado(self):
        self.assertRecusado(415, self.req("POST", "/api/programa/rodar", cru=json.dumps(self.CODIGO).encode(),
                                          headers={"Content-Type": "text/plain"}))

    def test_outra_origem_recusada(self):
        self.assertRecusado(403, self.req("POST", "/api/programa/rodar", self.CODIGO,
                                          headers={"Origin": "http://site.example"}))

    def test_origem_nula_recusada(self):
        self.assertRecusado(403, self.req("POST", "/api/programa/rodar", self.CODIGO, headers={"Origin": "null"}))

    def test_dominio_externo_no_host_recusado(self):
        self.assertRecusado(403, self.req("POST", "/api/programa/rodar", self.CODIGO,
                                          headers={"Host": "site.example", "Origin": "http://site.example"}))
        self.assertEqual(self.req("GET", "/api/estado", headers={"Host": "site.example"})[0], 403)

    def test_mesma_origem_aceita(self):
        origem = f"http://127.0.0.1:{self.porta}"
        self.assertEqual(self.req("POST", "/api/programa/rodar", self.CODIGO, headers={"Origin": origem})[0], 200)
        self.esperar(lambda: "26:1" in self.sync(), "o painel precisa conseguir comandar")

    def test_esp32_sem_origin_continua_funcionando(self):
        self.assertEqual(self.req("POST", "/api/esp32/sync", cru=b"a34=2000",
                                  headers={"Content-Type": "text/plain"})[0], 200)

    def test_sync_de_navegador_de_outra_origem_recusado(self):
        self.assertEqual(self.req("POST", "/api/esp32/sync", cru=b"a34=2000",
                                  headers={"Origin": "http://site.example"})[0], 403)


class TesteValidacao(Base):
    def test_calibracao_padrao(self):
        self.subir({"rodar_ao_iniciar": False})
        for cp in ({"seco": 1000, "molhado": 3000}, {"seco": 2000, "molhado": 2000},
                   {"seco": -5, "molhado": -9}, {"seco": 99999, "molhado": 1000}, 1):
            with self.subTest(cp=cp):
                self.assertEqual(self.req("PUT", "/api/ajustes", {"calibracao_padrao": cp})[0], 400)
        self.assertEqual(self.req("PUT", "/api/ajustes", {"calibracao_padrao": {"seco": 2800, "molhado": 1200}})[0], 200)

    def test_calibracao_padrao_sem_nenhum_canteiro(self):
        self.subir({"rodar_ao_iniciar": False, "canteiros": []})
        self.assertEqual(self.req("PUT", "/api/ajustes", {"calibracao_padrao": {"seco": 1000, "molhado": 3000}})[0], 400)

    def test_calibracao_herdada_do_padrao(self):
        self.subir({"rodar_ao_iniciar": False})
        c = self.estado()["canteiros"][0]["id"]
        st, d = self.req("PUT", f"/api/canteiros/{c}", {"seco": 1000, "molhado": None})
        self.assertEqual(st, 400, d)
        self.assertIn("herdado", d["erro"])
        self.assertEqual(self.req("PUT", f"/api/canteiros/{c}", {"seco": 2500, "molhado": None})[0], 200)
        # o padrão também não pode invalidar quem herda dele
        self.assertEqual(self.req("PUT", "/api/ajustes", {"calibracao_padrao": {"seco": 3500, "molhado": 2600}})[0], 400)

    def test_molhado_zero_vale(self):
        cfg = {"modo": "esp32", "calibracao_padrao": {"seco": 3000, "molhado": 1300},
               "canteiros": [{"pino_sensor": 34, "pino_bomba": 26, "seco": None, "molhado": 0}]}
        h = _hardware(cfg)
        h.sincronizar({"a34": "1300"}, "x")
        self.assertEqual(h.leitura(34)["umidade"], 57)

    def test_corpos_e_valores_invalidos_dao_400(self):
        self.subir({"rodar_ao_iniciar": False})
        base = {"nome": "T", "pino_sensor": 35, "pino_bomba": 27, "minimo": 30}
        casos = [(b"[]", None), (b'"x"', None), (b"3", None)]
        for cru, _ in casos:
            self.assertEqual(self.req("POST", "/api/canteiros", cru=cru,
                                      headers={"Content-Type": "application/json"})[0], 400)
        for extra in ({"minimo": "inf"}, {"seco": "inf"}, {"seco": "abc"}, {"nome": None}):
            with self.subTest(extra=extra):
                self.assertEqual(self.req("POST", "/api/canteiros", {**base, **extra})[0], 400)
        self.assertEqual(self.req("POST", "/api/esp32/sync", cru=b"a34=inf")[0], 200)

    def test_content_length_negativo_responde(self):
        self.subir({"rodar_ao_iniciar": False})
        with socket.create_connection(("127.0.0.1", self.porta), timeout=3) as s:
            s.sendall(b"POST /api/programa/compilar HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: -1\r\n\r\n")
            self.assertIn(b"400", s.recv(100))

    def test_maximo_de_bombas(self):
        self.subir({"rodar_ao_iniciar": False})
        for pino in (27, 25, 33, 32, 4, 5, 13):  # com a 26 do padrão, chega a 8
            nome = f"Local {pino}"
            sensor = 34  # todos no mesmo sensor: só a contagem de bombas importa aqui
            self.assertEqual(self.req("POST", "/api/canteiros", {"nome": nome, "pino_sensor": sensor, "pino_bomba": pino, "minimo": 30})[0], 201)
        st, d = self.req("POST", "/api/canteiros", {"nome": "Nona", "pino_sensor": 34, "pino_bomba": 14, "minimo": 30})
        self.assertEqual(st, 400)
        self.assertIn("8", d["erro"])
        self.assertEqual(self.req("POST", "/api/canteiros", {"nome": "Reusa", "pino_sensor": 34, "pino_bomba": 26, "minimo": 30})[0], 201)


class TesteCanteiros(Base):
    def test_nomes_nao_quebram_o_programa(self):
        for nome, bomba in (("Bomba 27", 27), ("Minimo cenouras", 26), ("Regar bomba 26", 27)):
            with self.subTest(nome=nome):
                self.subir()
                st, _ = self.req("POST", "/api/canteiros", {"nome": nome, "pino_sensor": 35, "pino_bomba": bomba, "minimo": 30})
                self.assertEqual(st, 201)
                self.assertTrue(self.req("GET", "/api/programa")[1]["compilacao"]["ok"])
                e = self.estado()
                self.assertIsNone(e["programa"]["erro"])
                self.assertTrue(all(c["usado_pelo_programa"] for c in e["canteiros"]), e["canteiros"])
                self.derrubar()
                os.unlink(os.path.join(self.pasta, "config.json"))

    def test_nomes_de_config_antiga_nao_quebram(self):
        canteiros = [
            {"id": "a", "nome": "Canteiro", "variavel": "cenouras", "pino_sensor": 34, "pino_bomba": 26, "minimo": 30},
            {"id": "b", "nome": "Bomba 27", "variavel": "bomba27", "pino_sensor": 35, "pino_bomba": 27, "minimo": 30},
        ]
        self.subir({"canteiros": canteiros})
        self.assertTrue(self.req("GET", "/api/programa")[1]["compilacao"]["ok"])
        self.assertTrue(self.estado()["programa"]["rodando"])

    def test_programa_volta_depois_de_remover_o_ultimo(self):
        self.subir()
        c = self.estado()["canteiros"][0]["id"]
        self.req("DELETE", f"/api/canteiros/{c}")
        self.esperar(lambda: not self.estado()["programa"]["rodando"], "sem canteiro o programa termina")
        self.req("POST", "/api/canteiros", {"nome": "Novo", "pino_sensor": 34, "pino_bomba": 26, "minimo": 30})
        self.esperar(lambda: self.estado()["programa"]["rodando"], "o programa deveria voltar")

    def test_parado_de_proposito_continua_parado(self):
        self.subir()
        self.req("POST", "/api/programa/parar")
        self.req("POST", "/api/canteiros", {"nome": "Novo", "pino_sensor": 35, "pino_bomba": 26, "minimo": 30})
        time.sleep(0.3)
        self.assertFalse(self.estado()["programa"]["rodando"])

    def test_remover_e_parar_nao_religa(self):
        self.subir()
        c = self.estado()["canteiros"][0]["id"]
        self.req("DELETE", f"/api/canteiros/{c}")
        self.esperar(lambda: not self.estado()["programa"]["rodando"], "sem canteiro o programa termina")
        self.req("POST", "/api/programa/parar")
        self.req("POST", "/api/canteiros", {"nome": "Novo", "pino_sensor": 34, "pino_bomba": 26, "minimo": 30})
        time.sleep(0.3)
        self.assertFalse(self.estado()["programa"]["rodando"])


class TestePorta(Base):
    def test_segundo_servidor_na_mesma_porta_nao_sobe(self):
        self.subir()
        outro = subprocess.run([sys.executable, SERVIDOR, "--porta", str(self.porta), "--host", "127.0.0.1",
                                "--dados", self.pasta], capture_output=True, text=True, encoding="utf-8", timeout=20,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8"))
        self.assertEqual(outro.returncode, 1)
        self.assertIn("porta", outro.stdout)
        self.assertNotIn("execução", outro.stdout, "não pode rodar programa sem a porta")


class TesteBombaManual(Base):
    def test_fim_do_pulso_nao_corta_a_rega_do_programa(self):
        self.subir({"rodar_ao_iniciar": False, "tempo_rega": 30, "intervalo": 60})
        self.req("POST", "/api/simulacao", {"pino": 34, "umidade": 0})
        self.req("POST", "/api/bomba/testar", {"pino": 26, "segundos": 1})
        self.req("POST", "/api/programa/rodar", {})
        self.esperar(lambda: self.estado()["canteiros"][0]["irrigando"], "o programa deveria regar")
        time.sleep(1.4)  # passa do fim do pulso
        self.assertTrue(self.estado()["canteiros"][0]["irrigando"])

    def test_segundo_pulso_vale_inteiro(self):
        self.subir({"rodar_ao_iniciar": False})
        self.req("POST", "/api/bomba/testar", {"pino": 26, "segundos": 1})
        time.sleep(0.5)
        self.req("POST", "/api/bomba/testar", {"pino": 26, "segundos": 2})
        time.sleep(0.9)  # 1,4 s depois do primeiro: ele já teria desligado
        self.assertTrue(self.estado()["canteiros"][0]["irrigando"])
        self.esperar(lambda: not self.estado()["canteiros"][0]["irrigando"], "o pulso precisa acabar", tempo=2.5)

    def test_trocar_de_pino_desliga_o_anterior(self):
        self.subir({"modo": "esp32", "rodar_ao_iniciar": False})
        self.req("POST", "/api/bomba/testar", {"pino": 26, "segundos": 5})
        self.assertIn("26:1", self.sync())
        self.req("POST", "/api/bomba/testar", {"pino": 27, "segundos": 5})
        r = self.sync()
        self.assertIn("26:0", r)
        self.assertIn("27:1", r)

    def test_teste_manual_funciona_com_sensor_sem_sinal(self):
        # é justamente quando alguém está mexendo nos fios
        self.subir({"modo": "esp32", "rodar_ao_iniciar": False})
        self.sync(b"a34=4095")
        self.req("POST", "/api/bomba/testar", {"pino": 26, "segundos": 2})
        self.assertIn("26:1", self.sync(b"a34=4095"))


class TesteSimulacao(Base):
    def test_rega_curta_nao_se_perde(self):
        h = _hardware({"modo": "simulacao", "fator_tempo": 60}, leitura={34}, saida={26})
        h.definir_simulado(34, 10)
        h.acionar("b", 26, True)
        time.sleep(0.05)  # bem menor que o passo de 0,5 s do laço
        h.acionar("b", 26, False)
        # 0,05 s a 60x são 3 s de rega, a 2% por segundo
        self.assertGreaterEqual(h.ler_sensor("u", 34, threading.Event()), 15)

    def test_acelerar_vale_para_a_espera_em_andamento(self):
        self.subir({"fator_tempo": 1, "rodar_ao_iniciar": False})
        self.req("POST", "/api/programa/rodar", {"codigo": 'wait 60\nsay "acabou"\nwait 3600'})
        time.sleep(0.3)
        self.req("PUT", "/api/ajustes", {"fator_tempo": 120})
        self.esperar(lambda: "acabou" in self.console("say"), "a espera de 60 s deveria durar cerca de 0,5 s", tempo=3)


class TestePainelServido(Base):
    def test_sessao_muda_a_cada_subida(self):
        self.subir({"rodar_ao_iniciar": False})
        a = self.estado()["sessao"]
        self.assertEqual(a, self.estado()["sessao"])
        self.derrubar()
        self.subir({"rodar_ao_iniciar": False})
        self.assertNotEqual(a, self.estado()["sessao"])

    def test_nao_serve_arquivo_de_fora_do_painel(self):
        self.subir({"rodar_ao_iniciar": False})
        self.assertEqual(self.req("GET", "/")[0], 200)
        for rota in ("/../servidor/servidor.py", "/..\\servidor\\servidor.py", "/C:/Windows/win.ini"):
            self.assertEqual(self.req("GET", rota)[0], 404, rota)


def _hardware(cfg, leitura=None, saida=None):
    cfg = {"canteiros": [], **cfg}
    return Hardware(lambda: cfg, lambda *a: None, lambda: leitura or {34}, lambda: saida or {26})


class TesteUnidades(unittest.TestCase):
    def test_limites_de_plausibilidade(self):
        self.assertFalse(leitura_plausivel(0))
        self.assertFalse(leitura_plausivel(4095))
        self.assertFalse(leitura_plausivel(-5000))
        self.assertTrue(leitura_plausivel(1300))
        self.assertTrue(leitura_plausivel(3000))

    def test_sync_so_manda_pino_em_uso(self):
        h = _hardware({"modo": "esp32"})
        h.acionar("velha", 27, True)
        self.assertIn("27:1", h.sincronizar({}, "x"))  # ligada: precisa aparecer até desligar
        h.acionar("velha", 27, False)
        self.assertNotIn("27", h.sincronizar({}, "x").split("saidas=")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
