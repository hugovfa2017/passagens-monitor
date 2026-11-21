"""Google Voos sem navegador: monta o endereço das buscas e interpreta o texto que a página devolve. Só funções puras.

O navegador (varredura.js) só abre as páginas e devolve o texto bruto; o que é regra fica aqui, com teste:
  url_busca / url_compra -> endereço de uma busca ou da tela de compra de um voo exato (parâmetro tfs)
  ler_lista              -> voos da lista de resultados (o preço da lista pode estar velho: serve para ordenar)
  ler_compra             -> preço de cada vendedor na tela de compra (é o preço que vale)
  voos_do_tfs            -> número de cada voo, lido do endereço da tela de compra

Uso: python3 nuvem/gvoos.py --teste
"""
import base64, re, sys, urllib.parse

SUFIXO = '&hl=pt-BR&gl=BR&curr=BRL'
# Vendedores da tela de compra, em dois níveis: os que entram na escolha (a própria companhia e agências conhecidas no
# Brasil) e os de fora (agências estrangeiras: aparecem na mensagem só como aviso, quando bem mais baratas).
CONFIAVEIS = re.compile(r'^(CIA |maxmilhas|123milhas|zupper|decolar|booking|viajanet|cvc|submarino|vai de promo|viaza)', re.I)


def _s(tag, txt):
    return bytes([tag, len(txt)]) + txt.encode()


def _tam(n):
    return bytes([n]) if n < 128 else bytes([n & 0x7f | 0x80, n >> 7])


def _ap(tag, c):
    return bytes([tag, 7, 8, 1, 0x12, 3]) + c.encode()


def tfs(pernas, pax, ida_e_volta=False):
    """O parâmetro tfs do Google Voos. pernas: [(data, origens, destinos, voos)], uma por sentido.
    voos: '' numa busca; na tela de compra, os voos exatos: 'FOR>GRU 2027-01-19 G3 1555' (conexões separadas por ';')."""
    corpo = b''
    for data, origens, destinos, voos in pernas:
        legs = b''
        for x in filter(None, (voos or '').split(';')):
            od, d, cod, num = x.split(' ')
            o, de = od.split('>')
            m = _s(0x0a, o) + _s(0x12, d) + _s(0x1a, de) + _s(0x2a, cod) + _s(0x32, num)
            legs += b'\x22' + _tam(len(m)) + m
        seg = _s(0x12, data) + legs + b''.join(_ap(0x6a, c) for c in origens) + b''.join(_ap(0x72, c) for c in destinos)
        corpo += b'\x1a' + _tam(len(seg)) + seg
    h = (b'\x08\x1c\x10\x02' + corpo + b'\x40\x01' * pax + bytes.fromhex('4801700182010b08ffffffffffffffffff019801')
         + (b'\x01' if ida_e_volta else b'\x02'))
    return base64.urlsafe_b64encode(h).decode().rstrip('=')


def url_busca(pernas, pax, ida_e_volta=False):
    return 'https://www.google.com/travel/flights/search?tfs=' + tfs(pernas, pax, ida_e_volta) + '&tfu=EgoIABAAGAAgAigB' + SUFIXO


def url_compra(pernas, pax, ida_e_volta=False):
    """Tela de compra do voo exato (lista os vendedores com 'Continuar' para o checkout). '' se faltar o número de algum voo."""
    if not all(p[3] for p in pernas):
        return ''
    return 'https://www.google.com/travel/flights/booking?tfs=' + tfs(pernas, pax, ida_e_volta) + SUFIXO


def codigo(nome, aeroportos):
    """'Aeroporto Internacional de Viracopos' -> 'VCP' (pelo campo google de cada aeroporto); None se não for dos nossos."""
    return next((c for c, a in aeroportos.items() if re.search(a['google'], nome)), None)


def _minutos(s):
    h, m = re.search(r'(\d+)\s*h', s), re.search(r'(\d+)\s*min', s)
    return (int(h[1]) * 60 if h else 0) + (int(m[1]) if m else 0)


def permitido(orig, dest, direto, aeroportos):
    """Aeroporto com so_direto_com (viagem.json) só vale em voo direto de ou para os aeroportos da lista dele."""
    for c, outro in ((orig, dest), (dest, orig)):
        so = aeroportos[c].get('so_direto_com')
        if so is not None and not (direto and outro in so):
            return False
    return True


def ler_lista(rotulos, pax, v):
    """Voos da lista de resultados: [{cia, orig, dest, saida, chegada, tipo, dur, lista_pp, chave}], do mais barato ao mais caro.
    Fica só voo direto ou com 1 conexão dentro do limite de espera do aeroporto do Nordeste; aeroporto fora da viagem sai.
    chave = o trecho do rótulo que identifica o voo na página (para clicar nele depois, mesmo que o preço mude)."""
    ap, lim, vistos, saida = v['aeroportos'], v['limites'], set(), []
    for t in rotulos:
        p = re.search(r'A partir de ([\d.]+) Reais', t)
        a = re.search(r'Voo (direto )?d[ae] (.+?)(?: com (\d+) paradas?)?\.', t)
        s = re.search(r'Sai do aeroporto (.+?) às (\d\d:\d\d) do dia .+? e chega no aeroporto (.+?) às (\d\d:\d\d)', t)
        if not (p and a and s):
            continue
        orig, dest = codigo(s[1], ap), codigo(s[3], ap)
        if not orig or not dest:
            continue
        ne = dest if orig in v['sp'] else orig
        esperas = [_minutos(x) for x in re.findall(r'Parada \(\d de \d\) de (.+?) no aeroporto', t)]
        direto = bool(a[1])
        if not direto and (len(esperas) != 1 or esperas[0] > ap[ne].get('escala_max', lim['escala_max'])):
            continue
        if not permitido(orig, dest, direto, ap):
            continue
        cia = re.sub(r' ?Operado.*', '', a[2])
        ident = (cia, orig, dest, s[2], s[4])
        if ident in vistos:
            continue
        vistos.add(ident)
        dur = re.search(r'Duração total: (.+?)\.', t)
        fim = t.find('Duração total')
        saida.append({'cia': cia, 'orig': orig, 'dest': dest, 'saida': s[2], 'chegada': s[4],
                      'tipo': 'direto' if direto else f'escala {esperas[0] // 60}h{esperas[0] % 60:02d}',
                      'dur': _minutos(dur[1]) if dur else None, 'lista_pp': int(p[1].replace('.', '')) / pax,
                      'chave': t[t.index('Voo '):fim if fim > 0 else len(t)].strip()})
    return sorted(saida, key=lambda x: x['lista_pp'])


def ler_compra(texto, pax):
    """Vendedores da tela de compra: [{vend, pp}] por pessoa, do mais barato ao mais caro. 'CIA Gol' = a própria companhia."""
    achados = re.findall(r'Reserve com a (.+?)(Companhia aérea)?\nR\$[\s\u00a0]([\d.]+)', texto)
    return sorted(({'vend': ('CIA ' if cia else '') + nome.strip(), 'pp': round(int(preco.replace('.', '')) / pax)}
                   for nome, cia, preco in achados), key=lambda x: x['pp'])


def ler_aviso(texto, pax):
    """O que o próprio Google diz do preço na tela de compra: {'nivel': 'baixo' | 'normal' | 'alto', 'de', 'ate'} por
    pessoa (de e ate = a faixa em que os voos mais baratos da rota costumam ficar), ou None se a tela não trouxe.
    Ex.: 'R$ 857 é um preço baixo para Econômica (...) geralmente custam R$ 1.250 a 3.250.'"""
    n = re.search(r'R\$[\s\u00a0]([\d.]+) é um preço (baixo|normal|típico|comum|alto)', texto)
    f = re.search(r'geralmente custam R\$[\s\u00a0]([\d.]+) a (?:R\$[\s\u00a0])?([\d.]+)', texto)
    if not n and not f:
        return None
    reais = lambda x: round(int(x.replace('.', '')) / pax)
    aviso = {'nivel': {'típico': 'normal', 'comum': 'normal'}.get(n[2], n[2]) if n else None,
             'de': reais(f[1]) if f else None, 'ate': reais(f[2]) if f else None}
    if not aviso['nivel']:      # a tela trouxe só a faixa: sem o preço do voo para comparar, não há nível
        return None
    return aviso


def melhor_vendedor(vendedores):
    """O mais barato entre os vendedores que contam; None se nenhum conta."""
    return next((x for x in vendedores if CONFIAVEIS.search(x['vend'])), None)


def melhor_de_fora(vendedores):
    """O mais barato entre os vendedores que não contam (agências de fora); None se não há."""
    return next((x for x in vendedores if not CONFIAVEIS.search(x['vend'])), None)


def voos_do_tfs(href):
    """'GRU>NAT 2026-12-22 G3 1678' (conexões separadas por ';') a partir do endereço da tela de compra; '' se não der."""
    try:
        bruto = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)['tfs'][0]
        b = base64.urlsafe_b64decode(bruto + '=' * (-len(bruto) % 4))
    except (KeyError, ValueError):
        return ''
    achados = re.findall(rb'\x0a\x03([A-Z]{3})\x12\x0a(\d{4}-\d\d-\d\d)\x1a\x03([A-Z]{3})\x2a\x02([A-Z0-9]{2})\x32[\s\S](\d+)', b)
    return ';'.join(f'{o.decode()}>{d.decode()} {dia.decode()} {cia.decode()} {num.decode()}' for o, dia, d, cia, num in achados)


def _teste():
    # ---- tfs igual, byte a byte, ao que o JavaScript antigo montava (busca só de ida, vários aeroportos, 1 lugar)
    busca = [('2026-12-28', ['GRU', 'CGH', 'VCP'], ['FOR', 'MVF', 'JDO', 'NAT'], '')]
    assert tfs(busca, 1) == ('CBwQAhpLEgoyMDI2LTEyLTI4agcIARIDR1JVagcIARIDQ0dIagcIARIDVkNQcgcIARIDRk9ScgcIARIDTVZGcgcIARIDSkRPcgcIARIDTkFU'
                             'QAFIAXABggELCP___________wGYAQI'), tfs(busca, 1)
    assert url_busca(busca, 1).startswith('https://www.google.com/travel/flights/search?tfs=CBwQAh') and url_busca(busca, 1).endswith('&curr=BRL')
    # ---- tela de compra igual, byte a byte, ao link que o Google gerou para o Gol 1555 FOR->GRU de 19/01 (2 lugares)
    esperado = (b'\x08\x1c\x10\x02\x1a\x5b\x12\x0a2027-01-19\x22\x20\x0a\x03FOR\x12\x0a2027-01-19\x1a\x03GRU\x2a\x02G3\x32\x041555'
                + b''.join(bytes([0x6a, 7, 8, 1, 0x12, 3]) + c for c in (b'FOR', b'NAT', b'JDO'))
                + b''.join(bytes([0x72, 7, 8, 1, 0x12, 3]) + c for c in (b'GRU', b'CGH'))
                + bytes.fromhex('4001400148017001') + bytes.fromhex('82010b08ffffffffffffffffff01980102'))
    compra = [('2027-01-19', ['FOR', 'NAT', 'JDO'], ['GRU', 'CGH'], 'FOR>GRU 2027-01-19 G3 1555')]
    t = tfs(compra, 2)
    assert base64.urlsafe_b64decode(t + '=' * (-len(t) % 4)) == esperado
    assert url_compra(compra, 2).startswith('https://www.google.com/travel/flights/booking?tfs=') and url_compra(busca, 1) == ''
    assert voos_do_tfs(url_compra(compra, 2)) == 'FOR>GRU 2027-01-19 G3 1555' and voos_do_tfs('https://x/?a=1') == ''
    # conexão: dois voos no mesmo sentido; ida e volta: dois sentidos e o último byte muda
    conexao = [('2027-01-19', ['JDO'], ['GRU'], 'JDO>BSB 2027-01-19 G3 1700;BSB>GRU 2027-01-19 G3 1420')]
    assert voos_do_tfs(url_compra(conexao, 1)) == 'JDO>BSB 2027-01-19 G3 1700;BSB>GRU 2027-01-19 G3 1420'
    iv = [('2026-12-28', ['GRU'], ['FOR'], ''), ('2027-01-10', ['FOR'], ['GRU'], '')]
    b = base64.urlsafe_b64decode(tfs(iv, 2, True) + '==')
    assert b.count(b'\x1a') >= 2 and b.endswith(b'\x98\x01\x01') and b.count(b'\x40\x01') == 2
    # trecho com 128 bytes ou mais: o tamanho ocupa dois bytes
    longo = [('2027-01-20', ['FOR', 'MVF', 'JDO', 'NAT', 'JPA', 'REC'] * 3, ['GRU', 'CGH', 'VCP'], '')]
    b = base64.urlsafe_b64decode(tfs(longo, 1) + '==')
    assert b[4:7] == b'\x1a\xc9\x01', b[4:7]            # 201 bytes -> 0xc9 0x01

    # ---- lista de resultados, com rótulos de verdade (sondagem de 20/11/2026)
    v = {'sp': ['GRU', 'CGH', 'VCP'], 'limites': {'escala_max': 120, 'duracao_max': 330},
         'aeroportos': {'GRU': {'google': 'Guarulhos'}, 'CGH': {'google': 'Congonhas'}, 'VCP': {'google': 'Viracopos'},
                        'FOR': {'google': 'Fortaleza'}, 'JDO': {'google': 'Juazeiro'}, 'NAT': {'google': 'Natal'},
                        'MVF': {'google': 'Mossor|Dix-Sept', 'escala_max': 360}, 'REC': {'google': 'Recife|Guararapes'}}}
    latam = ('A partir de 1373 Reais brasileiros. Voo direto da LATAM. Operado por Latam Airlines Brasil. Sai do aeroporto Aeroporto '
             'Internacional de São Paulo/Guarulhos–Governador André Franco Montoro às 20:10 do dia segunda-feira, dezembro 28 e chega no '
             'aeroporto Aeroporto de Juazeiro do Norte - Orlando Bezerra de Menezes às 23:15 do dia segunda-feira, dezembro 28. Duração '
             'total: 3 h 5 min.   Estimativa das emissões de carbono: 164 quilos. Selecionar voo')
    azul = ('A partir de 1804 Reais brasileiros. Voo direto da Azul. Sai do aeroporto Aeroporto Internacional de Viracopos às 08:45 do dia '
            'segunda-feira, dezembro 28 e chega no aeroporto Aeroporto Internacional de Fortaleza - Pinto Martins (FOR) às 12:10 do dia '
            'segunda-feira, dezembro 28. Duração total: 3 h 25 min.   Estimativa das emissões de carbono: 169 quilos. Selecionar voo')
    duas = ('A partir de 1425 Reais brasileiros. Voo da Gol com 2 paradas. Sai do aeroporto Aeroporto de São Paulo/Congonhas–Deputado '
            'Freitas Nobre às 16:40 do dia segunda-feira, dezembro 28 e chega no aeroporto Aeroporto Internacional de Natal às 03:00 do dia '
            'terça-feira, dezembro 29. Duração total: 10 h 20 min.  Parada (1 de 2) de 2 h 30 min no aeroporto Aeroporto Internacional de '
            'Navegantes, emNavegantes. Parada (2 de 2) de 2 h 10 min no aeroporto Aeroporto Internacional do Rio de Janeiro - Galeão, emRio.')
    uma = lambda espera, destino='Aeroporto Internacional de Natal': (
        'A partir de 1.500 Reais brasileiros. Voo da Gol com 1 parada. Sai do aeroporto Aeroporto de São Paulo/Congonhas–Deputado Freitas '
        f'Nobre às 06:00 do dia segunda-feira, dezembro 28 e chega no aeroporto {destino} às 11:40 do dia segunda-feira, dezembro 28. '
        f'Duração total: 5 h 40 min.  Parada (1 de 1) de {espera} no aeroporto Aeroporto Internacional de Brasília, emBrasília.')
    fora = latam.replace('Juazeiro do Norte - Orlando Bezerra de Menezes', 'Salvador - Deputado Luís Eduardo Magalhães')
    voos = ler_lista([azul, latam, duas, uma('1 h 35 min'), uma('4 h 10 min'), fora, latam, 'texto qualquer'], 1, v)
    assert [(x['cia'], x['orig'], x['dest'], x['saida'], x['chegada'], x['tipo'], x['lista_pp']) for x in voos] == [
        ('LATAM', 'GRU', 'JDO', '20:10', '23:15', 'direto', 1373), ('Gol', 'CGH', 'NAT', '06:00', '11:40', 'escala 1h35', 1500),
        ('Azul', 'VCP', 'FOR', '08:45', '12:10', 'direto', 1804)], voos
    assert voos[0]['dur'] == 185 and voos[0]['chave'].startswith('Voo direto da LATAM. Operado') and voos[0]['chave'].endswith('dezembro 28.')
    assert voos[0]['chave'] in latam and '1373' not in voos[0]['chave']
    # espera de 4h10 passa só onde o aeroporto tem limite próprio (Mossoró); preço da lista é dividido pelos lugares
    mossoro = ler_lista([uma('4 h 10 min', 'Aeroporto de Mossoró - Dix-Sept Rosado')], 2, v)
    assert [(x['dest'], x['tipo'], x['lista_pp']) for x in mossoro] == [('MVF', 'escala 4h10', 750)]

    # ---- Campinas só em voo direto com Mossoró: o direto para Fortaleza sai da lista; um direto para Mossoró ficaria
    v['aeroportos']['VCP']['so_direto_com'] = ['MVF']
    assert [x['orig'] for x in ler_lista([azul, latam], 1, v)] == ['GRU']
    direto_mvf = azul.replace('Aeroporto Internacional de Fortaleza - Pinto Martins (FOR)', 'Aeroporto de Mossoró - Dix-Sept Rosado')
    assert [(x['orig'], x['dest']) for x in ler_lista([direto_mvf], 1, v)] == [('VCP', 'MVF')]
    assert permitido('MVF', 'VCP', True, v['aeroportos']) and not permitido('VCP', 'MVF', False, v['aeroportos']) and permitido('GRU', 'FOR', False, v['aeroportos'])

    # ---- tela de compra
    texto = ('Opções de reserva\nReserve com a GolCompanhia aérea\nR$\u00a01.824\nContinuar\nReserve com a MaxMilhas\nR$ 1.650\nContinuar\n'
             'Reserve com a Kiwi.com\nR$ 1.600\nContinuar\n')
    vend = ler_compra(texto, 2)
    assert vend == [{'vend': 'Kiwi.com', 'pp': 800}, {'vend': 'MaxMilhas', 'pp': 825}, {'vend': 'CIA Gol', 'pp': 912}], vend
    assert melhor_vendedor(vend) == {'vend': 'MaxMilhas', 'pp': 825} and melhor_vendedor(vend[:1]) is None and ler_compra('nada', 1) == []
    # dois níveis: a agência de fora mais barata fica à parte; agência brasileira conhecida conta
    assert melhor_de_fora(vend) == {'vend': 'Kiwi.com', 'pp': 800} and melhor_de_fora(vend[1:]) is None
    assert melhor_vendedor([{'vend': 'Gotogate', 'pp': 1}, {'vend': 'ViajaNet', 'pp': 2}]) == {'vend': 'ViajaNet', 'pp': 2}

    # ---- aviso de preço do Google na tela de compra (textos de verdade, sondagem de 21/11/2026); a faixa vem no total dos lugares
    um = ('Reserve com a LATAMCompanhia aérea\nR$ 857\nContinuar\nVeja as opções\nOs preços incluem os tributos e tarifas obrigatórios para 1 adulto.\n'
          'Informações de preço\nR$ 857 é um preço baixo para Econômica (R$ 1.298 mais barato que o normal)\nOs voos mais baratos para viagens semelhantes '
          'que têm São Paulo como destino geralmente custam R$ 1.250 a 3.250. \nR$ 857 é um preço baixo\nR$ 1.250\nR$ 3.250\nHistórico de preços para esses voos')
    assert ler_aviso(um, 1) == {'nivel': 'baixo', 'de': 1250, 'ate': 3250}, ler_aviso(um, 1)
    dois = um.replace('R$ 857', 'R$ 1.714').replace('R$ 1.250 a 3.250', 'R$ 2.550 a 6.500')
    assert ler_aviso(dois, 2) == {'nivel': 'baixo', 'de': 1275, 'ate': 3250} and ler_compra(dois, 2) == [{'vend': 'CIA LATAM', 'pp': 857}]
    assert ler_aviso(um.replace('é um preço baixo', 'é um preço alto'), 1)['nivel'] == 'alto' and ler_aviso(um.replace('preço baixo', 'preço típico'), 1)['nivel'] == 'normal'
    # sem a frase do nível, ou tela sem aviso nenhum: nada
    assert ler_aviso('Reserve com a Gol\nR$ 500\ngeralmente custam R$ 400 a 900.', 1) is None and ler_aviso(texto, 2) is None
    assert ler_aviso('R$ 500 é um preço normal para Econômica', 1) == {'nivel': 'normal', 'de': None, 'ate': None}
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    else:
        print(__doc__)
