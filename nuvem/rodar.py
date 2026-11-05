"""Varredura completa no GitHub Actions: Google Voos (preço das companhias, confirmado na tela de compra)
+ busca direta na MaxMilhas e na 123milhas (que o Google não mostra para servidores fora do Brasil)
(MaxMilhas nos três aeroportos do Nordeste; 123milhas, uma busca por vez, no mais barato de cada data).

Uso: python nuvem/rodar.py          (roda tudo e registra)
     python nuvem/rodar.py --teste  (auto-checagem da junção, sem navegador)
"""
import asyncio, json, os, re, sys
from datetime import date

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'nuvem'))
import registrar  # noqa: E402
import vendedores  # noqa: E402

GOOGLE = 'https://www.google.com/travel/flights?hl=pt-BR&gl=BR&curr=BRL'
NORDESTE = ('NAT', 'FOR', 'JDO')   # na MaxMilhas, cada data é buscada nos três
BLOQUEIO = r'unusual traffic|tráfego incomum|captcha|not a robot|Antes de continuar|Before you continue'


def mesmo_voo(x, t):
    return x['cia'] == t['cia'] and x['saida'] == t['saida'] and f"{x['orig']}-{x['dest']}" == t['rota']


def juntar(scan, ofertas):
    """Põe as ofertas dos vendedores em cada data, fica com a mais barata e refaz as combinações.
    ofertas: {('ida'|'volta', 'AAAA-MM-DD'): [oferta de vendedores.buscar, ...]}"""
    cfg = scan.get('cfg') or {'minDias': 27, 'maxDias': 36, 'natalAte': '2026-12-24'}
    vend_google = scan.get('vend', {})
    trechos, vend = [], {}
    for t in scan['trechos']:
        chave = (t['s'], t['d'])
        lista = ofertas.get(chave, [])
        melhor = min(lista, key=lambda x: x['pp'], default=None)
        novo = dict(t)
        if melhor and melhor['pp'] < t['pp']:
            igual = mesmo_voo(melhor, t)
            novo.update({'pp': melhor['pp'], 'vend': melhor['vend'], 'cia': melhor['cia'], 'rota': f"{melhor['orig']}-{melhor['dest']}",
                         'saida': melhor['saida'], 'chegada': melhor['chegada'], 'tipo': melhor['tipo'], 'link': melhor['link'],
                         'ppCia': t.get('ppCia') if igual else None,
                         'voos': t.get('voos') if igual else (f"{melhor['orig']}>{melhor['dest']} {t['d']} {melhor['voo'][0]} {melhor['voo'][1]}"
                                                              if melhor.get('voo') and melhor['direto'] else '')})
        # vendedores do voo que ficou: os que o Google mostrou (se o voo é o mesmo) + MaxMilhas/123milhas no mesmo voo
        ks = f"{t['s']} {t['d']}"
        base = vend_google.get(ks) or ([[f"CIA {t['cia']}", t['ppCia']]] if t.get('ppCia') else [])
        lista_v = [list(v) for v in base] if mesmo_voo({**novo, 'orig': novo['rota'][:3], 'dest': novo['rota'][4:]}, t) else []
        lista_v += [[x['vend'], x['pp']] for x in lista if mesmo_voo(x, novo)]
        menor = {}
        for nome, pp in lista_v:
            menor[nome] = min(pp, menor.get(nome, pp))
        vend[ks] = sorted(([n, p] for n, p in menor.items()), key=lambda x: x[1])[:10]
        trechos.append(novo)
    idas = [t for t in trechos if t['s'] == 'ida']
    voltas = [t for t in trechos if t['s'] == 'volta']
    combos = []
    for i in idas:
        for v in voltas:
            dias = (date.fromisoformat(v['d']) - date.fromisoformat(i['d'])).days
            if cfg['minDias'] <= dias <= cfg['maxDias']:
                combos.append({'ida': i['d'], 'volta': v['d'], 'dias': dias, 'pp': i['pp'] + v['pp'], 'natal': i['d'] <= cfg['natalAte']})
    combos.sort(key=lambda c: c['pp'])
    sel = combos[:5]
    natal = next((c for c in combos if c['natal']), None)
    if natal and natal not in sel:
        sel.append(natal)
    return {**scan, 'trechos': trechos, 'vend': vend, 'combos': sel,
            'status': 'ok' if sel else 'erro', 'erro': None if sel else 'nenhuma combinação dentro da estadia'}


async def varrer():
    from playwright.async_api import async_playwright
    codigo = open(os.path.join(RAIZ, 'varredura.js'), encoding='utf-8').read()
    async with async_playwright() as p:
        nav = await p.chromium.launch(headless=True)
        ctx = await nav.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
        pg = await ctx.new_page()
        await pg.goto(GOOGLE, wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(3000)
        inicio = (await pg.inner_text('body'))[:3000]
        # não tenta contornar verificação: se aparecer, registra e para
        if re.search(BLOQUEIO, inicio, re.I):
            await nav.close()
            return {'status': 'erro', 'erro': 'Google pediu verificação: ' + inicio[:200].replace('\n', ' | ')}
        await pg.evaluate('() => {\n' + codigo + '\n}')
        for _ in range(60):
            if await pg.evaluate('() => window.__scan && window.__scan.status') in ('ok', 'erro'):
                break
            await asyncio.sleep(10)
        scan = json.loads(await pg.evaluate('() => window.__json || JSON.stringify(window.__scan || {status: "erro", erro: "varredura não terminou"})'))
        await pg.close()
        print('GOOGLE:', scan.get('status'), scan.get('erro') or '', '| datas:', len(scan.get('trechos', [])))
        if scan.get('status') == 'ok':
            pax, ofertas = scan['pax'], {}
            # 1) MaxMilhas: cada data nos três aeroportos, várias buscas juntas
            sem = asyncio.Semaphore(6)
            pedidos = [((t['s'], t['d']), vendedores.buscar(nav, 'MaxMilhas', t['s'], t['d'], ne, pax, sem))
                       for t in scan['trechos'] for ne in NORDESTE]
            res = await asyncio.gather(*(c for _, c in pedidos))
            for (chave, _), r in zip(pedidos, res):
                ofertas.setdefault(chave, []).extend(r)
            print(f'MAXMILHAS: {sum(1 for r in res if r)} de {len(res)} buscas com voo')
            # 2) 123milhas: devolve vazio se recebe muitas buscas juntas; então uma por vez,
            #    só no aeroporto mais barato de cada data até aqui (ela costuma vender o mesmo voo mais barato no Pix)
            achou = 0
            um = asyncio.Semaphore(1)
            for t in scan['trechos']:
                chave = (t['s'], t['d'])
                rota = min(ofertas.get(chave, []) + [{'pp': t['pp'], 'orig': t['rota'][:3], 'dest': t['rota'][4:]}], key=lambda x: x['pp'])
                r = await vendedores.buscar(nav, '123milhas', t['s'], t['d'], rota['dest'] if t['s'] == 'ida' else rota['orig'], pax, um)
                ofertas.setdefault(chave, []).extend(r)
                achou += bool(r)
                await asyncio.sleep(2)
            print(f'123MILHAS: {achou} de {len(scan["trechos"])} buscas com voo')
            scan = juntar(scan, ofertas)
        await nav.close()
    return scan


def _teste():
    t_ida = {'s': 'ida', 'd': '2026-12-27', 'pp': 927, 'vend': 'CIA Gol', 'cia': 'Gol', 'rota': 'GRU-JDO', 'saida': '22:10',
             'chegada': '01:00', 'tipo': 'direto', 'ppCia': 927, 'voos': 'GRU>JDO 2026-12-27 G3 1580'}
    t_volta = {'s': 'volta', 'd': '2027-01-23', 'pp': 718, 'vend': 'CIA LATAM', 'cia': 'LATAM', 'rota': 'JDO-GRU', 'saida': '03:20',
               'chegada': '06:25', 'tipo': 'direto', 'ppCia': 718, 'voos': 'JDO>GRU 2027-01-23 LA 3000'}
    scan = {'status': 'ok', 'pax': 2, 'cfg': {'minDias': 27, 'maxDias': 36, 'natalAte': '2026-12-24'},
            'trechos': [t_ida, t_volta], 'vend': {'ida 2026-12-27': [['CIA Gol', 927], ['Booking.com', 965]]}}
    of = {('ida', '2026-12-27'): [
        {'vend': '123milhas', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'JDO', 'saida': '22:10', 'chegada': '01:00', 'direto': True,
         'tipo': 'direto', 'voo': ('G3', '1580'), 'pp': 857, 'link': 'https://123milhas.com/x'},
        {'vend': 'MaxMilhas', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'JDO', 'saida': '22:10', 'chegada': '01:00', 'direto': True,
         'tipo': 'direto', 'voo': None, 'pp': 894, 'link': 'https://maxmilhas.com.br/x'}]}
    r = juntar(scan, of)
    ida = [t for t in r['trechos'] if t['s'] == 'ida'][0]
    assert ida['pp'] == 857 and ida['vend'] == '123milhas' and ida['link'] == 'https://123milhas.com/x' and ida['ppCia'] == 927, ida
    assert r['vend']['ida 2026-12-27'] == [['123milhas', 857], ['MaxMilhas', 894], ['CIA Gol', 927], ['Booking.com', 965]], r['vend']
    assert r['combos'][0] == {'ida': '2026-12-27', 'volta': '2027-01-23', 'dias': 27, 'pp': 857 + 718, 'natal': False}, r['combos']
    volta = [t for t in r['trechos'] if t['s'] == 'volta'][0]
    assert volta == t_volta and r['vend']['volta 2027-01-23'] == [['CIA LATAM', 718]]
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
        sys.exit(0)
    scan = asyncio.run(varrer())
    json.dump(scan, open(os.path.join(RAIZ, 'ultima.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('STATUS:', scan.get('status'), scan.get('erro') or '', '| combinações:', len(scan.get('combos', [])))
    for linha in registrar.registrar(scan):
        print(linha)
    sys.exit(0 if scan.get('status') == 'ok' else 1)
