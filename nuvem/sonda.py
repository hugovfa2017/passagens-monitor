"""Sonda: os vendedores brasileiros respondem a um navegador sem tela rodando no GitHub?

Abre a busca GRU -> JDO de 27/12 (2 adultos) em cada vendedor e relata o que voltou.
Não tenta contornar bloqueio nenhum: se o site barrar, só registra.
"""
import re
from playwright.sync_api import sync_playwright

ALVOS = {
    'MaxMilhas': 'https://www.maxmilhas.com.br/busca-passagens-aereas/OW/GRU/JDO/2026-12-27/2/0/0/EC',
    '123milhas': 'https://123milhas.com/v2/busca?de=GRU&para=JDO&adultos=2&criancas=0&bebes=0&ida=27-12-2026&classe=3',
    'Decolar': 'https://www.decolar.com/shop/flights/results/oneway/GRU/JDO/2026-12-27/2/0/0',
    # metabuscadores com o país definido pelo site (.com.br), não pela localização de quem acessa
    'Skyscanner': 'https://www.skyscanner.com.br/transporte/passagens-aereas/gru/jdo/261227/?adultsv2=2&cabinclass=economy&rtn=0',
    'Kayak': 'https://www.kayak.com.br/flights/GRU-JDO/2026-12-27/2adults?sort=price_a',
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
            print('   início:', txt[:250].replace('\n', ' | '))
        except Exception as e:
            print(f'== {nome}: ERRO {e}')
        finally:
            pg.close()
    nav.close()
