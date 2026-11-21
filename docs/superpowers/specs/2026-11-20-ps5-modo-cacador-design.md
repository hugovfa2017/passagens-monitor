# Modo caçador do buscador de PS5 (Black Friday 2026)

## Por quê
Na Black Friday (27/11) um PS5 barato pode esgotar em 10 a 15 minutos, e a melhor oferta costuma aparecer primeiro em um
grupo só. Com rodada de 30 em 30 minutos e oferta não confirmada chegando sem som, o dono pode saber tarde demais.

## O que muda (decidido pelo dono em 20/11/2026)
- Até `config.CACADOR_ATE` (01/12/2026, depois da Cyber Monday):
  - oferta de grupo **não confirmada** igual ou abaixo do alvo toca **com som** (com o aviso "confira antes");
  - a guarda do workflow pula a rodada só se a anterior terminou há menos de 7 min (antes: 12).
- Depois da data, volta sozinho: não confirmada sem som e guarda de 25 min (com o gatilho de 10 min, roda de 30 em 30).
- Gatilho do cron-job.org: de 30 para 10 minutos.
- PS5 Pro fora dos alertas (`MODELOS_COM_ALERTA`): fora do orçamento. Continua no painel.
- Alvos mantidos: com leitor R$ 3.799, Digital R$ 3.499.

## Fora
Canais novos, preço desatualizado do Zoom, "preço possível". Oferta não confirmada acima do alvo continua sem som.

## Abordagens consideradas
- Data de fim no `config.py` (escolhida): volta ao normal sem ninguém lembrar.
- Voltar na mão em 01/12: depende de lembrar.
- 10 min para sempre: muda a decisão dos 30 min.
