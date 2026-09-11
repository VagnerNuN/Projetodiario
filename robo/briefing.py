"""Rotina diária do briefing de Porto Velho.

    python robo/briefing.py
        Consulta as edições dos últimos 15 dias, processa as novas, resume com a API
        do Claude (se houver ANTHROPIC_API_KEY) e atualiza o painel em docs/.

    python robo/briefing.py --exportar-pendentes PASTA
        Salva em PASTA o texto das edições que ainda não têm resumo, no mesmo formato
        enviado à API, para resumir fora dela.

    python robo/briefing.py --aplicar-resumo ARQUIVO.json [--edicao NUMERO]
        Valida e aplica um resumo feito fora da API. Sem --edicao, usa o nome do
        arquivo (ex.: 2026-09-11_4317.json).
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coletar import ColetorAROM  # noqa: E402
from extrair import extrair_atos  # noqa: E402
from resumir import (  # noqa: E402
    FORMATO, INSTRUCOES, MODELO_PADRAO, montar_entrada, resumir_edicao, validar_resumo,
)

RAIZ = Path(__file__).resolve().parent.parent
PASTA_EDICOES = RAIZ / "docs" / "dados" / "edicoes"
ARQUIVO_PAINEL = RAIZ / "docs" / "dados.js"
PASTA_PDFS = RAIZ / ".cache" / "pdfs"
DIAS_HISTORICO = 15
FUSO_RO = timezone(timedelta(hours=-4))  # Rondônia não tem horário de verão
TAMANHO_TRECHO = 600


def hoje_ro() -> date:
    return datetime.now(FUSO_RO).date()


def arquivo_edicao(ed: dict) -> Path:
    return PASTA_EDICOES / f"{ed['data']}_{ed['numero']}.json"


def carregar_edicoes() -> list[dict]:
    if not PASTA_EDICOES.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(PASTA_EDICOES.glob("*.json"))]


def salvar_edicao(registro: dict):
    PASTA_EDICOES.mkdir(parents=True, exist_ok=True)
    arquivo_edicao(registro["edicao"]).write_text(
        json.dumps(registro, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def extrair_edicao(coletor: ColetorAROM, ed: dict) -> tuple[dict, list[dict]]:
    """Baixa o PDF e devolve (info, atos completos com texto)."""
    pdf = coletor.baixar(ed, PASTA_PDFS)
    resultado = extrair_atos(pdf)
    return resultado, resultado["atos"]


def registro_novo(ed: dict, resultado: dict) -> dict:
    atos = [
        {k: a[k] for k in ("id", "orgao", "titulo", "tipo", "pagina", "codigo")}
        | {"trecho": a["texto"][:TAMANHO_TRECHO]}
        for a in resultado["atos"]
    ]
    return {
        "edicao": ed,
        "total_paginas": resultado["total_paginas"],
        "atos": atos,
        "resumo": None,
        "resumo_origem": None,
        "processado_em": datetime.now(FUSO_RO).isoformat(timespec="seconds"),
    }


def resumo_atual(registro: dict) -> bool:
    return bool(registro.get("resumo")) and registro.get("resumo_formato") == FORMATO


def datas_a_consultar(conhecidas: set[str]) -> list[date]:
    """Janela de 15 dias + amanhã (a edição ordinária sai na noite anterior).
    Datas antigas que já têm edição salva não são consultadas de novo."""
    hoje = hoje_ro()
    datas = []
    for n in range(-1, DIAS_HISTORICO):
        dia = hoje - timedelta(days=n)
        if n >= 2 and dia.isoformat() in conhecidas:
            continue
        datas.append(dia)
    return sorted(datas)


def conferir_dia(coletor: ColetorAROM, dia: date, salvos: dict) -> bool:
    """Compara as matérias extraídas do PDF com a busca oficial da AROM (entidade Porto Velho).
    Grava o resultado nas edições do dia e devolve False se faltar ou sobrar matéria."""
    regs = [r for r in salvos.values() if r["edicao"]["data"] == dia.isoformat()]
    extraidos = {a["codigo"] for r in regs for a in r["atos"]}
    oficiais = coletor.codigos_oficiais(dia)
    if not oficiais and extraidos:
        print(f"[{dia}] busca oficial ainda sem resultados para a data; confiro na próxima execução")
        return True
    conferencia = {
        "site": len(oficiais),
        "robo": len(extraidos),
        "faltando": sorted(oficiais - extraidos),
        "sobrando": sorted(extraidos - oficiais),
        "em": datetime.now(FUSO_RO).isoformat(timespec="minutes"),
    }
    for r in regs:
        r["conferencia"] = conferencia
        salvar_edicao(r)
    if conferencia["faltando"] or conferencia["sobrando"]:
        print(f"[{dia}] CONFERÊNCIA NÃO BATEU: busca oficial {len(oficiais)}, extraídas {len(extraidos)}; "
              f"faltando {conferencia['faltando']}, a mais {conferencia['sobrando']}")
        return False
    print(f"[{dia}] conferido com a busca oficial: {len(oficiais)} matérias de Porto Velho")
    return True


def limpar_antigas():
    limite = (hoje_ro() - timedelta(days=DIAS_HISTORICO - 1)).isoformat()
    for p in PASTA_EDICOES.glob("*.json"):
        if p.name[:10] < limite:
            p.unlink()
            print(f"  removida do histórico: {p.name}")


def gerar_painel():
    edicoes = sorted(carregar_edicoes(), key=lambda r: (r["edicao"]["data"], r["edicao"]["numero"]), reverse=True)
    dados = {
        "atualizado_em": datetime.now(FUSO_RO).isoformat(timespec="minutes"),
        "edicoes": edicoes,
    }
    ARQUIVO_PAINEL.write_text(
        "window.BRIEFING = " + json.dumps(dados, ensure_ascii=False) + ";\n", encoding="utf-8"
    )
    print(f"Painel atualizado: {len(edicoes)} edições em {ARQUIVO_PAINEL.relative_to(RAIZ)}")


def rotina() -> int:
    tem_chave = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    if not tem_chave:
        print("ANTHROPIC_API_KEY não definida: os atos entram no painel sem resumo.")
    coletor = ColetorAROM()
    salvos = {r["edicao"]["id"]: r for r in carregar_edicoes()}
    # Datas com resumo desatualizado entram de novo na consulta para serem refeitas
    conhecidas = {r["edicao"]["data"] for r in salvos.values()
                  if resumo_atual(r) or not r["atos"] or not tem_chave}
    erros = 0

    for dia in datas_a_consultar(conhecidas):
        try:
            edicoes = coletor.edicoes_do_dia(dia)
        except Exception as e:  # site fora do ar, mudança de formato etc.
            print(f"[{dia}] erro ao consultar a AROM: {e}")
            erros += 1
            continue
        for ed in edicoes:
            registro = salvos.get(ed["id"])
            if registro and (resumo_atual(registro) or not registro["atos"] or not tem_chave):
                continue
            rotulo = f"[{ed['data']}] edição {ed['numero']}"
            try:
                resultado, atos = extrair_edicao(coletor, ed)
                if not registro:
                    registro = registro_novo(ed, resultado)
                    print(f"{rotulo}: {len(atos)} atos de Porto Velho")
                if tem_chave and atos:
                    print(f"{rotulo}: resumindo com {os.environ.get('MODELO') or MODELO_PADRAO}...")
                    registro["resumo"] = resumir_edicao(ed, atos)
                    registro["resumo_origem"] = os.environ.get("MODELO") or MODELO_PADRAO
                    registro["resumo_formato"] = FORMATO
            except Exception as e:
                print(f"{rotulo}: erro — {e}")
                erros += 1
            if registro:
                salvar_edicao(registro)
                salvos[ed["id"]] = registro
            time.sleep(1)
        if edicoes:
            try:
                if not conferir_dia(coletor, dia, salvos):
                    erros += 1
            except Exception as e:
                print(f"[{dia}] não foi possível conferir com a busca oficial: {e}")

    limpar_antigas()
    gerar_painel()
    return 1 if erros else 0


def exportar_pendentes(pasta: Path):
    pasta.mkdir(parents=True, exist_ok=True)
    coletor = ColetorAROM()
    for registro in carregar_edicoes():
        if resumo_atual(registro) or not registro["atos"]:
            continue
        ed = registro["edicao"]
        _, atos = extrair_edicao(coletor, ed)
        destino = pasta / f"{ed['data']}_{ed['numero']}.txt"
        destino.write_text(INSTRUCOES + "\n\n=====\n\n" + montar_entrada(ed, atos), encoding="utf-8")
        print(f"{destino}  ({len(atos)} atos)")


def aplicar_resumo(arquivo: Path, numero: str | None, origem: str):
    numero = numero or arquivo.stem.split("_")[1].split(".")[0]
    registro = next((r for r in carregar_edicoes() if r["edicao"]["numero"] == numero), None)
    if not registro:
        sys.exit(f"edição {numero} não encontrada em {PASTA_EDICOES}")
    resumo = validar_resumo(json.loads(arquivo.read_text(encoding="utf-8")), registro["atos"])
    registro["resumo"] = resumo
    registro["resumo_origem"] = origem
    registro["resumo_formato"] = FORMATO
    salvar_edicao(registro)
    print(f"Resumo aplicado na edição {numero} ({len(resumo['atos'])} atos).")
    gerar_painel()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--exportar-pendentes", type=Path, metavar="PASTA")
    p.add_argument("--aplicar-resumo", type=Path, metavar="ARQUIVO")
    p.add_argument("--edicao", metavar="NUMERO")
    p.add_argument("--origem", default="manual", help="quem fez o resumo aplicado (aparece no painel)")
    args = p.parse_args()
    if args.exportar_pendentes:
        exportar_pendentes(args.exportar_pendentes)
    elif args.aplicar_resumo:
        aplicar_resumo(args.aplicar_resumo, args.edicao, args.origem)
    else:
        sys.exit(rotina())
