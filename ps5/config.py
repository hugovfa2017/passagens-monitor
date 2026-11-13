"""Ajustes do monitor do PS5. Dá para editar direto no GitHub (lápis no canto do arquivo) e salvar: vale na próxima varredura."""

# Alerta com som no Telegram quando o melhor preço (com ou sem cupom) de um modelo ficar igual ou abaixo destes valores.
# Referência em 13/11/2026: com leitor ~R$4.300-4.500 nas lojas;
# Digital ~R$4.130 nas lojas e ~R$3.950 com cupom; Pro ~R$7.400.
ALVOS = {
    'leitor': 3799,    # PS5 com leitor de disco (mídia física), Slim ou Standard
    'digital': 3499,   # PS5 Edição Digital
    'pro': 6299,       # PS5 Pro
}

# Também avisa (com som) quando aparecer o menor preço já visto de um modelo, se cair pelo menos isto abaixo do recorde.
QUEDA_MINIMA = 50

# Modelos que podem disparar alerta (tire da lista o que não interessa; o painel continua mostrando todos).
MODELOS_COM_ALERTA = ['leitor', 'digital', 'pro']

# Resumo diário, sem som, com o melhor preço de cada modelo (hora de Brasília). None desliga.
RESUMO_DIARIO_HORA = 9

# Versões importadas (Oriente Médio, "International", KSA...) não têm garantia da Sony Brasil: ficam de fora.
INCLUIR_IMPORTADOS = False

# Ofertas postadas no Promobit valem por quantas horas depois de postadas (cupom costuma acabar rápido).
HORAS_OFERTA_PROMOBIT = 36
