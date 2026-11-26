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

A mesma oferta vista em várias fontes vira uma só, com "visto em N lugares". Oferta postada some da primeira página do
Promobit em poucas horas e continua lembrada por 36 h ("saiu da lista; pode ter acabado"); a cada varredura, a página
das mais baratas é aberta e a que o Promobit marca como encerrada sai na hora. Mercado Livre, Magazine Luiza (direto),
Casas Bahia, Carrefour, Shopee e outras bloqueiam robôs: as ofertas delas chegam pelos grupos e pelo Promobit.
A lista de lojas, canais e sites fica em [`lojas.py`](lojas.py); acrescentar um é acrescentar uma linha.
`python3 ps5/sondar.py` mostra quem responde.

## Quando chega mensagem

Toda mensagem começa pelo preço, que é o que aparece na notificação do celular: no alerta, a oferta que disparou o aviso;
no resumo, o PS5 novo mais barato agora. Depois vem um bloco curto por modelo (preço, cupom, distância do alvo), com o
nome da loja como link e um botão por oferta. Para receber o resumo na hora: Actions → **Buscador PS5** → Run workflow,
marcando "Mandar o resumo no Telegram agora".

- **Com som:** o melhor preço confirmado de um modelo chegou ao alvo, ou é o menor já visto (pelo menos R$ 50 abaixo
  do recorde). A mesma oferta não toca de novo por 24 h. Oferta só vista em post com mais de 6 h não toca.
- **Sem som:** oferta de grupo ainda não confirmada que parece muito boa; lote novo de leilão com PS5; resumo diário às 9h.
- **Plantão da Black Friday (modo caçador, `CACADOR_ATE` no [`config.py`](config.py)):** cupom de console acaba em
  minutos, e a varredura completa roda de 10 em 10. Entre elas, o workflow **Plantão PS5** lê uma vez por minuto a lista
  do Promobit, o feed do Gatry e a primeira página de cada canal do Telegram, e avisa com som o post de até 1 h, ainda não
  visto, de modelo com alerta e no preço que toca (alvo ou `AVISAR_ABAIXO`). A mensagem termina com "⚡ Plantão da Black
  Friday". O que ele avisou fica em `plantao.json`, e a varredura completa não repete. Fora do modo caçador, não roda.
- **Aviso antecipado de cupom (no plantão):** os canais costumam anunciar o cupom antes de liberar ("🔥 Às 18h: CUPOM DE
  4 DÍGITOS no Mercado Livre!", às 17h15 de 25/11; às 18h04 já tinha esgotado). Post que anuncia cupom ou oferta
  relâmpago com hora marcada, numa loja que vende PS5 e que tira pelo menos `ANUNCIO_MINIMO` (R$ 150) de um console,
  vira um aviso ⏰ na hora, com som, e um lembrete 🚨 `ANUNCIO_ANTES_MIN` (2) minutos antes de liberar. É um aviso por
  loja e horário, mesmo que vários canais repitam. Não conta: hora que já passou, hora de "usar a partir de",
  "liberado" e "esgotado", cupom de outra categoria (moda, casa, beleza...).
- **Só para o dono (raro):** nenhuma oferta em 6 varreduras seguidas; uma fonte quebrou (6 falhas seguidas por erro
  que não é bloqueio, sem som); ou uma fonte passou um dia inteiro barrando todas as buscas (sem som). No máximo um aviso por fonte a cada 3 dias. Bloqueio que vai e volta não gera
  mensagem: a Amazon, por exemplo, barra o servidor do GitHub em boa parte das buscas e volta sozinha. A proporção de
  buscas barradas no dia aparece em [`ultimo.md`](ultimo.md), na lista de fontes.

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
**Atendimento dos bots** ([`../atendimento.py`](../atendimento.py)) lê as mensagens toda vez que roda (veja "Quando
roda", abaixo); para atender na hora, Actions → Atendimento dos bots → Run workflow. O dono recebe um aviso
sem som de cada inscrição, com o comando para remover (`/remover número`); `/inscritos` mostra a lista. A lista fica em
`ps5/assinantes.cif`, cifrada com o token do bot: o repositório é público e o arquivo só abre com o token. O bot das
passagens funciona do mesmo jeito, com `assinantes.cif` na raiz.

## Quando roda

Neste repositório o agendamento do próprio GitHub dispara poucas vezes por dia, então ele é só reserva. O que vale:

- **Sem configurar nada:** o buscador e o atendimento pegam carona no monitor de passagens, que o cron-job.org já
  dispara de hora em hora. Rodam quando ele começa e quando termina, ou seja, umas duas vezes por hora.
- **Para 30 em 30 minutos (buscador) e 5 em 5 minutos (atendimento):** duplique no cron-job.org o gatilho das passagens
  duas vezes, trocando só o nome do arquivo na URL (`monitor.yml` → `ps5.yml` e `monitor.yml` → `atendimento.yml`) e o
  intervalo. O corpo continua `{"ref":"main","inputs":{"origem":"cron"}}`.

Resposta instantânea ao `/start` não dá para ter só com o GitHub Actions: seria preciso um endereço sempre ligado
recebendo as mensagens do Telegram (webhook).

## Ajustes e uso

- Alvos, seções ligadas e prazos: [`config.py`](config.py) (dá para editar direto no GitHub).
- Cupom testado no carrinho que não vale para console: acrescente o código em `CUPONS_QUE_NAO_VALEM`, no mesmo arquivo,
  e ele deixa de aparecer em "para testar".
- Rodar na mão: Actions → **Buscador PS5** → Run workflow; ou `python3 ps5/monitor.py` no computador.
- Auto-checagem sem internet: `python3 ps5/monitor.py --teste`.

Arquivos gerados: `ps5/historico.csv`, `ps5/ultimo.md`, `ps5/estado.json` e `docs/ps5/` (painel).
