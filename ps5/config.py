"""Ajustes do buscador do PS5. Dá para editar direto no GitHub (lápis no canto do arquivo) e salvar: vale na próxima rodada."""
from datetime import date

# Alerta com som no Telegram quando o melhor preço confirmado de um modelo ficar igual ou abaixo destes valores.
# Referência em 20/11/2026: Digital ~R$3.900 (já houve R$3.578 na Shopee e R$3.759 no Mercado Livre com cupom);
# com leitor ~R$4.275 (já houve R$3.644 na Vivo); Pro ~R$7.700.
ALVOS = {
    'leitor': 3799,    # PS5 com leitor de disco (mídia física), Slim ou Standard
    'digital': 3499,   # PS5 Edição Digital
    'pro': 6299,       # PS5 Pro
}

# Também avisa (com som) quando aparecer o menor preço já visto de um modelo, se cair pelo menos isto abaixo do recorde.
QUEDA_MINIMA = 50

# Cupom que economiza menos que isto não vale o trabalho: o melhor preço passa a ser a oferta sem cupom.
ECONOMIA_MINIMA_CUPOM = 10

# Modelos que podem disparar alerta (tire da lista o que não interessa; o painel continua mostrando todos).
MODELOS_COM_ALERTA = ['leitor', 'digital']   # Pro fora do orçamento (20/11/2026)

# Modo caçador (Black Friday): até esta data (exclusiva), oferta de grupo ainda não confirmada que esteja no alvo toca com
# som, e o workflow roda de 10 em 10 minutos (guarda de 7 min). Depois volta sozinho: sem som e uma rodada a cada 30 min.
CACADOR_ATE = date(2026, 12, 1)

# Resumo diário, sem som, com o melhor preço de cada modelo (hora de Brasília). None desliga.
RESUMO_DIARIO_HORA = 9

# Seções à parte, nunca misturadas ao melhor preço de console novo. True liga, False desliga.
SECOES = {
    'openbox': True,      # open box / reembalado / recondicionado vendido por loja
    'importados': True,   # versões de fora (Oriente Médio, internacional): sem garantia da Sony Brasil
    'leiloes': True,      # lotes de leilão de logística reversa que citam PS5
    'usados': False,      # usado de pessoa física
}

# Por quantas horas vale uma oferta postada (cupom costuma acabar rápido).
HORAS_OFERTA_POSTADA = 36   # Promobit e Gatry (comunidades com moderação)
HORAS_OFERTA_GRUPO = 12     # canais do Telegram
HORAS_ALERTA_POSTADA = 6    # oferta postada há mais que isso aparece no painel, mas não toca alerta

# Oferta de grupo abaixo destes preços é boa demais para acreditar sem confirmação: fica como "não confirmada".
PISO = {'leitor': 3000, 'digital': 2800, 'pro': 5200}

# "Preço possível" (preço da loja menos um cupom geral ainda não testado): avisa, sem som, quando ficar este tanto
# abaixo do alvo ou do melhor preço confirmado.
MARGEM_POSSIVEL = 100

# Tempo máximo de uma rodada, em segundos. Fonte que não terminar a tempo fica para a próxima.
ORCAMENTO_S = 150
