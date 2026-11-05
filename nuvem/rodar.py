"""Roda a varredura num Chromium sem tela (GitHub Actions) e registra o resultado.

Usa a mesma varredura.js do Mac, sem o limite de leitura em pedaços do Claude in Chrome.
Uso: python nuvem/rodar.py
"""
import json, os, re, sys, time
from playwright.sync_api import sync_playwright

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import registrar  # noqa: E402

codigo = open(os.path.join(RAIZ, 'varredura.js'), encoding='utf-8').read()

with sync_playwright() as p:
    nav = p.chromium.launch(headless=True)
    pg = nav.new_page(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
    pg.goto('https://www.google.com/travel/flights?hl=pt-BR&gl=BR&curr=BRL', wait_until='domcontentloaded', timeout=60000)
    pg.wait_for_timeout(3000)
    inicio = pg.inner_text('body')[:3000]
    # não tenta contornar verificação do Google: se aparecer, registra e para
    if re.search(r'unusual traffic|tráfego incomum|captcha|not a robot|Antes de continuar|Before you continue', inicio, re.I):
        scan = {'status': 'erro', 'erro': 'Google pediu verificação ou consentimento: ' + inicio[:200].replace('\n', ' | ')}
    else:
        pg.evaluate('() => {\n' + codigo + '\n}')
        prazo = time.time() + 600
        while time.time() < prazo:
            if pg.evaluate('() => window.__scan && window.__scan.status') in ('ok', 'erro'):
                break
            time.sleep(10)
        scan = json.loads(pg.evaluate('() => window.__json || JSON.stringify(window.__scan || {status: "erro", erro: "varredura não terminou"})'))
    nav.close()

json.dump(scan, open(os.path.join(RAIZ, 'ultima.json'), 'w', encoding='utf-8'), ensure_ascii=False)
print('STATUS:', scan.get('status'), scan.get('erro') or '')
print('TRECHOS VALIDADOS:', len(scan.get('trechos', [])), '| COMBINAÇÕES:', len(scan.get('combos', [])))
for linha in registrar.registrar(scan):
    print(linha)
sys.exit(0 if scan.get('status') == 'ok' else 1)
