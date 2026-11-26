"""Escolha da melhor opção de cada trecho da viagem. Só funções puras.

Uma oferta é um voo com preço confirmado (tela de compra do Google ou página de um vendedor):
  {sentido, d, orig, dest, cia, saida, chegada, tipo, pax, pp, vend, link, fonte, voos}
Um voo da lista do Google ainda não é oferta: tem só lista_pp, que pode estar velho e serve para ordenar.

A ordem de prioridade dos aeroportos vira um peso em reais por pessoa (viagem.json): a recomendada é a mais barata
depois de somar o peso. O voo já comprado, nos trechos que pedem, ganha um bônus (peso negativo).

Uso: python3 nuvem/selecao.py --teste
"""
import sys


def ident(x):
    """O que faz dois registros serem o mesmo voo (o preço e o vendedor podem mudar)."""
    return (x['d'], x['cia'], x['orig'], x['dest'], x['saida'])


def chave(x):
    """Texto que identifica um voo de uma rodada para a outra (para guardar o número dele): sentido, data, companhia,
    aeroportos e os dois horários. A chegada entra para não confundir duas conexões que saem no mesmo voo."""
    return '|'.join((x['sentido'], x['d'], x['cia'], x['orig'], x['dest'], x['saida'], x['chegada']))


def nordeste(x, v):
    return x['dest'] if x['orig'] in v['sp'] else x['orig']


def eh_comprado(x, v):
    c = v.get('comprado')
    return bool(c) and (x['d'], x['cia'], x['orig'], x['dest'], x['saida']) == (c['d'], c['cia'], c['orig'], c['dest'], c['saida'])


def peso(x, v, t):
    """Reais por pessoa somados ao preço para comparar: aeroporto do Nordeste + aeroporto de São Paulo - bônus do voo comprado."""
    ap = v['aeroportos']
    total = ap[x['orig']].get('peso', 0) + ap[x['dest']].get('peso', 0)
    return total - (v['comprado'].get('bonus', 0) if t.get('voo_comprado') and eh_comprado(x, v) else 0)


def ultimo_caso(x, v):
    """Aeroporto que só serve quando não há outro (viagem.json: ultimo_caso)."""
    return bool(v['aeroportos'][nordeste(x, v)].get('ultimo_caso'))


def do_trecho(itens, t):
    """Os voos ou ofertas que servem a um trecho: mesmo sentido, data na janela, mesmos lugares."""
    return [x for x in itens if x['sentido'] == t['sentido'] and x['pax'] == t['pax'] and x['d'] in t['datas']]


def variados(ordenados, n, chave):
    """Os n primeiros de uma lista já ordenada, sem repetir a chave (ex.: o aeroporto) enquanto houver chave nova;
    se as chaves acabarem antes de n, completa com os próximos da ordem."""
    usados, saida = set(), []
    for x in ordenados:
        if chave(x) not in usados and len(saida) < n:
            usados.add(chave(x))
            saida.append(x)
    return saida + [x for x in ordenados if x not in saida][:n - len(saida)]


def diversos(ordenados, n, *chaves):
    """Os n primeiros de uma lista já ordenada, variando: primeiro um por valor da primeira chave (ex.: o aeroporto),
    depois um por valor novo da segunda (ex.: a data), e só então os próximos da ordem."""
    saida = []
    for chave in chaves:
        usados = {chave(x) for x in saida}
        for x in ordenados:
            if len(saida) < n and x not in saida and chave(x) not in usados:
                usados.add(chave(x))
                saida.append(x)
    return saida + [x for x in ordenados if x not in saida][:n - len(saida)]


def candidatos(lista, v, t, anterior=None):
    """Voos da lista que vale conferir na tela de compra para um trecho: o melhor de cada aeroporto depois do peso (um
    aeroporto barato na lista não esconde os outros), depois o melhor de cada data ainda não coberta (numa janela longa,
    a mensagem mostra várias datas conferidas), o mais barato de todos, a recomendada da rodada anterior (para saber se
    subiu ou sumiu) e o voo já comprado, quando o trecho pede."""
    meus = do_trecho(lista, t)
    por_ajustado = sorted(meus, key=lambda x: (x['lista_pp'] + peso(x, v, t), x['d'], x['saida']))
    escolhidos = diversos(por_ajustado, max(1, v['selecao']['validar_por_trecho'] - 1), lambda x: nordeste(x, v), lambda x: x['d'])
    escolhidos += sorted(meus, key=lambda x: (x['lista_pp'], x['d'], x['saida']))[:1]
    if anterior:
        escolhidos += [x for x in meus if ident(x) == tuple(anterior)][:1]
    if t.get('voo_comprado'):
        escolhidos += [x for x in meus if eh_comprado(x, v)][:1]
    vistos, saida = set(), []
    for x in escolhidos:
        if ident(x) not in vistos:
            vistos.add(ident(x))
            saida.append(x)
    return saida


def chaves_lupa(lista, v, t):
    """(data, aeroporto do Nordeste) que vale buscar nos vendedores para um trecho: a melhor data de cada aeroporto depois
    do peso, um aeroporto por chave enquanto houver aeroporto novo. O voo já comprado, quando o trecho pede, entra sempre."""
    ordenados, chaves = sorted(do_trecho(lista, t), key=lambda x: (x['lista_pp'] + peso(x, v, t), x['d'], x['saida'])), []
    for x in ordenados:
        if (x['d'], nordeste(x, v)) not in chaves:
            chaves.append((x['d'], nordeste(x, v)))
    saida = variados(chaves, v['selecao']['lupa_por_trecho'], lambda c: c[1])
    c = v.get('comprado')
    if c and t.get('voo_comprado') and c['d'] in t['datas'] and (c['d'], c['dest']) not in saida:
        saida.append((c['d'], c['dest']))
    # por último (é o que sai primeiro se o tempo acabar): a mais barata de todas, para a mensagem poder comparar
    barata = min(ordenados, key=lambda x: (x['lista_pp'], x['d'], x['saida']), default=None)
    if barata and (barata['d'], nordeste(barata, v)) not in saida:
        saida.append((barata['d'], nordeste(barata, v)))
    return saida


def melhor_por_voo(ofertas):
    """Do mesmo voo e mesmos lugares em vários vendedores, fica o mais barato (empate: quem veio primeiro)."""
    melhor = {}
    for o in ofertas:
        k = (o['sentido'], o['pax'], *ident(o))
        if k not in melhor or o['pp'] < melhor[k]['pp']:
            melhor[k] = o
    return list(melhor.values())


def boas(ofertas, t, ate):
    """Todas as opções do trecho com preço confirmado até `ate` por pessoa (o melhor preço de cada voo), da mais barata à
    mais cara: para quem quer ver tudo o que está num preço aceitável, não só a recomendada (viagem.json: listar_ate)."""
    return sorted((o for o in melhor_por_voo(do_trecho(ofertas, t)) if o['pp'] <= ate), key=lambda o: (o['pp'], o['d'], o['saida']))


def recomendar(ofertas, v, t, anterior=None):
    """{'recomendada': oferta ou None, 'mais_barata': oferta ou None} de um trecho, só com preço confirmado.
    recomendada: menor preço + peso; se a da rodada anterior continua a até R$15 da melhor, fica ela (não fica trocando).
    mais_barata: a de menor preço puro, só quando é outro voo e economiza pelo menos selecao.margem_mais_barata."""
    meus = melhor_por_voo(do_trecho(ofertas, t))
    if not meus:
        return {'recomendada': None, 'mais_barata': None}
    ajustado = lambda o: o['pp'] + peso(o, v, t)
    # aeroporto de último caso só concorre a recomendada quando é o único com preço; como "mais barata" ele aparece sempre
    concorrem = [o for o in meus if not ultimo_caso(o, v)] or meus
    rec = min(concorrem, key=lambda o: (ajustado(o), o['d'], o['saida']))
    if anterior:
        fica = next((o for o in concorrem if ident(o) == tuple(anterior)), None)
        if fica and ajustado(fica) - ajustado(rec) <= 15:
            rec = fica
    barata = min(meus, key=lambda o: (o['pp'], o['d'], o['saida']))
    if ident(barata) == ident(rec) or rec['pp'] - barata['pp'] < v['selecao']['margem_mais_barata']:
        barata = None
    return {'recomendada': rec, 'mais_barata': barata}


def pacotes(series, grupo):
    """Para cada cenário do grupo (ida + volta), a soma das recomendadas: [{id, ida, volta, pp}] (pp por pessoa).
    Cenário sem uma das pernas fica de fora. series: {serie: {'recomendada': oferta ou None}}."""
    saida = []
    for c in grupo.get('cenarios', []):
        ida = (series.get(f"{grupo['id']}.{c['ida']}") or {}).get('recomendada')
        volta = (series.get(f"{grupo['id']}.{c['volta']}") or {}).get('recomendada')
        if ida and volta:
            saida.append({'id': c['id'], 'nome': c.get('nome', c['id']), 'ida': ida, 'volta': volta, 'pp': ida['pp'] + volta['pp']})
    return saida


def com_apelidos(series, ts, grupo_id):
    """As séries com os trechos igual_a do grupo apontando para o resultado do trecho de origem (para montar pacotes)."""
    return {**series, **{t['serie']: series.get(t['igual_a']) for t in ts if t['grupo'] == grupo_id and t['igual_a']}}


def lugares(precos, pax, margem):
    """O que muda no preço por pessoa quando o mesmo voo é comprado com outro número de lugares.
    precos: {lugares: preço por pessoa}, lidos na mesma fonte e na mesma hora; pax: os lugares do trecho.
    Devolve [{'n', 'dif'}], um por número de lugares diferente de pax (dif = quanto a mais, por pessoa, em relação a
    comprar pax lugares; diferença menor que a margem vale 0). Sem o preço de pax lugares não há com o que comparar: []."""
    base = precos.get(pax)
    if not base:
        return []
    return [{'n': n, 'dif': (pp - base) if abs(pp - base) >= margem else 0} for n, pp in sorted(precos.items()) if n != pax and pp]


def mesmo_voo(a, b):
    """Dois registros são o mesmo voo (mesmo dia, companhia, aeroportos e hora de saída)."""
    return ident(a) == ident(b)


def juntas(pacote, opcoes, margem):
    """A melhor forma de comprar ida e volta numa compra só, quando ela economiza pelo menos `margem` por pessoa sobre o
    pacote (as duas recomendadas, cada uma comprada no seu vendedor). opcoes: [{'pp', 'vend', 'link', 'ida', 'volta'}],
    com ida e volta no formato de oferta e pp = preço do par por pessoa. Devolve a opção com 'economia' e 'mesmos'
    (True se são exatamente os voos do pacote), ou None. Com preços a menos de `margem` um do outro, ganha a opção
    que mantém os voos do pacote."""
    boas = []
    for o in opcoes:
        economia = pacote['pp'] - o['pp']
        if economia >= margem:
            mesmos = mesmo_voo(o['ida'], pacote['ida']) and mesmo_voo(o['volta'], pacote['volta'])
            boas.append({**o, 'economia': economia, 'mesmos': mesmos})
    if not boas:
        return None
    menor = min(o['pp'] for o in boas)
    return min(boas, key=lambda o: (not (o['mesmos'] and o['pp'] - menor < margem), o['pp']))


def _teste():
    v = {'sp': ['GRU', 'CGH', 'VCP'],
         'aeroportos': {'GRU': {}, 'CGH': {}, 'VCP': {'peso': 60}, 'FOR': {'peso': 0}, 'MVF': {'peso': 40}, 'JDO': {'peso': 80},
                        'NAT': {'peso': 120}, 'JPA': {'peso': 200}, 'REC': {'peso': 400, 'ultimo_caso': True}},
         'comprado': {'d': '2026-12-22', 'saida': '14:05', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'NAT', 'voo': 'G3 1678', 'bonus': 150},
         'selecao': {'validar_por_trecho': 3, 'lupa_por_trecho': 2, 'margem_mais_barata': 30}}
    volta = {'serie': 'v.volta', 'sentido': 'volta', 'pax': 1, 'datas': ['2027-01-19', '2027-01-20'], 'voo_comprado': False}
    ida = {'serie': 'wk.ida', 'sentido': 'ida', 'pax': 2, 'datas': ['2026-12-22', '2026-12-23'], 'voo_comprado': True}

    def voo(d, orig, dest, saida, preco, cia='Gol', pax=1, sentido='volta', **mais):
        return {'sentido': sentido, 'd': d, 'orig': orig, 'dest': dest, 'cia': cia, 'saida': saida, 'chegada': '23:00', 'tipo': 'direto',
                'pax': pax, 'lista_pp': preco, 'pp': preco, 'vend': 'CIA ' + cia, 'link': 'https://x', 'fonte': 'google', 'voos': '', **mais}

    assert chave(voo('2027-01-19', 'FOR', 'GRU', '12:50', 1)) == 'volta|2027-01-19|Gol|FOR|GRU|12:50|23:00'

    # ---- peso: Fortaleza 0, Recife 200, Campinas +60; o voo comprado ganha o bônus só no trecho que pede
    assert peso(voo('2027-01-19', 'FOR', 'GRU', '10:00', 1), v, volta) == 0
    assert peso(voo('2027-01-19', 'REC', 'VCP', '10:00', 1), v, volta) == 460
    comprado = voo('2026-12-22', 'GRU', 'NAT', '14:05', 1650, pax=2, sentido='ida')
    assert eh_comprado(comprado, v) and peso(comprado, v, ida) == 120 - 150 and peso(comprado, v, volta) == 120
    assert not eh_comprado(voo('2026-12-22', 'CGH', 'NAT', '14:05', 1, sentido='ida'), v)

    # ---- candidatos: os 2 melhores com peso, o mais barato puro, a recomendada anterior e o voo comprado
    lista = [voo('2027-01-19', 'REC', 'CGH', '03:00', 400), voo('2027-01-19', 'FOR', 'GRU', '12:50', 520), voo('2027-01-20', 'FOR', 'GRU', '06:00', 540),
             voo('2027-01-20', 'JDO', 'GRU', '09:00', 700), voo('2027-01-21', 'FOR', 'GRU', '06:00', 100),      # fora da janela
             voo('2027-01-19', 'FOR', 'GRU', '12:50', 500, pax=2)]                                               # outros lugares
    # um voo por aeroporto: Fortaleza (520) e Juazeiro (700 + 80 de peso); Recife (400 + 400) entra por ser o mais barato de todos
    c = candidatos(lista, v, volta)
    assert [(x['orig'], x['saida']) for x in c] == [('FOR', '12:50'), ('JDO', '09:00'), ('REC', '03:00')], c
    c = candidatos(lista, v, volta, anterior=('2027-01-20', 'Gol', 'FOR', 'GRU', '06:00'))
    assert [(x['orig'], x['saida']) for x in c] == [('FOR', '12:50'), ('JDO', '09:00'), ('REC', '03:00'), ('FOR', '06:00')], c
    # só um aeroporto na lista: completa com o próximo voo da ordem
    so_for = [x for x in lista if x['orig'] == 'FOR' and x['pax'] == 1]
    assert [x['saida'] for x in candidatos(so_for, v, volta)] == ['12:50', '06:00']
    assert variados([1, 2, 3, 4], 3, lambda x: x % 2) == [1, 2, 3] and variados([1, 3, 5], 2, lambda x: 0) == [1, 3] and variados([], 2, str) == []
    # variando por duas chaves: primeiro uma de cada paridade, depois uma de cada dezena nova, depois o que vier
    assert diversos([1, 3, 5, 12, 14, 25, 27], 5, lambda x: x % 2, lambda x: x // 10) == [1, 12, 25, 3, 5]
    assert diversos([1, 2, 3], 2, lambda x: 0) == variados([1, 2, 3], 2, lambda x: 0) == [1, 2] and diversos([], 3, str, str) == []
    # janela longa, um trecho só: com 6 conferências, entra o melhor de cada aeroporto e depois datas novas, não 6 voos do mesmo dia
    longa = {'serie': 'h.depois', 'sentido': 'volta', 'pax': 1, 'datas': [f'2027-02-{d:02d}' for d in range(1, 11)], 'voo_comprado': False}
    cheia = [voo('2027-02-01', 'FOR', 'GRU', f'{h:02d}:00', 300 + h) for h in (6, 7, 8, 9)] + [voo('2027-02-03', 'FOR', 'GRU', '10:00', 320),
             voo('2027-02-05', 'JDO', 'GRU', '11:00', 330), voo('2027-02-07', 'FOR', 'GRU', '12:00', 340), voo('2027-02-08', 'NAT', 'GRU', '13:00', 400)]
    c = candidatos(cheia, {**v, 'selecao': {**v['selecao'], 'validar_por_trecho': 7}}, longa)
    assert [(x['d'][8:], x['orig'], x['saida']) for x in c] == [('01', 'FOR', '06:00'), ('05', 'JDO', '11:00'), ('08', 'NAT', '13:00'), ('03', 'FOR', '10:00'),
                                                              ('07', 'FOR', '12:00'), ('01', 'FOR', '07:00')], c
    lista_ida = [comprado, voo('2026-12-23', 'GRU', 'FOR', '07:00', 1200, pax=2, sentido='ida'), voo('2026-12-22', 'GRU', 'FOR', '08:00', 1300, pax=2, sentido='ida'),
                 voo('2026-12-23', 'VCP', 'FOR', '09:00', 1100, pax=2, sentido='ida')]
    c = candidatos(lista_ida, v, ida)
    # Fortaleza por Campinas (1.100 + 60) e Natal no voo comprado (1.650 + 120 - 150); o mais barato puro já está na lista
    assert [(x['orig'], x['dest'], x['saida']) for x in c] == [('VCP', 'FOR', '09:00'), ('GRU', 'NAT', '14:05')], c
    # vendedores: a melhor data de cada aeroporto; o voo já comprado entra sempre, mesmo que não esteja entre os melhores
    # (a mais barata de todas vai por último: Recife, para a mensagem poder mostrar a diferença)
    assert chaves_lupa(lista, v, volta) == [('2027-01-19', 'FOR'), ('2027-01-20', 'JDO'), ('2027-01-19', 'REC')]
    assert chaves_lupa(lista_ida, v, ida) == [('2026-12-23', 'FOR'), ('2026-12-22', 'NAT')]
    assert chaves_lupa(lista_ida[1:], v, ida) == [('2026-12-23', 'FOR'), ('2026-12-22', 'FOR'), ('2026-12-22', 'NAT')]
    assert chaves_lupa(lista_ida[1:], v, {**ida, 'voo_comprado': False}) == [('2026-12-23', 'FOR'), ('2026-12-22', 'FOR')]

    # ---- recomendada x mais barata: Fortaleza a 520 ganha de Recife a 400 (peso 200), e Recife aparece como mais barata
    ofertas = [voo('2027-01-19', 'REC', 'CGH', '03:00', 400, vend='Zupper'), voo('2027-01-19', 'FOR', 'GRU', '12:50', 520),
               voo('2027-01-19', 'FOR', 'GRU', '12:50', 505, vend='MaxMilhas'), voo('2027-01-20', 'JDO', 'GRU', '09:00', 450)]
    r = recomendar(ofertas, v, volta)
    assert (r['recomendada']['orig'], r['recomendada']['pp'], r['recomendada']['vend']) == ('FOR', 505, 'MaxMilhas'), r
    assert (r['mais_barata']['orig'], r['mais_barata']['pp']) == ('REC', 400)
    # Recife é o último caso: por mais barato que esteja, não vira a recomendada enquanto outro aeroporto tiver preço...
    r = recomendar([voo('2027-01-19', 'REC', 'CGH', '03:00', 90), *ofertas[1:]], v, volta)
    assert r['recomendada']['orig'] == 'FOR' and (r['mais_barata']['orig'], r['mais_barata']['pp']) == ('REC', 90)
    # ...e só é recomendado quando é o único com preço conferido
    r = recomendar([voo('2027-01-19', 'REC', 'CGH', '03:00', 290), voo('2027-01-20', 'REC', 'CGH', '08:00', 310)], v, volta)
    assert (r['recomendada']['orig'], r['recomendada']['pp']) == ('REC', 290) and r['mais_barata'] is None
    # Juazeiro barato o bastante para vencer o peso de R$80: vira a recomendada e não há "mais barata"
    r = recomendar([voo('2027-01-20', 'JDO', 'GRU', '09:00', 400), *ofertas[1:3]], v, volta)
    assert r['recomendada']['orig'] == 'JDO' and r['mais_barata'] is None
    # diferença pequena para a mais barata: não polui a mensagem
    r = recomendar([voo('2027-01-19', 'FOR', 'GRU', '12:50', 505), voo('2027-01-20', 'JDO', 'GRU', '09:00', 480)], v, volta)
    assert r['recomendada']['orig'] == 'FOR' and r['mais_barata'] is None
    # a recomendada anterior fica enquanto estiver a até R$15 da melhor; passa disso, troca
    par = [voo('2027-01-19', 'FOR', 'GRU', '12:50', 505), voo('2027-01-20', 'FOR', 'GRU', '06:00', 495)]
    assert recomendar(par, v, volta, anterior=('2027-01-19', 'Gol', 'FOR', 'GRU', '12:50'))['recomendada']['saida'] == '12:50'
    par[1]['pp'] = 480
    assert recomendar(par, v, volta, anterior=('2027-01-19', 'Gol', 'FOR', 'GRU', '12:50'))['recomendada']['saida'] == '06:00'
    assert recomendar([], v, volta) == {'recomendada': None, 'mais_barata': None}
    # todas até um preço: o melhor de cada voo, da mais barata à mais cara; acima do limite fica de fora
    assert [(o['orig'], o['pp']) for o in boas(ofertas, volta, 505)] == [('REC', 400), ('JDO', 450), ('FOR', 505)]
    assert [o['vend'] for o in boas(ofertas, volta, 505)][2] == 'MaxMilhas' and boas(ofertas, volta, 399) == []
    # voo comprado: com o bônus ganha de Fortaleza R$20 mais barata; preço só de lista nunca é recomendado (não entra aqui)
    r = recomendar([comprado, voo('2026-12-23', 'GRU', 'FOR', '07:00', 1630, pax=2, sentido='ida')], v, ida)
    assert eh_comprado(r['recomendada'], v) and r['mais_barata'] is None
    r = recomendar([comprado, voo('2026-12-23', 'GRU', 'FOR', '07:00', 1500, pax=2, sentido='ida')], v, ida)
    assert r['recomendada']['dest'] == 'FOR'

    # ---- pacotes: soma das recomendadas de cada cenário; cenário sem uma perna fica de fora
    grupo = {'id': 'g', 'cenarios': [{'id': 'A', 'ida': 'ida_dez', 'volta': 'volta'}, {'id': 'B', 'ida': 'ida_jan', 'volta': 'volta'}]}
    series = {'g.ida_dez': {'recomendada': {'pp': 1180}}, 'g.ida_jan': {'recomendada': None}, 'g.volta': {'recomendada': {'pp': 410}}}
    assert [(p['id'], p['pp']) for p in pacotes(series, grupo)] == [('A', 1590)] and pacotes(series, {'id': 'v'}) == []

    ts = [{'serie': 'g.volta', 'grupo': 'g', 'igual_a': 'v.volta'}, {'serie': 'h.junto', 'grupo': 'h', 'igual_a': 'v.volta'}, {'serie': 'v.volta', 'grupo': 'v', 'igual_a': None}]
    assert com_apelidos({'v.volta': {'recomendada': 1}}, ts, 'g') == {'v.volta': {'recomendada': 1}, 'g.volta': {'recomendada': 1}}

    # ---- lugares: quanto muda por pessoa com outro número de lugares no mesmo voo; diferença pequena vale zero
    assert lugares({1: 756, 2: 760, 3: 799}, 1, 15) == [{'n': 2, 'dif': 0}, {'n': 3, 'dif': 43}]
    assert lugares({1: 747, 2: 807}, 2, 15) == [{'n': 1, 'dif': -60}]
    # sem o preço dos lugares do próprio trecho, ou sem o do outro número de lugares, não há o que dizer
    assert lugares({2: 760}, 1, 15) == [] and lugares({1: 756, 2: None}, 1, 15) == [] and lugares({}, 2, 15) == []

    # ---- ida e volta numa compra só: só aparece se economiza a margem; os mesmos voos ganham de outros voos quase no mesmo preço
    i, vt = voo('2026-12-26', 'GRU', 'JDO', '14:50', 1357, cia='LATAM', sentido='ida'), voo('2027-01-08', 'FOR', 'GRU', '21:05', 807, cia='LATAM')
    pacote = {'ida': i, 'volta': vt, 'pp': 2164}
    outra_ida = voo('2026-12-26', 'GRU', 'JDO', '07:00', 0, cia='Gol', sentido='ida')
    par = lambda pp, ida=i, vend='CIA LATAM': {'pp': pp, 'vend': vend, 'link': 'https://x', 'ida': ida, 'volta': vt}
    assert juntas(pacote, [par(2150)], 30) is None and juntas(pacote, [], 30) is None
    j = juntas(pacote, [par(2050), par(2100, vend='Zupper')], 30)
    assert (j['pp'], j['vend'], j['economia'], j['mesmos']) == (2050, 'CIA LATAM', 114, True), j
    # outros voos R$20 mais baratos que os mesmos voos comprados juntos: ficam os mesmos voos
    j = juntas(pacote, [par(2030, outra_ida, 'Zupper'), par(2050)], 30)
    assert (j['pp'], j['mesmos']) == (2050, True), j
    # outros voos bem mais baratos: ganham, e a mensagem precisa dizer que são outros voos
    j = juntas(pacote, [par(1900, outra_ida, 'Zupper'), par(2050)], 30)
    assert (j['pp'], j['mesmos'], j['economia'], j['ida']['saida']) == (1900, False, 264, '07:00'), j
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    else:
        print(__doc__)
