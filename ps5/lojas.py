"""Só dados: lojas, canais do Telegram e sites de leilão que o buscador lê. Acrescentar um é acrescentar uma linha.

Depois de mexer aqui, rode `python3 ps5/sondar.py` para ver quem responde (testa também as CANDIDATAS).
"""

# Lojas lidas por um leitor genérico.
#   tipo 'vtex'   -> catálogo público da plataforma VTEX (base/api/catalog_system/pub/products/search)
#   tipo 'jsonld' -> página de busca com dados schema.org (Product/Offer) embutidos; 'urls' são as páginas
LOJAS = [
    dict(nome='Americanas', tipo='vtex', base='https://www.americanas.com.br'),
    dict(nome='Fast Shop', tipo='vtex', base='https://site.fastshop.com.br'),
    dict(nome='Webcontinental', tipo='vtex', base='https://www.webcontinental.com.br'),
    dict(nome='Bemol', tipo='vtex', base='https://www.bemol.com.br', obs='entrega só na região Norte'),
]

# Respondem, mas não tinham console de PS5 na sondagem de 20/11/2026 (não rodam; a sondagem mostra se isso mudar).
CANDIDATAS = [
    dict(nome="Sam's Club", tipo='vtex', base='https://www.samsclub.com.br', obs="só para sócios Sam's Club"),
    dict(nome='iByte', tipo='vtex', base='https://www.ibyte.com.br'),
    dict(nome='Lojas MM', tipo='vtex', base='https://www.lojasmm.com'),
    dict(nome='Lebes', tipo='vtex', base='https://www.lebes.com.br'),
    dict(nome='Berlanda', tipo='vtex', base='https://www.berlanda.com.br'),
    dict(nome='Multisom', tipo='vtex', base='https://www.multisom.com.br'),
    dict(nome='Schumann', tipo='vtex', base='https://www.schumann.com.br'),
    dict(nome='Ramsons', tipo='vtex', base='https://www.ramsons.com.br'),
    dict(nome='Havan', tipo='jsonld', urls=['https://www.havan.com.br/catalogsearch/result/?q=playstation+5']),
    dict(nome='Novo Mundo', tipo='jsonld', urls=['https://www.novomundo.com.br/busca?q=playstation+5']),
]

# Barraram o robô ou não abriram em 20/11/2026 (ficam de fora; não tentamos contornar):
# Mercado Livre, Magazine Luiza, Casas Bahia, Ponto, Extra, Carrefour, Shopee, TikTok Shop, Gazin, Casa & Vídeo, Pichau,
# Terabyte, Trocafy, Saldão da Informática, Hardmob (fórum), Pelando (site), Google Shopping (exige navegador),
# Zema, Taqi, Koerich, Girafa, Submarino. As ofertas dessas lojas chegam pelos grupos e pelo Promobit.

# Canais públicos do Telegram (prévia em t.me/s/<canal>, com busca por "PS5"). 'origem' agrupa canais da mesma rede:
# dois canais da mesma origem contam como uma confirmação só.
CANAIS = [
    dict(canal='jogobarato', nome='Jogo Barato'),
    dict(canal='meups4', nome='MeuPlayStation'),
    dict(canal='ctofertasgames', nome='Canaltech Games', origem='canaltech'),
    dict(canal='CanaltechOfertas', nome='Canaltech Ofertas', origem='canaltech'),
    dict(canal='achadosdotb', nome='Achados do Tecnoblog'),
    dict(canal='cupons_desconto', nome='Promobit (Telegram)', origem='promobit'),
    dict(canal='pelandobr', nome='Pelando'),
    dict(canal='pechinchou', nome='Pechinchou'),
    dict(canal='xetdaspromocoes', nome='Xet das Promoções'),
    dict(canal='promotop', nome='PromoTop'),
    dict(canal='nerdofertas', nome='Nerd Ofertas', origem='nerdofertas'),
    dict(canal='superofertasbr', nome='Super Ofertas BR', origem='nerdofertas'),
    dict(canal='nerdcupons', nome='Nerd Cupons', origem='nerdofertas'),
    dict(canal='garimposdodepinho', nome='Garimpos do De Pinho'),
    dict(canal='cjpromos', nome='CJ Promoções'),
    dict(canal='hardmob_promo', nome='Hardmob Promo', origem='hardmob'),
    dict(canal='OfertasDoDia', nome='Ofertas do Dia', origem='hardmob'),
    dict(canal='ofertasdogui', nome='Ofertas do Gui'),
    dict(canal='TudoPromo', nome='TudoCelular'),
    dict(canal='pichauofertas', nome='Pichau'),
    dict(canal='cuponomia', nome='Cuponomia (Telegram)'),
    dict(canal='reytergames', nome='Reyter Games'),
    dict(canal='promobyteacheips5', nome='Promobyte Achei PS5'),     # só PS5; avisa "voltou" e "cupom esgotando" (28/11/2026)
]
BUSCAS_TELEGRAM = ('PS5', 'PlayStation 5')

# Leilões de logística reversa na plataforma Leiloar (mesma busca de lotes nos três sites: /bens/pesquisaAvancada).
# O leitor procura lotes que citem console de PS5 e guarda descrição, lance, encerramento e link.
LEILOES = [
    dict(nome='Lance360', base='https://www.lance360.com.br'),
    dict(nome='HISA Leilões', base='https://www.hisaleiloes.com.br'),
    dict(nome='Bolsa de Leilões', base='https://www.bolsadeleiloes.com.br'),
]
BUSCAS_LEILAO = ('playstation', 'ps5')
# Não dá para ler sem navegador (20/11/2026): portal de leilões da Receita Federal, Superbid e Kwara (páginas montadas
# por JavaScript); Nosso Leilão e Tudo Leilão barram o robô.
