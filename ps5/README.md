# Buscador de preço do PS5

Procura o PS5 a cada 30 minutos em lojas, comparador, comunidades de oferta e grupos do Telegram, e avisa por um bot
próprio no Telegram quando o preço fica bom, com cupom ou sem.

**Painel:** https://hugovfa2017.github.io/passagens-monitor/ps5/

## O que ele acompanha

Três modelos (com leitor de disco, Digital e Pro), cada um com o melhor preço **sem cupom** e **com cupom**, e no topo o
**PS5 novo mais barato agora**, seja qual for o modelo. Bundles com jogo contam; acessórios e PlayStation Portal não.

| Tipo | Fontes | Como conta |
|---|---|---|
| Lojas | KaBuM!, Amazon, Americanas, Fast Shop, Webcontinental, Bemol | preço lido na loja (nível A) |
| Comparador | Zoom (Magazine Luiza, Amazon, KaBuM!...) | nível A |
| Ofertas postadas | Promobit, Gatry | preço final já com cupom (nível B) |
| Grupos do Telegram | 22 canais públicos (Jogo Barato, MeuPlayStation, Pelando, PromoTop, Xet, Pechinchou, Nerd Ofertas, Tecnoblog, Canaltech...) | nível B se vista em 2+ redes independentes; senão "não confirmada" (nível C) |
| Cupons | páginas de cupom do Promobit e cupons anunciados nos grupos | "para testar no carrinho" |
| À parte | open box, importados e leilões de logística reversa (Lance360, HISA, Bolsa de Leilões) | nunca entram no melhor preço |

A mesma oferta vista em várias fontes vira uma só, com "visto em N lugares". Mercado Livre, Magazine Luiza (direto),
Casas Bahia, Carrefour, Shopee e outras bloqueiam robôs: as ofertas delas chegam pelos grupos e pelo Promobit.
A lista de lojas, canais e sites fica em [`lojas.py`](lojas.py); acrescentar um é acrescentar uma linha.
`python3 ps5/sondar.py` mostra quem responde.

## Quando chega mensagem

- **Com som:** o melhor preço confirmado de um modelo chegou ao alvo, ou é o menor já visto (pelo menos R$ 50 abaixo
  do recorde). A mesma oferta não toca de novo por 24 h. Oferta só vista em post com mais de 6 h não toca.
- **Sem som:** oferta de grupo ainda não confirmada que parece muito boa; lote novo de leilão com PS5; resumo diário às 9h.

## Telegram: bot próprio

O buscador usa um bot só dele (nada sai pelo bot das passagens).

1. No Telegram, fale com o **@BotFather**, mande `/newbot` e guarde o token.
2. Abra a conversa com o bot novo e mande `/start`.
3. No GitHub: Settings → Secrets and variables → Actions → **New repository secret**, nome **`PS5_BOT`**
   (também valem `TELEGRAM_TOKEN_PS5` e `PS5_TOKEN`). Tem que aparecer na lista "Repository secrets": em
   "Variables", em segredo de ambiente, de Codespaces ou de Dependabot, o buscador não enxerga.
4. Teste: Actions → **Teste do Telegram (PS5)** → Run workflow. O primeiro passo diz, com sim ou não, se o segredo
   foi encontrado.

As mensagens vão para a lista fixa do dono (`TELEGRAM_CHAT_ID_PS5`, se existir; senão, o primeiro chat de
`TELEGRAM_CHAT_ID`) e para quem se inscreveu no bot.

**Inscrição pelo `/start`.** Qualquer pessoa que abrir o bot e mandar `/start` passa a receber os alertas; `/sair`
cancela. Ninguém precisa cadastrar número de chat. Um grupo onde o bot entra também é inscrito. O workflow
**Atendimento dos bots** ([`../atendimento.py`](../atendimento.py)) lê as mensagens de 5 em 5 minutos, então a
confirmação chega em poucos minutos (na hora, com Actions → Atendimento dos bots → Run workflow). O dono recebe um aviso
sem som de cada inscrição, com o comando para remover (`/remover número`); `/inscritos` mostra a lista. A lista fica em
`ps5/assinantes.cif`, cifrada com o token do bot: o repositório é público e o arquivo só abre com o token. O bot das
passagens funciona do mesmo jeito, com `assinantes.cif` na raiz.

## Rodada de 30 em 30 minutos

O agendamento do GitHub atrasa e pula execuções. Para valer de verdade, duplique no cron-job.org o gatilho que já existe
para as passagens, apontando para o workflow `ps5.yml`, a cada 30 minutos, com `origem=cron`.

## Ajustes e uso

- Alvos, seções ligadas e prazos: [`config.py`](config.py) (dá para editar direto no GitHub).
- Rodar na mão: Actions → **Buscador PS5** → Run workflow; ou `python3 ps5/monitor.py` no computador.
- Auto-checagem sem internet: `python3 ps5/monitor.py --teste`.

Arquivos gerados: `ps5/historico.csv`, `ps5/ultimo.md`, `ps5/estado.json` e `docs/ps5/` (painel).
