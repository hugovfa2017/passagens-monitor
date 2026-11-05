"""Sonda: os vendedores brasileiros respondem a um navegador sem tela rodando no GitHub?

Abre a busca GRU -> JDO de 27/12 (2 adultos) em cada vendedor e relata o que voltou.
Não tenta contornar bloqueio nenhum: se o site barrar, só registra.
"""
import re
from playwright.sync_api import sync_playwright

# Rodada 1-2 (05/11/2026): funcionam MaxMilhas, 123milhas, Zupper; bloqueiam Decolar, Skyscanner, Kayak, Kiwi, Expedia,
# Trip.com, LATAM, Gol; Booking tem desafio anti-robô; Smiles exige login.
# Rodada 3: primeiro só a página inicial de cada candidato, para ver quem deixa um navegador do GitHub entrar.
ALVOS = {
    'CVC': 'https://www.cvc.com.br/passagens/',
    'eDestinos': 'https://www.edestinos.com.br/',
    'Gotogate': 'https://br.gotogate.com/',
    'Mytrip': 'https://br.mytrip.com/',
    'kiss&fly': 'https://www.kissandfly.com.br/',
    'Passagens Promo': 'https://www.passagenspromo.com.br/',
    'Viajala': 'https://viajala.com.br/',
    'ViajaNet': 'https://www.viajanet.com.br/',
    'Voopter': 'https://www.voopter.com.br/',
    'Azul': 'https://www.voeazul.com.br/br/pt/home',
    'Momondo': 'https://www.momondo.com.br/',
    'Hurb': 'https://www.hurb.com/br/',
    'Submarino Viagens': 'https://www.submarinoviagens.com.br/',
}
BLOQUEIO = r'captcha|datadome|access denied|acesso negado|forbidden|blocked|bloquead|verify you are|verifique|robot|robô|cloudflare|press & hold|pressione e segure|humano'
VENDEDORES = r'maxmilhas|123milhas|decolar|zupper|gol|latam|azul'

with sync_playwright() as p:
    nav = p.chromium.launch(headless=True)
    for nome, url in ALVOS.items():
        pg = nav.new_page(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
        try:
            resp = pg.goto(url, wait_until='domcontentloaded', timeout=60000)
            pg.wait_for_timeout(8000)
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
