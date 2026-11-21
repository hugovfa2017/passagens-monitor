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


def nordeste(x, v):
    return x['dest'] if x['orig'] in v['sp'] else x['orig']


def eh_comprado(x, v):
    c = v['comprado']
    return (x['d'], x['cia'], x['orig'], x['dest'], x['saida']) == (c['d'], c['cia'], c['orig'], c['dest'], c['saida'])


def peso(x, v, t):
    """Reais por pessoa somados ao preço para comparar: aeroporto do Nordeste + aeroporto de São Paulo - bônus do voo comprado."""
    ap = v['aeroportos']
    total = ap[x['orig']].get('peso', 0) + ap[x['dest']].get('peso', 0)
    return total - (v['comprado'].get('bonus', 0) if t.get('voo_comprado') and eh_comprado(x, v) else 0)


def do_trecho(itens, t):
    """Os voos ou ofertas que servem a um trecho: mesmo sentido, data na janela, mesmos lugares."""
    return [x for x in itens if x['sentido'] == t['sentido'] and x['pax'] == t['pax'] and x['d'] in t['datas']]


def candidatos(lista, v, t, anterior=None):
    """Voos da lista que vale conferir na tela de compra para um trecho: os melhores depois do peso, o mais barato de
    todos, a recomendada da rodada anterior (para saber se subiu ou sumiu) e o voo já comprado, quando o trecho pede."""
    meus = do_trecho(lista, t)
    por_ajustado = sorted(meus, key=lambda x: (x['lista_pp'] + peso(x, v, t), x['d'], x['saida']))
    escolhidos = por_ajustado[:max(1, v['selecao']['validar_por_trecho'] - 1)]
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
    """(data, aeroporto do Nordeste) que vale buscar nos vendedores para um trecho: os melhores depois do peso, sem repetir."""
    saida = []
    for x in sorted(do_trecho(lista, t), key=lambda x: (x['lista_pp'] + peso(x, v, t), x['d'], x['saida'])):
        chave = (x['d'], nordeste(x, v))
        if chave not in saida:
            saida.append(chave)
        if len(saida) >= v['selecao']['lupa_por_trecho']:
            break
    return saida


def melhor_por_voo(ofertas):
    """Do mesmo voo e mesmos lugares em vários vendedores, fica o mais barato (empate: quem veio primeiro)."""
    melhor = {}
    for o in ofertas:
        k = (o['sentido'], o['pax'], *ident(o))
        if k not in melhor or o['pp'] < melhor[k]['pp']:
            melhor[k] = o
    return list(melhor.values())


def recomendar(ofertas, v, t, anterior=None):
    """{'recomendada': oferta ou None, 'mais_barata': oferta ou None} de um trecho, só com preço confirmado.
    recomendada: menor preço + peso; se a da rodada anterior continua a até R$15 da melhor, fica ela (não fica trocando).
    mais_barata: a de menor preço puro, só quando é outro voo e economiza pelo menos selecao.margem_mais_barata."""
    meus = melhor_por_voo(do_trecho(ofertas, t))
    if not meus:
        return {'recomendada': None, 'mais_barata': None}
    ajustado = lambda o: o['pp'] + peso(o, v, t)
    rec = min(meus, key=lambda o: (ajustado(o), o['d'], o['saida']))
    if anterior:
        fica = next((o for o in meus if ident(o) == tuple(anterior)), None)
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


def _teste():
    v = {'sp': ['GRU', 'CGH', 'VCP'],
         'aeroportos': {'GRU': {}, 'CGH': {}, 'VCP': {'peso': 60}, 'FOR': {'peso': 0}, 'MVF': {'peso': 40}, 'JDO': {'peso': 80},
                        'NAT': {'peso': 120}, 'JPA': {'peso': 160}, 'REC': {'peso': 200}},
         'comprado': {'d': '2026-12-22', 'saida': '14:05', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'NAT', 'voo': 'G3 1678', 'bonus': 150},
         'selecao': {'validar_por_trecho': 3, 'lupa_por_trecho': 2, 'margem_mais_barata': 30}}
    volta = {'serie': 'v.volta', 'sentido': 'volta', 'pax': 1, 'datas': ['2027-01-19', '2027-01-20'], 'voo_comprado': False}
    ida = {'serie': 'wk.ida', 'sentido': 'ida', 'pax': 2, 'datas': ['2026-12-22', '2026-12-23'], 'voo_comprado': True}

    def voo(d, orig, dest, saida, preco, cia='Gol', pax=1, sentido='volta', **mais):
        return {'sentido': sentido, 'd': d, 'orig': orig, 'dest': dest, 'cia': cia, 'saida': saida, 'chegada': '23:00', 'tipo': 'direto',
                'pax': pax, 'lista_pp': preco, 'pp': preco, 'vend': 'CIA ' + cia, 'link': 'https://x', 'fonte': 'google', 'voos': '', **mais}

    # ---- peso: Fortaleza 0, Recife 200, Campinas +60; o voo comprado ganha o bônus só no trecho que pede
    assert peso(voo('2027-01-19', 'FOR', 'GRU', '10:00', 1), v, volta) == 0
    assert peso(voo('2027-01-19', 'REC', 'VCP', '10:00', 1), v, volta) == 260
    comprado = voo('2026-12-22', 'GRU', 'NAT', '14:05', 1650, pax=2, sentido='ida')
    assert eh_comprado(comprado, v) and peso(comprado, v, ida) == 120 - 150 and peso(comprado, v, volta) == 120
    assert not eh_comprado(voo('2026-12-22', 'CGH', 'NAT', '14:05', 1, sentido='ida'), v)

    # ---- candidatos: os 2 melhores com peso, o mais barato puro, a recomendada anterior e o voo comprado
    lista = [voo('2027-01-19', 'REC', 'CGH', '03:00', 400), voo('2027-01-19', 'FOR', 'GRU', '12:50', 520), voo('2027-01-20', 'FOR', 'GRU', '06:00', 540),
             voo('2027-01-20', 'JDO', 'GRU', '09:00', 700), voo('2027-01-21', 'FOR', 'GRU', '06:00', 100),      # fora da janela
             voo('2027-01-19', 'FOR', 'GRU', '12:50', 500, pax=2)]                                               # outros lugares
    c = candidatos(lista, v, volta)
    assert [(x['orig'], x['saida']) for x in c] == [('FOR', '12:50'), ('FOR', '06:00'), ('REC', '03:00')], c
    c = candidatos(lista, v, volta, anterior=('2027-01-20', 'Gol', 'JDO', 'GRU', '09:00'))
    assert [(x['orig'], x['saida']) for x in c][-1] == ('JDO', '09:00') and len(c) == 4
    lista_ida = [comprado, voo('2026-12-23', 'GRU', 'FOR', '07:00', 1200, pax=2, sentido='ida'), voo('2026-12-22', 'GRU', 'FOR', '08:00', 1300, pax=2, sentido='ida'),
                 voo('2026-12-23', 'VCP', 'FOR', '09:00', 1100, pax=2, sentido='ida')]
    c = candidatos(lista_ida, v, ida)
    assert [(x['orig'], x['dest'], x['saida']) for x in c] == [('VCP', 'FOR', '09:00'), ('GRU', 'FOR', '07:00'), ('GRU', 'NAT', '14:05')], c
    assert chaves_lupa(lista, v, volta) == [('2027-01-19', 'FOR'), ('2027-01-20', 'FOR')]
    assert chaves_lupa(lista_ida, v, ida) == [('2026-12-23', 'FOR'), ('2026-12-22', 'FOR')]

    # ---- recomendada x mais barata: Fortaleza a 520 ganha de Recife a 400 (peso 200), e Recife aparece como mais barata
    ofertas = [voo('2027-01-19', 'REC', 'CGH', '03:00', 400, vend='Zupper'), voo('2027-01-19', 'FOR', 'GRU', '12:50', 520),
               voo('2027-01-19', 'FOR', 'GRU', '12:50', 505, vend='MaxMilhas'), voo('2027-01-20', 'JDO', 'GRU', '09:00', 450)]
    r = recomendar(ofertas, v, volta)
    assert (r['recomendada']['orig'], r['recomendada']['pp'], r['recomendada']['vend']) == ('FOR', 505, 'MaxMilhas'), r
    assert (r['mais_barata']['orig'], r['mais_barata']['pp']) == ('REC', 400)
    # Recife barato o bastante para vencer o peso: vira a recomendada e não há "mais barata"
    r = recomendar([voo('2027-01-19', 'REC', 'CGH', '03:00', 290), *ofertas[1:]], v, volta)
    assert r['recomendada']['orig'] == 'REC' and r['mais_barata'] is None
    # diferença pequena para a mais barata: não polui a mensagem
    r = recomendar([voo('2027-01-19', 'FOR', 'GRU', '12:50', 505), voo('2027-01-20', 'JDO', 'GRU', '09:00', 480)], v, volta)
    assert r['recomendada']['orig'] == 'FOR' and r['mais_barata'] is None
    # a recomendada anterior fica enquanto estiver a até R$15 da melhor; passa disso, troca
    par = [voo('2027-01-19', 'FOR', 'GRU', '12:50', 505), voo('2027-01-20', 'FOR', 'GRU', '06:00', 495)]
    assert recomendar(par, v, volta, anterior=('2027-01-19', 'Gol', 'FOR', 'GRU', '12:50'))['recomendada']['saida'] == '12:50'
    par[1]['pp'] = 480
    assert recomendar(par, v, volta, anterior=('2027-01-19', 'Gol', 'FOR', 'GRU', '12:50'))['recomendada']['saida'] == '06:00'
    assert recomendar([], v, volta) == {'recomendada': None, 'mais_barata': None}
    # voo comprado: com o bônus ganha de Fortaleza R$20 mais barata; preço só de lista nunca é recomendado (não entra aqui)
    r = recomendar([comprado, voo('2026-12-23', 'GRU', 'FOR', '07:00', 1630, pax=2, sentido='ida')], v, ida)
    assert eh_comprado(r['recomendada'], v) and r['mais_barata'] is None
    r = recomendar([comprado, voo('2026-12-23', 'GRU', 'FOR', '07:00', 1500, pax=2, sentido='ida')], v, ida)
    assert r['recomendada']['dest'] == 'FOR'

    # ---- pacotes: soma das recomendadas de cada cenário; cenário sem uma perna fica de fora
    grupo = {'id': 'g', 'cenarios': [{'id': 'A', 'ida': 'ida_dez', 'volta': 'volta'}, {'id': 'B', 'ida': 'ida_jan', 'volta': 'volta'}]}
    series = {'g.ida_dez': {'recomendada': {'pp': 1180}}, 'g.ida_jan': {'recomendada': None}, 'g.volta': {'recomendada': {'pp': 410}}}
    assert [(p['id'], p['pp']) for p in pacotes(series, grupo)] == [('A', 1590)] and pacotes(series, {'id': 'v'}) == []
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    else:
        print(__doc__)
