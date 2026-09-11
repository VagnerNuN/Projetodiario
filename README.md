# Briefing Porto Velho

Painel com o resumo diário dos atos do **Município de Porto Velho** publicados no
[Diário Oficial dos Municípios do Estado de Rondônia (AROM)](https://www.diariomunicipal.com.br/arom/).

Todo dia um robô no GitHub Actions:

1. consulta a AROM e baixa a edição do dia e as edições extras;
2. separa só as matérias publicadas pelo próprio município (Prefeitura, Câmara, IPAM,
   EMDUR, Agência Reguladora, secretarias…). Menções a Porto Velho feitas por outros municípios ficam de fora;
3. resume a edição com a API do Claude (manchete, resumo, destaques e uma frase por ato);
4. atualiza o painel em `docs/` (GitHub Pages), mantendo os últimos 15 dias.

## Estrutura

| Caminho | O que é |
|---|---|
| `robo/coletar.py` | Consulta o calendário da AROM e baixa os PDFs |
| `robo/extrair.py` | Recorta as matérias de Porto Velho, ato por ato (órgão, título, página, código) |
| `robo/resumir.py` | Instruções, formato do resumo e chamada à API do Claude |
| `robo/briefing.py` | Rotina completa: coleta → extração → resumo → histórico → painel |
| `docs/index.html` | O painel |
| `docs/dados/edicoes/*.json` | Uma edição por arquivo (atos + resumo) |
| `docs/dados.js` | Dados que o painel lê (gerado automaticamente) |
| `.github/workflows/briefing.yml` | Agendamento: 06:30, 18:30 e 21:30 (horário de Porto Velho) |

## Publicar no GitHub (uma vez)

1. Crie um repositório **público** no GitHub (ex.: `briefing-porto-velho`). O GitHub Pages
   gratuito exige repositório público, o que combina com informação pública.
2. Nesta pasta, rode:
   ```
   git init -b main
   git add .
   git commit -m "Briefing Porto Velho"
   git remote add origin https://github.com/SEU_USUARIO/briefing-porto-velho.git
   git push -u origin main
   ```
3. No repositório: **Settings → Pages → Build and deployment → Source: Deploy from a branch**,
   branch `main`, pasta `/docs`. O endereço do painel aparece ali
   (`https://SEU_USUARIO.github.io/briefing-porto-velho/`).
4. Em **Settings → Actions → General → Workflow permissions**, marque **Read and write permissions**
   (o robô precisa gravar os dados novos).
5. Em **Actions → Briefing diário → Run workflow**, rode uma vez para testar.

## Ligar os resumos automáticos (API do Claude)

Sem chave, o robô continua funcionando: os atos entram no painel com título e início do texto, sem resumo.

1. Crie uma chave em <https://console.anthropic.com/> (é preciso ter créditos na conta).
2. No repositório: **Settings → Secrets and variables → Actions → New repository secret**,
   nome `ANTHROPIC_API_KEY`, valor = a chave.
3. (Opcional) Na aba **Variables** da mesma tela, crie `MODELO` para trocar o modelo:
   `claude-opus-5` (padrão, melhor qualidade), `claude-sonnet-5` ou `claude-haiku-4-5` (mais baratos).

Estimativa de custo com o Opus 5: cerca de 80 mil tokens de entrada por edição
(~US$ 0,60/dia útil, ~US$ 13/mês). O Sonnet 5 fica em torno de US$ 5/mês. O valor real de
cada edição aparece no log do Actions (linha `tokens:`).

## Rodar no computador

```
pip install -r requirements.txt
python robo/briefing.py              # sem chave: só coleta e extrai
set ANTHROPIC_API_KEY=sk-ant-...     # (PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-...")
python robo/briefing.py              # com chave: resume o que estiver sem resumo
```
Depois abra `docs/index.html` no navegador.

Resumo feito fora da API (por exemplo, no Claude Code), no mesmo formato JSON:
```
python robo/briefing.py --exportar-pendentes pendentes/   # gera o texto de cada edição sem resumo
python robo/briefing.py --aplicar-resumo pendentes/2026-09-11_4317.resumo.json
```

## Limitações conhecidas

- O download depende da consulta interna do site da AROM. Se o site mudar, o robô avisa com erro
  no Actions e o ajuste fica em `robo/coletar.py`.
- A separação dos atos usa o "Código Identificador" que o diário põe no fim de cada matéria.
  Tabelas e anexos longos entram como texto corrido.
- Os resumos são gerados por IA: confira sempre o ato original pelo link da página.
