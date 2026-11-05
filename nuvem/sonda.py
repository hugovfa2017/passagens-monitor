"""Sonda: os vendedores brasileiros respondem a um navegador sem tela rodando no GitHub?

Abre a busca GRU -> JDO de 27/12 (2 adultos) em cada vendedor e relata o que voltou.
Não tenta contornar bloqueio nenhum: se o site barrar, só registra.
"""
import re
from playwright.sync_api import sync_playwright

# MaxMilhas e 123milhas já funcionam; Decolar, Skyscanner e Kayak bloqueiam (sondados em 05/11/2026).
# Rodada 2: candidatos novos. URL errada aparece no "parou em" (a home do site, por exemplo).
ALVOS = {
    'Zupper': 'https://www.zupper.com.br/resultados?originAirport=GRU&destinationAirport=JDO&departureDate=2026-12-27&rooms[0][adultQty]=2&tripType=oneway',
    'Kiwi': 'https://www.kiwi.com/deep?from=GRU&to=JDO&departure=27-12-2026&return=no-return&adults=2&currency=BRL&lang=pt',
    'Booking (Gotogate/Mytrip)': 'https://flights.booking.com/flights/GRU.AIRPORT-JDO.AIRPORT/?type=ONEWAY&adults=2&cabinClass=ECONOMY&from=GRU.AIRPORT&to=JDO.AIRPORT&depart=2026-12-27&sort=CHEAPEST&locale=pt-br&currency=BRL',
    'Expedia': 'https://www.expedia.com.br/Flights-Search?trip=oneway&leg1=from%3AGRU%2Cto%3AJDO%2Cdeparture%3A27%2F12%2F2026TANYT&passengers=adults%3A2&options=cabinclass%3Aeconomy&mode=search',
    'Trip.com': 'https://br.trip.com/flights/showfarefirst?dcity=sao&acity=jdo&ddate=2026-12-27&triptype=ow&class=y&quantity=2&locale=pt-BR&curr=BRL',
    'ViajaNet': 'https://www.viajanet.com.br/busca/passagens/voos#/GRU/JDO/OW/N/27-12-2026/-/-/2/0/0/-/-/-/-',
    'Voopter': 'https://www.voopter.com.br/passagens-aereas/GRU/JDO/2026-12-27?adultos=2',
    'LATAM': 'https://www.latamairlines.com/br/pt/oferta-voos?origin=GRU&outbound=2026-12-27T12%3A00%3A00.000Z&destination=JDO&inbound=null&adt=2&chd=0&inf=0&trip=OW&cabin=Economy&redemption=false&sort=RECOMMENDED',
    'Gol': 'https://b2c.voegol.com.br/compra/busca-parceiros?pv=br&tipo=DF&de=GRU&para=JDO&ida=27-12-2026&ADT=2&CHD=0&INF=0',
    'Smiles (milhas)': 'https://www.smiles.com.br/mfe/emissao-passagem/?adults=2&cabin=ALL&children=0&departureDate=1798329600000&infants=0&searchType=g3&segments=1&tripType=2&originAirport=GRU&destinationAirport=JDO',
}
BLOQUEIO = r'captcha|datadome|access denied|acesso negado|forbidden|blocked|bloquead|verify you are|verifique|robot|robô|cloudflare|press & hold|pressione e segure|humano'
VENDEDORES = r'maxmilhas|123milhas|decolar|zupper|gol|latam|azul'

with sync_playwright() as p:
    nav = p.chromium.launch(headless=True)
    for nome, url in ALVOS.items():
        pg = nav.new_page(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
        try:
            resp = pg.goto(url, wait_until='domcontentloaded', timeout=60000)
            pg.wait_for_timeout(20000)
            txt = pg.inner_text('body')
            precos = re.findall(r'R\$\s?[\d.]+(?:,\d\d)?', txt)
            print(f'== {nome}: HTTP {resp.status if resp else "?"} | título: {pg.title()[:80]}')
            print(f'   preços na página: {len(precos)} {precos[:6]}')
            print(f'   sinal de bloqueio: {bool(re.search(BLOQUEIO, txt[:3000], re.I))}')
            print(f'   vendedores citados: {sorted(set(m.lower() for m in re.findall(VENDEDORES, txt, re.I)))}')
            print('   parou em:', pg.url.split('?')[0][:90])
            print('   início:', txt[:250].replace('\n', ' | '))
        except Exception as e:
            print(f'== {nome}: ERRO {e}')
        finally:
            pg.close()
    nav.close()
