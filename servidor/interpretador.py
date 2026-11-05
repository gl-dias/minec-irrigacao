"""
Interpretador da AST do MineC.

Percorre a árvore gerada pelo parser e executa cada instrução. Tudo o que
toca o mundo físico passa por um objeto `hardware`:

    hardware.ler_sensor(nome, pino, parar)   -> umidade em %, ou -1 se não há leitura válida
    hardware.acionar(nome, pino, ligado)     -> liga/desliga o relé
    hardware.fator_tempo()                   -> 1.0 = tempo real (pode mudar durante um 'wait')

Assim o mesmo programa roda no ESP32 de verdade ou no simulador.
"""

import math
import sys
import threading
import time
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from compilador import (
    ProgramNode, CommandDeclNode, VarDeclNode, AssignNode, CallNode, ReadNode,
    SayNode, PowerNode, WaitNode, DropNode, IfNode, WhileNode, RepeatNode,
    BinaryOpNode, UnaryOpNode, LiteralNode, IdentifierNode,
    LIMITE_DIGITOS, LIMITE_TEXTO, numero_fora_do_limite,
)

LIMITE_RECURSAO = 200
FATIA_ESPERA = 0.2  # segundos reais entre uma releitura e outra de fator_tempo()
NAO_LIDO = object()  # valor de um sensor antes do primeiro 'mine'


class ErroExecucao(Exception):
    def __init__(self, texto: str, linha: Optional[int] = None):
        super().__init__(texto)
        self.texto = texto
        self.linha = linha

    def __str__(self):
        return f"Linha {self.linha}: {self.texto}" if self.linha else self.texto


class ProgramaParado(Exception):
    """Lançada quando o usuário pede para parar o programa."""


class _Retorno(Exception):
    def __init__(self, valor):
        self.valor = valor


class Ambiente:
    def __init__(self, pai: Optional["Ambiente"] = None):
        self.pai = pai
        self.valores: Dict[str, object] = {}

    def _achar(self, nome):
        amb = self
        while amb is not None:
            if nome in amb.valores:
                return amb
            amb = amb.pai
        return None

    def ler(self, nome, linha):
        amb = self._achar(nome)
        if amb is None:
            raise ErroExecucao(f"'{nome}' ainda não existe neste ponto do programa.", linha)
        valor = amb.valores[nome]
        if valor is NAO_LIDO:
            raise ErroExecucao(f"O sensor '{nome}' ainda não foi lido. Use 'mine {nome}' antes de usar o valor.", linha)
        return valor

    def definir(self, nome, valor):
        self.valores[nome] = valor

    def atribuir(self, nome, valor, linha):
        amb = self._achar(nome)
        if amb is None:
            raise ErroExecucao(f"'{nome}' ainda não existe neste ponto do programa.", linha)
        amb.valores[nome] = valor


def formatar(valor) -> str:
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, float):
        if valor.is_integer():
            return str(int(valor))  # -0.0 também sai como "0"
        # 15 algarismos significativos: 0.1 + 0.2 sai 0.3, e 0.001 não vira 0
        texto = f"{valor:.15g}"
        return f"{Decimal(texto):f}" if "e" in texto else texto
    if valor is None:
        return "nada"
    return str(valor)


def _tipo(v):
    if isinstance(v, bool):
        return "booleano"
    if isinstance(v, (int, float)):
        return "número"
    return "texto"


class Interpretador:
    def __init__(
        self,
        programa: ProgramNode,
        hardware,
        parar: threading.Event,
        ao_dizer: Callable[[str, Optional[int]], None] = lambda texto, linha: None,
        ao_ler: Callable[[str, int, object], None] = lambda nome, pino, valor: None,
        ao_esperar: Callable[[float, float], None] = lambda inicio, fim: None,
    ):
        self.programa = programa
        self.hw = hardware
        self.parar = parar
        self.ao_dizer = ao_dizer
        self.ao_ler = ao_ler
        self.ao_esperar = ao_esperar

        self.globais = Ambiente()
        self.commands: Dict[str, CommandDeclNode] = {}
        self.sensores: Dict[str, int] = {}
        self.atuadores: Dict[str, int] = {}
        self.ligados: set = set()
        self.profundidade = 0
        self._passos_sem_espera = 0
        self._linha_atual = None

    # ---------------- execução ----------------

    def executar(self):
        for no in self.programa.body:
            if isinstance(no, CommandDeclNode):
                self.commands[no.name] = no
        try:
            for no in self.programa.body:
                if not isinstance(no, CommandDeclNode):
                    self.instrucao(no, self.globais)
        except _Retorno:
            raise ErroExecucao("'drop' fora de um command.")
        except RecursionError:
            raise ErroExecucao("Chamadas ou blocos aninhados demais: a pilha de execução estourou. "
                               "Reduza a recursão ou simplifique a expressão.", self._linha_atual) from None
        except MemoryError:
            raise ErroExecucao("O programa usou memória demais e foi interrompido.", self._linha_atual) from None
        finally:
            self.desligar_tudo()

    def desligar_tudo(self):
        for nome in list(self.ligados):
            try:
                self.hw.acionar(nome, self.atuadores[nome], False)
            except Exception:
                pass
        self.ligados.clear()

    def _checar_parada(self):
        if self.parar.is_set():
            raise ProgramaParado()
        # cede a CPU em laços sem 'wait' para não travar o servidor
        self._passos_sem_espera += 1
        if self._passos_sem_espera >= 5000:
            self._passos_sem_espera = 0
            time.sleep(0.01)

    def bloco(self, instrucoes: List, amb: Ambiente):
        local = Ambiente(amb)
        for no in instrucoes:
            self.instrucao(no, local)

    def instrucao(self, no, amb: Ambiente):
        self._checar_parada()
        linha = getattr(no, "linha", None)
        self._linha_atual = linha

        if isinstance(no, VarDeclNode):
            valor = self.avaliar(no.value_expr, amb)
            if no.kind == "observer":
                self.sensores[no.name] = int(valor)
                valor = NAO_LIDO  # o número é o pino, não uma leitura
            elif no.kind == "lever":
                self.atuadores[no.name] = int(valor)
                self.hw.acionar(no.name, int(valor), False)  # começa desligado
                valor = False
            amb.definir(no.name, valor)

        elif isinstance(no, AssignNode):
            amb.atribuir(no.name, self.avaliar(no.expr, amb), linha)

        elif isinstance(no, CallNode):
            self.chamar(no, amb)

        elif isinstance(no, ReadNode):
            if no.name not in self.sensores:
                raise ErroExecucao(f"Sensor '{no.name}' não declarado.", linha)
            pino = self.sensores[no.name]
            valor = self.hw.ler_sensor(no.name, pino, self.parar)
            if self.parar.is_set():
                raise ProgramaParado()
            amb.atribuir(no.name, valor, linha)
            self.ao_ler(no.name, pino, valor)

        elif isinstance(no, PowerNode):
            if no.name not in self.atuadores:
                raise ErroExecucao(f"Atuador '{no.name}' não declarado.", linha)
            # anota antes de acionar: se o acionamento falhar no meio, desligar_tudo ainda desliga
            self.ligados.add(no.name)
            self.hw.acionar(no.name, self.atuadores[no.name], no.on)
            if not no.on:
                self.ligados.discard(no.name)

        elif isinstance(no, SayNode):
            self.ao_dizer(formatar(self.avaliar(no.expr, amb)), linha)

        elif isinstance(no, WaitNode):
            segundos = self.numero(self.avaliar(no.expr, amb), "wait", linha)
            if segundos < 0:
                raise ErroExecucao("'wait' recebeu um tempo negativo.", linha)
            self.esperar(segundos, linha)

        elif isinstance(no, DropNode):
            raise _Retorno(self.avaliar(no.expr, amb))

        elif isinstance(no, IfNode):
            if self.booleano(self.avaliar(no.condition, amb), "if", linha):
                self.bloco(no.then_branch, amb)
            elif no.else_branch is not None:
                self.bloco(no.else_branch, amb)

        elif isinstance(no, WhileNode):
            while self.booleano(self.avaliar(no.condition, amb), "while", linha):
                self._checar_parada()
                self.bloco(no.body, amb)

        elif isinstance(no, RepeatNode):
            vezes = self.avaliar(no.times, amb)
            if isinstance(vezes, bool) or not isinstance(vezes, (int, float)) or (isinstance(vezes, float) and not vezes.is_integer()):
                raise ErroExecucao(f"'repeat' precisa de um número inteiro, mas recebeu {formatar(vezes)}.", linha)
            for _ in range(int(vezes)):
                self._checar_parada()
                self.bloco(no.body, amb)

        else:
            raise ErroExecucao(f"Instrução desconhecida: {type(no).__name__}", linha)

    def esperar(self, segundos: float, linha: Optional[int] = None):
        """Desconta o tempo em fatias curtas, para a aceleração valer na espera em curso."""
        self._passos_sem_espera = 0
        try:
            restante = float(segundos)
        except OverflowError:
            restante = math.inf
        if not math.isfinite(restante):
            raise ErroExecucao("'wait' recebeu um tempo grande demais.", linha)
        while True:
            fator = max(self.hw.fator_tempo(), 0.001)
            agora = time.time()
            real = restante / fator
            self.ao_esperar(agora, min(agora + real, sys.float_info.max))
            inicio_fatia = time.perf_counter()
            if self.parar.wait(min(real, FATIA_ESPERA)):
                raise ProgramaParado()
            restante -= (time.perf_counter() - inicio_fatia) * fator
            if restante <= 0:
                break
        self.ao_esperar(0, 0)

    def chamar(self, no: CallNode, amb: Ambiente):
        linha = getattr(no, "linha", None)
        cmd = self.commands.get(no.name)
        if cmd is None:
            raise ErroExecucao(f"Command '{no.name}' não existe.", linha)
        if len(no.args) != len(cmd.params):
            raise ErroExecucao(f"'{no.name}' espera {len(cmd.params)} argumento(s) e recebeu {len(no.args)}.", linha)
        valores = [self.avaliar(a, amb) for a in no.args]
        if self.profundidade >= LIMITE_RECURSAO:
            raise ErroExecucao(f"Chamadas demais de '{no.name}' (recursão sem fim?).", linha)
        local = Ambiente(self.globais)
        for nome, valor in zip(cmd.params, valores):
            local.definir(nome, valor)
        self.profundidade += 1
        try:
            for instrucao in cmd.body:
                self.instrucao(instrucao, local)
        except _Retorno as r:
            return r.valor
        finally:
            self.profundidade -= 1
        return None

    # ---------------- expressões ----------------

    def numero(self, v, onde, linha):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ErroExecucao(f"'{onde}' precisa de número, mas recebeu {formatar(v)}.", linha)
        return v

    def booleano(self, v, onde, linha):
        if not isinstance(v, bool):
            raise ErroExecucao(f"A condição do '{onde}' precisa ser true ou false, mas deu {formatar(v)}.", linha)
        return v

    def conta(self, op, a, b, linha):
        try:
            if op == "+":
                r = a + b
            elif op == "-":
                r = a - b
            elif op == "*":
                r = a * b
            else:
                if b == 0:
                    raise ErroExecucao("Divisão por zero.", linha)
                exata = isinstance(a, int) and isinstance(b, int) and a % b == 0
                r = a // b if exata else a / b
        except OverflowError:
            r = math.inf  # só acontece com número fora do que um decimal consegue guardar
        if numero_fora_do_limite(r):
            raise ErroExecucao(f"Número grande demais na conta '{op}': o limite é de {LIMITE_DIGITOS} dígitos "
                               "em um inteiro e cerca de 1.8 x 10^308 em um decimal.", linha)
        return r

    def avaliar(self, no, amb: Ambiente):
        linha = getattr(no, "linha", None)

        if isinstance(no, LiteralNode):
            return no.value
        if isinstance(no, IdentifierNode):
            return amb.ler(no.name, linha)
        if isinstance(no, CallNode):
            valor = self.chamar(no, amb)
            if valor is None:
                raise ErroExecucao(f"O command '{no.name}' não devolveu valor (faltou 'drop').", linha)
            return valor
        if isinstance(no, UnaryOpNode):
            v = self.avaliar(no.operand, amb)
            if no.op == "not":
                return not self.booleano(v, "not", linha)
            return -self.numero(v, "-", linha)
        if isinstance(no, BinaryOpNode):
            op = no.op
            if op == "and":
                return self.booleano(self.avaliar(no.left, amb), "and", linha) and self.booleano(self.avaliar(no.right, amb), "and", linha)
            if op == "or":
                return self.booleano(self.avaliar(no.left, amb), "or", linha) or self.booleano(self.avaliar(no.right, amb), "or", linha)
            a = self.avaliar(no.left, amb)
            b = self.avaliar(no.right, amb)
            if op == "==":
                return _tipo(a) == _tipo(b) and a == b
            if op == "!=":
                return not (_tipo(a) == _tipo(b) and a == b)
            if op == "+" and (isinstance(a, str) or isinstance(b, str)):
                texto = formatar(a) + formatar(b)
                if len(texto) > LIMITE_TEXTO:
                    raise ErroExecucao(f"Texto grande demais: o limite é de {LIMITE_TEXTO} caracteres.", linha)
                return texto
            a = self.numero(a, op, linha)
            b = self.numero(b, op, linha)
            if op in ("+", "-", "*", "/"):
                return self.conta(op, a, b, linha)
            if op == "<":
                return a < b
            if op == ">":
                return a > b
            if op == "<=":
                return a <= b
            if op == ">=":
                return a >= b
        raise ErroExecucao(f"Expressão desconhecida: {type(no).__name__}", linha)
