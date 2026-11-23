"""Plano de buscas da viagem: lê viagem.json e diz o que procurar em cada rodada. Só funções puras.

viagem.json descreve os aeroportos, o voo já comprado e os grupos de viajantes; cada grupo tem trechos (ida ou volta,
uma janela de datas, um número de lugares). Daqui saem:
  trechos(v, hoje)        -> os trechos de cada grupo, só com as datas que ainda vão acontecer
  buscas(v, hoje, estado) -> as buscas do Google desta rodada, sem repetir (sentido, data, lugares)
O repositório é público: no arquivo os grupos têm só uma sigla. Os nomes vêm do segredo NOMES_VIAGEM (nomes()).

Uso: python3 nuvem/plano.py --teste   -> auto-checagem (confere o viagem.json de verdade e usa nuvem/viagem_teste.json)
"""
import json, os, re, sys
from datetime import date, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def datas(spec):
    """['2027-01-18', ...] a partir de uma lista ou de {'de': ..., 'ate': ...}."""
    if isinstance(spec, list):
        return sorted(spec)
    a, b = date.fromisoformat(spec['de']), date.fromisoformat(spec['ate'])
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def conferir(v):
    """Levanta ValueError com o que está errado no arquivo da viagem (aeroporto desconhecido, trecho que aponta para nada...)."""
    ap = v['aeroportos']
    for c in [*v['sp'], *v['ida_destinos'], *v['volta_origens'], v['comprado']['orig'], v['comprado']['dest']]:
        if c not in ap:
            raise ValueError(f'aeroporto sem cadastro: {c}')
    series = {f"{g['id']}.{t['id']}": (g, t) for g in v['grupos'] for t in g['trechos']}
    for s, (g, t) in series.items():
        if 'igual_a' in t:
            alvo = series.get(t['igual_a'])
            if not alvo or 'igual_a' in alvo[1] or alvo[0]['assentos'] != g['assentos']:
                raise ValueError(f"{s}: igual_a precisa apontar para um trecho de verdade com os mesmos lugares")
        elif t.get('sentido') not in ('ida', 'volta') or not datas(t['datas']):
            raise ValueError(f'{s}: falta sentido (ida ou volta) ou datas')
        if any(not isinstance(n, int) or n < 1 or n == g['assentos'] for n in t.get('lugares', [])):
            raise ValueError(f"{s}: lugares precisa ser uma lista de números diferentes dos assentos do grupo")
        for c in g.get('cenarios', []):
            if not {c['ida'], c['volta']} <= {x['id'] for x in g['trechos']}:
                raise ValueError(f"{g['id']}: cenário {c['id']} cita trecho que não existe")
    return v


def carregar(caminho=None):
    with open(caminho or os.path.join(RAIZ, 'viagem.json'), encoding='utf-8') as f:
        return conferir(json.load(f))


def carregar_teste():
    """A viagem usada nas auto-checagens (nuvem/viagem_teste.json): quatro grupos, com todos os recursos. Fica fixa, para
    mudar a viagem de verdade (viagem.json) não quebrar os testes."""
    return carregar(os.path.join(RAIZ, 'nuvem', 'viagem_teste.json'))


def aeroportos(v, sentido):
    """(origens, destinos) da busca num sentido."""
    return (v['sp'], v['ida_destinos']) if sentido == 'ida' else (v['volta_origens'], v['sp'])


def trechos(v, hoje):
    """Um dicionário por trecho de cada grupo: {serie, grupo, id, nome, sentido, datas, pax, alvo, rodizio, voo_comprado,
    igual_a, lugares}. serie = 'grupo.trecho'. Datas que já passaram saem. Trecho com igual_a herda sentido e datas do outro."""
    saida, por_serie = [], {}
    for g in v['grupos']:
        for t in g['trechos']:
            serie = f"{g['id']}.{t['id']}"
            x = {'serie': serie, 'grupo': g['id'], 'id': t['id'], 'nome': t.get('nome', t['id']), 'curto': t.get('curto', t.get('nome', t['id'])),
                 'pax': g['assentos'],
                 'alvo': t.get('alvo'), 'rodizio': t.get('rodizio'), 'voo_comprado': bool(t.get('voo_comprado')),
                 'igual_a': t.get('igual_a'), 'sentido': t.get('sentido'), 'lugares': t.get('lugares', []),
                 'datas': [d for d in datas(t['datas']) if d > hoje] if 'datas' in t else []}
            saida.append(x)
            por_serie[serie] = x
    for x in saida:
        if x['igual_a']:
            o = por_serie[x['igual_a']]
            x.update(sentido=o['sentido'], datas=o['datas'], alvo=x['alvo'] or o['alvo'])
    return saida


def rodizio(todas, vistas, fixas, giro):
    """Janela longa demais para olhar inteira: as `fixas` datas mais baratas já vistas e as `giro` que estão há mais
    tempo sem olhar (nunca vistas primeiro). vistas: {data: {'pp': preço ou None, 'visto': 'AAAA-MM-DD HH:MM'}}."""
    com_preco = sorted((d for d in todas if (vistas.get(d) or {}).get('pp')), key=lambda d: (vistas[d]['pp'], d))[:fixas]
    resto = sorted((d for d in todas if d not in com_preco), key=lambda d: ((vistas.get(d) or {}).get('visto', ''), d))[:giro]
    return sorted(com_preco + resto)


def datas_da_rodada(t, estado):
    """As datas que este trecho olha nesta rodada (todas, ou a fatia do rodízio)."""
    if not t['rodizio']:
        return t['datas']
    vistas = estado.get('series', {}).get(t['serie'], {}).get('datas', {})
    return rodizio(t['datas'], vistas, t['rodizio']['fixas'], t['rodizio']['giro'])


def buscas(v, hoje, estado=None):
    """As buscas do Google desta rodada: [{sentido, d, pax, de, para, series}], uma por (sentido, data, lugares).
    Uma busca cobre todos os aeroportos e serve a todos os trechos que precisam dela (series)."""
    juntas = {}
    for t in trechos(v, hoje):
        if t['igual_a']:
            continue
        for d in datas_da_rodada(t, estado or {}):
            de, para = aeroportos(v, t['sentido'])
            b = juntas.setdefault((t['sentido'], d, t['pax']), {'sentido': t['sentido'], 'd': d, 'pax': t['pax'], 'de': de, 'para': para, 'series': []})
            b['series'].append(t['serie'])
    return [juntas[k] for k in sorted(juntas)]


def nomes(v, bruto=None):
    """{id do grupo: nome para mostrar}. bruto: 'v=Fulana;h=Sicrano' (segredo NOMES_VIAGEM); sem ele, vale a sigla do arquivo."""
    dados = dict(p.split('=', 1) for p in re.split(r'[;\n]+', bruto or '') if '=' in p)
    dados = {k.strip(): x.strip() for k, x in dados.items() if x.strip()}
    return {g['id']: dados.get(g['id'], g['rotulo']) for g in v['grupos']}


def _teste():
    assert datas({'de': '2026-12-30', 'ate': '2027-01-02'}) == ['2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02']
    assert datas(['2026-12-23', '2026-12-22']) == ['2026-12-22', '2026-12-23']

    # ---- o arquivo de verdade: tem que estar certo e caber numa rodada (cada busca é uma página do Google)
    real = carregar()
    hoje_ = date.today().isoformat()
    assert all(t['datas'] or t['igual_a'] for t in trechos(real, '2026-11-23')), 'trecho sem datas'
    assert len(buscas(real, hoje_)) <= 60, f"buscas demais por rodada: {len(buscas(real, hoje_))}"

    # ---- a viagem dos testes (quatro grupos)
    v = carregar_teste()
    ts = {t['serie']: t for t in trechos(v, '2026-11-20')}
    assert list(ts) == ['v.volta', 'h.junto', 'h.depois', 'g.ida_dez', 'g.ida_jan', 'g.volta', 'wk.ida', 'wk.volta'], list(ts)
    assert ts['v.volta']['datas'] == ['2027-01-18', '2027-01-19', '2027-01-20', '2027-01-21', '2027-01-22'] and ts['v.volta']['pax'] == 1
    # o voo de V é conferido também para 2 e 3 lugares (quem volta junto); os de W + K, para 1 lugar (comprar um de cada vez)
    assert ts['v.volta']['lugares'] == [2, 3] and ts['wk.ida']['lugares'] == ts['wk.volta']['lugares'] == [1] and ts['h.depois']['lugares'] == []
    # quem volta junto usa a busca de V: mesmas datas, mesmo sentido, sem busca própria
    assert ts['h.junto']['datas'] == ts['g.volta']['datas'] == ts['v.volta']['datas'] and ts['g.volta']['sentido'] == 'volta'
    assert len(ts['h.depois']['datas']) == 68 and ts['h.depois']['datas'][0] == '2027-01-23' and ts['h.depois']['datas'][-1] == '2027-03-31'
    dez = ['2026-12-22', '2026-12-23', '2026-12-26', '2026-12-27', '2026-12-28', '2026-12-29', '2026-12-30']
    assert ts['g.ida_dez']['datas'] == ts['wk.ida']['datas'] == dez and ts['wk.ida']['pax'] == 2 and ts['wk.ida']['voo_comprado']
    assert not {'2026-12-24', '2026-12-25', '2026-12-31', '2027-01-01'} & set(dez + ts['g.ida_jan']['datas'])
    assert ts['g.ida_jan']['datas'][0] == '2027-01-02' and ts['g.ida_jan']['datas'][-1] == '2027-01-10'
    assert ts['wk.volta']['datas'] == ['2027-01-07', '2027-01-08', '2027-01-09', '2027-01-10', '2027-01-11', '2027-01-12']
    assert aeroportos(v, 'ida') == (['GRU', 'CGH', 'VCP'], ['FOR', 'MVF', 'JDO', 'NAT'])
    assert aeroportos(v, 'volta') == (['FOR', 'MVF', 'JDO', 'NAT', 'JPA', 'REC'], ['GRU', 'CGH', 'VCP'])
    # ordem de prioridade = ordem dos pesos
    pesos = [v['aeroportos'][c]['peso'] for c in v['volta_origens']]
    assert pesos == sorted(pesos) and v['volta_origens'] == ['FOR', 'MVF', 'JDO', 'NAT', 'JPA', 'REC']

    bs = buscas(v, '2026-11-20')
    chaves = [(b['sentido'], b['d'], b['pax']) for b in bs]
    assert len(chaves) == len(set(chaves))
    # dezembro: 1 lugar (G) e 2 lugares (W + K) em cada data; janeiro: só 1 lugar
    assert sum(1 for c in chaves if c[0] == 'ida') == 7 * 2 + 9
    # volta: 6 datas de W + K (2 lugares), 5 de V (1 lugar, que serve também a quem volta junto) e 5 do rodízio de H
    # (sem preço conhecido ainda, o rodízio são só as 5 datas do giro)
    assert sum(1 for c in chaves if c[0] == 'volta') == 6 + 5 + 5, chaves
    b = next(b for b in bs if (b['sentido'], b['d'], b['pax']) == ('ida', '2026-12-22', 2))
    assert b['series'] == ['wk.ida'] and b['de'] == ['GRU', 'CGH', 'VCP'] and b['para'][0] == 'FOR'
    assert next(b for b in bs if (b['d'], b['pax']) == ('2027-01-20', 1))['series'] == ['v.volta']

    # ---- datas passadas saem; trecho que acabou não gera busca
    assert not [b for b in buscas(v, '2026-12-29') if b['d'] <= '2026-12-29']
    assert trechos(v, '2027-01-23')[0]['datas'] == [] and [t['serie'] for t in trechos(v, '2027-01-23') if t['datas']] == ['h.depois']

    # ---- rodízio: mais baratas já vistas + as que estão há mais tempo sem olhar; sem histórico, as primeiras
    todas = [f'2027-02-{d:02d}' for d in range(1, 11)]
    assert rodizio(todas, {}, 3, 5) == todas[:5]
    vistas = {'2027-02-01': {'pp': 500, 'visto': '2026-11-20 10:00'}, '2027-02-02': {'pp': 300, 'visto': '2026-11-20 10:00'},
              '2027-02-03': {'pp': None, 'visto': '2026-11-20 10:00'}, '2027-02-04': {'pp': 400, 'visto': '2026-11-20 09:00'},
              '2027-02-05': {'pp': 900, 'visto': '2026-11-20 08:00'}}
    r = rodizio(todas, vistas, 3, 5)
    # fixas: 02 (300), 04 (400), 01 (500). giro: 06 a 10 nunca vistas
    assert r == ['2027-02-01', '2027-02-02', '2027-02-04', '2027-02-06', '2027-02-07', '2027-02-08', '2027-02-09', '2027-02-10'], r
    vistas.update({d: {'pp': None, 'visto': '2026-11-20 11:00'} for d in todas[5:]})
    # agora as mais antigas são 05 (08:00) e 03 (10:00), depois as de 11:00
    assert rodizio(todas, vistas, 3, 2) == ['2027-02-01', '2027-02-02', '2027-02-03', '2027-02-04', '2027-02-05']
    estado = {'series': {'h.depois': {'datas': {'2027-03-15': {'pp': 310, 'visto': '2026-11-20 10:00'}}}}}
    do_h = [b['d'] for b in buscas(v, '2026-11-20', estado) if 'h.depois' in b['series']]
    assert '2027-03-15' in do_h and len(do_h) == 6      # a data barata já vista + 5 do giro

    # ---- nomes: do segredo, ou a sigla; nome vazio não apaga a sigla
    assert nomes(v) == {'v': 'V', 'h': 'H', 'g': 'G', 'wk': 'W + K'}
    assert nomes(v, 'v=Ana; wk = Bia + Cris ;g=') == {'v': 'Ana', 'h': 'H', 'g': 'G', 'wk': 'Bia + Cris'}

    # ---- arquivo errado é recusado
    for estrago, erro in ((lambda x: x['ida_destinos'].append('XXX'), 'sem cadastro'),
                          (lambda x: x['grupos'][1]['trechos'][0].update(igual_a='nada.aqui'), 'igual_a'),
                          (lambda x: x['grupos'][0]['trechos'][0].pop('sentido'), 'sentido'),
                          (lambda x: x['grupos'][2]['cenarios'][0].update(ida='sumiu'), 'cenário'),
                          (lambda x: x['grupos'][0]['trechos'][0].update(lugares=[1, 2]), 'lugares')):
        ruim = json.loads(json.dumps(v))
        estrago(ruim)
        try:
            conferir(ruim)
            raise AssertionError('devia recusar: ' + erro)
        except ValueError as e:
            assert erro in str(e), e
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    else:
        print(__doc__)
