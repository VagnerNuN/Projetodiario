"""Extrai do PDF da AROM apenas as matérias publicadas pelo município de Porto Velho.

Estrutura do diário: cada matéria termina com "Publicado por: <nome>" seguido de
"Código Identificador:XXXXXXXX". A primeira matéria de cada ente vem precedida
do cabeçalho "ESTADO DE RONDÔNIA" / "PREFEITURA MUNICIPAL DE <MUNICÍPIO>"; as
seguintes do mesmo ente não repetem o cabeçalho. Por isso percorremos o diário
inteiro guardando qual é o ente corrente.

Menções a Porto Velho feitas por outros municípios (viagens, TFD etc.) ficam de
fora de propósito: só entra o que o próprio município publicou.
"""

import re
import unicodedata
from pathlib import Path

import pymupdf

RE_CABECALHO_PAGINA = re.compile(r"Diário Oficial dos Municípios do Estado de Rondônia|^www\.diariomunicipal\.com\.br/arom\b")
RE_CODIGO = re.compile(r"^C[óo]digo Identificador:\s*([0-9A-Z]+)")
RE_ESTADO = re.compile(r"^ESTADO DE ROND[ÔO]NIA$")
RE_CONECTIVO_FINAL = re.compile(r"(\s(E|DE|DA|DO|DAS|DOS|PORTO)|[,–-])$")
RE_SIGLA_FINAL = re.compile(r"[-–]\s*[A-Z]{2,}$")

# Tipo do ato a partir da primeira palavra do título
TIPOS = [
    ("DECRETO LEGISLATIVO", "Decreto legislativo"),
    ("DECRETO", "Decreto"),
    ("LEI COMPLEMENTAR", "Lei complementar"),
    ("LEI", "Lei"),
    ("PORTARIA", "Portaria"),
    ("RESOLU", "Resolução"),
    ("EDITAL", "Edital"),
    ("EXTRATO", "Extrato"),
    ("AVISO", "Aviso"),
    ("TERMO", "Termo"),
    ("ATA", "Ata"),
    ("HOMOLOGA", "Homologação"),
    ("RATIFICA", "Ratificação"),
    ("ADJUDICA", "Adjudicação"),
    ("ERRATA", "Errata"),
    ("RETIFICA", "Retificação"),
    ("CONTRATO", "Contrato"),
    ("INSTRU", "Instrução normativa"),
    ("DESPACHO", "Despacho"),
    ("CONVOCA", "Convocação"),
    ("NOTIFICA", "Notificação"),
    ("RELAT", "Relatório"),
    ("BALAN", "Balanço"),
    ("PREG", "Licitação"),
    ("RESULTADO", "Resultado"),
    ("JUSTIFICATIVA", "Justificativa"),
    ("REPUBLICA", "Republicação"),
]


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _tipo(titulo: str) -> str:
    t = _sem_acento(titulo.upper())
    for prefixo, nome in TIPOS:
        if t.startswith(_sem_acento(prefixo)):
            return nome
    return "Outro"


def _linhas(pdf: pymupdf.Document):
    """(página, linha) de todo o diário, sem os cabeçalhos repetidos de página."""
    for num, pagina in enumerate(pdf, start=1):
        for linha in pagina.get_text().splitlines():
            linha = linha.strip()
            if RE_CABECALHO_PAGINA.search(linha):
                continue
            yield num, linha


def _materias(pdf: pymupdf.Document):
    """Agrupa as linhas em matérias, delimitadas pelo Código Identificador."""
    atual, pagina_inicio = [], None
    for num, linha in _linhas(pdf):
        if pagina_inicio is None and linha:
            pagina_inicio = num
        m = RE_CODIGO.match(linha)
        if m:
            yield {"pagina": pagina_inicio, "linhas": atual, "codigo": m.group(1)}
            atual, pagina_inicio = [], None
        else:
            atual.append(linha)


def _montar_ato(linhas: list[str]) -> dict:
    # Tira linhas vazias das pontas e o rodapé "Publicado por: <nome>"
    while linhas and not linhas[0]:
        linhas.pop(0)
    publicado_por = None
    for i in range(len(linhas) - 1, -1, -1):
        if linhas[i].startswith("Publicado por:"):
            resto = linhas[i][len("Publicado por:"):].strip()
            publicado_por = resto or " ".join(l for l in linhas[i + 1:] if l).strip() or None
            linhas = linhas[:i]
            break

    orgao = linhas[0] if linhas else ""
    corpo = linhas[1:]
    # Nome do órgão quebrado em várias linhas ("...CONVÊNIOS E" / "LICITAÇÕES- SMCL", ou
    # "AGÊNCIA REGULADORA DOS SERVIÇOS PÚBLICOS" / "DELEGADOS ... DE PORTO" / "VELHO – ARDPV")
    while corpo and corpo[0] and RE_CONECTIVO_FINAL.search(orgao):
        orgao = f"{orgao} {corpo.pop(0)}"
    if not RE_SIGLA_FINAL.search(orgao):
        for n in range(min(3, len(corpo))):
            linha = corpo[n]
            if not linha or not linha.isupper():
                break
            if RE_SIGLA_FINAL.search(linha):
                orgao = " ".join([orgao] + corpo[:n + 1])
                del corpo[:n + 1]
                break
    titulo_partes = []
    while corpo and corpo[0]:
        titulo_partes.append(corpo.pop(0))
        if len(" ".join(titulo_partes)) > 250:
            break
    titulo = " ".join(titulo_partes)
    texto = re.sub(r"\n{3,}", "\n\n", "\n".join(corpo).strip())
    return {
        "orgao": orgao,
        "titulo": titulo or orgao,
        "tipo": _tipo(titulo or orgao),
        "texto": texto,
        "publicado_por": publicado_por,
    }


def extrair_atos(caminho_pdf: Path, municipio: str = "PORTO VELHO") -> dict:
    pdf = pymupdf.open(caminho_pdf)
    alvo = _sem_acento(municipio.upper())
    ente_atual = None
    atos = []
    for materia in _materias(pdf):
        linhas = materia["linhas"]
        nao_vazias = [i for i, l in enumerate(linhas) if l]
        # Cabeçalho de novo ente: "ESTADO DE RONDÔNIA" seguido do nome do ente
        if len(nao_vazias) >= 2 and RE_ESTADO.match(linhas[nao_vazias[0]]):
            ente_atual = _sem_acento(linhas[nao_vazias[1]].upper())
            linhas = linhas[nao_vazias[1] + 1:]
        if ente_atual is None or not ente_atual.endswith(" DE " + alvo):
            continue
        ato = _montar_ato(list(linhas))
        ato["id"] = len(atos) + 1
        ato["pagina"] = materia["pagina"]
        ato["codigo"] = materia["codigo"]
        atos.append(ato)
    return {"total_paginas": pdf.page_count, "atos": atos}


if __name__ == "__main__":
    import sys
    from collections import Counter

    resultado = extrair_atos(Path(sys.argv[1]))
    atos = resultado["atos"]
    print(f"{len(atos)} atos de Porto Velho em {resultado['total_paginas']} páginas")
    if atos:
        print(f"Páginas {atos[0]['pagina']}–{atos[-1]['pagina']}")
    print(Counter(a["tipo"] for a in atos).most_common())
    print(Counter(a["orgao"] for a in atos).most_common())
    for a in atos:
        print(f"  [{a['id']:>3}] p.{a['pagina']} {a['codigo']} | {a['orgao'][:40]} | {a['titulo'][:90]}")
