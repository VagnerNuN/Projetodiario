"""Consulta o Diário Oficial dos Municípios de Rondônia (AROM) e baixa as edições.

O site não tem API pública. Usamos a mesma consulta que o calendário da página
inicial faz (POST /arom/materia/calendario), que devolve a edição ordinária de
uma data; /arom/materia/calendario/extra devolve as edições extraordinárias.
O formulário usa o CSRF "stateless" do Symfony: o navegador gera um token
aleatório e grava um cookie __Host-csrf-token_<token>=csrf-token. Fazemos igual.
"""

import re
import secrets
import time
from datetime import date
from pathlib import Path

import requests

BASE = "https://www.diariomunicipal.com.br/arom"
UA = "Mozilla/5.0 (briefing-porto-velho; +https://github.com)"
ENTIDADE_PORTO_VELHO = "24707"  # "Prefeitura Municipal de Porto Velho" na busca avançada da AROM
RE_RESULTADO = re.compile(r'<a href="/arom/load/([0-9A-Z]+)"')


class ColetorAROM:
    def __init__(self):
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = UA
        self.sessao.get(BASE + "/", timeout=60)

    def _consultar(self, dia: date, extra: bool) -> list[dict]:
        token = secrets.token_hex(16)
        self.sessao.cookies.set(
            f"__Host-csrf-token_{token}", "csrf-token",
            domain="www.diariomunicipal.com.br", path="/", secure=True,
        )
        dados = {
            "calendar[day]": dia.day,
            "calendar[month]": dia.month,
            "calendar[year]": dia.year,
            "calendar[_token]": token,
        }
        url = BASE + "/materia/calendario" + ("/extra" if extra else "")
        resp = self.sessao.post(url, data=dados, timeout=60, headers={
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.diariomunicipal.com.br",
            "Referer": BASE + "/",
        })
        resp.raise_for_status()
        corpo = resp.json()
        # Datas sem edição (fins de semana, feriados) respondem {"error": "..."}
        if corpo.get("error") is not None:
            return []
        base_arquivos = corpo["url_arquivos"]
        edicoes = []
        for ed in corpo.get("edicao") or []:
            edicoes.append({
                "id": ed["id"],
                "numero": ed["numero_edicao"],
                "data": ed["data_circulacao"][:10],
                "publicado_em": ed["data_publicacao"],
                "extra": bool(ed.get("is_extraordinario")),
                "url_pdf": base_arquivos + ed["link_diario"] + ".pdf",
            })
        return edicoes

    def edicoes_do_dia(self, dia: date) -> list[dict]:
        """Edição ordinária + extraordinárias que circulam na data."""
        edicoes = self._consultar(dia, extra=False)
        time.sleep(1)
        edicoes += self._consultar(dia, extra=True)
        return edicoes

    def codigos_oficiais(self, dia: date) -> set[str]:
        """Códigos identificadores que a busca avançada do site atribui a Porto Velho na data.
        Serve para conferir se a extração do PDF pegou todas as matérias."""
        pagina_busca = self.sessao.get(BASE + "/pesquisar", timeout=60).text
        token = re.search(r'name="busca_avancada\[_token\]"[^>]*value="([^"]+)"', pagina_busca).group(1)
        data_br = dia.strftime("%d/%m/%Y")
        codigos, pagina = set(), 1
        while True:
            resp = self.sessao.get(BASE + "/pesquisar", timeout=60, params={
                "busca_avancada[entidadeUsuaria]": ENTIDADE_PORTO_VELHO,
                "busca_avancada[dataInicio]": data_br,
                "busca_avancada[dataFim]": data_br,
                "busca_avancada[_token]": token,
                "busca_avancada[page]": pagina,
            })
            resp.raise_for_status()
            achados = set(RE_RESULTADO.findall(resp.text))
            if not achados - codigos:  # página repetida ou vazia: acabou
                break
            codigos |= achados
            pagina += 1
            time.sleep(0.4)
        return codigos

    def baixar(self, edicao: dict, pasta: Path) -> Path:
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / f"{edicao['data']}_{edicao['numero']}.pdf"
        if destino.exists() and destino.stat().st_size > 0:
            return destino
        with self.sessao.get(edicao["url_pdf"], timeout=300, stream=True) as resp:
            resp.raise_for_status()
            with open(destino, "wb") as f:
                for pedaco in resp.iter_content(1 << 16):
                    f.write(pedaco)
        return destino
