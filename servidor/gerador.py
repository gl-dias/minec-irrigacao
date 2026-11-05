"""
Gera o programa MineC a partir dos canteiros cadastrados no painel.

O painel nunca fala com o hardware direto: ele monta um programa MineC,
que passa pelo compilador (léxico, sintático, semântico) e é executado
pelo interpretador. Assim tudo o que a fazenda faz está escrito em MineC.
"""

import re
import unicodedata
from typing import List

PALAVRAS_RESERVADAS = {
    "craft", "mine", "say", "if", "else", "while", "repeat", "power", "unpower", "wait",
    "observer", "lever", "command", "drop", "and", "or", "not", "true", "false",
}
NOMES_DO_GERADOR = {"intervalo", "tempoRega", "ciclo", "segundos"}
# nomes que gerar_programa cria por bomba e por canteiro: bomba27, regarBomba26, minimoCenouras
PADRAO_DO_GERADOR = re.compile(r"bomba\d*|regar(Bomba\d*)?|minimo[A-Z].*")
PALAVRAS_IGNORADAS = {"canteiro", "canteiros", "de", "do", "da", "dos", "das", "e", "o", "a", "horta", "vaso"}


def _sem_acentos(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")


def nome_de_variavel(nome: str, existentes: set) -> str:
    """'Canteiro de cenouras' -> 'cenouras'; 'Vaso de Manjericão 2' -> 'manjericao2'."""
    palavras = [p for p in re.split(r"[^A-Za-z0-9]+", _sem_acentos(nome).lower()) if p]
    uteis = [p for p in palavras if p not in PALAVRAS_IGNORADAS] or palavras or ["sensor"]
    base = uteis[0] + "".join(p.capitalize() for p in uteis[1:])
    if not base[0].isalpha():
        base = "sensor" + base
    base = base[:24]
    if PADRAO_DO_GERADOR.fullmatch(base):
        # prefixo, não sufixo: 'bomba' + número continuaria parecendo nome de bomba
        base = f"canteiro{base[0].upper()}{base[1:]}"[:24]
    candidato, n = base, 2
    while candidato in PALAVRAS_RESERVADAS or candidato in NOMES_DO_GERADOR or candidato in existentes:
        candidato = f"{base}{n}"
        n += 1
    return candidato


def _texto(s: str) -> str:
    # strings MineC não aceitam aspas nem quebra de linha
    return s.replace('"', "'").replace("\n", " ")


def gerar_programa(cfg: dict) -> str:
    canteiros: List[dict] = cfg.get("canteiros", [])
    intervalo = int(cfg.get("intervalo", 120))
    tempo_rega = int(cfg.get("tempo_rega", 10))

    linhas = [
        "# Programa gerado pelo painel MineC",
        "# A cada ciclo: lê a umidade de cada canteiro e rega os que estão secos.",
        "",
    ]

    if not canteiros:
        linhas += ['say "Nenhum canteiro cadastrado. Adicione um sensor no painel."']
        return "\n".join(linhas) + "\n"

    # Um config.json antigo pode ter variavel = 'bomba27' ou 'minimoCenouras'. As variáveis dos
    # canteiros escolhem primeiro; os nomes auxiliares desviam delas e uns dos outros.
    usados = set(PALAVRAS_RESERVADAS)

    def reservar(nome: str) -> str:
        candidato, n = nome, 2
        while candidato in usados:
            candidato = f"{nome}_{n}"
            n += 1
        usados.add(candidato)
        return candidato

    variaveis = [reservar(c["variavel"]) for c in canteiros]

    # atuadores: uma bomba por pino distinto
    pinos_bomba = []
    for c in canteiros:
        if c["pino_bomba"] not in pinos_bomba:
            pinos_bomba.append(c["pino_bomba"])
    unica = len(pinos_bomba) == 1
    nome_bomba = {p: reservar("bomba" if unica else f"bomba{p}") for p in pinos_bomba}
    nome_regar = {p: reservar("regar" if unica else f"regarBomba{p}") for p in pinos_bomba}
    var_intervalo, var_tempo, var_ciclo, var_segundos = map(reservar, ("intervalo", "tempoRega", "ciclo", "segundos"))
    minimos = [reservar(f"minimo{v[0].upper()}{v[1:]}") for v in variaveis]

    for c, v in zip(canteiros, variaveis):
        linhas.append(f"observer {v} = {c['pino_sensor']}")
    for p in pinos_bomba:
        linhas.append(f"lever {nome_bomba[p]} = {p}")

    linhas += ["", f"craft {var_intervalo} = {intervalo}", f"craft {var_tempo} = {tempo_rega}"]
    for c, minimo in zip(canteiros, minimos):
        linhas.append(f"craft {minimo} = {int(c['minimo'])}")
    linhas.append(f"craft {var_ciclo} = 0")

    for p in pinos_bomba:
        linhas += [
            "",
            f"command {nome_regar[p]}({var_segundos}) {{",
            f"    power {nome_bomba[p]}",
            f"    wait {var_segundos}",
            f"    unpower {nome_bomba[p]}",
            "}",
        ]

    qtd = len(canteiros)
    linhas += ["", f'say "Fazenda iniciada com {qtd} canteiro{"s" if qtd != 1 else ""}"', "", "while true {",
               f"    {var_ciclo} = {var_ciclo} + 1"]

    for c, v, minimo in zip(canteiros, variaveis, minimos):
        nome = _texto(c["nome"])
        linhas += [
            "",
            f"    mine {v}",
            f"    if {v} < 0 {{",
            f'        say "{nome}: sensor sem leitura válida, rega suspensa"',
            f"    }} else if {v} < {minimo} {{",
            f'        say "{nome}: solo seco (" + {v} + "%), irrigando"',
            f"        {nome_regar[c['pino_bomba']]}({var_tempo})",
            "    } else {",
            f'        say "{nome}: umidade ok (" + {v} + "%)"',
            "    }",
        ]

    linhas += ["", f"    wait {var_intervalo}", "}"]
    return "\n".join(linhas) + "\n"
