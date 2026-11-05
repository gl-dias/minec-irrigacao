"""
Testes do protótipo (compilador, interpretador, gerador e hardware).
A API tem os testes dela em testes_api.py.

    python servidor/testes.py
"""

import glob
import itertools
import json
import math
import os
import sys
import threading
import time
import unittest

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from compilador import (  # noqa: E402
    BinaryOpNode, LIMITE_TEXTO, LiteralNode, ProgramNode, Resultado, SayNode, compilar,
)
from gerador import PADRAO_DO_GERADOR, gerar_programa, nome_de_variavel  # noqa: E402
from hardware import Hardware, bruto_para_umidade  # noqa: E402
from interpretador import ErroExecucao, Interpretador, ProgramaParado, formatar  # noqa: E402


class HardwareFalso:
    """Sensor que devolve uma sequência fixa de leituras e grava os acionamentos."""

    def __init__(self, leituras):
        self.leituras = list(leituras)
        self.acoes = []

    def ler_sensor(self, nome, pino, parar):
        if not self.leituras:
            parar.set()
            return 0
        return self.leituras.pop(0)

    def acionar(self, nome, pino, ligado):
        self.acoes.append((nome, pino, ligado))

    def fator_tempo(self):
        return 10_000.0  # 'wait 60' vira 6 ms


def executar(codigo, leituras=(), checar_pinos=True):
    r = compilar(codigo, checar_pinos=checar_pinos)
    assert r.ok, [str(e) for e in r.erros]
    hw = HardwareFalso(leituras)
    saida = []
    parar = threading.Event()
    interp = Interpretador(r.ast, hw, parar, ao_dizer=lambda t, l: saida.append(t))
    try:
        interp.executar()
    except ProgramaParado:
        pass
    return saida, hw.acoes


def erro_ao_executar(caso, codigo, leituras=(), checar_pinos=True):
    """Compila, executa e devolve o ErroExecucao esperado e o hardware usado."""
    r = compilar(codigo, checar_pinos=checar_pinos)
    caso.assertTrue(r.ok, [str(e) for e in r.erros])
    hw = HardwareFalso(leituras)
    with caso.assertRaises(ErroExecucao) as ctx:
        Interpretador(r.ast, hw, threading.Event()).executar()
    return ctx.exception, hw


class TesteSemantica(unittest.TestCase):
    def erros(self, codigo):
        return [e.texto for e in compilar(codigo).erros]

    def test_exemplos_do_repositorio_passam_sem_checar_pinos(self):
        pasta = os.path.join(AQUI, "..", "..", "exemplos")
        arquivos = glob.glob(os.path.join(pasta, "*.minec"))
        if not arquivos:
            self.skipTest(f"Sem exemplos do repositório em {os.path.normpath(pasta)}: esta pasta está fora do repositório.")
        for arq in arquivos:
            with open(arq, encoding="utf-8") as f:
                r = compilar(f.read(), checar_pinos=False)
            self.assertTrue(r.ok, f"{arq}: {[str(e) for e in r.erros]}")

    def test_exemplo_do_prototipo_passa_com_pinos(self):
        with open(os.path.join(AQUI, "..", "exemplos", "04_prototipo_esp32.minec"), encoding="utf-8") as f:
            r = compilar(f.read())
        self.assertTrue(r.ok, [str(e) for e in r.erros])
        self.assertEqual(r.sensores, {"umidade": 34})
        self.assertEqual(r.atuadores, {"bomba": 26})

    def test_declaracao_antes_do_uso(self):
        self.assertIn("'x' não foi declarado antes do uso.", self.erros("say x"))

    def test_mine_so_em_sensor(self):
        e = self.erros("craft a = 1\nmine a")
        self.assertTrue(any("'mine' só funciona com sensores" in x for x in e))

    def test_power_so_em_atuador(self):
        e = self.erros("observer s = 34\npower s")
        self.assertTrue(any("'power' só funciona com atuadores" in x for x in e))

    def test_sensor_nao_recebe_atribuicao(self):
        e = self.erros("observer s = 34\ns = 10")
        self.assertTrue(any("só muda com 'mine s'" in x for x in e))

    def test_drop_fora_de_command(self):
        self.assertIn("'drop' só pode aparecer dentro de um command.", self.erros("drop 1"))

    def test_quantidade_de_argumentos(self):
        e = self.erros("command f(a) { drop a }\ncraft x = f(1, 2)")
        self.assertTrue(any("espera 1 argumento(s), mas recebeu 2" in x for x in e))

    def test_condicao_precisa_ser_booleana(self):
        e = self.erros("if 1 + 1 { say 1 }")
        self.assertTrue(any("condição do 'if'" in x for x in e))

    def test_repeat_inteiro(self):
        e = self.erros("repeat 2.5 { say 1 }")
        self.assertTrue(any("'repeat' precisa de um número inteiro" in x for x in e))

    def test_tipos_na_atribuicao(self):
        e = self.erros('craft t = "oi"\nt = 3')
        self.assertTrue(any("é texto e não pode receber" in x for x in e))

    def test_escopo_de_bloco(self):
        e = self.erros("if true { craft y = 1 }\nsay y")
        self.assertTrue(any("'y' não foi declarado" in x for x in e))

    def test_pino_flash_e_adc2(self):
        e = self.erros("observer s = 26\nlever b = 8")
        self.assertTrue(any("ADC2" in x for x in e))
        self.assertTrue(any("memória flash" in x for x in e))

    def test_pino_somente_entrada_nao_e_rele(self):
        e = self.erros("lever b = 34")
        self.assertTrue(any("somente entrada" in x for x in e))

    def test_mesmo_pino_sensor_e_atuador(self):
        e = self.erros("observer s = 33\nlever b = 33")
        self.assertTrue(any("ao mesmo tempo como sensor e atuador" in x for x in e))

    def test_while_true_sem_wait_gera_aviso(self):
        r = compilar("while true { say 1 }")
        self.assertTrue(r.ok)
        self.assertTrue(any("sem nenhum 'wait'" in a.texto for a in r.avisos))

    def test_erro_sintatico_tem_linha(self):
        r = compilar("craft x = \nsay x")
        self.assertFalse(r.ok)
        self.assertEqual(r.erros[0].fase, "Sintática")
        self.assertEqual(r.erros[0].linha, 2)


class TesteInterpretador(unittest.TestCase):
    def test_aritmetica_e_texto(self):
        saida, _ = executar('craft a = 7\ncraft b = 2\nsay a / b\nsay "v=" + (a * b - 4)\nsay 6 / 3\nsay not (a > b) or false')
        self.assertEqual(saida, ["3.5", "v=10", "2", "false"])

    def test_command_com_drop_e_recursao(self):
        codigo = "command fat(n) {\n if n <= 1 { drop 1 }\n drop n * fat(n - 1)\n}\nsay fat(5)"
        self.assertEqual(executar(codigo)[0], ["120"])

    def test_repeat_e_else_if(self):
        codigo = "craft i = 0\nrepeat 3 {\n i = i + 1\n if i == 1 { say \"um\" } else if i == 2 { say \"dois\" } else { say \"tres\" }\n}"
        self.assertEqual(executar(codigo)[0], ["um", "dois", "tres"])

    def test_irrigacao_liga_e_desliga_bomba(self):
        codigo = ("observer umidade = 34\nlever bomba = 26\n"
                  "while true {\n mine umidade\n if umidade < 30 {\n  power bomba\n  wait 10\n  unpower bomba\n }\n wait 60\n}")
        _, acoes = executar(codigo, leituras=[20, 50, 10])
        ligacoes = [a for a in acoes if a[2]]
        self.assertEqual(len(ligacoes), 2)  # regou nas leituras 20 e 10, não na 50
        self.assertEqual(acoes[-1], ("bomba", 26, False))  # termina com a bomba desligada

    def test_parar_desliga_bomba_no_meio_da_rega(self):
        r = compilar("lever bomba = 26\npower bomba\nwait 9999")
        hw = HardwareFalso([])
        hw.fator_tempo = lambda: 1.0
        parar = threading.Event()
        t = threading.Thread(target=lambda: self._rodar(r, hw, parar))
        t.start()
        threading.Timer(0.2, parar.set).start()
        t.join(3)
        self.assertFalse(t.is_alive())
        self.assertEqual(hw.acoes[-1], ("bomba", 26, False))

    def _rodar(self, r, hw, parar):
        try:
            Interpretador(r.ast, hw, parar).executar()
        except ProgramaParado:
            pass

    def test_divisao_por_zero(self):
        r = compilar("craft z = 0\nsay 1 / z")
        with self.assertRaises(ErroExecucao) as ctx:
            Interpretador(r.ast, HardwareFalso([]), threading.Event()).executar()
        self.assertEqual(ctx.exception.linha, 2)


class TesteGerador(unittest.TestCase):
    def test_nome_de_variavel(self):
        self.assertEqual(nome_de_variavel("Canteiro de cenouras", set()), "cenouras")
        self.assertEqual(nome_de_variavel("Vaso de Manjericão 2", set()), "manjericao2")
        self.assertEqual(nome_de_variavel("while", set()), "while2")
        self.assertEqual(nome_de_variavel("Trigo", {"trigo"}), "trigo2")

    def test_programa_gerado_compila(self):
        cfg = {"intervalo": 120, "tempo_rega": 10, "canteiros": [
            {"nome": "Canteiro de cenouras", "variavel": "cenouras", "pino_sensor": 34, "pino_bomba": 26, "minimo": 30},
            {"nome": 'Trigo "norte"', "variavel": "trigoNorte", "pino_sensor": 35, "pino_bomba": 27, "minimo": 40},
        ]}
        r = compilar(gerar_programa(cfg))
        self.assertTrue(r.ok, [str(e) for e in r.erros])
        self.assertEqual(r.atuadores, {"bomba26": 26, "bomba27": 27})

    def test_sem_canteiros(self):
        self.assertTrue(compilar(gerar_programa({"canteiros": []})).ok)


class TesteHardware(unittest.TestCase):
    def test_conversao_bruto_umidade(self):
        self.assertEqual(bruto_para_umidade(3000, 3000, 1300), 0)
        self.assertEqual(bruto_para_umidade(1300, 3000, 1300), 100)
        self.assertEqual(bruto_para_umidade(2150, 3000, 1300), 50)
        self.assertEqual(bruto_para_umidade(3500, 3000, 1300), 0)

    def test_sincronizacao_com_esp32(self):
        cfg = {"modo": "esp32", "limite_bomba": 60, "canteiros": [{"pino_sensor": 34, "pino_bomba": 26}]}
        hw = Hardware(lambda: cfg, lambda *a: None, lambda: {34}, lambda: {26})
        resposta = hw.sincronizar({"a34": "2150", "rssi": "-60"}, "10.0.0.5")
        self.assertIn("ler=34", resposta)
        self.assertIn("saidas=26:0", resposta)
        self.assertEqual(hw.leitura(34)["umidade"], 50)
        hw.acionar("bomba", 26, True)
        self.assertIn("saidas=26:1", hw.sincronizar({}, "10.0.0.5"))
        cfg["modo"] = "simulacao"  # simulação nunca liga o relé de verdade
        self.assertIn("saidas=26:0", hw.sincronizar({}, "10.0.0.5"))


class TesteRepositorio(unittest.TestCase):
    def test_minec_da_raiz_igual_a_copia(self):
        raiz = os.path.join(AQUI, "..", "..", "minec.py")
        if not os.path.exists(raiz):
            self.skipTest(f"Sem minec.py em {os.path.normpath(raiz)}: esta pasta está fora do repositório.")

        def conteudo(caminho):
            with open(caminho, "rb") as f:
                return f.read().replace(b"\r\n", b"\n")

        self.assertEqual(conteudo(raiz), conteudo(os.path.join(AQUI, "minec.py")))


class TesteCompilarNuncaLanca(unittest.TestCase):
    """ACHADO-001: nenhum conteúdo de código pode virar exceção Python."""

    RECUSADOS = {
        "parenteses 500": "say " + "(" * 500 + "1" + ")" * 500,
        "if aninhado 300": "if true {" * 300 + "}" * 300,
        "soma 2000 termos": "say " + "+".join(["1"] * 2000),
        "not 2000": "say " + "not " * 2000 + "true",
        "inteiro 5000 digitos": "craft x = " + "9" * 5000,
        "inteiro 4001 digitos": "craft x = " + "9" * 4001,
        "decimal 400 digitos": "say " + "9" * 400 + ".0",
        "texto gigante": 'say "' + "a" * (LIMITE_TEXTO + 1) + '"',
    }
    # perto do limite da pilha: compilar ou não depende de quem chama, mas nada pode vazar
    NA_FRONTEIRA = {
        "parenteses 60": "say " + "(" * 60 + "1" + ")" * 60,
        "if aninhado 195": "if true {" * 195 + "}" * 195,
        "soma 992 termos": "say " + "+".join(["1"] * 992),
    }

    def test_entrada_extrema_vira_erro_com_mensagem_util(self):
        for nome, codigo in self.RECUSADOS.items():
            with self.subTest(nome):
                r = compilar(codigo)
                self.assertFalse(r.ok)
                self.assertRegex(r.erros[0].texto, "Simplifique|grande demais")
                json.dumps(r.para_dict(), allow_nan=False)

    def test_fronteira_da_pilha_nao_vaza_nem_para_o_interpretador(self):
        for nome, codigo in self.NA_FRONTEIRA.items():
            with self.subTest(nome):
                r = compilar(codigo)
                json.dumps(r.para_dict(), allow_nan=False)
                self.assertEqual(r.ok, not r.erros)
                if r.ok:
                    try:
                        Interpretador(r.ast, HardwareFalso([]), threading.Event()).executar()
                    except ErroExecucao:
                        pass

    def test_erro_de_inteiro_gigante_aponta_a_linha(self):
        r = compilar("say 1\nsay " + "9" * 5000)
        self.assertEqual(r.erros[0].linha, 2)
        self.assertEqual(r.erros[0].fase, "Léxica")

    def test_literais_no_limite_continuam_valendo(self):
        self.assertEqual(executar("say " + "9" * 4000)[0], ["9" * 4000])
        texto = "a" * LIMITE_TEXTO
        self.assertEqual(executar(f'say "{texto}"')[0], [texto])

    def test_codigo_que_nao_e_texto(self):
        for valor in (123, None, [], b"say 1", 1.5, {"a": 1}):
            with self.subTest(repr(valor)):
                r = compilar(valor)
                self.assertFalse(r.ok)
                self.assertIn("precisa ser um texto", r.erros[0].texto)
                json.dumps(r.para_dict(), allow_nan=False)

    def test_ast_texto_nao_estoura_com_arvore_funda(self):
        no = LiteralNode(1, "INTEIRO")
        for _ in range(5000):
            no = BinaryOpNode(no, "+", LiteralNode(1, "INTEIRO"))
        r = Resultado(True, ast=ProgramNode([SayNode(no)]))
        self.assertIn("grande demais", r.para_dict()["ast"])


class TesteGeradorRegressao(unittest.TestCase):
    NOMES = ["Bomba 27", "Bomba 26", "Bomba", "Bomba 2", "Minimo cenouras", "Minimo minimo cenouras",
             "Regar bomba 26", "Regar bomba 27", "Regar bomba", "Regar", "cenouras", "Intervalo",
             "Tempo rega", "Ciclo", "Segundos", "While", "Minimo", "Canteiro bomba 27"]

    @staticmethod
    def canteiro(nome, variavel, sensor=34, bomba=26, minimo=30):
        return {"nome": nome, "variavel": variavel, "pino_sensor": sensor, "pino_bomba": bomba, "minimo": minimo}

    def cfg_de_nomes(self, nomes, bombas):
        existentes, canteiros = set(), []
        for i, nome in enumerate(nomes):
            variavel = nome_de_variavel(nome, existentes)
            existentes.add(variavel)
            canteiros.append(self.canteiro(nome, variavel, sensor=100 + i, bomba=bombas[i % len(bombas)]))
        return {"intervalo": 60, "tempo_rega": 5, "canteiros": canteiros}

    def test_leitura_invalida_suspende_a_rega(self):
        cfg = {"intervalo": 60, "tempo_rega": 5, "canteiros": [self.canteiro("Horta", "horta")]}
        saida, acoes = executar(gerar_programa(cfg), leituras=[-1, 20, 50])
        self.assertEqual(len([a for a in acoes if a[2]]), 1)  # só a leitura 20 rega
        textos = saida[1:]
        self.assertIn("sensor sem leitura válida", textos[0])
        self.assertIn("rega suspensa", textos[0])
        self.assertIn("solo seco (20%)", textos[1])
        self.assertIn("umidade ok (50%)", textos[2])

    def test_leitura_zero_ainda_e_solo_seco(self):
        cfg = {"intervalo": 60, "tempo_rega": 5, "canteiros": [self.canteiro("Horta", "horta")]}
        _, acoes = executar(gerar_programa(cfg), leituras=[0])
        self.assertEqual(len([a for a in acoes if a[2]]), 1)

    def test_nome_novo_nao_parece_nome_auxiliar(self):
        for nome in self.NOMES:
            with self.subTest(nome):
                self.assertIsNone(PADRAO_DO_GERADOR.fullmatch(nome_de_variavel(nome, set())))
        self.assertEqual(nome_de_variavel("Bomba 27", set()), "canteiroBomba27")
        self.assertEqual(nome_de_variavel("Bomba", set()), "canteiroBomba")
        self.assertEqual(nome_de_variavel("Regar", set()), "canteiroRegar")
        self.assertEqual(nome_de_variavel("Regar bomba 26", set()), "canteiroRegarBomba26")
        self.assertEqual(nome_de_variavel("Minimo cenouras", set()), "canteiroMinimoCenouras")
        self.assertEqual(nome_de_variavel("Bomba 27", {"canteiroBomba27"}), "canteiroBomba272")
        self.assertEqual(nome_de_variavel("Minimo", set()), "minimo")

    def test_nomes_em_pares_sempre_compilam(self):
        for par in itertools.permutations(self.NOMES, 2):
            for bombas in ([26], [26, 27]):
                with self.subTest(par=par, bombas=bombas):
                    r = compilar(gerar_programa(self.cfg_de_nomes(par, bombas)), checar_pinos=False)
                    self.assertTrue(r.ok, [str(e) for e in r.erros])

    def test_todos_os_nomes_juntos_compilam_e_rodam(self):
        cfg = self.cfg_de_nomes(self.NOMES, [26, 27])
        saida, acoes = executar(gerar_programa(cfg), leituras=[20] * len(self.NOMES), checar_pinos=False)
        self.assertEqual(len([a for a in acoes if a[2]]), len(self.NOMES))
        self.assertIn("Fazenda iniciada", saida[0])

    def test_config_antigo_com_variavel_que_colide(self):
        grupos = [
            ["bomba27", "cenouras"], ["bomba26", "bomba27"], ["cenouras", "minimoCenouras"],
            ["minimoCenouras", "cenouras"], ["minimoCenouras", "minimoMinimoCenouras"],
            ["regarBomba26", "regarBomba27"], ["bomba", "regar"], ["intervalo", "tempoRega"],
            ["ciclo", "segundos"], ["a", "A"],
        ]
        for variaveis in grupos:
            for bombas in ((26, 27), (26, 26)):
                with self.subTest(variaveis=variaveis, bombas=bombas):
                    cfg = {"canteiros": [
                        self.canteiro("Um", variaveis[0], sensor=34, bomba=bombas[0], minimo=30),
                        self.canteiro("Dois", variaveis[1], sensor=35, bomba=bombas[1], minimo=30),
                    ]}
                    programa = gerar_programa(cfg)
                    r = compilar(programa)
                    self.assertTrue(r.ok, [str(e) for e in r.erros])
                    # a variável do canteiro salva continua igual; quem desvia é o nome auxiliar
                    self.assertEqual(r.sensores, {variaveis[0]: 34, variaveis[1]: 35})
                    self.assertEqual(set(r.atuadores.values()), set(bombas))
                    # só o primeiro canteiro está seco: tem de ligar a bomba dele
                    _, acoes = executar(programa, leituras=[20, 50])
                    self.assertEqual([a[1] for a in acoes if a[2]], [bombas[0]])

    def test_config_antigo_com_variavel_repetida(self):
        cfg = {"canteiros": [self.canteiro("Um", "dup", sensor=34), self.canteiro("Dois", "dup", sensor=35)]}
        r = compilar(gerar_programa(cfg))
        self.assertTrue(r.ok, [str(e) for e in r.erros])
        self.assertEqual(set(r.sensores.values()), {34, 35})


class HardwareRelogio(HardwareFalso):
    def __init__(self, fator=1.0):
        super().__init__([])
        self.fator = fator

    def fator_tempo(self):
        return self.fator


class TesteEspera(unittest.TestCase):
    """ACHADO-012: a espera relê a aceleração e obedece o parar a cada fatia."""

    def rodar_em_thread(self, codigo, hw, ao_esperar=lambda i, f: None):
        r = compilar(codigo)
        self.assertTrue(r.ok, [str(e) for e in r.erros])
        parar = threading.Event()
        erros = []

        def alvo():
            try:
                Interpretador(r.ast, hw, parar, ao_esperar=ao_esperar).executar()
            except ProgramaParado:
                pass
            except Exception as exc:  # o teste confere que a lista fica vazia
                erros.append(exc)

        t = threading.Thread(target=alvo, daemon=True)
        t.start()
        return t, parar, erros

    def test_mudar_aceleracao_vale_na_espera_em_curso(self):
        previsoes = []
        hw = HardwareRelogio(1.0)
        t, parar, erros = self.rodar_em_thread("wait 3000", hw, lambda i, f: previsoes.append((i, f)))
        time.sleep(0.5)
        self.assertTrue(t.is_alive())
        hw.fator = 100_000.0
        t.join(1.5)
        vivo = t.is_alive()
        parar.set()
        self.assertFalse(vivo, "a espera ignorou a nova aceleração")
        self.assertEqual(erros, [])
        self.assertGreater(previsoes[0][1] - previsoes[0][0], 2900)  # previsão inicial: 3000 s
        self.assertGreater(len(previsoes), 2)                        # previsão atualizada a cada fatia
        self.assertLess(previsoes[-2][1] - previsoes[-2][0], 1)      # com a nova aceleração
        self.assertEqual(previsoes[-1], (0, 0))                      # acabou a espera

    def test_tempo_total_desconta_em_fatias(self):
        hw = HardwareRelogio(1.0)
        inicio = time.perf_counter()
        t, _, erros = self.rodar_em_thread("wait 0.7", hw)
        t.join(3)
        duracao = time.perf_counter() - inicio
        self.assertFalse(t.is_alive())
        self.assertEqual(erros, [])
        self.assertGreaterEqual(duracao, 0.65)
        self.assertLess(duracao, 1.5)

    def test_parar_interrompe_na_hora(self):
        hw = HardwareRelogio(1.0)
        t, parar, _ = self.rodar_em_thread("wait 3000", hw)
        time.sleep(0.01)
        inicio = time.perf_counter()
        parar.set()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertLess(time.perf_counter() - inicio, 0.1)

    def test_wait_gigante_finito_espera_ate_parar(self):
        previsoes = []
        hw = HardwareRelogio(0.001)
        t, parar, erros = self.rodar_em_thread("craft x = " + "1" + "0" * 308 + ".0\nwait x", hw,
                                               lambda i, f: previsoes.append(f))
        time.sleep(0.1)
        self.assertTrue(t.is_alive())
        parar.set()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(erros, [])
        self.assertTrue(all(math.isfinite(f) for f in previsoes), "previsão infinita quebra o JSON do painel")

    def test_wait_maior_que_um_decimal_vira_erro(self):
        exc, _ = erro_ao_executar(self, "craft x = " + "9" * 400 + "\nwait x")
        self.assertIn("grande demais", exc.texto)
        self.assertEqual(exc.linha, 2)

    def test_wait_infinito_vira_erro(self):
        r = compilar("say 1")
        for valor in (math.inf, -math.inf, math.nan):
            with self.subTest(valor):
                erros = []
                interp = Interpretador(r.ast, HardwareRelogio(), threading.Event())

                def alvo():
                    try:
                        interp.esperar(valor, 7)
                    except ErroExecucao as exc:
                        erros.append(exc)

                # em thread: sem o conserto a espera nunca acabaria, e o teste só falharia travando
                threading.Thread(target=alvo, daemon=True).start()
                time.sleep(0.2)
                self.assertEqual([e.linha for e in erros], [7])


class TesteSensorSemLeitura(unittest.TestCase):
    """ACHADO-015: antes do 'mine' o sensor não vale o número do pino."""

    def avisos_de_mine(self, codigo):
        return [a for a in compilar(codigo).avisos if "não existe 'mine" in a.texto]

    def test_usar_sensor_antes_do_mine_para_com_erro(self):
        exc, _ = erro_ao_executar(self, 'observer umidade = 34\nsay "oi"\nif umidade < 50 { say "regaria" }')
        self.assertEqual(exc.linha, 3)
        self.assertIn("mine umidade", exc.texto)

    def test_depois_do_mine_vale_a_leitura(self):
        self.assertEqual(executar("observer u = 34\nmine u\nsay u", leituras=[55])[0], ["55"])

    def test_leitura_invalida_chega_ao_programa(self):
        self.assertEqual(executar("observer u = 34\nmine u\nsay u", leituras=[-1])[0], ["-1"])

    def test_mine_dentro_de_command_vale_para_o_global(self):
        codigo = "observer u = 34\ncommand ler() {\n mine u\n}\nler()\nsay u"
        self.assertEqual(self.avisos_de_mine(codigo), [])
        self.assertEqual(executar(codigo, leituras=[40])[0], ["40"])

    def test_aviso_quando_nao_ha_mine_nenhum(self):
        for codigo, linha in (("observer u = 34\nsay u", 2),
                              ("observer u = 34\ncommand f() {\n say u\n}\nf()", 3),
                              ("observer u = 34\nobserver v = 35\nmine v\nif u > v { say 1 }", 4)):
            with self.subTest(codigo):
                avisos = self.avisos_de_mine(codigo)
                self.assertEqual(len(avisos), 1)
                self.assertIn("'mine u'", avisos[0].texto)
                self.assertEqual(avisos[0].linha, linha)

    def test_sem_aviso_falso(self):
        for codigo in ("observer u = 34",                                         # não usa o valor
                       "observer u = 34\nmine u",                                 # lê e não usa
                       "observer u = 34\nsay 1\nmine u\nsay u",
                       "observer u = 34\nsay u\nmine u",                          # erro é na execução
                       "observer u = 34\ncommand ler() { mine u }\nsay u",        # command nunca chamado
                       "observer u = 34\ncommand ler() { if true { mine u } }\ncommand m() { say u }",
                       "observer u = 34\ncommand f(u) { drop u }\nsay f(1)",      # parâmetro, não o sensor
                       "observer u = 34\nif true { craft u = 1\n say u }"):       # variável local, não o sensor
            with self.subTest(codigo):
                self.assertEqual(self.avisos_de_mine(codigo), [])

    def test_exemplos_sem_aviso_de_mine(self):
        pastas = [os.path.join(AQUI, "..", "exemplos"), os.path.join(AQUI, "..", "..", "exemplos")]
        arquivos = [a for p in pastas for a in glob.glob(os.path.join(p, "*.minec"))]
        self.assertTrue(arquivos)
        for arq in arquivos:
            with self.subTest(arq), open(arq, encoding="utf-8") as f:
                r = compilar(f.read(), checar_pinos=False)
                self.assertTrue(r.ok)
                self.assertEqual([a.texto for a in r.avisos if "mine" in a.texto], [])

    def test_sensor_e_inteiro_no_repeat(self):
        codigo = 'observer u = 34\nmine u\nrepeat u { say "x" }'
        self.assertEqual(executar(codigo, leituras=[3])[0], ["x", "x", "x"])


class TesteNumerosETextos(unittest.TestCase):
    """ACHADO-017: extremos viram ErroExecucao, nunca exceção Python nem 'inf'."""

    def test_inteiro_gigante(self):
        exc, _ = erro_ao_executar(self, "craft x = 10\nrepeat 13 {\n x = x * x\n}\nsay x")
        self.assertIn("grande demais", exc.texto)
        self.assertEqual(exc.linha, 3)

    def test_inteiro_abaixo_do_limite_continua_exato(self):
        fatorial = "command f(n) {\n if n <= 1 { drop 1 }\n drop n * f(n - 1)\n}\nsay f(100)"
        self.assertEqual(executar(fatorial)[0], [str(math.factorial(100))])

    def test_divisao_enorme(self):
        exc, _ = erro_ao_executar(self, "craft x = 10\nrepeat 11 { x = x * x }\nsay x / 3")
        self.assertIn("grande demais", exc.texto)

    def test_divisao_exata_de_inteiro_grande_nao_perde_digitos(self):
        codigo = "craft x = 10\nrepeat 9 { x = x * x }\nsay x / 10\nsay 1000000000000000000000000000000 / 1"
        self.assertEqual(executar(codigo)[0], ["1" + "0" * 511, "1" + "0" * 30])

    def test_decimal_nao_estoura_em_silencio(self):
        for codigo in ("craft x = 10.0\nrepeat 12 { x = x * x }\nsay x",
                       "craft x = " + "1" + "0" * 308 + ".0\nsay x + x",
                       "craft x = " + "1" + "0" * 308 + ".0\nsay x / 0.001",
                       "craft x = " + "9" * 400 + "\nsay x + 0.5"):
            with self.subTest(codigo[:40]):
                exc, _ = erro_ao_executar(self, codigo)
                self.assertIn("grande demais", exc.texto)

    def test_repeat_com_inteiro_grande_nao_estoura(self):
        codigo = "craft x = " + "9" * 400 + '\nrepeat x {\n say "um"\n}'
        r = compilar(codigo)
        self.assertTrue(r.ok)
        parar = threading.Event()
        saida = []

        def dizer(texto, linha):
            saida.append(texto)
            parar.set()

        with self.assertRaises(ProgramaParado):
            Interpretador(r.ast, HardwareFalso([]), parar, ao_dizer=dizer).executar()
        self.assertEqual(saida, ["um"])

    def test_texto_gigante(self):
        exc, _ = erro_ao_executar(self, 'craft s = "abcdefgh"\nrepeat 20 {\n s = s + s\n}')
        self.assertIn("Texto grande demais", exc.texto)
        self.assertEqual(exc.linha, 3)

    def test_texto_normal_passa(self):
        saida, _ = executar('craft s = "ab"\nrepeat 10 { s = s + s }\nsay s')
        self.assertEqual(saida, ["ab" * 1024])

    def test_recursao_na_execucao(self):
        ifs = "if true { " * 5 + "f(n + 1)" + " }" * 5
        codigo = f"lever b = 26\npower b\ncommand f(n) {{\n {ifs}\n}}\nf(1)"
        exc, hw = erro_ao_executar(self, codigo)
        self.assertIn("pilha", exc.texto)
        self.assertIsNotNone(exc.linha)
        self.assertEqual(hw.acoes[-1], ("b", 26, False))

    def test_bomba_desliga_se_o_acionamento_estourar(self):
        class Estoura(HardwareFalso):
            def acionar(self, nome, pino, ligado):
                super().acionar(nome, pino, ligado)
                if ligado:
                    raise RecursionError

        r = compilar("lever b = 26\npower b")
        hw = Estoura([])
        with self.assertRaises(ErroExecucao):
            Interpretador(r.ast, hw, threading.Event()).executar()
        self.assertEqual(hw.acoes[-1], ("b", 26, False))

    def test_formatar(self):
        for valor, esperado in ((0.001, "0.001"), (1.999, "1.999"), (-0.001, "-0.001"), (3.5, "3.5"),
                                (-0.0, "0"), (0.0, "0"), (2.0, "2"), (-2.0, "-2"), (0.1 + 0.2, "0.3"),
                                (1 / 3, "0.333333333333333"), (1e-7, "0.0000001"), (-1e-7, "-0.0000001"),
                                (1e-300, "0." + "0" * 299 + "1"), (1e20, "100000000000000000000"),
                                (1234567.25, "1234567.25")):
            with self.subTest(valor):
                self.assertEqual(formatar(valor), esperado)

    def test_say_de_decimais_pequenos(self):
        saida, _ = executar('say 0.001\nsay 1.999\nsay -0.001\nsay 0.0 * -1\nsay "v=" + -0.0\nsay 7 / 2')
        self.assertEqual(saida, ["0.001", "1.999", "-0.001", "0", "v=0", "3.5"])


class TesteLinhaDoOperador(unittest.TestCase):
    def test_divisao_por_zero_em_varias_linhas(self):
        exc, _ = erro_ao_executar(self, "craft z = 0\nsay 1 +\n 2 +\n 3 / z")
        self.assertEqual(exc.linha, 4)

    def test_operador_em_outra_linha_do_operando(self):
        exc, _ = erro_ao_executar(self, "craft z = 0\nsay 1\n /\n z")
        self.assertEqual(exc.linha, 3)

    def test_parenteses_e_argumentos(self):
        exc, _ = erro_ao_executar(self, "command f(a) { drop a }\ncraft z = 0\nsay f(\n (1 +\n 2) /\n z\n)")
        self.assertEqual(exc.linha, 5)

    def test_erro_semantico_em_varias_linhas(self):
        r = compilar("say 1 +\n y")
        self.assertEqual(r.erros[0].linha, 2)
        r = compilar("say 1 +\n 2 *\n true")
        self.assertEqual(r.erros[0].linha, 2)

    def test_identificador_em_varias_linhas_em_execucao(self):
        exc, _ = erro_ao_executar(self, "command f() { say 1 }\nsay 1 +\n f()")
        self.assertEqual(exc.linha, 3)


class TesteRegrasDoCompilador(unittest.TestCase):
    def test_wait_negativo_recusado_na_compilacao(self):
        for codigo in ("wait -1", "wait -0.5", "wait -(1)", "wait (-1)", "wait 1 - 2", "wait - - -1"):
            with self.subTest(codigo):
                r = compilar(codigo)
                self.assertFalse(r.ok)
                self.assertIn("não aceita tempo negativo", r.erros[0].texto)

    def test_wait_valido_continua_compilando(self):
        for codigo in ("wait 0", "wait 1", "wait 0.5", "wait - -1", "wait 2 - 1", "craft a = 1\nwait -a", "wait 1 / 0"):
            with self.subTest(codigo):
                self.assertTrue(compilar(codigo).ok)

    def test_wait_negativo_em_variavel_falha_na_execucao(self):
        exc, _ = erro_ao_executar(self, "craft a = 1\nwait -a")
        self.assertIn("tempo negativo", exc.texto)

    def tem_aviso_de_while(self, codigo):
        r = compilar(codigo)
        self.assertTrue(r.ok, [str(e) for e in r.erros])
        return any("sem nenhum 'wait'" in a.texto for a in r.avisos)

    def test_aviso_de_while_sem_wait(self):
        for codigo in ("while true { }",
                       "while 1 == 1 { }",
                       "while not false { }",
                       "while true and 1 < 2 { }",
                       "while 1 + 1 == 2 or false { }",
                       'while "a" == "a" { }',
                       "while true { if false { wait 1 } }",
                       "while true { if true { } else { wait 1 } }",
                       "while true { while false { wait 1 } }",
                       "while true { repeat 0 { wait 1 } }",
                       "command e() { if false { wait 1 } }\nwhile true { e() }",
                       "command e() { drop 1 }\nwhile true { e() }"):
            with self.subTest(codigo):
                self.assertTrue(self.tem_aviso_de_while(codigo))

    def test_sem_aviso_falso_de_while(self):
        for codigo in ("while true { wait 1 }",
                       "while 1 == 1 { wait 1 }",
                       "while false { }",
                       "while 1 == 2 { }",
                       "while 1 == \"a\" { }",
                       "craft x = 1\nwhile x == 1 { x = 2 }",
                       "craft x = 1\nwhile true { if x > 0 { wait 1 } }",
                       "craft x = 1\nwhile true { if x > 0 { } else { wait 1 } }",
                       "while true { if true { wait 1 } }",
                       "while true { repeat 2 { wait 1 } }",
                       "command e() { wait 1 }\nwhile true { e() }",
                       "command e() { wait 1\n drop 1 }\nwhile true { say e() }",
                       "command e() { wait 1\n drop true }\nwhile true { if e() { } }",
                       "command a() { b() }\ncommand b() { a()\n wait 1 }\nwhile true { a() }",
                       "command f() {\n while true { drop 1 }\n}\nsay f()",
                       "craft c = 1\ncommand f() {\n while true { if c > 0 { drop 1 } }\n}\nsay f()"):
            with self.subTest(codigo):
                self.assertFalse(self.tem_aviso_de_while(codigo))

    def test_dois_levers_no_mesmo_pino(self):
        r = compilar("lever a = 26\nlever b = 26")
        self.assertTrue(r.ok)
        avisos = [a for a in r.avisos if "GPIO 26" in a.texto]
        self.assertEqual(len(avisos), 1)
        self.assertEqual(avisos[0].linha, 2)
        self.assertIn("a, b", avisos[0].texto)

    def test_sem_aviso_com_pinos_diferentes(self):
        self.assertEqual(compilar("lever a = 26\nlever b = 27").avisos, [])
        self.assertEqual(compilar("observer a = 34\nobserver b = 34").avisos, [])

    def test_sensor_com_lever_no_mesmo_pino_continua_erro(self):
        self.assertFalse(compilar("observer s = 33\nlever b = 33").ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
