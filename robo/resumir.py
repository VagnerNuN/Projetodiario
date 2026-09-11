"""Gera o briefing de uma edição a partir dos atos de Porto Velho, usando a API do Claude.

Sem ANTHROPIC_API_KEY o robô continua funcionando: o painel mostra os atos sem
resumo. Um resumo feito fora da API (por exemplo, no Claude Code) pode ser
aplicado depois com `briefing.py --aplicar-resumo`, desde que siga o mesmo
formato (ESQUEMA) — ele passa pela mesma validação.
"""

import json
import os
import re

MODELO_PADRAO = "claude-opus-5"
# Versão do formato do resumo. Edições resumidas num formato anterior são refeitas
# pelo robô quando houver chave da API.
FORMATO = 2

CATEGORIAS = [
    "Legislação",              # leis, decretos normativos, decretos legislativos, regulamentos
    "Licitações e contratos",  # avisos, pregões, homologações, contratos, aditivos, atas de registro de preço
    "Pessoal",                 # nomeações, exonerações, promoções, férias, diárias, comissões
    "Concursos e seleções",    # concursos, processos seletivos, chamamentos de candidatos
    "Finanças e orçamento",    # créditos, remanejamentos, prestações de contas, previdência (IPAM)
    "Conselhos e participação social",
    "Outros",
]

ETAPAS = [
    "Aviso de licitação", "Resultado", "Homologação", "Contrato", "Aditivo",
    "Ata de registro de preços", "Adesão a ata", "Dispensa", "Inexigibilidade",
    "Chamamento público", "Suspensão ou revogação", "Outro",
]

ACOES_PESSOAL = ["Nomeação", "Exoneração", "Designação", "Dispensa", "Substituição", "Outro"]

TEXTO = {"type": "string"}
INTEIRO = {"type": "integer"}
IDS = {"type": "array", "items": INTEIRO}
TEXTO_OU_NULO = {"anyOf": [{"type": "string"}, {"type": "null"}]}
NUMERO_OU_NULO = {"anyOf": [{"type": "number"}, {"type": "null"}]}


def _objeto(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


ESQUEMA = _objeto({
    "manchete": TEXTO,
    "resumo": TEXTO,
    "destaques": {"type": "array", "items": _objeto({"titulo": TEXTO, "texto": TEXTO, "atos": IDS})},
    "atencao": {"type": "array", "items": _objeto({"ato": INTEIRO, "motivo": TEXTO})},
    "contratacoes": {"type": "array", "items": _objeto({
        "ato": INTEIRO,
        "etapa": {"type": "string", "enum": ETAPAS},
        "modalidade": TEXTO_OU_NULO,
        "objeto": TEXTO,
        "fornecedor": TEXTO_OU_NULO,
        "valor": NUMERO_OU_NULO,
    })},
    "agenda": {"type": "array", "items": _objeto({
        "data": TEXTO,
        "hora": TEXTO_OU_NULO,
        "evento": TEXTO,
        "ato": INTEIRO,
    })},
    "normas": {"type": "array", "items": _objeto({"ato": INTEIRO, "identificacao": TEXTO, "o_que_muda": TEXTO})},
    "pessoal": _objeto({
        "movimentacoes": {"type": "array", "items": _objeto({
            "ato": INTEIRO,
            "acao": {"type": "string", "enum": ACOES_PESSOAL},
            "nome": TEXTO,
            "cargo": TEXTO,
        })},
        "rotina": TEXTO,
    }),
    "atos": {"type": "array", "items": _objeto({
        "id": INTEIRO,
        "categoria": {"type": "string", "enum": CATEGORIAS},
        "relevancia": {"type": "string", "enum": ["alta", "media", "baixa"]},
        "resumo": TEXTO,
    })},
})

INSTRUCOES = f"""Você prepara o briefing diário dos atos oficiais do Município de Porto Velho (RO) \
publicados no Diário Oficial dos Municípios do Estado de Rondônia (AROM). Quem lê é a equipe da \
Controladoria-Geral do Município e gestores que precisam saber, em poucos minutos, o que a Prefeitura, \
a Câmara e os demais órgãos municipais publicaram — e o que exige acompanhamento.

Você recebe todos os atos de Porto Velho da edição, cada um dentro de <ato id="...">. Produza:

- manchete: uma frase com o fato mais importante da edição para a cidade.
- resumo: um parágrafo de 3 a 5 frases sobre o conjunto da edição (o que predominou, o que merece atenção).
- destaques: de 3 a 6 itens com os atos de maior impacto para a população ou para as contas públicas. \
Atos repetitivos do mesmo tipo viram um único destaque (ex.: "25 decretos de nomeação e exoneração na \
Secretaria de Governo"). Cada destaque tem título curto, texto de 1 a 2 frases e os ids dos atos.
- atencao: pontos de atenção para o controle interno, só quando houver motivo objetivo no texto: \
contratação por dispensa ou inexigibilidade, contratação emergencial, aditivo de valor ou de prazo, \
adesão a ata de registro de preços de outro órgão, revogação, anulação ou suspensão de licitação, \
retificação que altere valor, objeto ou prazo, valor alto sem justificativa aparente, prazo muito curto. \
Em "motivo", diga em uma frase o fato objetivo que justifica a atenção. Não faça acusações nem suponha \
irregularidades. Lista vazia se não houver.
- contratacoes: uma linha para cada ato que trate de licitação, contratação, aditivo, ata de registro de \
preços, dispensa, inexigibilidade ou chamamento público. etapa: uma de {", ".join(ETAPAS)}. modalidade: \
como está no texto (ex.: "Pregão eletrônico nº 329/2026"), ou null. objeto: o que está sendo comprado ou \
contratado, em poucas palavras. fornecedor: empresa ou entidade contratada (nome como no texto), ou null \
se ainda não houver. valor: valor total em reais como número (ex.: 51260529.50), ou null se o texto não \
trouxer valor.
- agenda: datas futuras citadas nos atos que alguém precise acompanhar — sessões de licitação, prazos de \
proposta, recurso ou impugnação, provas e convocações de concurso, audiências públicas, início de \
vigência de lei, prazos para apresentar documentos. data no formato AAAA-MM-DD; hora como "09:00" ou \
null; evento em uma frase curta. Não inclua datas passadas em relação à data da edição.
- normas: leis, decretos normativos, decretos legislativos, resoluções e instruções normativas que criam, \
mudam ou revogam regras. identificacao: ex. "Lei nº 3.478, de 13/07/2026". o_que_muda: 1 a 2 frases, em \
linguagem simples, dizendo o que passa a valer e para quem. Decretos de nomeação ou exoneração não entram aqui.
- pessoal: movimentacoes lista nomeações, exonerações, designações, dispensas e substituições em cargos \
em comissão, funções de chefia, direção ou conselhos (uma linha por pessoa: acao, nome, cargo com o órgão). \
rotina resume em uma frase o restante dos atos de pessoal (férias, diárias, progressões, licenças etc.), \
ou "" se não houver.
- atos: exatamente um item para CADA ato recebido, com o mesmo id: categoria (uma de \
{", ".join(CATEGORIAS)}); relevancia "alta" (afeta muita gente, envolve valor alto ou cria regra nova), \
"media" ou "baixa" (rotina administrativa); resumo em uma frase direta com nomes, valores em R$, prazos \
e locais quando houver.

Regras: escreva em português claro, sem juridiquês. Use só o que está no texto dos atos — não invente \
números, nomes ou datas; se o texto estiver truncado ou confuso, diga isso no resumo do ato. Todo campo \
"ato"/"atos" deve usar ids de atos recebidos."""


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
    for campo in ESQUEMA["required"]:
        if campo not in resumo:
            raise ValueError(f"campo ausente no resumo: {campo}")
    ids = {a["id"] for a in atos}

    def conferir_id(i, onde):
        if i not in ids:
            raise ValueError(f"{onde} cita ato inexistente: {i}")

    vistos = set()
    for item in resumo["atos"]:
        conferir_id(item["id"], "atos")
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
    for x in resumo["atencao"]:
        conferir_id(x["ato"], "atencao")
    for x in resumo["contratacoes"]:
        conferir_id(x["ato"], "contratacoes")
        if x["etapa"] not in ETAPAS:
            raise ValueError(f"etapa inválida no ato {x['ato']}: {x['etapa']}")
        if x["valor"] is not None and not isinstance(x["valor"], (int, float)):
            raise ValueError(f"valor não numérico no ato {x['ato']}: {x['valor']!r}")
    for x in resumo["agenda"]:
        conferir_id(x["ato"], "agenda")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", x["data"]):
            raise ValueError(f"data fora do formato AAAA-MM-DD na agenda: {x['data']!r}")
    for x in resumo["normas"]:
        conferir_id(x["ato"], "normas")
    for x in resumo["pessoal"]["movimentacoes"]:
        conferir_id(x["ato"], "pessoal")
        if x["acao"] not in ACOES_PESSOAL:
            raise ValueError(f"ação inválida em pessoal no ato {x['ato']}: {x['acao']}")
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
