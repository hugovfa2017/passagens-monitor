# Monitor de preço do PS5

Procura o PS5 de hora em hora em lojas brasileiras e avisa no Telegram (o mesmo bot das passagens) quando o preço fica bom,
com cupom ou sem.

**Painel:** https://hugovfa2017.github.io/passagens-monitor/ps5/

## O que ele acompanha

Três modelos, cada um com o melhor preço **sem cupom** e **com cupom**:

- **PS5 com leitor de disco** (Slim ou Standard, mídia física)
- **PS5 Digital**
- **PS5 Pro**

Bundles com jogo contam. Usados, acessórios, PlayStation Portal e versões importadas (Oriente Médio, KSA, "International")
ficam de fora.

| Fonte | O que traz |
|---|---|
| KaBuM! | preço à vista no Pix; o selo "CUPOM X" do produto vira preço com cupom |
| Amazon | preço da busca; o selo "Economize R$ X com cupom" vira preço com cupom (às vezes a Amazon barra o robô) |
| Americanas | catálogo da loja, inclusive vendedores parceiros (Magazine Luiza) |
| Zoom | comparador: Magazine Luiza, Fast Shop, Webcontinental, Amazon, KaBuM!... |
| Promobit | ofertas de PS5 postadas pela comunidade, já com o preço final usando o cupom (Mercado Livre, Shopee, AliExpress, Magalu...), e os cupons ativos de cada loja |

Mercado Livre, Magazine Luiza (direto), Casas Bahia, Carrefour e Pelando bloqueiam robôs; as ofertas deles chegam pelo
Promobit e pelo Zoom.

"Com cupom" só conta preço confirmado. Cupons gerais da loja (de games, eletrônicos ou site todo) aparecem no painel como
**"para testar no carrinho"**, com quanto ficaria se valerem, mas não entram na conta.

## Quando chega mensagem

- **Com som:** o melhor preço de um modelo chegou ao alvo, ou é o menor preço já visto (pelo menos R$ 50 abaixo do recorde).
  A mesma oferta no mesmo preço não é repetida por 24 h.
- **Sem som:** um resumo por dia, às 9h, com o melhor preço de cada modelo.

As mensagens vão só para o **primeiro** chat do segredo `TELEGRAM_CHAT_ID` (não para os outros viajantes). Para mandar para
outro(s) chat(s), crie o segredo `TELEGRAM_CHAT_ID_PS5` (Settings → Secrets and variables → Actions), com os ids separados
por vírgula.

## Mudar os alvos

Edite [`config.py`](config.py) direto no GitHub (ícone de lápis) e salve. Ali estão os alvos de cada modelo, quais modelos
podem alertar, a hora do resumo diário e se versões importadas entram.

## Rodar na mão

- No GitHub: Actions → **Monitor PS5** → Run workflow.
- No computador: `python3 ps5/monitor.py` (varredura) ou `python3 ps5/monitor.py --teste` (auto-checagem, sem internet).

Arquivos gerados: `ps5/historico.csv` (melhor preço de cada varredura), `ps5/ultimo.md` (resumo da última),
`ps5/estado.json` (recordes e alertas já mandados) e `docs/ps5/` (painel).
