"""Rodada completa no GitHub Actions, por pessoa (a viagem está em viagem.json).

1. Radar: Google Voos para cada (sentido, data, lugares) do plano (nuvem/plano.py). Uma busca cobre todos os aeroportos.
2. Conferência: os candidatos de cada trecho (nuvem/selecao.py) são abertos na tela de compra. Só esse preço vale.
3. Lupa: MaxMilhas, 123milhas, Zupper, Viaza e Vai de Promo nas melhores (data, aeroporto) de cada trecho, com prazo.
4. Escolha: a recomendada e a mais barata de cada trecho. O registrador (registrar.py) monta a mensagem por pessoa.
5. Extras, depois da escolha: o voo recomendado com outro número de lugares (tela de compra do Google, aberta direto
   pelo número do voo) e as duas recomendadas de cada cenário numa compra só de ida e volta (Zupper).

Nada aqui tenta contornar bloqueio: se o Google pedir verificação, a rodada segue só com os vendedores.

Rodada rápida (--rapida), para o intervalo entre duas completas: só as listas do Google e, onde a lista mostrar um voo
mais barato que a recomendada guardada, a tela de compra desse voo. Se a queda se confirma, avisa na hora; senão, não
manda nada e não mexe no histórico.

Uso: python nuvem/rodar.py           (roda tudo e registra)
     python nuvem/rodar.py --rapida  (só o Google; avisa se algum preço caiu)
     python nuvem/rodar.py --teste   (auto-checagem das etapas, sem navegador)
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
PRAZO_EXTRAS_S = 120      # lugares e ida e volta numa compra só: o que não couber não aparece na mensagem
PARALELO_COMPRA = 3       # telas de compra abertas direto ao mesmo tempo (mais que isso, o Google responde "Algo deu errado")
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


def quedas_possiveis(lista, ts, v, estado):
    """As séries em que a lista do Google mostra um voo que, depois do peso dos aeroportos, sairia pelo menos
    selecao.queda_rapida mais barato que a recomendada guardada no estado. Numa rodada rápida só essas vão para a tela de
    compra (o preço da lista pode estar velho). Trecho sem recomendada guardada fica de fora: quem estabelece o preço é
    a rodada completa. Aeroporto de último caso não conta."""
    saida = set()
    for t in ts:
        antes = estado.get('series', {}).get(t['serie'], {}).get('rec')
        if t['igual_a'] or not antes:
            continue
        d, cia, orig, dest, hora = antes['ident']
        referencia = antes['pp'] + selecao.peso({'d': d, 'cia': cia, 'orig': orig, 'dest': dest, 'saida': hora}, v, t)
        melhor = min((x['lista_pp'] + selecao.peso(x, v, t) for x in selecao.do_trecho(lista, t) if not selecao.ultimo_caso(x, v)), default=None)
        if melhor is not None and melhor <= referencia - v['selecao'].get('queda_rapida', v['selecao']['queda_min']):
            saida.add(t['serie'])
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
                      'pp_cia': cia, 'voos': voos, 'fonte': 'google', 'aviso': gvoos.ler_aviso((r or {}).get('texto') or '', x['pax']),
                      'fora': gvoos.melhor_de_fora(vend),
                      'link': gvoos.url_compra([(x['d'], de, para, voos)], x['pax']) or x['url']})
    return saida


def telas_do_comprado(v, ts, ofertas):
    """Telas de compra do voo já comprado (aberto direto pelo número do voo), para os lugares dos trechos que pedem e
    que a conferência ainda não cobriu: [{pax, sentido, voos, url}]. É a resposta definitiva sobre haver lugar à venda."""
    c = v['comprado']
    sentido = 'ida' if c['orig'] in v['sp'] else 'volta'
    voos = f"{c['orig']}>{c['dest']} {c['d']} {c['voo']}"
    de, para = plano.aeroportos(v, sentido)
    lugares = sorted({t['pax'] for t in ts if t['voo_comprado'] and not t['igual_a'] and c['d'] in t['datas']})
    ja = {o['pax'] for o in ofertas if selecao.eh_comprado(o, v)}
    return [{'pax': n, 'sentido': sentido, 'voos': voos, 'url': gvoos.url_compra([(c['d'], de, para, voos)], n)} for n in lugares if n not in ja]


def ofertas_do_comprado(telas, respostas, v):
    """Ofertas do voo já comprado lidas nas telas de telas_do_comprado (só quando há vendedor que conta)."""
    c, saida = v['comprado'], []
    for t, r in zip(telas, respostas):
        texto = (r or {}).get('texto') or ''
        vend = gvoos.ler_compra(texto, t['pax'])
        melhor = gvoos.melhor_vendedor(vend)
        if melhor:
            saida.append({'sentido': t['sentido'], 'd': c['d'], 'pax': t['pax'], 'orig': c['orig'], 'dest': c['dest'], 'cia': c['cia'], 'saida': c['saida'],
                          'chegada': c.get('chegada', ''), 'tipo': 'direto', 'pp': melhor['pp'], 'vend': melhor['vend'],
                          'pp_cia': next((q['pp'] for q in vend if q['vend'].startswith('CIA ')), None), 'voos': t['voos'], 'fonte': 'google',
                          'aviso': gvoos.ler_aviso(texto, t['pax']), 'fora': gvoos.melhor_de_fora(vend), 'link': t['url']})
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
        # primeiro o que a rodada anterior achou de melhor (a recomendada e a mais barata): preço bom, uma vez achado,
        # é conferido de novo toda rodada até sumir; depois as melhores chaves da lista desta rodada
        antes = estado.get('series', {}).get(t['serie'], {})
        fixas = [(x['d'], x['ne']) for x in (antes.get('rec'), antes.get('barata')) if x and x['d'] in t['datas']]
        chaves = fixas + [c for c in selecao.chaves_lupa(lista, v, t) if c not in fixas]
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
    """O resultado de cada trecho: {serie: {recomendada, mais_barata, comprado, celulas, olhadas, aviso, fora}}.
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
        # o que o Google diz do preço do voo recomendado (baixo, normal, alto), se ele passou pela tela de compra nesta rodada
        rec = r['recomendada']
        do_voo = [o for o in selecao.do_trecho(ofertas, t) if rec and selecao.ident(o) == selecao.ident(rec)]
        r['aviso'] = next((o['aviso'] for o in do_voo if o.get('aviso')), None)
        # o mesmo voo numa agência de fora do Brasil (não entra na escolha; a mensagem cita quando é bem mais barata)
        r['fora'] = min((o['fora'] for o in do_voo if o.get('fora')), key=lambda q: q['pp'], default=None)
        series[t['serie']] = r
    return series


def plano_dos_extras(v, ts, series):
    """O que conferir depois da escolha das recomendadas. Devolve (telas, pares):
    telas: [{serie, pax, n, url}] -> tela de compra do Google do voo recomendado com n lugares: com os lugares do próprio
           trecho (para ler o aviso de preço do Google, se ainda não veio) e com os outros números que o trecho pede
           (viagem.json: lugares). Só dá para abrir quando o número do voo é conhecido;
    pares: [{chave, pax, ida, volta}] -> as duas recomendadas de cada cenário dos grupos com ida_e_volta, para buscar
           numa compra só. chave = 'grupo.cenário'."""
    telas, pares = [], []
    for t in ts:
        r = series.get(t['serie']) or {}
        rec = r.get('recomendada')
        if t['igual_a'] or not rec or not rec.get('voos') or (not t['lugares'] and r.get('aviso')):
            continue
        de, para = plano.aeroportos(v, t['sentido'])
        telas += [{'serie': t['serie'], 'pax': t['pax'], 'n': n, 'url': gvoos.url_compra([(rec['d'], de, para, rec['voos'])], n)}
                  for n in sorted({t['pax'], *t['lugares']})]
    for g in v['grupos']:
        if g.get('ida_e_volta'):
            pares += [{'chave': f"{g['id']}.{p['id']}", 'pax': g['assentos'], 'ida': p['ida'], 'volta': p['volta']}
                      for p in selecao.pacotes(selecao.com_apelidos(series, ts, g['id']), g)]
    return telas, pares


def avisos_lidos(telas, respostas):
    """{serie: {'aviso': aviso de preço do Google, 'fora': agência de fora mais barata}} lidos na tela dos lugares do
    próprio trecho (gvoos.ler_aviso, gvoos.melhor_de_fora). Só entra o que a tela trouxe."""
    saida = {}
    for t, r in zip(telas, respostas):
        if t['n'] != t['pax']:
            continue
        texto = (r or {}).get('texto') or ''
        lido = {'aviso': gvoos.ler_aviso(texto, t['n']), 'fora': gvoos.melhor_de_fora(gvoos.ler_compra(texto, t['n']))}
        lido = {k: x for k, x in lido.items() if x}
        if lido:
            saida[t['serie']] = lido
    return saida


def lugares_lidos(telas, respostas):
    """{serie: [{n, pp}]}: preço por pessoa do mesmo voo com cada número de lugares, sempre no mesmo vendedor, para a
    diferença ser só dos lugares: o mais barato que conta entre os que aparecem em todas as telas lidas (em geral a
    própria companhia); se nenhum aparece em todas, o mais barato da tela dos lugares do próprio trecho.
    Sem a tela do próprio trecho, a série fica de fora."""
    por_serie = {}
    for t, r in zip(telas, respostas):
        vend = gvoos.ler_compra((r or {}).get('texto') or '', t['n'])
        if vend:
            por_serie.setdefault(t['serie'], {'pax': t['pax'], 'telas': {}})['telas'][t['n']] = vend
    saida = {}
    for serie, x in por_serie.items():
        base = x['telas'].get(x['pax']) or []
        em_todas = set.intersection(*({q['vend'] for q in vend} for vend in x['telas'].values()))
        escolhido = gvoos.melhor_vendedor([q for q in base if q['vend'] in em_todas]) or gvoos.melhor_vendedor(base)
        if not escolhido:
            continue
        precos = [{'n': n, 'pp': next((q['pp'] for q in vend if q['vend'] == escolhido['vend']), None)} for n, vend in sorted(x['telas'].items())]
        saida[serie] = [q for q in precos if q['pp']]
    return saida


def opcoes_juntas(par, achados):
    """Opções de comprar ida e volta numa compra só (formato de selecao.juntas), a partir do que a Zupper devolveu para
    as datas e os aeroportos do par (vendedores.buscar_iv): os mesmos voos do pacote, quando algum cartão os oferece, e
    o par mais barato de todos (que pode ser em outros horários)."""
    ida, volta = par['ida'], par['volta']
    igual = lambda o, x: (o['cia'], o['orig'], o['dest'], o['saida']) == (x['cia'], x['orig'], x['dest'], x['saida'])
    melhor = lambda ops: min(ops, key=lambda o: (not o['direto'], o['dur']))
    perna = lambda o, x: {'sentido': x['sentido'], 'd': x['d'], 'pax': par['pax'], 'orig': o['orig'], 'dest': o['dest'], 'cia': o['cia'],
                          'saida': o['saida'], 'chegada': o['chegada'], 'tipo': o['tipo'], 'voos': ''}

    def opcao(c, mesmos):
        i = ida if mesmos else next((perna(o, ida) for o in c['idas'] if igual(o, ida)), None) or perna(melhor(c['idas']), ida)
        vt = volta if mesmos else next((perna(o, volta) for o in c['voltas'] if igual(o, volta)), None) or perna(melhor(c['voltas']), volta)
        link = vendedores.url_zupper_iv(i['dest'], vt['orig'], i['d'], vt['d'], par['pax'], i['orig'], vt['dest'])
        return {'pp': c['pp'], 'vend': c['vend'], 'link': link, 'ida': i, 'volta': vt}

    iguais = [c for c in achados if any(igual(o, ida) for o in c['idas']) and any(igual(o, volta) for o in c['voltas'])]
    saida = [opcao(min(iguais, key=lambda c: c['pp']), True)] if iguais else []
    barato = min(achados, key=lambda c: c['pp'], default=None)
    if barato and barato not in iguais:
        saida.append(opcao(barato, False))
    return saida


# ---------------------------------------------------------------- etapas com navegador
async def abrir_google(nav):
    """Abre o Google Voos numa sessão nova. Devolve (sessão, página, texto do bloqueio ou None)."""
    ctx = await nav.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
    pg = await ctx.new_page()
    try:
        await pg.goto(GOOGLE, wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(3000)
        inicio = (await pg.inner_text('body'))[:3000]
        if re.search(BLOQUEIO, inicio, re.I):
            return ctx, pg, inicio[:200].replace('\n', ' | ')
        await pg.evaluate(open(os.path.join(RAIZ, 'varredura.js'), encoding='utf-8').read())
        return ctx, pg, None
    except Exception:
        await ctx.close()
        raise


async def radar_e_conferencia(nav, v, ts, bs, estado, rapida=False):
    """Google Voos: lista de cada busca e tela de compra dos candidatos. Devolve (lista, ofertas, resumo da fonte).
    rapida: só vão para a tela de compra os trechos em que a lista mostra queda (quedas_possiveis)."""
    info = {'buscas': len(bs), 'voos': 0, 'conferidos': 0, 'ofertas': 0, 'bloqueio': None}
    try:
        ctx, pg, bloqueio = await abrir_google(nav)
    except Exception as e:   # o Google nem abriu: a rodada segue com os vendedores
        info['erro'] = f'{type(e).__name__}: {str(e)[:120]}'
        return [], [], info
    try:
        if bloqueio:
            info['bloqueio'] = bloqueio
            return [], [], info
        chamar = lambda fn, itens: asyncio.wait_for(pg.evaluate(f'(a) => window.__gv.{fn}(a.itens, a.paralelo)',
                                                                {'itens': itens, 'paralelo': PARALELO}), timeout=600)
        respostas = await chamar('listar', [b['url'] for b in bs])
        lista = voos_da_lista(bs, respostas, v)
        # saúde das listas: quantas buscas vieram vazias (o Google demorou ou falhou) e quantas precisaram de segunda tentativa
        info['listas'] = {'vazias': sum(1 for r in respostas if not (r or {}).get('rotulos')), 'repetidas': sum(1 for r in respostas if (r or {}).get('repetida')),
                          'exemplo': next(((r or {}).get('inicio', '')[:120].replace('\n', ' | ') for r in respostas if (r or {}).get('repetida')), None)}
        if rapida:
            com_queda = quedas_possiveis(lista, ts, v, estado)
            info['com_queda'] = sorted(com_queda)
        cands = a_conferir(lista, [t for t in ts if not rapida or t['serie'] in com_queda], v, estado)
        info.update(voos=len(lista), conferidos=len(cands), bloqueio=next((r['bloqueio'] for r in respostas if (r or {}).get('bloqueio')), None))
        if info['bloqueio']:
            return lista, [], info
        validados = await chamar('validar', [{'url': x['url'], 'chave': x['chave']} for x in cands]) if cands else []
        ofertas = ofertas_google(list(zip(cands, validados)), v)
        motivos = {}
        for x, r in zip(cands, validados):
            if (r or {}).get('erro'):
                motivos[r['erro'][:40]] = motivos.get(r['erro'][:40], 0) + 1
            elif not gvoos.melhor_vendedor(gvoos.ler_compra((r or {}).get('texto') or '', x['pax'])):
                motivos['nenhum vendedor que conta'] = motivos.get('nenhum vendedor que conta', 0) + 1
        info.update(ofertas=len(ofertas), sem_preco=motivos, bloqueio=next((r['bloqueio'] for r in validados if (r or {}).get('bloqueio')), None))
        # o voo já comprado, direto pelo número: mesmo fora da lista do Google, a tela de compra diz se há lugar à venda
        telas = [] if info['bloqueio'] or rapida else telas_do_comprado(v, ts, ofertas)
        if telas:
            abertas = await asyncio.wait_for(pg.evaluate('(a) => window.__gv.comprar(a.itens, a.paralelo)',
                                                         {'itens': [t['url'] for t in telas], 'paralelo': PARALELO_COMPRA}), timeout=120)
            do_comprado = ofertas_do_comprado(telas, abertas, v)
            ofertas += do_comprado
            info['comprado'] = {'telas': len(telas), 'a_venda': len(do_comprado)}
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


async def extras(nav, v, ts, series, google_ok, prazo_s=PRAZO_EXTRAS_S):
    """Depois da escolha: lugares (Google) e ida e volta numa compra só (Zupper), ao mesmo tempo e com prazo.
    Grava series[serie]['lugares'] e devolve ({chave do cenário: opções de compra junta}, resumo). Nada aqui derruba a rodada."""
    telas, pares = plano_dos_extras(v, ts, series)
    fim, juntas = time.monotonic() + prazo_s, {}
    resumo = {'telas': len(telas), 'telas_lidas': 0, 'pares': len(pares), 'pares_lidos': 0}

    async def lugares():
        if not telas or not google_ok:
            return
        try:
            ctx, pg, bloqueio = await abrir_google(nav)
        except Exception as e:
            resumo['erro_google'] = type(e).__name__
            return
        try:
            if bloqueio:
                resumo['bloqueio'] = bloqueio
                return
            respostas = await asyncio.wait_for(pg.evaluate('(a) => window.__gv.comprar(a.itens, a.paralelo)',
                                                           {'itens': [t['url'] for t in telas], 'paralelo': PARALELO_COMPRA}), timeout=prazo_s)
            resumo['telas_lidas'] = sum(1 for r in respostas if (r or {}).get('texto'))
            for serie, precos in lugares_lidos(telas, respostas).items():
                if len(precos) > 1:
                    series[serie]['lugares'] = precos
            for serie, lido in avisos_lidos(telas, respostas).items():
                for k, x in lido.items():
                    series[serie][k] = series[serie].get(k) or x
        except Exception as e:
            resumo['erro_google'] = type(e).__name__
        finally:
            await ctx.close()

    async def ida_e_volta():
        sem = asyncio.Semaphore(1)
        for par in pares:
            if time.monotonic() > fim:
                return
            try:
                achados = await vendedores.buscar_iv(nav, par['ida']['d'], par['volta']['d'], selecao.nordeste(par['ida'], v),
                                                     selecao.nordeste(par['volta'], v), par['pax'], sem)
            except Exception:
                achados = []
            resumo['pares_lidos'] += bool(achados)
            juntas[par['chave']] = opcoes_juntas(par, achados)
            await asyncio.sleep(2)

    await asyncio.gather(lugares(), ida_e_volta())
    return juntas, resumo


def preparar(v, estado, agora):
    """(trechos, buscas do Google com endereço, {serie: datas olhadas}) de uma rodada. agora: datetime no fuso de Brasília."""
    hoje = agora.strftime('%Y-%m-%d')
    ts = plano.trechos(v, hoje)
    bs = plano.buscas(v, hoje, estado)
    olhadas = {}
    for b in bs:
        b['url'] = gvoos.url_busca([(b['d'], b['de'], b['para'], '')], b['pax'])
        for s in b['series']:
            olhadas.setdefault(s, []).append(b['d'])
    return ts, bs, olhadas


def opcoes_do_navegador():
    opcoes = {'headless': True}
    if os.environ.get('PASSAGENS_CHROMIUM'):          # navegador já instalado na máquina (uso fora do GitHub)
        opcoes['executable_path'] = os.environ['PASSAGENS_CHROMIUM']
    if os.environ.get('HTTPS_PROXY'):
        opcoes['proxy'] = {'server': os.environ['HTTPS_PROXY']}
    return opcoes


async def varrer_rapido(v, estado, agora):
    """Rodada rápida: só o Google. Devolve um scan só com as recomendadas dos trechos em que a queda foi conferida."""
    from playwright.async_api import async_playwright
    ts, bs, olhadas = preparar(v, estado, agora)
    scan = {'versao': 2, 'modo': 'rapida', 'inicio': datetime.now(timezone.utc).isoformat()}
    async with async_playwright() as p:
        nav = await p.chromium.launch(**opcoes_do_navegador())
        t0 = time.monotonic()
        lista, ofertas, google = await radar_e_conferencia(nav, v, ts, bs, estado, rapida=True)
        google['seg'] = round(time.monotonic() - t0)
        print('GOOGLE:', json.dumps(google, ensure_ascii=False))
        await nav.close()
    scan.update(status='erro' if google.get('bloqueio') or google.get('erro') else 'ok', fim=datetime.now(timezone.utc).isoformat(),
                fontes={'Google': google}, series=montar(v, ts, lista, ofertas, estado, olhadas))
    return scan


async def varrer(v, estado, agora):
    """Uma rodada inteira. agora: datetime no fuso de Brasília. Devolve o scan (versão 2) para o registrador."""
    from playwright.async_api import async_playwright
    ts, bs, olhadas = preparar(v, estado, agora)
    vendedores.configurar(v)
    scan = {'versao': 2, 'inicio': datetime.now(timezone.utc).isoformat()}
    async with async_playwright() as p:
        nav = await p.chromium.launch(**opcoes_do_navegador())
        t0 = time.monotonic()
        lista, ofertas, google = await radar_e_conferencia(nav, v, ts, bs, estado)
        google['seg'] = round(time.monotonic() - t0)
        print('GOOGLE:', json.dumps(google, ensure_ascii=False))
        t0 = time.monotonic()
        fila = fila_da_lupa(lista, ts, v, estado, agora.hour)
        do_vendedor, fontes = await lupa(nav, fila)
        print(f'VENDEDORES: {len(fila)} chaves em {round(time.monotonic() - t0)}s |', json.dumps(fontes, ensure_ascii=False))
        ofertas = completar_numeros(ofertas + do_vendedor)
        series = montar(v, ts, lista, ofertas, estado, olhadas)
        t0 = time.monotonic()
        juntas, resumo = await extras(nav, v, ts, series, not (google.get('bloqueio') or google.get('erro')))
        resumo['seg'] = round(time.monotonic() - t0)
        print('EXTRAS:', json.dumps(resumo, ensure_ascii=False))
        await nav.close()
    mossoro = [o for o in do_vendedor if 'MVF' in (o['orig'], o['dest'])]
    achou = any(s['recomendada'] for s in series.values())
    scan.update(status='ok' if achou else 'erro', fim=datetime.now(timezone.utc).isoformat(),
                erro=None if achou else ('Google pediu verificação e nenhum vendedor respondeu' if google.get('bloqueio') else 'nenhum voo com preço confirmado'),
                fontes={'Google': google, **fontes}, series=series, juntas=juntas, extras=resumo, taxa_mala=taxas_mala(do_vendedor),
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
                                          rotulo(900, 'LATAM', 'NAT', 'GRU', '10:00', '13:30'), rotulo(300, 'Azul', 'FOR', 'VCP', '09:00', '12:30')]})
        elif (b['sentido'], b['d'], b['pax']) == ('ida', '2026-12-22', 2):
            respostas.append({'rotulos': [rotulo('3.300', 'Gol', 'GRU', 'NAT', '14:05', '17:25'), rotulo('2.400', 'LATAM', 'GRU', 'FOR', '07:00', '10:30')]})
        else:
            respostas.append({'rotulos': []} if b['sentido'] == 'ida' else None)
    lista = voos_da_lista(bs, respostas, v)
    # o voo por Campinas (o mais barato da lista) não entra: Campinas só vale em voo direto com Mossoró
    assert len(lista) == 5 and {(x['sentido'], x['d'], x['pax']) for x in lista} == {('volta', '2027-01-19', 1), ('ida', '2026-12-22', 2)}
    assert not [x for x in lista if 'VCP' in (x['orig'], x['dest'])]
    assert lista[0]['url'].startswith('https://google/') and next(x for x in lista if x['dest'] == 'NAT' and x['pax'] == 2)['lista_pp'] == 1650

    # ---- conferência: candidatos de cada trecho, cada voo uma vez; o voo já comprado entra no trecho de 2 lugares
    cands = a_conferir(lista, ts, v, {})
    # (Natal a R$900 não entra: com o peso fica atrás de Fortaleza e de Recife, e não é a mais barata)
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
    assert og[0]['aviso'] is None and og[0]['fora'] == {'vend': 'Kiwi.com', 'pp': 300} and og[1]['fora'] is None

    # ---- voo já comprado direto na tela de compra: só para os lugares que a conferência não cobriu (aqui, 1 lugar: G; W + K já veio da lista)
    telas_c = telas_do_comprado(v, ts, og)
    assert [(t['pax'], t['sentido'], t['voos']) for t in telas_c] == [(1, 'ida', 'GRU>NAT 2026-12-22 G3 1678')], telas_c
    assert gvoos.voos_do_tfs(telas_c[0]['url']) == 'GRU>NAT 2026-12-22 G3 1678' and [t['pax'] for t in telas_do_comprado(v, ts, [])] == [1, 2]
    oc = ofertas_do_comprado(telas_c, [compra(('GolCompanhia aérea', '1.480'), ('Kiwi.com', '1.300'))], v)
    assert len(oc) == 1 and selecao.eh_comprado(oc[0], v) and (oc[0]['pp'], oc[0]['vend'], oc[0]['pax'], oc[0]['chegada'], oc[0]['tipo']) == (1480, 'CIA Gol', 1, '17:25', 'direto')
    assert oc[0]['fora'] == {'vend': 'Kiwi.com', 'pp': 1300} and oc[0]['link'] == telas_c[0]['url']
    # sem vendedor que conta (ou tela que não abriu: voo sem lugar), não há oferta
    assert ofertas_do_comprado(telas_c, [compra(('Kiwi.com', '900'))], v) == [] and ofertas_do_comprado(telas_c, [{'erro': 'tela de compra sem vendedores'}], v) == []
    # passada a data do voo, não há mais o que conferir
    assert telas_do_comprado(v, plano.trechos(v, '2026-12-22'), []) == []

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
    # o que a rodada anterior achou de melhor vem primeiro (recomendada e mais barata), mesmo com o Google bloqueado (sem lista)
    estado = {'series': {'v.volta': {'rec': {'d': '2027-01-20', 'ne': 'JDO', 'ident': ['2027-01-20', 'Gol', 'JDO', 'GRU', '09:00']},
                                     'barata': {'d': '2027-01-22', 'ne': 'REC', 'pp': 411}}}}
    assert fila_da_lupa([], ts, v, estado, 0)[0] == ('volta', '2027-01-20', 'JDO', 1)
    com_memoria = fila_da_lupa(lista, ts, v, estado, 0)
    assert com_memoria[0] == ('volta', '2027-01-20', 'JDO', 1) and ('volta', '2027-01-22', 'REC', 1) in com_memoria[:6] and len(com_memoria) == len(set(com_memoria))
    # data que saiu da janela não é mais buscada
    velho = {'series': {'v.volta': {'rec': {'d': '2027-01-05', 'ne': 'FOR'}}}}
    assert ('volta', '2027-01-05', 'FOR', 1) not in fila_da_lupa(lista, ts, v, velho, 0)

    # ---- rodada rápida: só vale conferir o trecho em que a lista mostra um voo R$40 ou mais abaixo da recomendada guardada, já com o peso
    guardado = lambda pp, orig='FOR': {'series': {'v.volta': {'rec': {'ident': ['2027-01-19', 'Gol', orig, 'GRU', '12:50'], 'pp': pp}}}}
    # na lista: Fortaleza a R$520, Natal a R$900 (+120 de peso), Recife a R$400 (último caso: não conta)
    assert v['selecao']['queda_rapida'] == 40
    assert quedas_possiveis(lista, ts, v, guardado(560)) == {'v.volta'} and quedas_possiveis(lista, ts, v, guardado(550)) == set()
    # a recomendada guardada era por Natal (R$500 + 120 de peso): Fortaleza a R$520 já é melhor
    assert quedas_possiveis(lista, ts, v, guardado(500, 'NAT')) == {'v.volta'}
    # trecho sem recomendada guardada, ou sem voo na lista, fica de fora
    assert quedas_possiveis(lista, ts, v, {}) == set() and quedas_possiveis([], ts, v, guardado(900)) == set()

    # ---- montagem: recomendada com peso, mais barata, voo comprado, células por data e aeroporto
    olhadas = {'v.volta': ['2027-01-19'], 'wk.ida': ['2026-12-22']}
    series = montar(v, ts, lista, og + [ov], {}, olhadas)
    assert set(series) == {'v.volta', 'h.depois', 'g.ida_dez', 'g.ida_jan', 'wk.ida', 'wk.volta'}      # igual_a não tem resultado próprio
    sv = series['v.volta']
    assert (sv['recomendada']['orig'], sv['recomendada']['pp']) == ('FOR', 505) and (sv['mais_barata']['orig'], sv['mais_barata']['vend']) == ('REC', 'Zupper')
    assert sv['celulas'] == {'2027-01-19': {'FOR': {'pp': 505, 'conf': True}, 'REC': {'pp': 360, 'conf': True}, 'NAT': {'pp': 900, 'conf': False}}}, sv['celulas']
    assert sv['olhadas'] == ['2027-01-19'] and 'comprado' not in sv and sv['aviso'] is None
    # o aviso de preço do Google acompanha a recomendada quando o mesmo voo passou pela tela de compra (mesmo que o melhor preço seja de outro vendedor)
    com = montar(v, ts, lista, [{**og[0], 'aviso': {'nivel': 'baixo', 'de': 900, 'ate': 2000}}, {**og[0], 'vend': 'Zupper', 'pp': 400, 'aviso': None}], {}, olhadas)['v.volta']
    assert com['recomendada']['vend'] == 'Zupper' and com['aviso'] == {'nivel': 'baixo', 'de': 900, 'ate': 2000} and com['fora'] == {'vend': 'Kiwi.com', 'pp': 300}
    assert sv['fora'] == {'vend': 'Kiwi.com', 'pp': 300} and series['wk.ida']['fora'] is None
    sw = series['wk.ida']
    # o voo comprado (Natal, R$1.650) perde para Fortaleza R$1.250 mesmo com o bônus, mas fica registrado à parte
    assert sw['recomendada']['dest'] == 'FOR' and sw['comprado']['pp'] == 1650 and sw['mais_barata'] is None
    assert series['g.ida_dez']['recomendada'] is None and series['g.ida_dez']['comprado'] is None and series['h.depois']['celulas'] == {}

    # ---- extras: o voo recomendado com outro número de lugares (só com o número do voo) e os pares de ida e volta
    rec = lambda d, orig, dest, saida, pp, cia, sentido, pax=1, voos='': {
        'sentido': sentido, 'd': d, 'pax': pax, 'orig': orig, 'dest': dest, 'cia': cia, 'saida': saida, 'chegada': '23:00', 'tipo': 'direto',
        'pp': pp, 'vend': 'Viaza', 'voos': voos, 'fonte': 'vendedor', 'link': 'https://x'}
    escolhidas = {'v.volta': {'recomendada': rec('2027-01-22', 'FOR', 'GRU', '02:10', 756, 'LATAM', 'volta', voos='FOR>GRU 2027-01-22 LA 4777')},
                  'h.depois': {'recomendada': rec('2027-02-21', 'FOR', 'CGH', '08:55', 364, 'Gol', 'volta', voos='FOR>CGH 2027-02-21 G3 1511')},
                  'g.ida_dez': {'recomendada': rec('2026-12-29', 'GRU', 'JDO', '21:25', 1326, 'Gol', 'ida', voos='GRU>JDO 2026-12-29 G3 1580')},
                  'g.ida_jan': {'recomendada': None},
                  'wk.ida': {'recomendada': rec('2026-12-26', 'GRU', 'JDO', '14:50', 1357, 'LATAM', 'ida', 2)},          # vendedor sem número do voo
                  'wk.volta': {'recomendada': rec('2027-01-08', 'FOR', 'GRU', '21:05', 807, 'LATAM', 'volta', 2, 'FOR>GRU 2027-01-08 LA 3361')}}
    telas, pares = plano_dos_extras(v, ts, escolhidas)
    # V: 1, 2 e 3 lugares; W + K na volta: 1 e 2; a ida de W + K fica de fora (sem número do voo não há endereço);
    # quem não pede outros lugares tem só a tela do próprio trecho, para o aviso de preço do Google
    assert [(t['serie'], t['pax'], t['n']) for t in telas] == [('v.volta', 1, 1), ('v.volta', 1, 2), ('v.volta', 1, 3), ('h.depois', 1, 1), ('g.ida_dez', 1, 1),
                                                               ('wk.volta', 2, 1), ('wk.volta', 2, 2)], telas
    assert all(t['url'].startswith('https://www.google.com/travel/flights/booking?tfs=') for t in telas) and len({t['url'] for t in telas}) == 7
    # aviso que já veio da conferência não é pedido de novo; quem pede outros lugares abre as telas mesmo assim
    com_aviso = {k: {**x, 'aviso': {'nivel': 'baixo', 'de': 1, 'ate': 2}} for k, x in escolhidas.items()}
    assert [t['serie'] for t in plano_dos_extras(v, ts, com_aviso)[0]] == ['v.volta'] * 3 + ['wk.volta'] * 2
    telas = [t for t in telas if t['serie'] in ('v.volta', 'wk.volta')]
    assert gvoos.voos_do_tfs(telas[1]['url']) == 'FOR>GRU 2027-01-22 LA 4777'
    # cenário sem uma das recomendadas (G indo em janeiro) não gera par; a volta de G é a de V
    assert [(x['chave'], x['pax'], x['ida']['d'], x['volta']['d']) for x in pares] == [('g.A', 1, '2026-12-29', '2027-01-22'), ('wk.U', 2, '2026-12-26', '2027-01-08')], pares
    tela = lambda *vend: {'texto': ''.join(f'Reserve com a {n}\nR$ {p}\nContinuar\n' for n, p in vend)}
    respostas = [tela(('LATAMCompanhia aérea', '786'), ('MaxMilhas', '760')), tela(('LATAMCompanhia aérea', '1.572'), ('MaxMilhas', '1.530')),
                 tela(('LATAMCompanhia aérea', '2.490')),      # com 3 lugares a MaxMilhas não aparece
                 {'erro': 'tela de compra sem vendedores'}, tela(('LATAMCompanhia aérea', '1.714'))]
    lidos = lugares_lidos(telas, respostas)
    # V: o mesmo vendedor nas três telas (a LATAM, a única que aparece em todas), e não o mais barato de cada uma;
    # W + K: a tela de 1 lugar falhou, sobra só a dos 2 (sem comparação possível)
    assert lidos == {'v.volta': [{'n': 1, 'pp': 786}, {'n': 2, 'pp': 786}, {'n': 3, 'pp': 830}], 'wk.volta': [{'n': 2, 'pp': 857}]}, lidos
    # só as telas de 1 e 2 lugares: a MaxMilhas está nas duas e é a mais barata, então vale ela
    assert lugares_lidos(telas[:2], respostas[:2]) == {'v.volta': [{'n': 1, 'pp': 760}, {'n': 2, 'pp': 765}]}
    assert lugares_lidos(telas[:3], [None, respostas[1], respostas[2]]) == {}          # sem a tela dos lugares do próprio trecho, nada
    # o aviso de preço do Google sai da tela dos lugares do próprio trecho (V: 1 lugar; W + K: 2 lugares)
    aviso = lambda preco, faixa: {'texto': f'Reserve com a LATAMCompanhia aérea\nR$ {preco}\nR$ {preco} é um preço baixo para Econômica\ngeralmente custam R$ {faixa}.'}
    assert avisos_lidos(telas, [aviso('786', '1.250 a 3.250'), aviso('1.572', '2.500 a 6.500'), None, aviso('857', '1.250 a 3.250'), aviso('1.714', '2.550 a 6.500')]) == {
        'v.volta': {'aviso': {'nivel': 'baixo', 'de': 1250, 'ate': 3250}}, 'wk.volta': {'aviso': {'nivel': 'baixo', 'de': 1275, 'ate': 3250}}}
    assert avisos_lidos(telas, respostas) == {}
    # a mesma tela traz a agência de fora mais barata (só a dos lugares do próprio trecho conta)
    assert avisos_lidos(telas[:2], [tela(('Kiwi.com', '500'), ('GolCompanhia aérea', '600')), tela(('Kiwi.com', '700'))]) == {'v.volta': {'fora': {'vend': 'Kiwi.com', 'pp': 500}}}
    # vendedor que não conta (Kiwi) não vira a referência, mesmo aparecendo em todas
    assert lugares_lidos(telas[:2], [tela(('Kiwi.com', '500'), ('GolCompanhia aérea', '600')), tela(('Kiwi.com', '900'), ('GolCompanhia aérea', '1.300'))]) == {
        'v.volta': [{'n': 1, 'pp': 600}, {'n': 2, 'pp': 650}]}

    # ---- ida e volta numa compra só: os mesmos voos do pacote e o par mais barato (cartões como os da Zupper)
    hora = lambda cia, orig, dest, saida, direto=True, dur=200: {'cia': cia, 'orig': orig, 'dest': dest, 'saida': saida, 'chegada': '23:59', 'dur': dur,
                                                                 'direto': direto, 'voo': None, 'tipo': 'direto' if direto else 'com escala'}
    cartao = lambda pp, idas, voltas: {'vend': 'Zupper', 'pp': pp, 'link': 'https://zupper/par', 'idas': idas, 'voltas': voltas}
    par = pares[1]
    achados = [cartao(2203, [hora('LATAM', 'GRU', 'JDO', '14:50')], [hora('Gol', 'FOR', 'GRU', '12:50')]),
               cartao(2021, [hora('LATAM', 'GRU', 'JDO', '14:50')], [hora('LATAM', 'FOR', 'CGH', '04:10'), hora('LATAM', 'FOR', 'GRU', '21:05')]),
               cartao(2100, [hora('LATAM', 'GRU', 'JDO', '14:50')], [hora('LATAM', 'FOR', 'GRU', '21:05')])]
    ops = opcoes_juntas(par, achados)
    # os mesmos voos existem (no cartão de R$2.021, entre os horários da volta): uma opção só, com as pernas do próprio pacote
    assert len(ops) == 1 and ops[0]['pp'] == 2021 and ops[0]['ida'] is par['ida'] and ops[0]['volta'] is par['volta'], ops
    assert 'type=roundTrip' in ops[0]['link'] and '%22originAirport%22%3A%22GRU%22' in ops[0]['link'] and 'SAO' not in ops[0]['link']
    assert selecao.juntas({'ida': par['ida'], 'volta': par['volta'], 'pp': 2164}, ops, 30)['economia'] == 143
    # um par mais barato em outros horários entra como segunda opção, com as pernas descritas (o melhor horário de cada trecho)
    outro = cartao(1900, [hora('Gol', 'CGH', 'JDO', '06:00', False, 300), hora('Gol', 'GRU', 'JDO', '07:00')], [hora('LATAM', 'FOR', 'GRU', '21:05')])
    ops = opcoes_juntas(par, achados + [outro])
    assert [(o['pp'], o['ida']['saida'], o['ida']['cia'], o['volta']['saida'], o['ida']['d'], o['ida']['pax']) for o in ops] == [
        (2021, '14:50', 'LATAM', '21:05', '2026-12-26', 2), (1900, '07:00', 'Gol', '21:05', '2026-12-26', 2)], ops
    assert ops[1]['volta']['voos'] == '' and ops[1]['ida']['sentido'] == 'ida' and ops[1]['ida']['tipo'] == 'direto'
    # sem os mesmos voos: só o mais barato; sem nada lido: nenhuma opção
    assert [o['pp'] for o in opcoes_juntas(par, achados[:1])] == [2203] and opcoes_juntas(par, []) == []
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
        sys.exit(0)
    viagem = plano.carregar()
    estado_ = registrar.ler_estado()
    if sys.argv[1:] == ['--rapida']:
        # se o Google barrou a última rodada completa, a rápida não insiste
        anterior = (registrar.ler_json(os.path.join(RAIZ, 'ultima.json'), {}).get('fontes') or {}).get('Google') or {}
        if anterior.get('bloqueio') or anterior.get('erro'):
            print('RAPIDA_PULADA o Google não respondeu bem na última rodada completa')
            sys.exit(0)
        scan_ = asyncio.run(varrer_rapido(viagem, estado_, datetime.now()))
        for linha in registrar.registrar_rapida(scan_, viagem):
            print(linha)
        sys.exit(0)
    scan_ = asyncio.run(varrer(viagem, estado_, datetime.now()))
    json.dump(scan_, open(os.path.join(RAIZ, 'ultima.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('STATUS:', scan_['status'], scan_.get('erro') or '', '| trechos com preço:', sum(1 for s in scan_['series'].values() if s['recomendada']))
    for linha in registrar.registrar(scan_, viagem):
        print(linha)
    sys.exit(0 if scan_['status'] == 'ok' else 1)
