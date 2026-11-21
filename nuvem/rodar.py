"""Rodada completa no GitHub Actions, por pessoa (a viagem está em viagem.json).

1. Radar: Google Voos para cada (sentido, data, lugares) do plano (nuvem/plano.py). Uma busca cobre todos os aeroportos.
2. Conferência: os candidatos de cada trecho (nuvem/selecao.py) são abertos na tela de compra. Só esse preço vale.
3. Lupa: MaxMilhas, 123milhas, Zupper, Viaza e Vai de Promo nas melhores (data, aeroporto) de cada trecho, com prazo.
4. Escolha: a recomendada e a mais barata de cada trecho. O registrador (registrar.py) monta a mensagem por pessoa.

Nada aqui tenta contornar bloqueio: se o Google pedir verificação, a rodada segue só com os vendedores.

Uso: python nuvem/rodar.py          (roda tudo e registra)
     python nuvem/rodar.py --teste  (auto-checagem das etapas, sem navegador)
"""
import asyncio, json, os, re, sys, time
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, 'nuvem'))
import gvoos       # noqa: E402
import plano       # noqa: E402
import registrar   # noqa: E402
import selecao     # noqa: E402
import vendedores  # noqa: E402

GOOGLE = 'https://www.google.com/travel/flights?hl=pt-BR&gl=BR&curr=BRL'
BLOQUEIO = r'unusual traffic|tráfego incomum|captcha|not a robot|Antes de continuar|Before you continue'
PARALELO = 5              # buscas do Google abertas ao mesmo tempo
PRAZO_LUPA_S = 300        # tempo máximo dos vendedores numa rodada; o que não couber fica para a próxima
UM_POR_VEZ = ('123milhas', 'Zupper', 'Viaza', 'Vai de Promo')   # devolvem vazio se recebem várias buscas juntas


# ---------------------------------------------------------------- etapas puras (têm teste)
def voos_da_lista(buscas, respostas, v):
    """Junta o que o radar leu: [voo da lista + {sentido, d, pax, url}]. respostas: o que window.__gv.listar devolveu."""
    lista = []
    for b, r in zip(buscas, respostas):
        for x in gvoos.ler_lista((r or {}).get('rotulos') or [], b['pax'], v):
            lista.append({**x, 'sentido': b['sentido'], 'd': b['d'], 'pax': b['pax'], 'url': b['url']})
    return lista


def a_conferir(lista, ts, v, estado):
    """Os voos que vão para a tela de compra: os candidatos de cada trecho, sem repetir o mesmo voo com os mesmos lugares."""
    vistos, saida = set(), []
    for t in ts:
        if t['igual_a']:
            continue
        anterior = (estado.get('series', {}).get(t['serie'], {}).get('rec') or {}).get('ident')
        for x in selecao.candidatos(lista, v, t, anterior):
            k = (x['pax'], x['sentido'], *selecao.ident(x))
            if k not in vistos:
                vistos.add(k)
                saida.append(x)
    return saida


def ofertas_google(conferidos, v):
    """[(voo da lista, resposta de window.__gv.validar)] -> ofertas com o preço da tela de compra (o melhor vendedor que conta)."""
    saida = []
    for x, r in conferidos:
        vend = gvoos.ler_compra((r or {}).get('texto') or '', x['pax'])
        melhor = gvoos.melhor_vendedor(vend)
        if not melhor:
            continue
        voos = gvoos.voos_do_tfs((r or {}).get('href') or '')
        de, para = plano.aeroportos(v, x['sentido'])
        cia = next((q['pp'] for q in vend if q['vend'].startswith('CIA ')), None)
        saida.append({'sentido': x['sentido'], 'd': x['d'], 'pax': x['pax'], 'orig': x['orig'], 'dest': x['dest'], 'cia': x['cia'],
                      'saida': x['saida'], 'chegada': x['chegada'], 'tipo': x['tipo'], 'pp': melhor['pp'], 'vend': melhor['vend'],
                      'pp_cia': cia, 'voos': voos, 'fonte': 'google',
                      'link': gvoos.url_compra([(x['d'], de, para, voos)], x['pax']) or x['url']})
    return saida


def oferta_vendedor(x, sentido, d, pax):
    """Oferta lida na página de um vendedor (nuvem/vendedores.py) no formato comum."""
    voos = f"{x['orig']}>{x['dest']} {d} {x['voo'][0]} {x['voo'][1]}" if x.get('voo') and x.get('direto') else ''
    o = {'sentido': sentido, 'd': d, 'pax': pax, 'orig': x['orig'], 'dest': x['dest'], 'cia': x['cia'], 'saida': x['saida'],
         'chegada': x['chegada'], 'tipo': x['tipo'], 'pp': x['pp'], 'vend': x['vend'], 'voos': voos, 'fonte': 'vendedor', 'link': x['link']}
    if x.get('mala') is not None:
        o['mala'] = x['mala']
    return o


def fila_da_lupa(lista, ts, v, estado, hora):
    """O que buscar nos vendedores, por ordem de importância: [(sentido, data, aeroporto do Nordeste, lugares)].
    Primeiro a melhor (data, aeroporto) de cada trecho, depois a segunda. Sem lista do Google (bloqueio), vale a
    recomendada da rodada anterior. No fim, uma data de Mossoró em cada sentido: o Google não vende voo para lá, então
    só os vendedores podem dizer se apareceu algum (as datas giram a cada hora)."""
    por_ordem = []
    for t in ts:
        if t['igual_a'] or not t['datas']:
            continue
        chaves = selecao.chaves_lupa(lista, v, t)
        rec = estado.get('series', {}).get(t['serie'], {}).get('rec')
        if not chaves and rec and rec['d'] in t['datas']:
            chaves = [(rec['d'], rec['ne'])]
        por_ordem.append([(t['sentido'], d, ne, t['pax']) for d, ne in chaves])
    fila = [c for nivel in range(max(map(len, por_ordem), default=0)) for cs in por_ordem for c in cs[nivel:nivel + 1]]
    if 'MVF' in v['aeroportos']:
        for sentido in ('ida', 'volta'):
            ds = sorted({d for t in ts if t['sentido'] == sentido and not t['rodizio'] for d in t['datas']})
            if ds and 'MVF' in plano.aeroportos(v, sentido)[0 if sentido == 'volta' else 1]:
                fila.append((sentido, ds[hora % len(ds)], 'MVF', 1))
    vistos = set()
    return [c for c in fila if not (c in vistos or vistos.add(c))]


def completar_numeros(ofertas):
    """Vendedor que não mostra o número do voo ganha o número do mesmo voo visto em outra fonte (a mensagem fica exata)."""
    numero = {}
    for o in ofertas:
        if o.get('voos'):
            numero.setdefault((o['sentido'], *selecao.ident(o)), o['voos'])
    for o in ofertas:
        if not o.get('voos'):
            o['voos'] = numero.get((o['sentido'], *selecao.ident(o)), '')
    return ofertas


def taxas_mala(ofertas):
    """Quanto custa a mala de 23 kg por companhia (por pessoa e trecho), medido nas tarifas com mala da Zupper desta rodada."""
    por_cia = {}
    for o in ofertas:
        if o.get('mala') is not None:
            por_cia.setdefault(o['cia'], []).append(o['mala'])
    return {c: sorted(vals)[len(vals) // 2] for c, vals in sorted(por_cia.items())}


def celulas(lista, ofertas, t, v):
    """Melhor preço de cada data e aeroporto do Nordeste de um trecho: {data: {aeroporto: {'pp', 'conf'}}}.
    conf = preço confirmado (tela de compra ou vendedor); sem isso, é o preço da lista do Google."""
    saida = {}
    for x in selecao.do_trecho(lista, t):
        c = saida.setdefault(x['d'], {}).setdefault(selecao.nordeste(x, v), {'pp': round(x['lista_pp']), 'conf': False})
        if not c['conf'] and x['lista_pp'] < c['pp']:
            c['pp'] = round(x['lista_pp'])
    for o in selecao.do_trecho(ofertas, t):
        c = saida.setdefault(o['d'], {}).setdefault(selecao.nordeste(o, v), {'pp': o['pp'], 'conf': True})
        if not c['conf'] or o['pp'] < c['pp']:
            c.update(pp=o['pp'], conf=True)
    return saida


def montar(v, ts, lista, ofertas, estado, olhadas):
    """O resultado de cada trecho: {serie: {recomendada, mais_barata, comprado, celulas, olhadas}}.
    Trecho com igual_a aponta para o resultado do outro (o registrador mostra como "no mesmo voo")."""
    series = {}
    for t in ts:
        if t['igual_a']:
            continue
        anterior = (estado.get('series', {}).get(t['serie'], {}).get('rec') or {}).get('ident')
        r = selecao.recomendar(ofertas, v, t, anterior)
        if t['voo_comprado']:
            meus = [o for o in selecao.do_trecho(ofertas, t) if selecao.eh_comprado(o, v)]
            r['comprado'] = min(meus, key=lambda o: o['pp']) if meus else None
        r['celulas'] = celulas(lista, ofertas, t, v)
        r['olhadas'] = olhadas.get(t['serie'], [])
        series[t['serie']] = r
    return series


# ---------------------------------------------------------------- etapas com navegador
async def radar_e_conferencia(nav, v, ts, bs, estado):
    """Google Voos: lista de cada busca e tela de compra dos candidatos. Devolve (lista, ofertas, resumo da fonte)."""
    codigo = open(os.path.join(RAIZ, 'varredura.js'), encoding='utf-8').read()
    ctx = await nav.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
    pg = await ctx.new_page()
    info = {'buscas': len(bs), 'voos': 0, 'conferidos': 0, 'ofertas': 0, 'bloqueio': None}
    try:
        await pg.goto(GOOGLE, wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(3000)
        inicio = (await pg.inner_text('body'))[:3000]
        if re.search(BLOQUEIO, inicio, re.I):
            info['bloqueio'] = inicio[:200].replace('\n', ' | ')
            return [], [], info
        await pg.evaluate(codigo)
        chamar = lambda fn, itens: asyncio.wait_for(pg.evaluate(f'(a) => window.__gv.{fn}(a.itens, a.paralelo)',
                                                                {'itens': itens, 'paralelo': PARALELO}), timeout=600)
        respostas = await chamar('listar', [b['url'] for b in bs])
        lista = voos_da_lista(bs, respostas, v)
        cands = a_conferir(lista, ts, v, estado)
        info.update(voos=len(lista), conferidos=len(cands), bloqueio=next((r['bloqueio'] for r in respostas if (r or {}).get('bloqueio')), None))
        if not cands or info['bloqueio']:
            return lista, [], info
        validados = await chamar('validar', [{'url': x['url'], 'chave': x['chave']} for x in cands])
        ofertas = ofertas_google(list(zip(cands, validados)), v)
        info.update(ofertas=len(ofertas), bloqueio=next((r['bloqueio'] for r in validados if (r or {}).get('bloqueio')), None))
        return lista, ofertas, info
    except Exception as e:   # o Google falhou inteiro: a rodada segue com os vendedores
        info['erro'] = f'{type(e).__name__}: {str(e)[:120]}'
        return [], [], info
    finally:
        await ctx.close()


async def lupa(nav, fila, prazo_s=PRAZO_LUPA_S):
    """Vendedores nas chaves da fila, até o prazo. Devolve (ofertas, {vendedor: {buscas, com_voo}}).
    MaxMilhas aguenta várias buscas juntas; os outros fazem uma por vez (os vendedores entre si, em paralelo)."""
    fim, ofertas, fontes = time.monotonic() + prazo_s, [], {}

    async def uma(vendedor, chave, sem):
        sentido, d, ne, pax = chave
        if time.monotonic() > fim:
            return None
        achados = await vendedores.buscar(nav, vendedor, sentido, d, ne, pax, sem)
        f = fontes.setdefault(vendedor, {'buscas': 0, 'com_voo': 0})
        f['buscas'] += 1
        f['com_voo'] += bool(achados)
        ofertas.extend(oferta_vendedor(x, sentido, d, pax) for x in achados)
        return bool(achados)

    async def maxmilhas():
        sem = asyncio.Semaphore(6)
        await asyncio.gather(*(uma('MaxMilhas', c, sem) for c in fila))

    async def um_por_vez(vendedor):
        sem, achou = asyncio.Semaphore(1), 0
        for k, c in enumerate(x for x in fila if x[2] != 'MVF'):
            if k == 4 and not achou:   # nada nas 4 primeiras: o site barrou o servidor; não gasta a rodada com ele
                fontes.setdefault(vendedor, {'buscas': 0, 'com_voo': 0})['pulado'] = True
                return
            r = await uma(vendedor, c, sem)
            if r is None:
                return
            achou += r
            await asyncio.sleep(2)

    await asyncio.gather(maxmilhas(), *(um_por_vez(x) for x in UM_POR_VEZ))
    return ofertas, fontes


async def varrer(v, estado, agora):
    """Uma rodada inteira. agora: datetime no fuso de Brasília. Devolve o scan (versão 2) para o registrador."""
    from playwright.async_api import async_playwright
    hoje = agora.strftime('%Y-%m-%d')
    ts = plano.trechos(v, hoje)
    bs = plano.buscas(v, hoje, estado)
    for b in bs:
        b['url'] = gvoos.url_busca([(b['d'], b['de'], b['para'], '')], b['pax'])
    olhadas = {}
    for b in bs:
        for s in b['series']:
            olhadas.setdefault(s, []).append(b['d'])
    vendedores.configurar(v)
    scan = {'versao': 2, 'inicio': datetime.now(timezone.utc).isoformat()}
    async with async_playwright() as p:
        opcoes = {'headless': True}
        if os.environ.get('PASSAGENS_CHROMIUM'):          # navegador já instalado na máquina (uso fora do GitHub)
            opcoes['executable_path'] = os.environ['PASSAGENS_CHROMIUM']
        if os.environ.get('HTTPS_PROXY'):
            opcoes['proxy'] = {'server': os.environ['HTTPS_PROXY']}
        nav = await p.chromium.launch(**opcoes)
        t0 = time.monotonic()
        lista, ofertas, google = await radar_e_conferencia(nav, v, ts, bs, estado)
        google['seg'] = round(time.monotonic() - t0)
        print('GOOGLE:', json.dumps(google, ensure_ascii=False))
        t0 = time.monotonic()
        fila = fila_da_lupa(lista, ts, v, estado, agora.hour)
        do_vendedor, fontes = await lupa(nav, fila)
        print(f'VENDEDORES: {len(fila)} chaves em {round(time.monotonic() - t0)}s |', json.dumps(fontes, ensure_ascii=False))
        await nav.close()
    ofertas = completar_numeros(ofertas + do_vendedor)
    mossoro = [o for o in do_vendedor if 'MVF' in (o['orig'], o['dest'])]
    series = montar(v, ts, lista, ofertas, estado, olhadas)
    achou = any(s['recomendada'] for s in series.values())
    scan.update(status='ok' if achou else 'erro', fim=datetime.now(timezone.utc).isoformat(),
                erro=None if achou else ('Google pediu verificação e nenhum vendedor respondeu' if google.get('bloqueio') else 'nenhum voo com preço confirmado'),
                fontes={'Google': google, **fontes}, series=series, taxa_mala=taxas_mala(do_vendedor),
                mossoro={'buscas': sum(1 for c in fila if c[2] == 'MVF'), 'voos': len(mossoro)})
    return scan


# ---------------------------------------------------------------- auto-checagem
def _teste():
    v = plano.carregar()
    hoje = '2026-11-20'
    ts = plano.trechos(v, hoje)
    bs = plano.buscas(v, hoje)
    for b in bs:
        b['url'] = 'https://google/' + b['sentido'] + b['d'] + str(b['pax'])
    gru, cgh, vcp = ('Aeroporto Internacional de São Paulo/Guarulhos–Governador André Franco Montoro', 'Aeroporto de São Paulo/Congonhas–Deputado Freitas Nobre',
                     'Aeroporto Internacional de Viracopos')
    nome = {'FOR': 'Aeroporto Internacional de Fortaleza - Pinto Martins (FOR)', 'REC': 'Aeroporto Internacional do Recife/Guararapes–Gilberto Freyre',
            'NAT': 'Aeroporto Internacional de Natal', 'JDO': 'Aeroporto de Juazeiro do Norte - Orlando Bezerra de Menezes', 'GRU': gru, 'CGH': cgh, 'VCP': vcp}
    rotulo = lambda preco, cia, o, d, saida, chegada: (
        f'A partir de {preco} Reais brasileiros. Voo direto da {cia}. Sai do aeroporto {nome[o]} às {saida} do dia terça-feira, janeiro 19 e chega no '
        f'aeroporto {nome[d]} às {chegada} do dia terça-feira, janeiro 19. Duração total: 3 h 30 min.   Selecionar voo')
    respostas = []
    for b in bs:
        if (b['sentido'], b['d'], b['pax']) == ('volta', '2027-01-19', 1):
            respostas.append({'rotulos': [rotulo(520, 'Gol', 'FOR', 'GRU', '12:50', '16:30'), rotulo(400, 'Azul', 'REC', 'CGH', '03:00', '06:25'),
                                          rotulo(900, 'LATAM', 'NAT', 'VCP', '10:00', '13:30')]})
        elif (b['sentido'], b['d'], b['pax']) == ('ida', '2026-12-22', 2):
            respostas.append({'rotulos': [rotulo('3.300', 'Gol', 'GRU', 'NAT', '14:05', '17:25'), rotulo('2.400', 'LATAM', 'GRU', 'FOR', '07:00', '10:30')]})
        else:
            respostas.append({'rotulos': []} if b['sentido'] == 'ida' else None)
    lista = voos_da_lista(bs, respostas, v)
    assert len(lista) == 5 and {(x['sentido'], x['d'], x['pax']) for x in lista} == {('volta', '2027-01-19', 1), ('ida', '2026-12-22', 2)}
    assert lista[0]['url'].startswith('https://google/') and next(x for x in lista if x['dest'] == 'NAT' and x['pax'] == 2)['lista_pp'] == 1650

    # ---- conferência: candidatos de cada trecho, cada voo uma vez; o voo já comprado entra no trecho de 2 lugares
    cands = a_conferir(lista, ts, v, {})
    # (Natal por Campinas a R$900 não entra: com o peso fica atrás de Fortaleza e de Recife, e não é a mais barata)
    assert [(x['pax'], x['orig'], x['dest']) for x in cands] == [(1, 'FOR', 'GRU'), (1, 'REC', 'CGH'), (2, 'GRU', 'FOR'), (2, 'GRU', 'NAT')], cands
    compra = lambda *vend: {'texto': ''.join(f'Reserve com a {n}\nR$ {p}\nContinuar\n' for n, p in vend),
                            'href': gvoos.url_compra([('2027-01-19', ['FOR'], ['GRU'], 'FOR>GRU 2027-01-19 G3 1555')], 1)}
    conferidos = [(cands[0], compra(('GolCompanhia aérea', '530'), ('MaxMilhas', '505'), ('Kiwi.com', '300'))), (cands[1], compra(('Kiwi.com', '380'))),
                  (cands[2], compra(('LATAMCompanhia aérea', '2.500'))), (cands[3], compra(('GolCompanhia aérea', '3.300'))),
                  (cands[1], {'erro': 'voo não está mais na lista'}), (cands[1], None)]
    og = ofertas_google(conferidos, v)
    # Kiwi não conta; sem vendedor que conta, o voo fica sem preço confirmado; preço é por pessoa
    assert [(o['orig'], o['pp'], o['vend'], o['pax']) for o in og] == [('FOR', 505, 'MaxMilhas', 1), ('GRU', 1250, 'CIA LATAM', 2), ('GRU', 1650, 'CIA Gol', 2)], og
    assert og[0]['voos'] == 'FOR>GRU 2027-01-19 G3 1555' and og[0]['link'].startswith('https://www.google.com/travel/flights/booking?tfs=') and og[0]['pp_cia'] == 530

    # ---- vendedor no formato comum; número do voo só quando é direto
    bruto = {'vend': 'Zupper', 'cia': 'Azul', 'orig': 'REC', 'dest': 'CGH', 'saida': '03:00', 'chegada': '06:25', 'dur': 205, 'direto': True,
             'voo': ('AD', '4716'), 'pp': 360, 'tipo': 'direto', 'link': 'https://zupper/x', 'mala': 93}
    ov = oferta_vendedor(bruto, 'volta', '2027-01-19', 1)
    assert ov['voos'] == 'REC>CGH 2027-01-19 AD 4716' and ov['fonte'] == 'vendedor' and ov['mala'] == 93 and ov['pp'] == 360
    assert oferta_vendedor({**bruto, 'direto': False, 'mala': None}, 'volta', '2027-01-19', 1)['voos'] == ''
    assert taxas_mala([ov, {**ov, 'mala': 120}, {**ov, 'cia': 'Gol', 'mala': 136}, og[0]]) == {'Azul': 120, 'Gol': 136}
    # o mesmo voo no vendedor que não mostra o número ganha o número lido no Google; voo diferente fica sem
    sem_numero = [{**og[0], 'vend': 'Vai de Promo', 'voos': '', 'pp': 480}, {**og[0], 'saida': '23:00', 'voos': ''}]
    cheios = completar_numeros([og[0], *sem_numero])
    assert [o['voos'] for o in cheios] == ['FOR>GRU 2027-01-19 G3 1555', 'FOR>GRU 2027-01-19 G3 1555', '']

    # ---- fila da lupa: a melhor chave de cada trecho antes da segunda; Mossoró no fim, uma data por sentido, girando com a hora
    fila = fila_da_lupa(lista, ts, v, {}, 0)
    # o voo já comprado é conferido sempre nos trechos que pedem, mesmo sem aparecer na lista do Google (1 lugar)
    assert fila[:3] == [('volta', '2027-01-19', 'FOR', 1), ('ida', '2026-12-22', 'NAT', 1), ('ida', '2026-12-22', 'FOR', 2)], fila
    # segunda chave de cada trecho: Recife na volta; Natal na ida de 2 lugares (o voo já comprado)
    assert fila[3:5] == [('volta', '2027-01-19', 'REC', 1), ('ida', '2026-12-22', 'NAT', 2)], fila
    assert fila[-2:] == [('ida', '2026-12-22', 'MVF', 1), ('volta', '2027-01-07', 'MVF', 1)] and len(fila) == len(set(fila))
    assert fila_da_lupa(lista, ts, v, {}, 1)[-2:] == [('ida', '2026-12-23', 'MVF', 1), ('volta', '2027-01-08', 'MVF', 1)]
    # Google bloqueado: sem lista, vale a recomendada da rodada anterior
    estado = {'series': {'v.volta': {'rec': {'d': '2027-01-20', 'ne': 'JDO', 'ident': ['2027-01-20', 'Gol', 'JDO', 'GRU', '09:00']}}}}
    assert fila_da_lupa([], ts, v, estado, 0)[0] == ('volta', '2027-01-20', 'JDO', 1)

    # ---- montagem: recomendada com peso, mais barata, voo comprado, células por data e aeroporto
    olhadas = {'v.volta': ['2027-01-19'], 'wk.ida': ['2026-12-22']}
    series = montar(v, ts, lista, og + [ov], {}, olhadas)
    assert set(series) == {'v.volta', 'h.depois', 'g.ida_dez', 'g.ida_jan', 'wk.ida', 'wk.volta'}      # igual_a não tem resultado próprio
    sv = series['v.volta']
    assert (sv['recomendada']['orig'], sv['recomendada']['pp']) == ('FOR', 505) and (sv['mais_barata']['orig'], sv['mais_barata']['vend']) == ('REC', 'Zupper')
    assert sv['celulas'] == {'2027-01-19': {'FOR': {'pp': 505, 'conf': True}, 'REC': {'pp': 360, 'conf': True}, 'NAT': {'pp': 900, 'conf': False}}}, sv['celulas']
    assert sv['olhadas'] == ['2027-01-19'] and 'comprado' not in sv
    sw = series['wk.ida']
    # o voo comprado (Natal, R$1.650) perde para Fortaleza R$1.250 mesmo com o bônus, mas fica registrado à parte
    assert sw['recomendada']['dest'] == 'FOR' and sw['comprado']['pp'] == 1650 and sw['mais_barata'] is None
    assert series['g.ida_dez']['recomendada'] is None and series['g.ida_dez']['comprado'] is None and series['h.depois']['celulas'] == {}
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
        sys.exit(0)
    viagem = plano.carregar()
    estado_ = registrar.ler_estado()
    scan_ = asyncio.run(varrer(viagem, estado_, datetime.now()))
    json.dump(scan_, open(os.path.join(RAIZ, 'ultima.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('STATUS:', scan_['status'], scan_.get('erro') or '', '| trechos com preço:', sum(1 for s in scan_['series'].values() if s['recomendada']))
    for linha in registrar.registrar(scan_, viagem):
        print(linha)
    sys.exit(0 if scan_['status'] == 'ok' else 1)
