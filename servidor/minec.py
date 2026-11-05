import enum
from typing import List, Optional, Any

# =====================================================================
# 1. ESPECIFICAÇÃO DE TOKENS (ANALISADOR LÉXICO)
# =====================================================================

class TokenType(enum.Enum):
    # Palavras reservadas
    TK_CRAFT = "craft"          # Declara variável
    TK_MINE = "mine"            # Lê sensor
    TK_SAY = "say"              # Saída
    TK_IF = "if"                # Condição
    TK_ELSE = "else"            # Caso contrário
    TK_WHILE = "while"          # Repetição com condição
    TK_REPEAT = "repeat"        # Repetição determinada
    TK_POWER = "power"          # Liga atuador
    TK_UNPOWER = "unpower"      # Desliga atuador
    TK_WAIT = "wait"            # Espera (segundos)
    TK_OBSERVER = "observer"    # Declara sensor
    TK_LEVER = "lever"          # Declara atuador
    TK_COMMAND = "command"      # Função
    TK_DROP = "drop"            # Retorno
    TK_AND = "and"
    TK_OR = "or"
    TK_NOT = "not"

    # Booleanos
    TK_TRUE = "true"
    TK_FALSE = "false"

    # Identificador e literais
    TK_IDENTIFIER = "IDENTIFIER"
    TK_INTEGER = "INTEGER"
    TK_DECIMAL = "DECIMAL"
    TK_STRING = "STRING"

    # Operadores
    TK_ASSIGN = "="
    TK_EQUAL = "=="
    TK_NOT_EQUAL = "!="
    TK_LESS = "<"
    TK_GREATER = ">"
    TK_LESS_EQUAL = "<="
    TK_GREATER_EQUAL = ">="
    TK_PLUS = "+"
    TK_MINUS = "-"
    TK_STAR = "*"
    TK_SLASH = "/"

    # Delimitadores
    TK_LBRACE = "{"
    TK_RBRACE = "}"
    TK_LPAREN = "("
    TK_RPAREN = ")"
    TK_COMMA = ","

    TK_EOF = "EOF"


class Token:
    def __init__(self, type_: TokenType, value: Any, line: int, column: int):
        self.type = type_
        self.value = value
        self.line = line
        self.column = column

    def __repr__(self):
        return f"⛏️ Token({self.type.name:<17}, Value='{self.value}', Line={self.line}:{self.column})"


class LexerMineC:
    def __init__(self, source_code: str):
        self.source = source_code
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []

        self.keywords = {
            "craft": TokenType.TK_CRAFT,
            "mine": TokenType.TK_MINE,
            "say": TokenType.TK_SAY,
            "if": TokenType.TK_IF,
            "else": TokenType.TK_ELSE,
            "while": TokenType.TK_WHILE,
            "repeat": TokenType.TK_REPEAT,
            "power": TokenType.TK_POWER,
            "unpower": TokenType.TK_UNPOWER,
            "wait": TokenType.TK_WAIT,
            "observer": TokenType.TK_OBSERVER,
            "lever": TokenType.TK_LEVER,
            "command": TokenType.TK_COMMAND,
            "drop": TokenType.TK_DROP,
            "and": TokenType.TK_AND,
            "or": TokenType.TK_OR,
            "not": TokenType.TK_NOT,
            "true": TokenType.TK_TRUE,
            "false": TokenType.TK_FALSE,
        }

        self.two_char_ops = {
            "==": TokenType.TK_EQUAL,
            "!=": TokenType.TK_NOT_EQUAL,
            "<=": TokenType.TK_LESS_EQUAL,
            ">=": TokenType.TK_GREATER_EQUAL,
        }

        self.one_char_symbols = {
            "=": TokenType.TK_ASSIGN,
            "<": TokenType.TK_LESS,
            ">": TokenType.TK_GREATER,
            "+": TokenType.TK_PLUS,
            "-": TokenType.TK_MINUS,
            "*": TokenType.TK_STAR,
            "/": TokenType.TK_SLASH,
            "{": TokenType.TK_LBRACE,
            "}": TokenType.TK_RBRACE,
            "(": TokenType.TK_LPAREN,
            ")": TokenType.TK_RPAREN,
            ",": TokenType.TK_COMMA,
        }

    def error(self, msg: str):
        raise SyntaxError(f"💥 [Análise Léxica Error] Linha {self.line}, Coluna {self.col}: {msg}")

    def peek(self, offset: int = 1) -> str:
        if self.pos + offset < len(self.source):
            return self.source[self.pos + offset]
        return ''

    def advance(self, step: int = 1):
        self.pos += step
        self.col += step

    def add_token(self, type_: TokenType, value: Any, col: Optional[int] = None):
        self.tokens.append(Token(type_, value, self.line, col if col is not None else self.col))

    def tokenize(self) -> List[Token]:
        while self.pos < len(self.source):
            char = self.source[self.pos]

            # Espaços e quebras de linha
            if char == '\n':
                self.line += 1
                self.col = 1
                self.pos += 1
                continue
            elif char in ' \t\r':
                self.advance()
                continue

            # Comentários de linha (# comentário)
            if char == '#':
                while self.pos < len(self.source) and self.source[self.pos] != '\n':
                    self.advance()
                continue

            # Operadores de dois caracteres (maior casamento primeiro)
            two = char + self.peek()
            if two in self.two_char_ops:
                self.add_token(self.two_char_ops[two], two)
                self.advance(2)
                continue

            # "!" sozinho não existe na linguagem (negação é 'not')
            if char == '!':
                self.error("Caractere inválido '!'. Para negação use 'not'.")

            # Operadores e delimitadores de um caractere
            if char in self.one_char_symbols:
                self.add_token(self.one_char_symbols[char], char)
                self.advance()
                continue

            # Strings
            if char == '"':
                start_col = self.col
                self.advance()
                val = ""
                while self.pos < len(self.source) and self.source[self.pos] not in '"\n':
                    val += self.source[self.pos]
                    self.advance()
                if self.pos >= len(self.source) or self.source[self.pos] == '\n':
                    self.error("String não fechada (faltou a aspas de fechamento).")
                self.advance()  # consome a aspas de fechamento
                self.add_token(TokenType.TK_STRING, val, start_col)
                continue

            # Números (inteiro ou decimal)
            if char.isascii() and char.isdigit():
                start_col = self.col
                num_str = ""
                while self.pos < len(self.source) and self.source[self.pos].isascii() and self.source[self.pos].isdigit():
                    num_str += self.source[self.pos]
                    self.advance()
                if self.pos < len(self.source) and self.source[self.pos] == '.':
                    nxt = self.peek()
                    if not (nxt.isascii() and nxt.isdigit()):
                        self.error("Número decimal malformado (falta dígito após o ponto).")
                    num_str += '.'
                    self.advance()
                    while self.pos < len(self.source) and self.source[self.pos].isascii() and self.source[self.pos].isdigit():
                        num_str += self.source[self.pos]
                        self.advance()
                    self.add_token(TokenType.TK_DECIMAL, float(num_str), start_col)
                else:
                    self.add_token(TokenType.TK_INTEGER, int(num_str), start_col)
                continue

            # Identificadores e palavras reservadas
            if char.isascii() and (char.isalpha() or char == '_'):
                start_col = self.col
                ident = ""
                while (self.pos < len(self.source) and self.source[self.pos].isascii()
                       and (self.source[self.pos].isalnum() or self.source[self.pos] == '_')):
                    ident += self.source[self.pos]
                    self.advance()
                tok_type = self.keywords.get(ident, TokenType.TK_IDENTIFIER)
                self.add_token(tok_type, ident, start_col)
                continue

            self.error(f"Caractere inválido: '{char}'")

        self.add_token(TokenType.TK_EOF, "EOF")
        return self.tokens


# =====================================================================
# 2. NÓS DA AST (ÁRVORE DE SINTAXE ABSTRATA)
# =====================================================================

class ASTNode:
    def label(self) -> str:
        raise NotImplementedError

    def children(self) -> List["ASTNode"]:
        return []

    def print_tree(self, indent: str = "", is_last: bool = True) -> str:
        marker = "└── " if is_last else "├── "
        res = f"{indent}{marker}{self.label()}\n"
        new_indent = indent + ("    " if is_last else "│   ")
        kids = self.children()
        for i, kid in enumerate(kids):
            res += kid.print_tree(new_indent, i == len(kids) - 1)
        return res


class GroupNode(ASTNode):
    """Agrupa uma lista de nós sob um título (blocos, parâmetros, argumentos)."""
    def __init__(self, caption: str, nodes: List[ASTNode]):
        self.caption = caption
        self.nodes = nodes

    def label(self) -> str:
        return self.caption

    def children(self) -> List[ASTNode]:
        return self.nodes


class ProgramNode(ASTNode):
    def __init__(self, body: List[ASTNode]):
        self.body = body

    def label(self) -> str:
        return "🌾 PROGRAMA MineC"

    def children(self):
        return self.body


class CommandDeclNode(ASTNode):
    def __init__(self, name: str, params: List[str], body: List[ASTNode]):
        self.name = name
        self.params = params
        self.body = body

    def label(self) -> str:
        return f"📦 COMMAND: {self.name}({', '.join(self.params)})"

    def children(self):
        return [GroupNode("🧱 Corpo:", self.body)]


class VarDeclNode(ASTNode):
    ICONS = {"craft": "🔨 CRAFT (variável)", "observer": "👁️ OBSERVER (sensor)", "lever": "🕹️ LEVER (atuador)"}

    def __init__(self, kind: str, name: str, value_expr: ASTNode):
        self.kind = kind
        self.name = name
        self.value_expr = value_expr

    def label(self) -> str:
        return f"{self.ICONS[self.kind]}: {self.name}"

    def children(self):
        return [self.value_expr]


class AssignNode(ASTNode):
    def __init__(self, name: str, expr: ASTNode):
        self.name = name
        self.expr = expr

    def label(self) -> str:
        return f"📝 ASSIGN (=): {self.name}"

    def children(self):
        return [self.expr]


class CallNode(ASTNode):
    def __init__(self, name: str, args: List[ASTNode]):
        self.name = name
        self.args = args

    def label(self) -> str:
        return f"📞 CALL: {self.name}"

    def children(self):
        return [GroupNode("🎯 Argumentos:", self.args)] if self.args else []


class ReadNode(ASTNode):
    def __init__(self, name: str):
        self.name = name

    def label(self) -> str:
        return f"⛏️ MINE (lê sensor): {self.name}"


class SayNode(ASTNode):
    def __init__(self, expr: ASTNode):
        self.expr = expr

    def label(self) -> str:
        return "💬 SAY (saída)"

    def children(self):
        return [self.expr]


class PowerNode(ASTNode):
    def __init__(self, on: bool, name: str):
        self.on = on
        self.name = name

    def label(self) -> str:
        return f"{'🔴 POWER (liga)' if self.on else '⚫ UNPOWER (desliga)'}: {self.name}"


class WaitNode(ASTNode):
    def __init__(self, expr: ASTNode):
        self.expr = expr

    def label(self) -> str:
        return "⏳ WAIT (segundos)"

    def children(self):
        return [self.expr]


class DropNode(ASTNode):
    def __init__(self, expr: ASTNode):
        self.expr = expr

    def label(self) -> str:
        return "🎁 DROP (retorno)"

    def children(self):
        return [self.expr]


class IfNode(ASTNode):
    def __init__(self, condition: ASTNode, then_branch: List[ASTNode], else_branch: Optional[List[ASTNode]] = None):
        self.condition = condition
        self.then_branch = then_branch
        self.else_branch = else_branch

    def label(self) -> str:
        return "❓ IF"

    def children(self):
        kids = [GroupNode("🔎 Condição:", [self.condition]),
                GroupNode("✅ Then:", self.then_branch)]
        if self.else_branch is not None:
            kids.append(GroupNode("❌ Else:", self.else_branch))
        return kids


class WhileNode(ASTNode):
    def __init__(self, condition: ASTNode, body: List[ASTNode]):
        self.condition = condition
        self.body = body

    def label(self) -> str:
        return "🔄 WHILE"

    def children(self):
        return [GroupNode("🔎 Condição:", [self.condition]),
                GroupNode("🌀 Corpo:", self.body)]


class RepeatNode(ASTNode):
    def __init__(self, times: ASTNode, body: List[ASTNode]):
        self.times = times
        self.body = body

    def label(self) -> str:
        return "🔁 REPEAT"

    def children(self):
        return [GroupNode("🔢 Vezes:", [self.times]),
                GroupNode("🌀 Corpo:", self.body)]


class BinaryOpNode(ASTNode):
    def __init__(self, left: ASTNode, op: str, right: ASTNode):
        self.left = left
        self.op = op
        self.right = right

    def label(self) -> str:
        return f"⚡ BINARY_OP ({self.op})"

    def children(self):
        return [self.left, self.right]


class UnaryOpNode(ASTNode):
    def __init__(self, op: str, operand: ASTNode):
        self.op = op
        self.operand = operand

    def label(self) -> str:
        return f"⚡ UNARY_OP ({self.op})"

    def children(self):
        return [self.operand]


class LiteralNode(ASTNode):
    def __init__(self, value: Any, type_name: str):
        self.value = value
        self.type_name = type_name

    def label(self) -> str:
        return f"💎 LITERAL [{self.type_name}]: {repr(self.value)}"


class IdentifierNode(ASTNode):
    def __init__(self, name: str):
        self.name = name

    def label(self) -> str:
        return f"🔮 IDENTIFIER: {self.name}"


# =====================================================================
# 3. ANALISADOR SINTÁTICO (PARSER)
# =====================================================================

class ParserMineC:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.current = 0

    def error(self, message: str):
        tok = self.peek()
        near = "fim do arquivo" if tok.type == TokenType.TK_EOF else f"'{tok.value}'"
        raise SyntaxError(f"💥 [Análise Sintática Error] Linha {tok.line}, Coluna {tok.column} perto de {near}: {message}")

    def peek(self) -> Token:
        return self.tokens[self.current]

    def previous(self) -> Token:
        return self.tokens[self.current - 1]

    def is_at_end(self) -> bool:
        return self.peek().type == TokenType.TK_EOF

    def advance(self) -> Token:
        if not self.is_at_end():
            self.current += 1
        return self.previous()

    def check(self, type_: TokenType) -> bool:
        if self.is_at_end():
            return False
        return self.peek().type == type_

    def match(self, *types: TokenType) -> bool:
        for t in types:
            if self.check(t):
                self.advance()
                return True
        return False

    def consume(self, type_: TokenType, message: str) -> Token:
        if self.check(type_):
            return self.advance()
        self.error(message)

    # REGRAS DA GRAMÁTICA LL(1) / DESCIDA RECURSIVA

    def parse(self) -> ProgramNode:
        # Programa -> { Comando | Instrucao } EOF
        body = []
        while not self.is_at_end():
            if self.match(TokenType.TK_COMMAND):
                body.append(self.command_declaration())
            else:
                body.append(self.statement())
        return ProgramNode(body)

    def command_declaration(self) -> CommandDeclNode:
        name_tok = self.consume(TokenType.TK_IDENTIFIER, "O 'command' precisa de um nome.")
        self.consume(TokenType.TK_LPAREN, "Esperado '(' após o nome do command.")
        params = []
        if not self.check(TokenType.TK_RPAREN):
            params.append(self.consume(TokenType.TK_IDENTIFIER, "Esperado nome do parâmetro.").value)
            while self.match(TokenType.TK_COMMA):
                params.append(self.consume(TokenType.TK_IDENTIFIER, "Esperado nome do parâmetro após ','.").value)
        self.consume(TokenType.TK_RPAREN, "Esperado ')' após os parâmetros.")
        body = self.block()
        return CommandDeclNode(name_tok.value, params, body)

    def block(self) -> List[ASTNode]:
        self.consume(TokenType.TK_LBRACE, "Esperado '{' para iniciar o bloco.")
        stmts = []
        while not self.check(TokenType.TK_RBRACE) and not self.is_at_end():
            stmts.append(self.statement())
        self.consume(TokenType.TK_RBRACE, "Esperado '}' para fechar o bloco.")
        return stmts

    def statement(self) -> ASTNode:
        if self.match(TokenType.TK_CRAFT, TokenType.TK_OBSERVER, TokenType.TK_LEVER):
            return self.declaration()
        elif self.match(TokenType.TK_MINE):
            name = self.consume(TokenType.TK_IDENTIFIER, "Esperado o nome do sensor após 'mine'.")
            return ReadNode(name.value)
        elif self.match(TokenType.TK_SAY):
            return SayNode(self.expression())
        elif self.match(TokenType.TK_IF):
            return self.if_statement()
        elif self.match(TokenType.TK_WHILE):
            cond = self.expression()
            return WhileNode(cond, self.block())
        elif self.match(TokenType.TK_REPEAT):
            times = self.expression()
            return RepeatNode(times, self.block())
        elif self.match(TokenType.TK_POWER):
            name = self.consume(TokenType.TK_IDENTIFIER, "Esperado o nome do atuador após 'power'.")
            return PowerNode(True, name.value)
        elif self.match(TokenType.TK_UNPOWER):
            name = self.consume(TokenType.TK_IDENTIFIER, "Esperado o nome do atuador após 'unpower'.")
            return PowerNode(False, name.value)
        elif self.match(TokenType.TK_WAIT):
            return WaitNode(self.expression())
        elif self.match(TokenType.TK_DROP):
            return DropNode(self.expression())
        elif self.match(TokenType.TK_IDENTIFIER):
            return self.identifier_statement()
        elif self.check(TokenType.TK_COMMAND):
            self.error("'command' só pode ser declarado no nível global do programa.")
        else:
            self.error("Comando não reconhecido.")

    def declaration(self) -> VarDeclNode:
        kind = self.previous().value  # craft | observer | lever
        name_tok = self.consume(TokenType.TK_IDENTIFIER, f"Esperado um nome após '{kind}'.")
        self.consume(TokenType.TK_ASSIGN, "Esperado '=' na declaração.")
        expr = self.expression()
        return VarDeclNode(kind, name_tok.value, expr)

    def identifier_statement(self) -> ASTNode:
        name = self.previous().value
        if self.match(TokenType.TK_ASSIGN):
            return AssignNode(name, self.expression())
        elif self.match(TokenType.TK_LPAREN):
            return CallNode(name, self.arguments())
        self.error("Esperado '=' (atribuição) ou '(' (chamada de command) após o identificador.")

    def if_statement(self) -> IfNode:
        cond = self.expression()
        then_branch = self.block()
        else_branch = None
        if self.match(TokenType.TK_ELSE):
            if self.match(TokenType.TK_IF):
                else_branch = [self.if_statement()]   # else if
            else:
                else_branch = self.block()
        return IfNode(cond, then_branch, else_branch)

    def arguments(self) -> List[ASTNode]:
        # já consumiu '('; consome até ')'
        args = []
        if not self.check(TokenType.TK_RPAREN):
            args.append(self.expression())
            while self.match(TokenType.TK_COMMA):
                args.append(self.expression())
        self.consume(TokenType.TK_RPAREN, "Esperado ')' após os argumentos.")
        return args

    # EXPRESSÕES (PRECEDÊNCIA: or < and < ==,!= < <,>,<=,>= < +,- < *,/ < not,- unários)

    def _binary(self, next_level, *ops: TokenType) -> ASTNode:
        expr = next_level()
        while self.match(*ops):
            op = self.previous().value
            right = next_level()
            expr = BinaryOpNode(expr, op, right)
        return expr

    def expression(self) -> ASTNode:
        return self.or_expr()

    def or_expr(self) -> ASTNode:
        return self._binary(self.and_expr, TokenType.TK_OR)

    def and_expr(self) -> ASTNode:
        return self._binary(self.equality, TokenType.TK_AND)

    def equality(self) -> ASTNode:
        return self._binary(self.relational, TokenType.TK_EQUAL, TokenType.TK_NOT_EQUAL)

    def relational(self) -> ASTNode:
        return self._binary(self.additive, TokenType.TK_LESS, TokenType.TK_GREATER,
                            TokenType.TK_LESS_EQUAL, TokenType.TK_GREATER_EQUAL)

    def additive(self) -> ASTNode:
        return self._binary(self.multiplicative, TokenType.TK_PLUS, TokenType.TK_MINUS)

    def multiplicative(self) -> ASTNode:
        return self._binary(self.unary, TokenType.TK_STAR, TokenType.TK_SLASH)

    def unary(self) -> ASTNode:
        if self.match(TokenType.TK_NOT, TokenType.TK_MINUS):
            op = self.previous().value
            return UnaryOpNode(op, self.unary())
        return self.primary()

    def primary(self) -> ASTNode:
        if self.match(TokenType.TK_INTEGER):
            return LiteralNode(self.previous().value, "INTEIRO")
        elif self.match(TokenType.TK_DECIMAL):
            return LiteralNode(self.previous().value, "DECIMAL")
        elif self.match(TokenType.TK_STRING):
            return LiteralNode(self.previous().value, "TEXTO")
        elif self.match(TokenType.TK_TRUE):
            return LiteralNode(True, "BOOLEANO")
        elif self.match(TokenType.TK_FALSE):
            return LiteralNode(False, "BOOLEANO")
        elif self.match(TokenType.TK_IDENTIFIER):
            name = self.previous().value
            if self.match(TokenType.TK_LPAREN):
                return CallNode(name, self.arguments())
            return IdentifierNode(name)
        elif self.match(TokenType.TK_LPAREN):
            expr = self.expression()
            self.consume(TokenType.TK_RPAREN, "Esperado ')' após a expressão.")
            return expr
        else:
            self.error("Expressão inválida.")
