"""Consulta o Diário Oficial dos Municípios de Rondônia (AROM) e baixa as edições.

O site não tem API pública. Usamos a mesma consulta que o calendário da página
inicial faz (POST /arom/materia/calendario), que devolve a edição ordinária de
uma data; /arom/materia/calendario/extra devolve as edições extraordinárias.
O formulário é protegido por CSRF. Até set/2026 era o CSRF "stateless" do
Symfony (o campo vinha com o valor fixo "csrf-token" e o navegador gerava um
token aleatório + cookie); desde 22/09/2026 o campo traz um token de sessão,
que basta reenviar junto com o cookie PHPSESSID. O coletor aceita os dois.
"""

import html
import re
import secrets
import time
from datetime import date
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://www.diariomunicipal.com.br/arom"
UA = "Mozilla/5.0 (briefing-porto-velho; +https://github.com)"
ENTIDADE_PORTO_VELHO = "24707"  # "Prefeitura Municipal de Porto Velho" na busca avançada da AROM
RE_RESULTADO = re.compile(r'href="/arom/materia/([0-9A-Z]+)"')
RE_TOTAL_BUSCA = re.compile(r"de\s*<strong>\s*([\d.]+)\s*</strong>\s*mat")
RE_TOKEN_CALENDARIO = re.compile(r'name="calendar\[_token\]"[^>]*value="([^"]*)"')


class ColetorAROM:
    def __init__(self):
        self.sessao = requests.Session()
        self.sessao.headers["User-Agent"] = UA
        # O site às vezes derruba a conexão no meio de uma sequência de consultas
        tentativas = Retry(total=4, backoff_factor=3, status_forcelist=(429, 500, 502, 503, 504),
                           allowed_methods=frozenset({"GET", "POST"}), raise_on_status=False)
        self.sessao.mount("https://", HTTPAdapter(max_retries=tentativas))
        self.token = None
        self._renovar_token()

    def _renovar_token(self):
        pagina = self.sessao.get(BASE + "/", timeout=60)
        pagina.raise_for_status()
        m = RE_TOKEN_CALENDARIO.search(pagina.text)
        if not m:
            raise RuntimeError("formulário do calendário não encontrado na página da AROM: o site mudou")
        self.token = m.group(1)

    def _token_para_envio(self) -> str:
        if self.token != "csrf-token":
            return self.token
        # CSRF "stateless" (formato antigo do site): token aleatório + cookie correspondente
        token = secrets.token_hex(16)
        self.sessao.cookies.set(
            f"__Host-csrf-token_{token}", "csrf-token",
            domain="www.diariomunicipal.com.br", path="/", secure=True,
        )
        return token

    def _consultar(self, dia: date, extra: bool, renovar: bool = True) -> list[dict]:
        dados = {
            "calendar[day]": dia.day,
            "calendar[month]": dia.month,
            "calendar[year]": dia.year,
            "calendar[_token]": self._token_para_envio(),
        }
        url = BASE + "/materia/calendario" + ("/extra" if extra else "")
        resp = self.sessao.post(url, data=dados, timeout=60, headers={
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.diariomunicipal.com.br",
            "Referer": BASE + "/",
        })
        resp.raise_for_status()
        corpo = resp.json()
        if corpo.get("error") is not None:
            # O site responde o mesmo "erro inesperado" para data sem edição e para token
            # recusado. Renova o token uma vez antes de concluir que não há edição.
            if renovar:
                self._renovar_token()
                return self._consultar(dia, extra, renovar=False)
            return []
        edicoes = []
        for ed in corpo.get("edicao") or []:
            # Desde a reformulação do site (set/2026) a resposta não traz mais
            # data_publicacao nem is_extraordinario; a consulta /extra já diz o tipo.
            base_arquivos = corpo.get("url_arquivos") or ed["url_arquivo"]
            edicoes.append({
                "id": ed["id"],
                "numero": ed["numero_edicao"],
                "data": ed["data_circulacao"][:10],
                "publicado_em": ed.get("data_publicacao"),
                "extra": bool(ed.get("is_extraordinario", extra)),
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
        codigos, total, pagina = set(), None, 1
        while True:
            resp = self.sessao.get(BASE + "/pesquisar", timeout=60, params={
                "busca_avancada[entidade]": ENTIDADE_PORTO_VELHO,
                "busca_avancada[dataInicio]": dia.isoformat(),
                "busca_avancada[dataFim]": dia.isoformat(),
                "busca_avancada[ordenacao]": "antiga",
                "busca_avancada[pagina]": pagina,
            })
            resp.raise_for_status()
            achados = set(RE_RESULTADO.findall(resp.text))
            m = RE_TOTAL_BUSCA.search(resp.text)
            if m:
                total = int(m.group(1).replace(".", ""))
            elif achados:
                raise RuntimeError("a busca da AROM devolveu resultados sem o total de matérias: o layout mudou")
            if not achados - codigos:  # página repetida ou vazia: acabou
                break
            codigos |= achados
            if total is not None and len(codigos) >= total:
                break
            pagina += 1
            time.sleep(0.4)
        if total is not None and len(codigos) != total:
            raise RuntimeError(f"a busca da AROM anunciou {total} matérias em {dia}, mas só {len(codigos)} foram lidas")
        return codigos

    def texto_oficial(self, codigo: str) -> str:
        """Texto da matéria na página oficial /arom/materia/<código>, sem o resto da página."""
        resp = self.sessao.get(f"{BASE}/materia/{codigo}", timeout=60)
        resp.raise_for_status()
        pagina = resp.text
        ini = pagina.find('id="materia"')
        fim = pagina.find('<footer class="verificacao"', ini)
        if ini < 0 or fim < 0:
            raise RuntimeError(f"página da matéria {codigo} sem o bloco do texto: o layout mudou")
        trecho = pagina[pagina.index(">", ini) + 1:fim]
        trecho = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", trecho)
        trecho = re.sub(r"(?i)<br\s*/?>|</(p|div|h\d|li|tr)>", "\n", trecho)
        texto = html.unescape(re.sub(r"<[^>]+>", " ", trecho)).replace("\xa0", " ")
        return "\n".join(l for l in (re.sub(r"[ \t]+", " ", x).strip() for x in texto.splitlines()) if l)

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
