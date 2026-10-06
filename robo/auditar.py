"""Auditoria da extração: compara cada ato extraído do PDF com a página oficial da matéria.

    python robo/auditar.py            # só relata
    python robo/auditar.py --gravar   # regrava os atos das edições sem resumo com a extração atual

Para cada data do histórico, confere:
  - códigos: as matérias que a busca avançada da AROM atribui a Porto Velho x as extraídas;
  - título e órgão: os do PDF x os da página /arom/materia/<código>;
  - texto: cobertura das palavras do texto oficial no texto extraído e vice-versa.
Sai com código 1 se encontrar qualquer divergência.
"""

import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from briefing import PASTA_EDICOES, PASTA_PDFS, TAMANHO_TRECHO, carregar_edicoes, gerar_painel, salvar_edicao  # noqa: E402
from coletar import ColetorAROM  # noqa: E402
from extrair import extrair_atos  # noqa: E402

LIMITE_COBERTURA = 0.95  # fração mínima das palavras oficiais presentes no texto extraído
LIMITE_PRECISAO = 0.95   # fração mínima das palavras extraídas presentes no texto oficial


def normalizar(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.upper()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def palavras(s: str) -> set[str]:
    # Junta hifenização de fim de linha do PDF ("atribui-\nções") antes de separar
    s = re.sub(r"-\s*\n\s*", "", s)
    return set(re.findall(r"[A-Z0-9]{3,}", normalizar(s)))


def junto(s: str) -> str:
    """Texto sem espaços nem pontuação: tolera palavras que o PDF (ou o site) cola."""
    return re.sub(r"[^A-Z0-9]", "", normalizar(re.sub(r"-\s*\n\s*", "", s)))


def sem_rodape(oficial: str) -> str:
    """Tira o rodapé "Publicado por / Código Identificador", que a extração remove de propósito."""
    linhas = oficial.split("\n")
    for i, linha in enumerate(linhas):
        if linha.startswith("Publicado por"):
            return "\n".join(linhas[:i])
    return oficial


def main() -> int:
    gravar = "--gravar" in sys.argv
    coletor = ColetorAROM()
    registros = carregar_edicoes()
    por_data = defaultdict(list)
    for r in registros:
        por_data[r["edicao"]["data"]].append(r)

    problemas, total_atos, piores = [], 0, []
    for data in sorted(por_data):
        extraidos_dia = {}
        for reg in por_data[data]:
            ed = reg["edicao"]
            pdf = PASTA_PDFS / f"{ed['data']}_{ed['numero']}.pdf"
            if not pdf.exists():
                coletor.baixar(ed, PASTA_PDFS)
            atos = extrair_atos(pdf)["atos"]
            for a in atos:
                extraidos_dia[a["codigo"]] = (ed, a)
            if gravar and not reg.get("resumo"):
                reg["atos"] = [
                    {k: a[k] for k in ("id", "orgao", "titulo", "tipo", "pagina", "codigo")}
                    | {"trecho": a["texto"][:TAMANHO_TRECHO]}
                    for a in atos
                ]
                salvar_edicao(reg)
            elif gravar and [a["codigo"] for a in reg["atos"]] != [a["codigo"] for a in atos]:
                problemas.append(f"{data} edição {ed['numero']}: extração mudou mas a edição já tem resumo; não regravada")

        oficiais = coletor.codigos_oficiais(date.fromisoformat(data))
        falt, sobra = sorted(oficiais - set(extraidos_dia)), sorted(set(extraidos_dia) - oficiais)
        if falt or sobra:
            problemas.append(f"{data}: códigos divergentes — faltando {falt}, a mais {sobra}")

        for codigo in sorted(set(extraidos_dia) & oficiais):
            ed, a = extraidos_dia[codigo]
            total_atos += 1
            oficial = coletor.texto_oficial(codigo)
            linhas = oficial.split("\n")
            # A página oficial começa com "ÓRGÃO" e o título da matéria
            cab = normalizar(" ".join(linhas[:4]))
            if normalizar(a["titulo"])[:40] not in cab:
                problemas.append(f"{data} {codigo} p.{a['pagina']}: título difere — PDF {a['titulo'][:70]!r}")
            if normalizar(a["orgao"])[:25] not in cab:
                problemas.append(f"{data} {codigo} p.{a['pagina']}: órgão difere — PDF {a['orgao'][:60]!r}")
            texto_of = sem_rodape(oficial)
            texto_ex = a["orgao"] + "\n" + a["titulo"] + "\n" + a["texto"]
            po, pe = palavras(texto_of), palavras(texto_ex)
            jo, je = junto(texto_of), junto(texto_ex)
            cobertura = sum(1 for w in po if w in je) / max(1, len(po))
            precisao = sum(1 for w in pe if w in jo) / max(1, len(pe))
            piores.append((min(cobertura, precisao), data, codigo, cobertura, precisao, len(po)))
            if cobertura < LIMITE_COBERTURA or precisao < LIMITE_PRECISAO:
                problemas.append(f"{data} {codigo} p.{a['pagina']}: texto difere — cobertura {cobertura:.1%}, "
                                 f"precisão {precisao:.1%} ({len(po)} palavras oficiais) — {a['titulo'][:60]!r}")
            time.sleep(0.25)
        print(f"{data}: {len(extraidos_dia)} extraídos, {len(oficiais)} na busca oficial", flush=True)

    if gravar:
        gerar_painel()
    piores.sort()
    print(f"\n{total_atos} atos comparados com a página oficial. Menores índices de texto:")
    for x in piores[:8]:
        print(f"  {x[1]} {x[2]}: cobertura {x[3]:.1%}, precisão {x[4]:.1%}, {x[5]} palavras")
    print(f"\n{len(problemas)} divergência(s):")
    for p in problemas:
        print("  -", p)
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
