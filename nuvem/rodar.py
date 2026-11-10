"""Varredura completa no GitHub Actions: Google Voos (preço das companhias, confirmado na tela de compra)
+ busca direta na MaxMilhas, na 123milhas e na Zupper (que o Google não mostra para servidores fora do Brasil).

Ida dos extras: SP -> Natal nas datas perto do voo já comprado, para 1, 2 e 3 adultos (o preço por pessoa muda
com a quantidade de lugares). Volta dos dois que já têm a ida: Nordeste -> SP, como antes
(MaxMilhas nos três aeroportos; 123milhas e Zupper, uma busca por vez, no mais barato de cada data).

Uso: python nuvem/rodar.py          (roda tudo e registra)
     python nuvem/rodar.py --teste  (auto-checagem da junção, sem navegador)
"""
import asyncio, json, os, re, sys
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'nuvem'))
import registrar  # noqa: E402
import vendedores  # noqa: E402

GOOGLE = 'https://www.google.com/travel/flights?hl=pt-BR&gl=BR&curr=BRL'
NORDESTE = ('NAT', 'FOR', 'JDO')   # na MaxMilhas, cada data é buscada nos três
BLOQUEIO = r'unusual traffic|tráfego incomum|captcha|not a robot|Antes de continuar|Before you continue'


def cfg_js(codigo):
    """Datas, passageiros e o voo comprado, lidos do CFG de varredura.js (fonte única), para não depender do Google
    devolver todas as datas."""
    lista = lambda k: re.findall(r"'([\w-]+)'", re.search(r'\b' + k + r':\s*\[([^\]]*)\]', codigo)[1])
    num = lambda k: int(re.search(r'\b' + k + r':\s*(\d+)', codigo)[1])
    d, saida, cia, rota, *voo = re.search(r"alvo:\s*'([^']+)'", codigo)[1].split(' ')
    return {'ida': lista('ida'), 'volta': lista('volta'), 'pax': num('pax'), 'paxIda': num('paxIda'), 'idaDestinos': lista('idaDestinos'),
            'alvo': {'d': d, 'saida': saida, 'cia': cia, 'orig': rota[:3], 'dest': rota[4:], 'voo': ' '.join(voo)}}


def completar(scan, cfg):
    """Garante uma linha para cada data de volta. Data que o Google não validou entra vazia (sem preço) e só
    fica se algum vendedor tiver voo nela."""
    tem = {t['d'] for t in scan.get('trechos', [])}
    vazias = [{'s': 'volta', 'd': d, 'pp': SEM_PRECO, 'vend': '', 'cia': '', 'rota': 'JDO-SAO', 'saida': '', 'chegada': '', 'tipo': '',
               'ppCia': None, 'voos': '', 'vazio': True} for d in cfg['volta'] if d not in tem]
    return {**scan, 'pax': cfg['pax'], 'paxIda': cfg['paxIda'], 'alvo': cfg['alvo'], 'idas': scan.get('idas', []),
            'trechos': scan.get('trechos', []) + vazias}, len(vazias)


SEM_PRECO = 10 ** 9


def mesmo_voo(x, t):
    return x['cia'] == t['cia'] and x['saida'] == t['saida'] and f"{x['orig']}-{x['dest']}" == t['rota']


def taxas_mala(ofertas):
    """Quanto custa a mala de 23 kg por companhia (por pessoa e trecho), medido nas tarifas com mala da Zupper
    desta varredura: {cia: valor}, e a mediana geral para companhia sem medida."""
    por_cia = {}
    for lista in ofertas.values():
        for x in lista:
            if x.get('mala') is not None:
                por_cia.setdefault(x['cia'], []).append(x['mala'])
    med = lambda v: sorted(v)[len(v) // 2]
    todas = [m for v in por_cia.values() for m in v]
    return {c: med(v) for c, v in por_cia.items()}, (med(todas) if todas else None)


def melhor_com_mala(t, lista, taxa, geral):
    """Melhor opção de um trecho com 1 mala de 23 kg por pessoa: tarifa com mala real (Zupper) ou
    preço sem mala + mala da companhia (estimada)."""
    opcoes = []
    google = [] if t.get('vazio') else [{**t, 'orig': t['rota'][:3], 'dest': t['rota'][4:], 'link': t.get('link'), 'google': True}]
    for x in google + lista:
        custo = x['mala'] if x.get('mala') is not None else taxa.get(x['cia'], geral)
        if custo is not None:
            opcoes.append((x['pp'] + custo, x.get('mala') is None, x))
    if not opcoes:
        return None
    pp, estimado, x = min(opcoes, key=lambda o: o[0])
    return {'pp': pp, 'estimado': estimado, 'vend': x['vend'], 'cia': x['cia'], 'rota': f"{x['orig']}-{x['dest']}", 'saida': x['saida'],
            'chegada': x['chegada'], 'tipo': x['tipo'], 'link': x.get('link'), 'voos': t.get('voos') if x.get('google') else ''}


def juntar(scan, ofertas):
    """Volta: põe as ofertas dos vendedores em cada data e fica com a mais barata (sem e com mala).
    ofertas: {('volta', 'AAAA-MM-DD'): [oferta de vendedores.buscar, ...]}"""
    vend_google = scan.get('vend', {})
    taxa, geral = taxas_mala(ofertas)
    trechos, vend = [], {}
    for t in scan['trechos']:
        chave = (t['s'], t['d'])
        lista = ofertas.get(chave, [])
        melhor = min(lista, key=lambda x: x['pp'], default=None)
        if t.get('vazio') and not melhor:
            continue           # nem o Google nem os vendedores acharam voo nesta data
        novo = {k: v for k, v in t.items() if k != 'vazio'}
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
        mala = melhor_com_mala(t, lista, taxa, geral)
        if mala:
            novo['mala'] = mala
        trechos.append(novo)
    return {**scan, 'trechos': trechos, 'vend': vend, 'taxa_mala': {'cias': taxa, 'geral': geral}}


def juntar_ida(ofertas, alvo, taxa, geral):
    """Voos de ida dos extras: cada voo com o melhor preço por pessoa para 1, 2 e 3 adultos, sem e com 1 mala de
    23 kg (tarifa com mala real da Zupper ou mala da companhia somada, estimada). O voo comprado vem primeiro,
    depois os outros do mais barato ao mais caro para o máximo de extras.
    ofertas: {n adultos: [oferta de vendedores.buscar com 'd' (data)]}"""
    voos = {}
    for n, lista in ofertas.items():
        for x in lista:
            v = voos.setdefault((x['d'], x['cia'], x['orig'], x['saida']), {
                **{k: x[k] for k in ('d', 'cia', 'orig', 'dest', 'saida', 'chegada', 'tipo')}, 'voo': '', 'precos': {}})
            v['voo'] = v['voo'] or (' '.join(x['voo']) if x.get('voo') else '')
            p = v['precos'].setdefault(n, {})
            if x['pp'] < p.get('pp', SEM_PRECO):
                p.update(pp=x['pp'], vend=x['vend'], link=x['link'])
            custo = x['mala'] if x.get('mala') is not None else taxa.get(x['cia'], geral)
            if custo is not None and x['pp'] + custo < p.get('mala_pp', SEM_PRECO):
                p.update(mala_pp=x['pp'] + custo, mala_vend=x['vend'], mala_link=x['link'], mala_estimado=x.get('mala') is None)
    n_max = max(ofertas, default=1)
    for v in voos.values():
        v['mesmo'] = (v['d'], v['cia'], v['orig'], v['saida']) == (alvo['d'], alvo['cia'], alvo['orig'], alvo['saida'])
    return sorted(voos.values(), key=lambda v: (not v['mesmo'], v['precos'].get(n_max, {}).get('pp', SEM_PRECO), v['d'], v['saida']))


def ofertas_google_ida(idas, pax):
    """Voos de ida validados no Google (preço para o máximo de extras) no formato das ofertas dos vendedores."""
    return [{'d': t['d'], 'vend': t['vend'], 'cia': t['cia'], 'orig': t['rota'][:3], 'dest': t['rota'][4:], 'saida': t['saida'],
             'chegada': t['chegada'], 'tipo': t['tipo'], 'pp': t['pp'], 'voo': tuple(t['voos'].split(' ')[2:4]) if t.get('voos') else None,
             'link': registrar.link_compra('ida', t['d'], t.get('voos'), pax)} for t in idas]


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
        # não tenta contornar verificação: se aparecer, registra e segue só com os vendedores
        if re.search(BLOQUEIO, inicio, re.I):
            scan = {'status': 'erro', 'erro': 'Google pediu verificação: ' + inicio[:200].replace('\n', ' | ')}
        else:
            await pg.evaluate('() => {\n' + codigo + '\n}')
            for _ in range(60):
                if await pg.evaluate('() => window.__scan && window.__scan.status') in ('ok', 'erro'):
                    break
                await asyncio.sleep(10)
            scan = json.loads(await pg.evaluate('() => window.__json || JSON.stringify(window.__scan || {status: "erro", erro: "varredura não terminou"})'))
        await pg.close()
        cfg = cfg_js(codigo)
        scan, faltaram = completar(scan, cfg)
        print('GOOGLE:', scan.get('status'), scan.get('erro') or '', '| voltas validadas:', len(scan['trechos']) - faltaram,
              '| idas validadas:', len(scan['idas']))
        pax, ofertas, ida = scan['pax'], {}, {}   # ofertas: volta por data; ida: {n adultos: [ofertas]}
        com_data = lambda r, d: [{**x, 'd': d} for x in r]
        # 1) MaxMilhas, várias buscas juntas: ida para Natal com 1, 2 e 3 adultos; volta nos três aeroportos
        sem = asyncio.Semaphore(6)
        pedidos = [((n, d), vendedores.buscar(nav, 'MaxMilhas', 'ida', d, ne, n, sem))
                   for d in cfg['ida'] for ne in cfg['idaDestinos'] for n in range(1, cfg['paxIda'] + 1)]
        pedidos += [(('volta', t['d']), vendedores.buscar(nav, 'MaxMilhas', 'volta', t['d'], ne, pax, sem))
                    for t in scan['trechos'] for ne in NORDESTE]
        res = await asyncio.gather(*(c for _, c in pedidos))
        for ((a, d), _), r in zip(pedidos, res):
            (ofertas.setdefault((a, d), []) if a == 'volta' else ida.setdefault(a, [])).extend(com_data(r, d))
        print(f'MAXMILHAS: {sum(1 for r in res if r)} de {len(res)} buscas com voo')
        # 2) 123milhas e Zupper: a 123milhas devolve vazio se recebe muitas buscas juntas; então cada uma faz
        #    uma busca por vez (as duas em paralelo); a volta só no aeroporto mais barato de cada data até aqui
        fila = [('ida', d, ne, n) for d in cfg['ida'] for ne in cfg['idaDestinos'] for n in range(1, cfg['paxIda'] + 1)]
        for t in scan['trechos']:
            rota = min(ofertas.get(('volta', t['d']), []) + [{'pp': t['pp'], 'orig': t['rota'][:3]}], key=lambda x: x['pp'])
            fila.append(('volta', t['d'], rota['orig'], pax))

        async def uma_por_vez(vendedor):
            achou, um = 0, asyncio.Semaphore(1)
            for s, d, ne, n in fila:
                r = com_data(await vendedores.buscar(nav, vendedor, s, d, ne, n, um), d)
                (ofertas.setdefault(('volta', d), []) if s == 'volta' else ida.setdefault(n, [])).extend(r)
                achou += bool(r)
                await asyncio.sleep(2)
            print(f'{vendedor.upper()}: {achou} de {len(fila)} buscas com voo')

        await asyncio.gather(uma_por_vez('123milhas'), uma_por_vez('Zupper'))
        ida.setdefault(cfg['paxIda'], []).extend(ofertas_google_ida(scan['idas'], cfg['paxIda']))
        # as ofertas da ida entram só para medir a mala de cada companhia (juntar usa as chaves da volta)
        scan = juntar(scan, {**ofertas, **{('ida', n): v for n, v in ida.items()}})
        tx = scan['taxa_mala']
        scan['voos_ida'] = juntar_ida(ida, cfg['alvo'], tx['cias'], tx['geral'])
        mesmo = next((v for v in scan['voos_ida'] if v['mesmo']), None)
        print(f"IDA: {len(scan['voos_ida'])} voos; mesmo voo: " +
              (', '.join(f"{n}: R${p['pp']} {p['vend']}" for n, p in sorted(mesmo['precos'].items())) if mesmo else 'não achado'),
              f"| VOLTA: {len(scan['trechos'])} datas | mala: {tx}")
        achou = bool(scan['voos_ida'] or scan['trechos'])
        scan.update(status='ok' if achou else 'erro', erro=None if achou else (scan.get('erro') or 'nenhum voo encontrado'))
        await nav.close()
    return scan


def _teste():
    t_volta = {'s': 'volta', 'd': '2027-01-23', 'pp': 718, 'vend': 'CIA LATAM', 'cia': 'LATAM', 'rota': 'JDO-GRU', 'saida': '03:20',
               'chegada': '06:25', 'tipo': 'direto', 'ppCia': 718, 'voos': 'JDO>GRU 2027-01-23 LA 3000'}
    scan = {'status': 'ok', 'pax': 2, 'trechos': [t_volta], 'vend': {'volta 2027-01-23': [['CIA LATAM', 718], ['Booking.com', 760]]}}
    v123 = {'vend': '123milhas', 'cia': 'LATAM', 'orig': 'JDO', 'dest': 'GRU', 'saida': '03:20', 'chegada': '06:25', 'direto': True,
            'tipo': 'direto', 'voo': ('LA', '3000'), 'pp': 650, 'link': 'https://123milhas.com/v'}
    r = juntar(scan, {('volta', '2027-01-23'): [v123]})
    v = r['trechos'][0]
    assert (v['pp'], v['vend'], v['ppCia'], v['link']) == (650, '123milhas', 718, 'https://123milhas.com/v') and 'mala' not in v, v
    assert r['vend']['volta 2027-01-23'] == [['123milhas', 650], ['CIA LATAM', 718], ['Booking.com', 760]], r['vend']
    # mala medida na ida (Zupper Gol +119 e LATAM +150) vale para a volta: 123milhas LATAM 650 + 150 estimado
    zi = {'d': '2026-12-22', 'vend': 'Zupper', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'NAT', 'saida': '14:05', 'chegada': '17:25', 'direto': True,
          'tipo': 'direto', 'voo': None, 'pp': 900, 'mala': 119, 'link': 'https://www.zupper.com.br/1'}
    zl = {**zi, 'cia': 'LATAM', 'saida': '13:35', 'chegada': '16:55', 'pp': 1000, 'mala': 150}
    r2 = juntar(scan, {('volta', '2027-01-23'): [v123], ('ida', 1): [zi, zl]})
    assert r2['trechos'][0]['mala']['pp'] == 800 and r2['trechos'][0]['mala']['estimado'], r2['trechos'][0]
    assert r2['taxa_mala'] == {'cias': {'Gol': 119, 'LATAM': 150}, 'geral': 150}, r2['taxa_mala']
    # ida dos extras: o voo comprado vem primeiro, com preço por pessoa para 1, 2 e 3; mala real (Zupper) ou estimada
    alvo = {'d': '2026-12-22', 'saida': '14:05', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'NAT', 'voo': 'G3 1678'}
    m1 = {**zi, 'vend': '123milhas', 'voo': ('G3', '1678'), 'pp': 480, 'mala': None, 'link': 'https://123milhas.com/1'}
    m3 = {**m1, 'pp': 1283, 'link': 'https://123milhas.com/3'}
    perto = {**zl, 'd': '2026-12-21', 'vend': 'MaxMilhas', 'pp': 700, 'mala': None, 'link': 'https://maxmilhas/3'}
    vi = juntar_ida({1: [zi, m1], 3: [m3, {**zi, 'pp': 1300, 'link': 'https://www.zupper.com.br/3'}, perto]}, alvo, {'Gol': 119, 'LATAM': 150}, 150)
    assert [(x['d'], x['cia'], x['mesmo']) for x in vi] == [('2026-12-22', 'Gol', True), ('2026-12-21', 'LATAM', False)], vi
    p1, p3 = vi[0]['precos'][1], vi[0]['precos'][3]
    assert (p1['pp'], p1['vend'], p1['mala_pp'], p1['mala_vend'], p1['mala_estimado']) == (480, '123milhas', 599, '123milhas', True), p1
    assert (p3['pp'], p3['link'], p3['mala_pp'], p3['mala_vend'], p3['mala_estimado']) == (1283, 'https://123milhas.com/3', 1402, '123milhas', True), p3
    assert vi[0]['voo'] == 'G3 1678' and 2 not in vi[0]['precos'] and vi[1]['precos'][3]['mala_pp'] == 850, vi
    # Zupper com mala real mais barata que a estimada fica como a opção com mala
    vz = juntar_ida({1: [{**m1, 'pp': 500}, {**zi, 'pp': 520, 'mala': 60}]}, alvo, {'Gol': 119}, 119)[0]['precos'][1]
    assert (vz['pp'], vz['mala_pp'], vz['mala_vend'], vz['mala_estimado']) == (500, 580, 'Zupper', False), vz
    # Google: voo validado vira oferta para o máximo de extras, com link da tela de compra
    g = ofertas_google_ida([{'d': '2026-12-22', 'pp': 1315, 'vend': 'CIA Gol', 'cia': 'Gol', 'rota': 'GRU-NAT', 'saida': '14:05',
                             'chegada': '17:25', 'tipo': 'direto', 'ppCia': 1315, 'voos': 'GRU>NAT 2026-12-22 G3 1678'}], 3)[0]
    assert g['voo'] == ('G3', '1678') and g['link'].startswith('https://www.google.com/travel/flights/booking?tfs=') and g['orig'] == 'GRU', g
    assert juntar_ida({3: [g]}, alvo, {}, None)[0]['mesmo']
    # configuração lida de varredura.js; datas de volta que o Google não validou entram vazias
    cfg = cfg_js(open(os.path.join(RAIZ, 'varredura.js'), encoding='utf-8').read())
    assert cfg['ida'] == ['2026-12-21', '2026-12-22', '2026-12-23'] and cfg['idaDestinos'] == ['NAT'], cfg
    assert (cfg['pax'], cfg['paxIda'], cfg['volta'][0], cfg['volta'][-1]) == (2, 1, '2027-01-18', '2027-01-26'), cfg
    assert cfg['alvo'] == alvo, cfg['alvo']
    s8, faltaram = completar({'status': 'erro', 'erro': 'Google pediu verificação'}, cfg)
    assert faltaram == 9 and s8['pax'] == 2 and s8['paxIda'] == 1 and s8['idas'] == [] and s8['alvo'] == alvo
    r8 = juntar(s8, {('volta', '2027-01-23'): [v123]})
    assert [(t['d'], t['pp']) for t in r8['trechos']] == [('2027-01-23', 650)] and 'vazio' not in r8['trechos'][0], r8['trechos']
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
        sys.exit(0)
    scan = asyncio.run(varrer())
    json.dump(scan, open(os.path.join(RAIZ, 'ultima.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    scan.setdefault('fim', datetime.now(timezone.utc).isoformat())   # o workflow usa para não rodar duas vezes seguidas
    print('STATUS:', scan.get('status'), scan.get('erro') or '', '| voos de ida:', len(scan.get('voos_ida', [])),
          '| datas de volta:', len(scan.get('trechos', [])))
    for linha in registrar.registrar(scan):
        print(linha)
    sys.exit(0 if scan.get('status') == 'ok' else 1)
