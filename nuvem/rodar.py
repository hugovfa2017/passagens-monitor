"""Varredura completa no GitHub Actions: Google Voos (preço das companhias, confirmado na tela de compra)
+ busca direta na MaxMilhas, na 123milhas e na Zupper (que o Google não mostra para servidores fora do Brasil)
(MaxMilhas nos três aeroportos do Nordeste; 123milhas e Zupper, uma busca por vez, no mais barato de cada data)
e, nas melhores combinações, ida e volta comprada junta na Zupper.

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
    for x in [{**t, 'orig': t['rota'][:3], 'dest': t['rota'][4:], 'link': t.get('link'), 'google': True}] + lista:
        custo = x['mala'] if x.get('mala') is not None else taxa.get(x['cia'], geral)
        if custo is not None:
            opcoes.append((x['pp'] + custo, x.get('mala') is None, x))
    if not opcoes:
        return None
    pp, estimado, x = min(opcoes, key=lambda o: o[0])
    return {'pp': pp, 'estimado': estimado, 'vend': x['vend'], 'cia': x['cia'], 'rota': f"{x['orig']}-{x['dest']}", 'saida': x['saida'],
            'chegada': x['chegada'], 'tipo': x['tipo'], 'link': x.get('link'), 'voos': t.get('voos') if x.get('google') else ''}


def _selecao(combos, n):
    """As n melhores combinações e, se ficou de fora, a melhor com Natal."""
    combos.sort(key=lambda c: c['pp'])
    sel = combos[:n]
    natal = next((c for c in combos if c['natal']), None)
    return sel + [natal] if natal and natal not in sel else sel


def juntar(scan, ofertas):
    """Põe as ofertas dos vendedores em cada data, fica com a mais barata e refaz as combinações.
    ofertas: {('ida'|'volta', 'AAAA-MM-DD'): [oferta de vendedores.buscar, ...]}"""
    cfg = scan.get('cfg') or {'minDias': 27, 'maxDias': 36, 'natalAte': '2026-12-24'}
    vend_google = scan.get('vend', {})
    taxa, geral = taxas_mala(ofertas)
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
        mala = melhor_com_mala(t, lista, taxa, geral)
        if mala:
            novo['mala'] = mala
        trechos.append(novo)
    idas = [t for t in trechos if t['s'] == 'ida']
    voltas = [t for t in trechos if t['s'] == 'volta']
    combos, combos_mala = [], []
    for i in idas:
        for v in voltas:
            dias = (date.fromisoformat(v['d']) - date.fromisoformat(i['d'])).days
            if cfg['minDias'] <= dias <= cfg['maxDias']:
                c = {'ida': i['d'], 'volta': v['d'], 'dias': dias, 'pp': i['pp'] + v['pp'], 'natal': i['d'] <= cfg['natalAte']}
                combos.append(c)
                if 'mala' in i and 'mala' in v:
                    combos_mala.append({**c, 'pp': i['mala']['pp'] + v['mala']['pp']})
    sel = _selecao(combos, 8)   # as 8 melhores (+ a melhor com Natal) também são buscadas como ida e volta na Zupper
    return {**scan, 'trechos': trechos, 'vend': vend, 'combos': sel, 'combos_mala': _selecao(combos_mala, 3),
            'taxa_mala': {'cias': taxa, 'geral': geral},
            'status': 'ok' if sel else 'erro', 'erro': None if sel else 'nenhuma combinação dentro da estadia'}


def com_pacotes(scan, pacotes):
    """Ida e volta compradas juntas: se sai mais barato que somar os trechos, a combinação passa a ser o pacote.
    pacotes: {(ida, volta): oferta de vendedores.buscar_iv}"""
    tx = scan.get('taxa_mala') or {}
    taxa, geral = tx.get('cias', {}), tx.get('geral')
    for p in pacotes.values():   # com mala: tarifa com mala do pacote (real) ou mala das companhias (estimada)
        custos = [taxa.get(p['pernas'][s]['cia'], geral) for s in ('ida', 'volta')]
        p['pp_mala'], p['mala_estimada'] = ((p['pp'] + p['mala'], False) if p.get('mala') is not None else
                                            (p['pp'] + sum(custos), True) if None not in custos else (None, True))
    combos, combos_mala = [], []
    for c in scan['combos']:
        p = pacotes.get((c['ida'], c['volta']))
        combos.append({**c, 'pp': p['pp'], 'iv': p} if p and p['pp'] < c['pp'] else c)
    for c in scan.get('combos_mala', []):
        p = pacotes.get((c['ida'], c['volta']))
        combos_mala.append({**c, 'pp': p['pp_mala'], 'iv': p} if p and p['pp_mala'] is not None and p['pp_mala'] < c['pp'] else c)
    combos.sort(key=lambda c: c['pp'])
    combos_mala.sort(key=lambda c: c['pp'])
    return {**scan, 'combos': combos, 'combos_mala': combos_mala,
            'pacotes': [{'ida': i, 'volta': v, **p} for (i, v), p in pacotes.items()]}


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
            # 2) 123milhas e Zupper: a 123milhas devolve vazio se recebe muitas buscas juntas; então cada uma faz
            #    uma busca por vez (as duas em paralelo), só no aeroporto mais barato de cada data até aqui
            alvo = {}
            for t in scan['trechos']:
                rota = min(ofertas.get((t['s'], t['d']), []) + [{'pp': t['pp'], 'orig': t['rota'][:3], 'dest': t['rota'][4:]}],
                           key=lambda x: x['pp'])
                alvo[(t['s'], t['d'])] = rota['dest'] if t['s'] == 'ida' else rota['orig']

            async def uma_por_vez(vendedor):
                achou, um = 0, asyncio.Semaphore(1)
                for (s, d), ne in alvo.items():
                    r = await vendedores.buscar(nav, vendedor, s, d, ne, pax, um)
                    ofertas.setdefault((s, d), []).extend(r)
                    achou += bool(r)
                    await asyncio.sleep(2)
                print(f'{vendedor.upper()}: {achou} de {len(alvo)} buscas com voo')

            await asyncio.gather(uma_por_vez('123milhas'), uma_por_vez('Zupper'))
            scan = juntar(scan, ofertas)
            # 3) Zupper ida e volta juntas nas melhores combinações (às vezes sai mais barato que somar os trechos)
            if scan['status'] == 'ok':
                tre, um, pacotes = {(t['s'], t['d']): t for t in scan['trechos']}, asyncio.Semaphore(1), {}
                for c in scan['combos']:
                    r = await vendedores.buscar_iv(nav, c['ida'], c['volta'], tre[('ida', c['ida'])]['rota'][4:],
                                                   tre[('volta', c['volta'])]['rota'][:3], pax, um)
                    if r:
                        pacotes[(c['ida'], c['volta'])] = min(r, key=lambda x: x['pp'])
                    await asyncio.sleep(2)
                scan = com_pacotes(scan, pacotes)
                print(f"ZUPPER IDA E VOLTA: {len(pacotes)} pacotes; {sum(1 for c in scan['combos'] if c.get('iv'))} mais baratos que separado")
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
    assert 'mala' not in r['trechos'][0] and r['combos_mala'] == []   # sem tarifa com mala na Zupper: nada a estimar
    ida = [t for t in r['trechos'] if t['s'] == 'ida'][0]
    assert ida['pp'] == 857 and ida['vend'] == '123milhas' and ida['link'] == 'https://123milhas.com/x' and ida['ppCia'] == 927, ida
    assert r['vend']['ida 2026-12-27'] == [['123milhas', 857], ['MaxMilhas', 894], ['CIA Gol', 927], ['Booking.com', 965]], r['vend']
    assert r['combos'][0] == {'ida': '2026-12-27', 'volta': '2027-01-23', 'dias': 27, 'pp': 857 + 718, 'natal': False}, r['combos']
    volta = [t for t in r['trechos'] if t['s'] == 'volta'][0]
    assert volta == t_volta and r['vend']['volta 2027-01-23'] == [['CIA LATAM', 718]]
    # ida e volta junta mais barata vira a combinação; mais cara é ignorada
    perna = {'cia': 'Gol', 'orig': 'GRU', 'dest': 'JDO', 'saida': '22:10', 'chegada': '01:00', 'direto': True, 'tipo': 'direto', 'dur': 170}
    pac = {'vend': 'Zupper', 'pp': 1488, 'link': 'https://www.zupper.com.br/x', 'pernas': {'ida': perna, 'volta': {**perna, 'orig': 'JDO', 'dest': 'GRU'}}}
    r2 = com_pacotes(r, {('2026-12-27', '2027-01-23'): pac})
    assert r2['combos'][0]['pp'] == 1488 and r2['combos'][0]['iv'] is pac and r2['pacotes'][0]['ida'] == '2026-12-27' and r2['pacotes'][0]['pernas']['ida'] == perna, r2['combos']
    r3 = com_pacotes(r, {('2026-12-27', '2027-01-23'): {**pac, 'pp': 1700}})
    assert r3['combos'][0]['pp'] == 1575 and 'iv' not in r3['combos'][0] and len(r3['pacotes']) == 1
    # com mala: Zupper mostra Gol Classic +119 (real); 123milhas Gol 857 + 119 estimado = 976 é a melhor ida com mala.
    # Volta: LATAM da Zupper 760 + 150 real = 910; Google LATAM 718 + 150 estimado = 868 fica.
    zup = {'vend': 'Zupper', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'JDO', 'saida': '22:10', 'chegada': '01:00', 'direto': True,
           'tipo': 'direto', 'voo': None, 'pp': 928, 'mala': 119, 'link': 'https://www.zupper.com.br/x'}
    zv = {**zup, 'cia': 'LATAM', 'orig': 'JDO', 'dest': 'GRU', 'saida': '03:20', 'chegada': '06:25', 'pp': 760, 'mala': 150}
    of2 = {k: list(v) for k, v in of.items()}
    of2[('ida', '2026-12-27')].append(zup)
    of2[('volta', '2027-01-23')] = [zv]
    r4 = juntar(scan, of2)
    mi, mv = r4['trechos'][0]['mala'], r4['trechos'][1]['mala']
    assert (mi['pp'], mi['vend'], mi['estimado']) == (976, '123milhas', True), mi
    assert (mv['pp'], mv['vend'], mv['estimado'], mv['voos']) == (868, 'CIA LATAM', True, 'JDO>GRU 2027-01-23 LA 3000'), mv
    assert r4['combos_mala'][0]['pp'] == 976 + 868 and r4['taxa_mala'] == {'cias': {'Gol': 119, 'LATAM': 150}, 'geral': 150}, r4['taxa_mala']
    # pacote com tarifa com mala (real) mais barato que os trechos com mala vira a combinação com mala
    r5 = com_pacotes(r4, {('2026-12-27', '2027-01-23'): {**pac, 'pp': 1500, 'mala': 200}})
    assert r5['combos_mala'][0]['pp'] == 1700 and r5['combos_mala'][0]['iv']['mala_estimada'] is False, r5['combos_mala']
    # sem tarifa com mala no pacote: estima Gol 119 + Gol 119 = 1838, ainda abaixo dos trechos com mala (976 + 868 = 1844)
    r6 = com_pacotes(r4, {('2026-12-27', '2027-01-23'): {**pac, 'pp': 1600}})
    assert r6['pacotes'][0]['pp_mala'] == 1838 and r6['combos_mala'][0]['pp'] == 1838 and r6['combos_mala'][0]['iv']['mala_estimada'], r6
    r7 = com_pacotes(r4, {('2026-12-27', '2027-01-23'): {**pac, 'pp': 1700}})   # 1700 + 238 = 1938: trechos separados ficam
    assert r7['combos_mala'][0]['pp'] == 1844 and 'iv' not in r7['combos_mala'][0], r7['combos_mala']
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
