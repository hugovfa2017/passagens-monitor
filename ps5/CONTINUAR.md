# Continuar o buscador do PS5

Roteiro do que falta para o buscador entrar em produção. A branch `claude/ps5-price-monitor-brazil-cqvxiw` já está no
GitHub (enviada em 20/11/2026). Apague este arquivo quando tudo abaixo estiver feito.

## Onde estamos

Feito e testado (`python3 ps5/monitor.py --teste` passa; varredura real com 11 fontes em menos de 30 s):

- Fontes novas: lojas VTEX genéricas (Americanas, Fast Shop, Webcontinental, Bemol), Gatry, 22 canais do Telegram
  (prévia pública `t.me/s/<canal>?q=PS5`), leilões de logística reversa (Lance360, HISA, Bolsa de Leilões).
- `texto.py`: leitor de oferta em texto livre, calibrado em 821 posts reais.
- `monitor.py`: junção da mesma oferta entre fontes (`fundir`), níveis de confiança A/B/C, "não confirmada",
  seções à parte (open box, importados, leilões), saúde das fontes, cupons anunciados nos grupos.
- Telegram por bot próprio: lê só `TELEGRAM_TOKEN_PS5`.
- Workflow `ps5.yml` de 30 em 30 minutos e `ps5-telegram.yml` para testar o bot.
- `sondar.py`: mostra quais fontes e candidatas respondem.

## O que falta, em ordem

1. **Enviar a branch.** Feito em 20/11. O workflow "Buscador PS5" rodou nela sem erro e sem mandar Telegram; a partir
   dos servidores do GitHub responderam todas as fontes, menos a Amazon (falhou nessa rodada).
2. **Token do bot novo.** Feito em 20/11. O dono salvou o token em "Repository secrets" com o nome `PS5_BOT`; os
   workflows aceitam `PS5_BOT`, `TELEGRAM_TOKEN_PS5` e `PS5_TOKEN`. O token nunca deve ser colado em chat nem em arquivo.
3. **Testar o bot.** Feito em 20/11: Actions → "Teste do Telegram (PS5)" → Run workflow mandou a mensagem pelo bot
   novo. O primeiro passo do teste diz, com sim ou não, se o segredo foi encontrado.
4. **Juntar na `main`.** Feito em 20/11 (pull request #1, com o OK do dono). O buscador roda a partir dela.
   Desde então, quem manda `/start` a um dos bots é inscrito sozinho (workflow "Atendimento dos bots").
5. **Gatilho confiável.** O agendamento do GitHub quase não dispara neste repositório. Hoje o buscador e o atendimento
   rodam de carona no monitor de passagens (quando ele começa e quando termina, de hora em hora). Para 30 e 5 minutos,
   o dono precisa duplicar no cron-job.org o gatilho das passagens para `ps5.yml` e para `atendimento.yml`, com
   `origem=cron`.
6. **Sondar a partir do computador.** `python3 ps5/sondar.py`. Internet residencial no Brasil costuma ser aceita por
   lojas que barram servidores (Mercado Livre, Magazine Luiza, Casas Bahia, Shopee). Se alguma abrir, vale escrever um
   leitor para ela; se barrar, fica de fora. Não tentar contornar bloqueio.

## Pendências do plano (ainda não feitas)

- **Preço possível por modelo**: preço da loja (nível A, sem cupom) menos um cupom geral plausível, mostrado ao lado do
  confirmado, com aviso sem som quando ficar `config.MARGEM_POSSIVEL` abaixo do alvo. Hoje existe só a lista
  "cupons para testar" (`cupons_para_testar`), que já calcula o preço estimado por loja.
- **Cashback**: `fontes.zoom()` já anota `cashback` na oferta da loja mais barata; falta usar no preço possível.
- **Painel**: está funcional com os campos novos, mas merece uma passada (filtro por nível, cartão do "mais barato").
- **Preço do Zoom desatualizado**: em 20/11 o Zoom dava R$ 4.049,91 na Amazon para um produto que a própria Amazon
  mostrava a R$ 4.499,90. Ideia: quando a loja respondeu direto nesta rodada, preferir o preço dela ao do comparador.
- **Leilões da Receita Federal, Superbid e Kwara**: páginas montadas por JavaScript; precisariam de navegador.
- **Kalunga e Colombo**: respondem, mas precisam de leitor próprio.

## Decisões do dono (não mudar sem perguntar)

- Quer o PS5 novo mais barato, de qualquer modelo; os três modelos seguem monitorados.
- Seções ligadas: open box, importados, leilões. Usado de pessoa física fica de fora.
- Telegram por bot novo, separado do bot das passagens.
- Rodada de 30 em 30 minutos.

## O que a sondagem de 20/11/2026 mostrou

- Ofertas que só apareceram nos grupos: PS5 Digital a R$ 3.759 no Mercado Livre (cupom MP500PS, cartão Mercado Pago,
  18/11), a R$ 3.578 na Shopee (15/11) e PS5 com leitor a R$ 3.644 na Vivo (18/11).
- Barram robô a partir de servidor: Mercado Livre, Magazine Luiza, Casas Bahia, Ponto, Extra, Carrefour, Shopee,
  TikTok Shop, Gazin, Casa & Vídeo, Pichau, Terabyte, Trocafy, Hardmob, Pelando (site), Nosso Leilão, Tudo Leilão.
- Google Shopping exige navegador. A Amazon barra de vez em quando.
