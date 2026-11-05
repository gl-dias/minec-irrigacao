"""
Front-end completo do compilador MineC usado pelo protótipo.

Reaproveita o LexerMineC e o ParserMineC do projeto (minec.py) e acrescenta
as duas fases que faltavam:

  1. Parser com linhas: anota em cada nó da AST a linha do código-fonte,
     para que erros semânticos e de execução apontem onde está o problema.
  2. Análise semântica: implementa as regras informais da seção 4 da
     documentação (declaração antes do uso, escopo, tipos, mine/power só em
     sensor/atuador, drop só em command, quantidade de argumentos) e as
     regras de hardware do ESP32 (pinos válidos para sensor e atuador).

Uso:
    resultado = compilar(codigo)
    resultado.ok, resultado.ast, resultado.erros, resultado.avisos
"""

import math
import operator
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Dentro do repositório vale o minec.py da raiz (dois níveis acima desta pasta).
# Fora dele, vale a cópia ao lado deste arquivo; testes.py confere se as duas batem.
AQUI = os.path.abspath(os.path.dirname(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, "..", ".."))
sys.path[:0] = [RAIZ, AQUI]

from minec import (  # noqa: E402
    LexerMineC, ParserMineC, TokenType, ASTNode, ProgramNode, CommandDeclNode,
    VarDeclNode, AssignNode, CallNode, ReadNode, SayNode, PowerNode, WaitNode,
    DropNode, IfNode, WhileNode, RepeatNode, BinaryOpNode, UnaryOpNode,
    LiteralNode, IdentifierNode,
)


# =====================================================================
# 1. PARSER COM NÚMERO DE LINHA
# =====================================================================

class ParserComLinhas(ParserMineC):
    """Mesmo parser do projeto, mas grava `node.linha` em cada nó criado."""

    def _marcar(self, no, linha):
        if isinstance(no, ASTNode) and getattr(no, "linha", None) is None:
            no.linha = linha
        return no

    def statement(self):
        linha = self.peek().line
        return self._marcar(super().statement(), linha)

    def command_declaration(self):
        linha = self.previous().line
        return self._marcar(super().command_declaration(), linha)

    def if_statement(self):
        # cobre o 'else if', que não passa por statement()
        linha = self.previous().line
        return self._marcar(super().if_statement(), linha)

    def _binary(self, next_level, *ops):
        # operação de várias linhas: o erro aponta a linha do operador
        expr = next_level()
        while self.match(*ops):
            operador = self.previous()
            direita = next_level()
            expr = self._marcar(BinaryOpNode(expr, operador.value, direita), operador.line)
        return expr

    def unary(self):
        token = self.peek()
        if self.match(TokenType.TK_NOT, TokenType.TK_MINUS):
            return self._marcar(UnaryOpNode(token.value, self.unary()), token.line)
        return self._marcar(self.primary(), token.line)


# =====================================================================
# 2. REGRAS DE HARDWARE (ESP32 clássico, DevKit V1 / WROOM-32)
# =====================================================================

# ADC1 continua funcionando com o Wi-Fi ligado; ADC2 não.
PINOS_ADC1 = {32, 33, 34, 35, 36, 39}
PINOS_ADC2 = {0, 2, 4, 12, 13, 14, 15, 25, 26, 27}
PINOS_FLASH = {6, 7, 8, 9, 10, 11}
PINOS_SO_ENTRADA = {34, 35, 36, 39}
PINOS_SAIDA_OK = {2, 4, 5, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33}
PINOS_BOOT = {0, 2, 5, 12, 15}  # pinos de strapping: funcionam, mas exigem cuidado


def problema_pino_sensor(pino: int) -> Optional[str]:
    if pino in PINOS_ADC1:
        return None
    if pino in PINOS_FLASH:
        return f"GPIO {pino} é ligado à memória flash do ESP32 e não pode ser usado. Use 32, 33, 34, 35, 36 ou 39."
    if pino in PINOS_ADC2:
        return f"GPIO {pino} é ADC2 e não lê sensor analógico com o Wi-Fi ligado. Use 32, 33, 34, 35, 36 ou 39."
    return f"GPIO {pino} não tem entrada analógica no ESP32. Use 32, 33, 34, 35, 36 ou 39."


def problema_pino_atuador(pino: int) -> Optional[str]:
    if pino in PINOS_FLASH:
        return f"GPIO {pino} é ligado à memória flash do ESP32 e não pode ser usado. Use, por exemplo, 26, 27, 25 ou 33."
    if pino in PINOS_SO_ENTRADA:
        return f"GPIO {pino} é somente entrada no ESP32 e não aciona relé. Use, por exemplo, 26, 27, 25 ou 33."
    if pino not in PINOS_SAIDA_OK:
        return f"GPIO {pino} não é uma saída válida no ESP32. Use, por exemplo, 26, 27, 25 ou 33."
    return None


# =====================================================================
# 3. ANÁLISE SEMÂNTICA
# =====================================================================

INTEIRO, DECIMAL, NUMERO, TEXTO, BOOLEANO = "inteiro", "decimal", "número", "texto", "booleano"
NUMERICOS = {INTEIRO, DECIMAL, NUMERO}
TIPO_LITERAL = {"INTEIRO": INTEIRO, "DECIMAL": DECIMAL, "TEXTO": TEXTO, "BOOLEANO": BOOLEANO}

# Limites de valores, para um programa não comer a memória da máquina.
# O interpretador aplica os mesmos durante a execução.
LIMITE_DIGITOS = 4000      # abaixo dos 4300 dígitos que o Python aceita converter em texto
LIMITE_INTEIRO = 10 ** LIMITE_DIGITOS
LIMITE_TEXTO = 100_000     # caracteres; o console do painel guarda 400 mensagens


def numero_fora_do_limite(valor) -> bool:
    if isinstance(valor, float):
        return not math.isfinite(valor)
    return abs(valor) >= LIMITE_INTEIRO


# ---------------- expressões que só dependem de literais ----------------

NAO_CONSTANTE = object()
_OPERACOES = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv,
              "<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge}


def _e_numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _mesmo_tipo(a, b) -> bool:
    return (isinstance(a, bool) == isinstance(b, bool)) and (isinstance(a, str) == isinstance(b, str))


def constante(no):
    """Valor da expressão, com as regras do interpretador, se ela só depende de literais."""
    if isinstance(no, LiteralNode):
        return no.value
    if isinstance(no, UnaryOpNode):
        valor = constante(no.operand)
        if no.op == "not":
            return not valor if isinstance(valor, bool) else NAO_CONSTANTE
        return -valor if _e_numero(valor) else NAO_CONSTANTE
    if isinstance(no, BinaryOpNode):
        a = constante(no.left)
        if (no.op == "and" and a is False) or (no.op == "or" and a is True):
            return a
        b = constante(no.right)
        if a is NAO_CONSTANTE or b is NAO_CONSTANTE:
            return NAO_CONSTANTE
        if no.op in ("and", "or"):
            return b if isinstance(a, bool) and isinstance(b, bool) else NAO_CONSTANTE
        if no.op == "==":
            return _mesmo_tipo(a, b) and a == b
        if no.op == "!=":
            return not (_mesmo_tipo(a, b) and a == b)
        if not (_e_numero(a) and _e_numero(b)):
            return NAO_CONSTANTE
        try:
            resultado = _OPERACOES[no.op](a, b)
        except (ZeroDivisionError, OverflowError):
            return NAO_CONSTANTE
        if _e_numero(resultado) and numero_fora_do_limite(resultado):
            return NAO_CONSTANTE
        return resultado
    return NAO_CONSTANTE


@dataclass
class Simbolo:
    nome: str
    tipo_simbolo: str            # craft | observer | lever | param | command
    tipo: Optional[str] = None   # tipo do valor (None = desconhecido)
    pino: Optional[int] = None
    params: Optional[List[str]] = None
    linha: Optional[int] = None


class Escopo:
    def __init__(self, pai: Optional["Escopo"] = None):
        self.pai = pai
        self.simbolos: Dict[str, Simbolo] = {}

    def buscar(self, nome: str) -> Optional[Simbolo]:
        escopo = self
        while escopo is not None:
            if nome in escopo.simbolos:
                return escopo.simbolos[nome]
            escopo = escopo.pai
        return None

    def declarar(self, simbolo: Simbolo):
        self.simbolos[simbolo.nome] = simbolo


@dataclass
class Mensagem:
    fase: str
    texto: str
    linha: Optional[int] = None

    def para_dict(self):
        return {"fase": self.fase, "texto": self.texto, "linha": self.linha}

    def __str__(self):
        onde = f"Linha {self.linha}: " if self.linha else ""
        return f"[{self.fase}] {onde}{self.texto}"


class AnalisadorSemantico:
    def __init__(self, checar_pinos: bool = True, pinos_como_erro: bool = True):
        self.checar_pinos = checar_pinos
        self.pinos_como_erro = pinos_como_erro
        self.erros: List[Mensagem] = []
        self.avisos: List[Mensagem] = []
        self.global_ = Escopo()
        self.dentro_de_command = False
        self.commands: Dict[str, CommandDeclNode] = {}
        self.sensores_usados: Dict[str, ASTNode] = {}  # nome -> primeiro uso em expressão
        self.sensores_lidos: set = set()
        self.no_atual = None

    # ---------------- utilidades ----------------

    def erro(self, texto, no=None):
        self.erros.append(Mensagem("Semântica", texto, getattr(no, "linha", None)))

    def aviso(self, texto, no=None):
        self.avisos.append(Mensagem("Aviso", texto, getattr(no, "linha", None)))

    def problema_pino(self, texto, no):
        (self.erro if self.pinos_como_erro else self.aviso)(texto, no)

    @staticmethod
    def compativeis(a, b):
        if a is None or b is None:
            return True
        if a in NUMERICOS and b in NUMERICOS:
            return True
        return a == b

    # ---------------- programa ----------------

    def analisar(self, programa: ProgramNode):
        # Passo 1: commands são visíveis em todo o programa (regra 2)
        for no in programa.body:
            if isinstance(no, CommandDeclNode):
                if no.name in self.commands:
                    self.erro(f"O command '{no.name}' já foi declarado.", no)
                    continue
                if len(set(no.params)) != len(no.params):
                    self.erro(f"O command '{no.name}' tem parâmetros repetidos.", no)
                self.commands[no.name] = no
                self.global_.declarar(Simbolo(no.name, "command", params=no.params, linha=no.linha))

        # Passo 2: instruções globais, em ordem
        for no in programa.body:
            if not isinstance(no, CommandDeclNode):
                self.instrucao(no, self.global_, nivel_global=True)

        # Passo 3: corpos dos commands, enxergando todos os globais
        for cmd in self.commands.values():
            escopo = Escopo(self.global_)
            for p in cmd.params:
                escopo.declarar(Simbolo(p, "param", linha=cmd.linha))
            self.dentro_de_command = True
            self.bloco(cmd.body, escopo)
            self.dentro_de_command = False

        self.checar_pinos_repetidos()
        self.checar_sensores_sem_mine()

    def checar_sensores_sem_mine(self):
        for nome, no in self.sensores_usados.items():
            if nome not in self.sensores_lidos:
                self.aviso(f"O sensor '{nome}' é usado, mas não existe 'mine {nome}' no programa. "
                           f"Sem a leitura, o programa para com erro ao usar o valor.", no)

    def checar_pinos_repetidos(self):
        usados = {}
        for s in self.global_.simbolos.values():
            if s.tipo_simbolo in ("observer", "lever") and s.pino is not None:
                usados.setdefault(s.pino, []).append(s)
        for pino, simbolos in usados.items():
            tipos = {s.tipo_simbolo for s in simbolos}
            if len(tipos) > 1:
                nomes = ", ".join(s.nome for s in simbolos)
                self.erros.append(Mensagem("Semântica", f"GPIO {pino} usado ao mesmo tempo como sensor e atuador ({nomes}).", simbolos[-1].linha))
            elif tipos == {"lever"} and len(simbolos) > 1:
                nomes = ", ".join(s.nome for s in simbolos)
                self.aviso(f"GPIO {pino} controla mais de um atuador ({nomes}): desligar um também desliga o outro.", simbolos[-1])

    def bloco(self, instrucoes, escopo_pai):
        escopo = Escopo(escopo_pai)  # cada bloco cria um escopo (regra 2)
        for no in instrucoes:
            self.instrucao(no, escopo)

    # ---------------- instruções ----------------

    def instrucao(self, no, escopo: Escopo, nivel_global=False):
        self.no_atual = no
        if isinstance(no, VarDeclNode):
            self.declaracao(no, escopo, nivel_global)
        elif isinstance(no, AssignNode):
            simbolo = escopo.buscar(no.name)
            tipo = self.expressao(no.expr, escopo)
            if simbolo is None:
                self.erro(f"'{no.name}' não foi declarado. Use 'craft {no.name} = ...' antes.", no)
            elif simbolo.tipo_simbolo == "observer":
                self.erro(f"'{no.name}' é um sensor: o valor dele só muda com 'mine {no.name}'.", no)
            elif simbolo.tipo_simbolo == "lever":
                self.erro(f"'{no.name}' é um atuador: use 'power {no.name}' ou 'unpower {no.name}'.", no)
            elif simbolo.tipo_simbolo == "command":
                self.erro(f"'{no.name}' é um command e não pode receber valor.", no)
            elif not self.compativeis(simbolo.tipo, tipo):
                self.erro(f"'{no.name}' é {simbolo.tipo} e não pode receber um valor {tipo}.", no)
            elif simbolo.tipo == INTEIRO and tipo in (DECIMAL, NUMERO):
                simbolo.tipo = NUMERO
        elif isinstance(no, CallNode):
            self.chamada(no, escopo)
        elif isinstance(no, ReadNode):
            simbolo = escopo.buscar(no.name)
            if simbolo is None:
                self.erro(f"Sensor '{no.name}' não foi declarado. Use 'observer {no.name} = <pino>'.", no)
            elif simbolo.tipo_simbolo != "observer":
                self.erro(f"'mine' só funciona com sensores (observer), e '{no.name}' é {self.descrever(simbolo)}.", no)
            else:
                self.sensores_lidos.add(no.name)
        elif isinstance(no, PowerNode):
            simbolo = escopo.buscar(no.name)
            palavra = "power" if no.on else "unpower"
            if simbolo is None:
                self.erro(f"Atuador '{no.name}' não foi declarado. Use 'lever {no.name} = <pino>'.", no)
            elif simbolo.tipo_simbolo != "lever":
                self.erro(f"'{palavra}' só funciona com atuadores (lever), e '{no.name}' é {self.descrever(simbolo)}.", no)
        elif isinstance(no, SayNode):
            self.expressao(no.expr, escopo)
        elif isinstance(no, WaitNode):
            tipo = self.expressao(no.expr, escopo)
            if tipo is not None and tipo not in NUMERICOS:
                self.erro(f"'wait' precisa de um número de segundos, mas recebeu {tipo}.", no)
            valor = constante(no.expr)
            if _e_numero(valor) and valor < 0:
                self.erro("'wait' não aceita tempo negativo.", no)
        elif isinstance(no, DropNode):
            if not self.dentro_de_command:
                self.erro("'drop' só pode aparecer dentro de um command.", no)
            self.expressao(no.expr, escopo)
        elif isinstance(no, IfNode):
            self.condicao(no.condition, escopo, "if", no)
            self.bloco(no.then_branch, escopo)
            if no.else_branch is not None:
                self.bloco(no.else_branch, escopo)
        elif isinstance(no, WhileNode):
            self.condicao(no.condition, escopo, "while", no)
            if constante(no.condition) is True and not self.tem_wait(no.body) and not self.tem_drop(no.body):
                self.aviso("'while' com condição sempre verdadeira e sem nenhum 'wait' vai rodar sem parar e travar a fazenda.", no)
            self.bloco(no.body, escopo)
        elif isinstance(no, RepeatNode):
            tipo = self.expressao(no.times, escopo)
            if tipo is not None and tipo != INTEIRO:
                self.erro(f"'repeat' precisa de um número inteiro de vezes, mas recebeu {tipo}.", no)
            self.bloco(no.body, escopo)
        elif isinstance(no, CommandDeclNode):
            self.erro("'command' só pode ser declarado no nível global do programa.", no)
        else:
            self.erro(f"Instrução desconhecida: {type(no).__name__}", no)

    def declaracao(self, no: VarDeclNode, escopo: Escopo, nivel_global: bool):
        if no.name in escopo.simbolos:
            self.erro(f"'{no.name}' já foi declarado neste bloco.", no)
        if no.name in self.commands:
            self.erro(f"'{no.name}' já é o nome de um command.", no)

        tipo = self.expressao(no.value_expr, escopo)

        if no.kind == "craft":
            escopo.declarar(Simbolo(no.name, "craft", tipo=tipo, linha=no.linha))
            return

        nome_tipo = "sensor (observer)" if no.kind == "observer" else "atuador (lever)"
        if not nivel_global:
            self.erro(f"O {nome_tipo} '{no.name}' deve ser declarado no nível global, fora de blocos e commands.", no)

        pino = None
        if isinstance(no.value_expr, LiteralNode) and no.value_expr.type_name == "INTEIRO":
            pino = no.value_expr.value
        else:
            self.erro(f"O {nome_tipo} '{no.name}' precisa de um número inteiro com o pino do ESP32 (ex.: {'34' if no.kind == 'observer' else '26'}).", no)

        if pino is not None and self.checar_pinos:
            problema = problema_pino_sensor(pino) if no.kind == "observer" else problema_pino_atuador(pino)
            if problema:
                self.problema_pino(f"'{no.name}': {problema}", no)
            elif no.kind == "lever" and pino in PINOS_BOOT:
                self.aviso(f"'{no.name}': GPIO {pino} participa do boot do ESP32; se o relé disparar ao ligar, troque para 26 ou 27.", no)

        tipo_valor = INTEIRO if no.kind == "observer" else None
        escopo.declarar(Simbolo(no.name, no.kind, tipo=tipo_valor, pino=pino, linha=no.linha))

    def condicao(self, expr, escopo, palavra, no):
        tipo = self.expressao(expr, escopo)
        if tipo is not None and tipo != BOOLEANO:
            self.erro(f"A condição do '{palavra}' precisa ser verdadeira ou falsa, mas é {tipo}.", no)

    def chamada(self, no: CallNode, escopo: Escopo):
        for arg in no.args:
            self.expressao(arg, escopo)
        simbolo = escopo.buscar(no.name)
        if simbolo is None:
            self.erro(f"Command '{no.name}' não foi declarado.", no)
        elif simbolo.tipo_simbolo != "command":
            self.erro(f"'{no.name}' é {self.descrever(simbolo)}, não um command.", no)
        elif len(no.args) != len(simbolo.params):
            self.erro(f"O command '{no.name}' espera {len(simbolo.params)} argumento(s), mas recebeu {len(no.args)}.", no)
        return None  # tipo de retorno só é conhecido na execução

    def tem_wait(self, instrucoes) -> bool:
        return self._alcanca(instrucoes, WaitNode, set())

    def tem_drop(self, instrucoes) -> bool:
        # um 'drop' só encerra o próprio command, então não entra nos commands chamados
        return self._alcanca(instrucoes, DropNode, None)

    def _alcanca(self, nos, tipo, visitados) -> bool:
        """Algum nó de `tipo` em trecho que pode rodar? Com `visitados`, segue os commands chamados."""
        for no in nos:
            if isinstance(no, tipo):
                return True
            if visitados is not None and isinstance(no, CallNode) and no.name in self.commands and no.name not in visitados:
                visitados.add(no.name)
                if self._alcanca(self.commands[no.name].body, tipo, visitados):
                    return True
            if self._alcanca(self._filhos_que_podem_rodar(no), tipo, visitados):
                return True
        return False

    @staticmethod
    def _filhos_que_podem_rodar(no):
        if isinstance(no, IfNode):
            cond = constante(no.condition)
            return ([no.condition]
                    + (no.then_branch if cond is not False else [])
                    + ((no.else_branch or []) if cond is not True else []))
        if isinstance(no, WhileNode):
            return [no.condition] + (no.body if constante(no.condition) is not False else [])
        if isinstance(no, RepeatNode):
            vezes = constante(no.times)
            return [no.times] + ([] if _e_numero(vezes) and vezes <= 0 else no.body)
        return no.children()

    @staticmethod
    def descrever(simbolo: Simbolo) -> str:
        return {"craft": "uma variável", "observer": "um sensor", "lever": "um atuador",
                "param": "um parâmetro", "command": "um command"}[simbolo.tipo_simbolo]

    # ---------------- expressões ----------------

    def expressao(self, no, escopo: Escopo) -> Optional[str]:
        if isinstance(no, LiteralNode):
            return TIPO_LITERAL.get(no.type_name)
        if isinstance(no, IdentifierNode):
            simbolo = escopo.buscar(no.name)
            if simbolo is None:
                self.erro(f"'{no.name}' não foi declarado antes do uso.", no)
                return None
            if simbolo.tipo_simbolo == "command":
                self.erro(f"'{no.name}' é um command; para chamar use '{no.name}(...)'.", no)
                return None
            if simbolo.tipo_simbolo == "lever":
                self.erro(f"'{no.name}' é um atuador e não tem valor; use 'power {no.name}' ou 'unpower {no.name}'.", no)
                return None
            if simbolo.tipo_simbolo == "observer":
                self.sensores_usados.setdefault(no.name, no)
            return simbolo.tipo
        if isinstance(no, CallNode):
            return self.chamada(no, escopo)
        if isinstance(no, UnaryOpNode):
            tipo = self.expressao(no.operand, escopo)
            if no.op == "not":
                if tipo is not None and tipo != BOOLEANO:
                    self.erro(f"'not' só funciona com verdadeiro/falso, mas recebeu {tipo}.", no)
                return BOOLEANO
            if tipo is not None and tipo not in NUMERICOS:
                self.erro(f"'-' só funciona com números, mas recebeu {tipo}.", no)
            return tipo
        if isinstance(no, BinaryOpNode):
            esq = self.expressao(no.left, escopo)
            dir_ = self.expressao(no.right, escopo)
            op = no.op
            if op in ("and", "or"):
                for t in (esq, dir_):
                    if t is not None and t != BOOLEANO:
                        self.erro(f"'{op}' só funciona com verdadeiro/falso, mas recebeu {t}.", no)
                return BOOLEANO
            if op in ("==", "!="):
                if esq and dir_ and not self.compativeis(esq, dir_):
                    self.aviso(f"Comparação '{op}' entre {esq} e {dir_} sempre dá {'false' if op == '==' else 'true'}.", no)
                return BOOLEANO
            if op in ("<", ">", "<=", ">="):
                for t in (esq, dir_):
                    if t is not None and t not in NUMERICOS:
                        self.erro(f"'{op}' compara números, mas recebeu {t}.", no)
                return BOOLEANO
            if op == "+" and TEXTO in (esq, dir_):
                return TEXTO
            for t in (esq, dir_):
                if t is not None and t not in NUMERICOS:
                    self.erro(f"'{op}' só funciona com números{' (ou texto, no caso do +)' if op == '+' else ''}, mas recebeu {t}.", no)
                    return None
            if esq is None or dir_ is None:
                return None
            if op == "/":
                return NUMERO
            return INTEIRO if esq == dir_ == INTEIRO else (DECIMAL if DECIMAL in (esq, dir_) else NUMERO)
        self.erro(f"Expressão desconhecida: {type(no).__name__}", no)
        return None


# =====================================================================
# 4. PIPELINE COMPLETO
# =====================================================================

@dataclass
class Resultado:
    ok: bool
    tokens: list = field(default_factory=list)
    ast: Optional[ProgramNode] = None
    erros: List[Mensagem] = field(default_factory=list)
    avisos: List[Mensagem] = field(default_factory=list)
    sensores: Dict[str, int] = field(default_factory=dict)   # nome -> pino
    atuadores: Dict[str, int] = field(default_factory=dict)  # nome -> pino

    def tokens_dict(self):
        return [{"tipo": t.type.name, "valor": t.value if not isinstance(t.value, bool) else str(t.value).lower(),
                 "linha": t.line, "coluna": t.column} for t in self.tokens]

    def ast_texto(self):
        if not self.ast:
            return ""
        try:
            return self.ast.print_tree()
        except (RecursionError, MemoryError):
            return "(árvore grande demais para mostrar)"

    def para_dict(self):
        return {
            "ok": self.ok,
            "erros": [e.para_dict() for e in self.erros],
            "avisos": [a.para_dict() for a in self.avisos],
            "tokens": self.tokens_dict(),
            "ast": self.ast_texto(),
            "sensores": self.sensores,
            "atuadores": self.atuadores,
        }


def _mensagem_de_syntaxerror(fase, exc):
    texto = str(exc)
    for prefixo in ("💥 [Análise Léxica Error] ", "💥 [Análise Sintática Error] "):
        texto = texto.replace(prefixo, "")
    linha = None
    if texto.startswith("Linha "):
        try:
            linha = int(texto.split()[1].rstrip(","))
        except ValueError:
            pass
    return Mensagem(fase, texto, linha)


MSG_COMPLEXO_DEMAIS = ("Programa complexo demais para compilar: expressão muito longa ou blocos e parênteses "
                       "aninhados demais. Simplifique: guarde partes da conta em variáveis com 'craft' e "
                       "separe os blocos em commands.")
MSG_NUMERO_GRANDE = (f"Número grande demais. Use no máximo {LIMITE_DIGITOS} dígitos em um inteiro "
                     "e 308 dígitos antes do ponto em um decimal.")
MSG_TEXTO_GRANDE = f"Texto grande demais. Use no máximo {LIMITE_TEXTO} caracteres."


def _literal_fora_do_limite(tokens) -> Optional[Mensagem]:
    for t in tokens:
        if t.type in (TokenType.TK_INTEGER, TokenType.TK_DECIMAL) and numero_fora_do_limite(t.value):
            return Mensagem("Léxica", f"Linha {t.line}, Coluna {t.column}: {MSG_NUMERO_GRANDE}", t.line)
        if t.type == TokenType.TK_STRING and len(t.value) > LIMITE_TEXTO:
            return Mensagem("Léxica", f"Linha {t.line}, Coluna {t.column}: {MSG_TEXTO_GRANDE}", t.line)
    return None


def compilar(codigo: str, checar_pinos: bool = True, pinos_como_erro: bool = True) -> Resultado:
    """Nunca lança exceção por causa do conteúdo de `codigo`: problemas vão em `Resultado.erros`."""
    if not isinstance(codigo, str):
        return Resultado(False, erros=[Mensagem("Entrada", f"O código precisa ser um texto, mas recebeu {type(codigo).__name__}.")])

    lexer = LexerMineC(codigo)
    try:
        tokens = lexer.tokenize()
    except SyntaxError as exc:
        return Resultado(False, erros=[_mensagem_de_syntaxerror("Léxica", exc)])
    except ValueError:  # o int() do lexer recusa inteiro com mais de 4300 dígitos
        return Resultado(False, erros=[Mensagem("Léxica", f"Linha {lexer.line}, Coluna {lexer.col}: {MSG_NUMERO_GRANDE}", lexer.line)])
    except MemoryError:
        return Resultado(False, erros=[Mensagem("Léxica", MSG_COMPLEXO_DEMAIS, lexer.line)])

    problema = _literal_fora_do_limite(tokens)
    if problema:
        return Resultado(False, erros=[problema])

    parser = ParserComLinhas(tokens)
    try:
        ast = parser.parse()
    except SyntaxError as exc:
        return Resultado(False, tokens=tokens, erros=[_mensagem_de_syntaxerror("Sintática", exc)])
    except (RecursionError, MemoryError):
        return Resultado(False, tokens=tokens, erros=[Mensagem("Sintática", MSG_COMPLEXO_DEMAIS, parser.peek().line)])

    sem = AnalisadorSemantico(checar_pinos, pinos_como_erro)
    try:
        sem.analisar(ast)
    except (RecursionError, MemoryError):
        linha = getattr(sem.no_atual, "linha", None)
        return Resultado(False, tokens=tokens, erros=[Mensagem("Semântica", MSG_COMPLEXO_DEMAIS, linha)])
    sensores = {s.nome: s.pino for s in sem.global_.simbolos.values() if s.tipo_simbolo == "observer" and s.pino is not None}
    atuadores = {s.nome: s.pino for s in sem.global_.simbolos.values() if s.tipo_simbolo == "lever" and s.pino is not None}
    return Resultado(not sem.erros, tokens, ast, sem.erros, sem.avisos, sensores, atuadores)
