"""Gera o briefing de uma edição a partir dos atos de Porto Velho, usando a API do Claude.

Sem ANTHROPIC_API_KEY o robô continua funcionando: o painel mostra os atos sem
resumo. Um resumo feito fora da API (por exemplo, no Claude Code) pode ser
aplicado depois com `briefing.py --aplicar-resumo`, desde que siga o mesmo
formato (ESQUEMA) — ele passa pela mesma validação.
"""

import json
import os

MODELO_PADRAO = "claude-opus-5"

CATEGORIAS = [
    "Legislação",              # leis, decretos normativos, decretos legislativos, regulamentos
    "Licitações e contratos",  # avisos, pregões, homologações, contratos, aditivos, atas de registro de preço
    "Pessoal",                 # nomeações, exonerações, promoções, férias, diárias, comissões
    "Concursos e seleções",    # concursos, processos seletivos, chamamentos de candidatos
    "Finanças e orçamento",    # créditos, remanejamentos, prestações de contas, previdência (IPAM)
    "Conselhos e participação social",
    "Outros",
]

ESQUEMA = {
    "type": "object",
    "properties": {
        "manchete": {"type": "string"},
        "resumo": {"type": "string"},
        "destaques": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titulo": {"type": "string"},
                    "texto": {"type": "string"},
                    "atos": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["titulo", "texto", "atos"],
                "additionalProperties": False,
            },
        },
        "atos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "categoria": {"type": "string", "enum": CATEGORIAS},
                    "relevancia": {"type": "string", "enum": ["alta", "media", "baixa"]},
                    "resumo": {"type": "string"},
                },
                "required": ["id", "categoria", "relevancia", "resumo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["manchete", "resumo", "destaques", "atos"],
    "additionalProperties": False,
}

INSTRUCOES = f"""Você prepara o briefing diário dos atos oficiais do Município de Porto Velho (RO) \
publicados no Diário Oficial dos Municípios do Estado de Rondônia (AROM). Quem lê são pessoas que \
acompanham a gestão municipal — jornalistas, assessores, vereadores, cidadãos — e querem entender \
em dois minutos o que a Prefeitura, a Câmara e os demais órgãos municipais publicaram na edição.

Você recebe todos os atos de Porto Velho da edição, cada um dentro de <ato id="...">. Produza:

- manchete: uma frase com o fato mais importante da edição para a cidade.
- resumo: um parágrafo de 3 a 5 frases sobre o conjunto da edição (o que predominou, o que merece atenção).
- destaques: de 3 a 6 itens com os atos de maior impacto para a população ou para as contas públicas \
(novas leis, licitações e contratos de valor alto, concursos, nomeações para cargos de direção, mudanças \
em serviços públicos). Atos repetitivos do mesmo tipo viram um único destaque (ex.: "25 decretos de \
nomeação e exoneração na Secretaria de Governo"). Cada destaque tem título curto, texto de 1 a 2 frases \
e a lista de ids dos atos.
- atos: exatamente um item para CADA ato recebido, com o mesmo id, contendo:
  - categoria: uma de {", ".join(CATEGORIAS)};
  - relevancia: "alta" (afeta muita gente, envolve valor alto ou cria regra nova), "media" ou "baixa" (rotina administrativa);
  - resumo: uma frase direta dizendo o que o ato faz, com nomes, valores em R$, prazos e locais quando houver.

Regras: escreva em português claro, sem juridiquês. Use só o que está no texto dos atos — não invente \
números, nomes ou datas; se o texto estiver truncado ou confuso, diga isso no resumo do ato."""


def montar_entrada(edicao: dict, atos: list[dict]) -> str:
    """Texto enviado ao modelo: cabeçalho da edição + todos os atos, sem cortes."""
    tipo = "extraordinária" if edicao["extra"] else "ordinária"
    partes = [f"Edição {tipo} nº {edicao['numero']}, de {edicao['data']}. {len(atos)} atos de Porto Velho.\n"]
    for a in atos:
        partes.append(
            f'<ato id="{a["id"]}" tipo="{a["tipo"]}" orgao="{a["orgao"]}" pagina="{a["pagina"]}">\n'
            f'{a["titulo"]}\n\n{a["texto"]}\n</ato>'
        )
    return "\n\n".join(partes)


def validar_resumo(resumo: dict, atos: list[dict]) -> dict:
    """Confere o formato e se todo ato recebeu resumo. Levanta ValueError se algo não bater."""
    for campo in ("manchete", "resumo", "destaques", "atos"):
        if campo not in resumo:
            raise ValueError(f"campo ausente no resumo: {campo}")
    ids = {a["id"] for a in atos}
    vistos = set()
    for item in resumo["atos"]:
        if item["id"] not in ids:
            raise ValueError(f"resumo cita ato inexistente: {item['id']}")
        if item["categoria"] not in CATEGORIAS:
            raise ValueError(f"categoria inválida no ato {item['id']}: {item['categoria']}")
        if item["relevancia"] not in ("alta", "media", "baixa"):
            raise ValueError(f"relevância inválida no ato {item['id']}: {item['relevancia']}")
        vistos.add(item["id"])
    faltando = sorted(ids - vistos)
    if faltando:
        raise ValueError(f"atos sem resumo: {faltando}")
    for d in resumo["destaques"]:
        d["atos"] = [i for i in d["atos"] if i in ids]
    return resumo


def resumir_edicao(edicao: dict, atos: list[dict], modelo: str | None = None) -> dict:
    import anthropic

    modelo = modelo or os.environ.get("MODELO") or MODELO_PADRAO
    cliente = anthropic.Anthropic()
    parametros = dict(
        model=modelo,
        max_tokens=64000,
        system=INSTRUCOES,
        messages=[{"role": "user", "content": montar_entrada(edicao, atos)}],
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
    )
    if not modelo.startswith("claude-haiku"):
        parametros["output_config"]["effort"] = "medium"
    if modelo in ("claude-opus-5", "claude-fable-5-1"):
        # Se o filtro de segurança recusar a edição, a API refaz o pedido em outro modelo.
        parametros["betas"] = ["server-side-fallback-2026-07-01"]
        parametros["fallbacks"] = "default"
        chamada = cliente.beta.messages.stream(**parametros)
    else:
        chamada = cliente.messages.stream(**parametros)

    with chamada as stream:
        resposta = stream.get_final_message()

    if resposta.stop_reason == "refusal":
        raise RuntimeError(f"modelo recusou a edição {edicao['numero']}")
    if resposta.stop_reason == "max_tokens":
        raise RuntimeError(f"resposta cortada por limite de tokens na edição {edicao['numero']}")
    texto = next(b.text for b in resposta.content if b.type == "text")
    resumo = validar_resumo(json.loads(texto), atos)
    uso = resposta.usage
    print(f"    tokens: entrada {uso.input_tokens}, saída {uso.output_tokens} ({resposta.model})")
    return resumo
