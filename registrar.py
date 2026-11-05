"""Registra uma varredura (JSON de window.__scan): histórico, resumo, dados do painel e alerta no Telegram.

Uso:  python3 registrar.py ultima.json ["linha MaxMilhas" ...]
      python3 registrar.py --teste                -> auto-checagem (não envia nada)
      python3 registrar.py --configurar-telegram  -> acha o seu chat depois que você manda /start ao bot
      python3 registrar.py --teste-telegram       -> manda uma mensagem de teste

O token do bot fica no Keychain (serviço telegram-passagens); o id do chat em telegram_chat.txt.
Saídas na pasta: historico.csv, ultimo.md, painel.json (o que o painel mostra).
Imprime uma linha por evento: ALERTA ..., SEM_NOVIDADE ..., ERRO_VARREDURA ..., TELEGRAM_* ...
"""
import base64, csv, json, os, subprocess, sys, tempfile, urllib.parse, urllib.request
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
PAINEL_URL = 'https://hugovfa2017.github.io/passagens-monitor/'

# "Algo muito bom" = preço por pessoa (ida + volta, sem mala) igual ou abaixo destes alvos.
ALVO_GERAL = 1400
ALVO_NATAL = 1450
REALERTA = 30     # depois de avisar, só avisa de novo se cair mais R$30 por pessoa
QUEDA_MIN = 20    # notificação no computador quando bate recorde por pelo menos isso

CAMPOS = ['data_hora', 'pax', 'melhor_pp', 'melhor_ida', 'melhor_volta', 'melhor_dias', 'melhor_voo_ida', 'melhor_voo_volta',
          'natal_pp', 'natal_ida', 'natal_volta', 'natal_voo_ida', 'natal_voo_volta']
SEMANA = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']


def dia(d):  # '2026-12-18' -> 'sex 18/12'
    dt = datetime.strptime(d, '%Y-%m-%d')
    return f"{SEMANA[dt.weekday()]} {dt.day:02d}/{dt.month:02d}"


def brl(n):
    return 'R$' + f'{round(n):,}'.replace(',', '.')


def texto_trecho(t):
    return f"{brl(t['pp'])}/pessoa via {t['vend']} | {t['cia']} {t['rota']} {t['saida']}-{t['chegada']} {t['tipo']}" + \
        (f" | na cia {brl(t['ppCia'])}" if t.get('ppCia') and t['ppCia'] != t['pp'] else '')


def link_compra(sentido, data, voos, pax):
    """Tela de compra do Google Voos para o voo exato (lista todos os vendedores com 'Continuar' p/ o checkout).
    voos: 'FOR>GRU 2027-01-19 G3 1555' (trechos separados por ';'), lido pela varredura."""
    if not voos:
        return ''
    s = lambda tag, txt: bytes([tag, len(txt)]) + txt.encode()
    tam = lambda n: bytes([n]) if n < 128 else bytes([n & 0x7f | 0x80, n >> 7])
    trechos = b''
    for v in voos.split(';'):
        od, d, cod, num = v.split(' ')
        o, de = od.split('>')
        m = s(0x0a, o) + s(0x12, d) + s(0x1a, de) + s(0x2a, cod) + s(0x32, num)
        trechos += b'\x22' + tam(len(m)) + m
    O, D = (['GRU', 'CGH'], ['FOR', 'NAT', 'JDO']) if sentido == 'ida' else (['FOR', 'NAT', 'JDO'], ['GRU', 'CGH'])
    ap = lambda tag, c: bytes([tag, 7, 8, 1, 0x12, 3]) + c.encode()
    seg = s(0x12, data) + trechos + b''.join(ap(0x6a, c) for c in O) + b''.join(ap(0x72, c) for c in D)
    h = b'\x08\x1c\x10\x02\x1a' + tam(len(seg)) + seg + b'\x40\x01' * pax + bytes.fromhex('4801700182010b08ffffffffffffffffff01980102')
    return 'https://www.google.com/travel/flights/booking?tfs=' + base64.urlsafe_b64encode(h).decode().rstrip('=') + '&hl=pt-BR&gl=BR&curr=BRL'


def link_mm(rota, data, pax):
    o, de = rota.split('-')
    return f'https://www.maxmilhas.com.br/busca-passagens-aereas/OW/{o}/{de}/{data}/{pax}/0/0/EC'


def nome_vendedor(v):
    v = v.removeprefix('CIA ')
    return 'MaxMilhas' if v.lower() == 'maxmilhas' else v


def trecho_compra(rotulo, sentido, data, t, pax):
    """Texto de um trecho com o link de compra e em qual vendedor clicar (o link direto ao pagamento expira em minutos)."""
    link = t.get('link') or link_compra(sentido, data, t.get('voos'), pax)
    acao = f"Comprar na {nome_vendedor(t['vend'])}, {brl(t['pp'])}: {link}" if link else \
        f"Ver na MaxMilhas: {link_mm(t['rota'], data, pax)}"
    return f"{rotulo} {dia(data)}: {t['cia']} {t['rota']} {t['saida']}-{t['chegada']} {t['tipo']}\n{acao}"


CHAT_FILE = os.path.join(AQUI, 'telegram_chat.txt')


def _token():
    # no GitHub Actions o token vem do segredo TELEGRAM_TOKEN; no Mac, do Keychain
    if os.environ.get('TELEGRAM_TOKEN'):
        return os.environ['TELEGRAM_TOKEN'].strip()
    try:
        return subprocess.run(['security', 'find-generic-password', '-s', 'telegram-passagens', '-w'],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ''


def _api(token, metodo, campos=None):
    dados = urllib.parse.urlencode(campos).encode() if campos else None
    with urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/{metodo}', data=dados), timeout=30) as r:
        return json.loads(r.read().decode())


def telegram(texto):
    token = _token()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip() or (open(CHAT_FILE).read().strip() if os.path.exists(CHAT_FILE) else '')
    if not token or not chat:
        return 'TELEGRAM_NAO_CONFIGURADO'
    try:
        r = _api(token, 'sendMessage', {'chat_id': chat, 'text': texto, 'disable_web_page_preview': 'true'})
        return 'TELEGRAM_ENVIADO' if r.get('ok') else f"TELEGRAM_FALHOU {r.get('description', '')[:150]}"
    except (OSError, ValueError) as e:
        return f'TELEGRAM_FALHOU {e}'


def configurar_telegram():
    """Lê a última mensagem que você mandou ao bot e guarda o id do seu chat."""
    token = _token()
    if not token:
        return 'TELEGRAM_SEM_TOKEN salve o token no Keychain primeiro'
    try:
        msgs = [u['message'] for u in _api(token, 'getUpdates').get('result', []) if 'message' in u]
    except (OSError, ValueError) as e:
        return f'TELEGRAM_FALHOU {e}'
    if not msgs:
        return 'TELEGRAM_SEM_MENSAGEM mande /start para o seu bot no Telegram e rode de novo'
    chat = msgs[-1]['chat']
    open(CHAT_FILE, 'w').write(str(chat['id']))
    return f"TELEGRAM_CONFIGURADO chat de {chat.get('first_name', '')}"


def registrar(scan, pasta=AQUI, enviar=telegram, maxmilhas=()):
    hist, ultimo, painel = (os.path.join(pasta, f) for f in ('historico.csv', 'ultimo.md', 'painel.json'))
    estado_wpp = os.path.join(pasta, 'alerta_estado.json')
    # hora da varredura (não a do registro), no fuso do computador
    fim = scan.get('fim')
    agora = (datetime.fromisoformat(fim.replace('Z', '+00:00')).astimezone() if fim else datetime.now()).strftime('%Y-%m-%d %H:%M')
    if scan.get('status') != 'ok' or not scan.get('combos'):
        with open(ultimo, 'a', encoding='utf-8') as f:
            f.write(f"\n\nVarredura de {agora} falhou: {scan.get('erro') or 'sem combinações válidas'}\n")
        return [f"ERRO_VARREDURA {scan.get('erro') or 'sem combinações válidas'}"]

    pax = scan['pax']
    linhas = list(csv.DictReader(open(hist, encoding='utf-8'))) if os.path.exists(hist) else []
    linhas = [r for r in linhas if int(r.get('pax') or 0) == pax]
    tre = {(t['s'], t['d']): t for t in scan['trechos']}
    m = scan['combos'][0]
    n = next((c for c in scan['combos'] if c['natal']), None)

    def menor(campo):
        vals = [float(r[campo]) for r in linhas if r.get(campo)]
        return min(vals) if vals else None

    ant_m, ant_n = menor('melhor_pp'), menor('natal_pp')
    ult = linhas[-1] if linhas else {}
    nova = {'data_hora': agora, 'pax': pax, 'melhor_pp': m['pp'], 'melhor_ida': m['ida'], 'melhor_volta': m['volta'], 'melhor_dias': m['dias'],
            'melhor_voo_ida': texto_trecho(tre[('ida', m['ida'])]), 'melhor_voo_volta': texto_trecho(tre[('volta', m['volta'])]),
            'natal_pp': n['pp'] if n else '', 'natal_ida': n['ida'] if n else '', 'natal_volta': n['volta'] if n else '',
            'natal_voo_ida': texto_trecho(tre[('ida', n['ida'])]) if n else '', 'natal_voo_volta': texto_trecho(tre[('volta', n['volta'])]) if n else ''}
    novo_arquivo = not os.path.exists(hist)
    with open(hist, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo_arquivo:
            w.writeheader()
        w.writerow(nova)
    linhas.append({k: str(v) for k, v in nova.items()})

    saida = []
    # recorde (notificação no computador)
    alertas = []
    if ant_m is not None and m['pp'] <= ant_m - QUEDA_MIN:
        alertas.append(f"melhor geral caiu p/ {brl(m['pp'])}/pessoa (antes {brl(ant_m)}): {dia(m['ida'])} -> {dia(m['volta'])}")
    if n and ant_n is not None and n['pp'] <= ant_n - QUEDA_MIN:
        alertas.append(f"com Natal caiu p/ {brl(n['pp'])}/pessoa (antes {brl(ant_n)}): {dia(n['ida'])} -> {dia(n['volta'])}")
    saida.append(('ALERTA ' + ' | '.join(alertas)) if alertas else f"SEM_NOVIDADE melhor {brl(m['pp'])}/pessoa")

    # compra boa (Telegram)
    estado = json.load(open(estado_wpp)) if os.path.exists(estado_wpp) else {}
    boas = []
    for chave, c, alvo, rotulo in (('natal', n, ALVO_NATAL, 'Com Natal'), ('geral', m, ALVO_GERAL, 'Melhor geral')):
        if c and c['pp'] <= alvo and c['pp'] <= estado.get(chave, 10**9) - REALERTA:
            boas.append((chave, c, f"{rotulo}: {brl(c['pp'])}/pessoa, {c['dias']} dias (alvo {brl(alvo)})\n\n"
                                   f"{trecho_compra('Ida', 'ida', c['ida'], tre[('ida', c['ida'])], pax)}\n\n"
                                   f"{trecho_compra('Volta', 'volta', c['volta'], tre[('volta', c['volta'])], pax)}"))
    status_wpp = ''
    if boas:
        msg = f"Passagem boa para o Nordeste ({pax} adultos, sem mala)\n\n" + '\n\n'.join(b[2] for b in boas) + \
              f"\n\nCada link abre o vendedor indicado já com o voo (MaxMilhas e 123milhas: preço no Pix). " \
              f"No link do Google, clique em Continuar ao lado do vendedor." \
              f"\nPreço muda rápido: confira e compre logo.\nPainel: {PAINEL_URL}"
        status_wpp = enviar(msg)
        saida.append(status_wpp)
        if status_wpp == 'TELEGRAM_ENVIADO':
            estado.update({b[0]: b[1]['pp'] for b in boas})
            json.dump(estado, open(estado_wpp, 'w'))

    # painel.json: uma tabela, cada linha com seu "tipo"
    def grupo(prefixo, c):
        if not c:
            return {}
        out = {f'{prefixo}_pp': c['pp'], f'{prefixo}_dias': c['dias'], f'{prefixo}_total': c['pp'] * pax}
        for sentido, d in (('ida', c['ida']), ('volta', c['volta'])):
            t = tre[(sentido, d)]
            o, de = t['rota'].split('-')
            out.update({f'{prefixo}_{sentido}': d, f'{prefixo}_{sentido}_dia': dia(d), f'{prefixo}_{sentido}_cia': t['cia'],
                        f'{prefixo}_{sentido}_orig': o, f'{prefixo}_{sentido}_dest': de, f'{prefixo}_{sentido}_saida': t['saida'],
                        f'{prefixo}_{sentido}_chegada': t['chegada'], f'{prefixo}_{sentido}_tipo': t['tipo'],
                        f'{prefixo}_{sentido}_vend': t['vend'], f'{prefixo}_{sentido}_pp': t['pp'],
                        f'{prefixo}_{sentido}_pp_cia': t.get('ppCia') or t['pp'],
                        f'{prefixo}_{sentido}_link': t.get('link') or link_compra(sentido, d, t.get('voos'), pax),
                        f'{prefixo}_{sentido}_link_mm': link_mm(t['rota'], d, pax)})
        return out

    cfg = scan.get('cfg') or {'minDias': 27, 'maxDias': 36, 'natalAte': '2026-12-24'}
    resumo = {'tipo': 'resumo', 'quando': agora, 'pax': pax, 'alvo_geral': ALVO_GERAL, 'alvo_natal': ALVO_NATAL,
              'min_dias': cfg['minDias'], 'max_dias': cfg['maxDias'], 'natal_ate': cfg['natalAte'],
              'menor_geral': min(float(r['melhor_pp']) for r in linhas),
              'menor_natal': min((float(r['natal_pp']) for r in linhas if r.get('natal_pp')), default=None),
              'ant_geral': float(ult['melhor_pp']) if ult.get('melhor_pp') else None,
              'ant_natal': float(ult['natal_pp']) if ult.get('natal_pp') else None,
              'alerta': status_wpp or 'sem alerta', **grupo('geral', m), **grupo('natal', n)}
    rows = [resumo]
    rows += [{'tipo': 'hist', 'quando': r['data_hora'], 'geral_pp': float(r['melhor_pp']),
              'natal_pp': float(r['natal_pp']) if r.get('natal_pp') else None} for r in linhas]
    # um trecho por data: o painel monta a grade ida x volta somando estes preços
    for t in scan['trechos']:
        o, de = t['rota'].split('-')
        rows.append({'tipo': 'data', 'chave': f"{t['s']} {t['d']}", 'sentido': t['s'], 'data': t['d'], 'dia': dia(t['d']),
                     'pp': t['pp'], 'vend': t['vend'], 'pp_cia': t.get('ppCia') or t['pp'], 'cia': t['cia'], 'orig': o, 'dest': de,
                     'saida': t['saida'], 'chegada': t['chegada'], 'tipo_voo': t['tipo'],
                     'link': t.get('link') or link_compra(t['s'], t['d'], t.get('voos'), pax) or link_mm(t['rota'], t['d'], pax),
                     'link_mm': link_mm(t['rota'], t['d'], pax)})
    # vendedores dos trechos que a varredura detalhou (os das combinações vencedoras)
    for chave, lista in scan.get('vend', {}).items():
        sentido, d = chave.split(' ')
        rows += [{'tipo': 'vend', 'chave': f'{chave} {v}', 'sentido': sentido, 'data': d, 'vendedor': v, 'pp': pp} for v, pp in lista]
    json.dump(rows, open(painel, 'w', encoding='utf-8'), ensure_ascii=False)

    # ultimo.md
    def bloco(titulo, c):
        if not c:
            return f"## {titulo}\n\nNenhuma combinação.\n"
        ti, tv = tre[('ida', c['ida'])], tre[('volta', c['volta'])]
        def compra(sentido, d, t):
            link = t.get('link') or link_compra(sentido, d, t.get('voos'), pax)
            return f"[comprar na {nome_vendedor(t['vend'])}]({link})" if link else f"[ver na MaxMilhas]({link_mm(t['rota'], d, pax)})"
        return (f"## {titulo}: {brl(c['pp'])}/pessoa ({c['dias']} dias)\n\n"
                f"- **Ida {dia(c['ida'])}:** {texto_trecho(ti)} ({compra('ida', c['ida'], ti)})\n"
                f"- **Volta {dia(c['volta'])}:** {texto_trecho(tv)} ({compra('volta', c['volta'], tv)})\n")
    md = [f"# Última varredura: {agora} ({pax} adultos, preço por pessoa, sem mala despachada)\n",
          ('**ALERTA:** ' + ' | '.join(alertas) + '\n') if alertas else 'Sem queda nova de preço.\n',
          f"Telegram: {status_wpp or 'nenhuma oferta abaixo do alvo'} (alvos: geral {brl(ALVO_GERAL)}, com Natal {brl(ALVO_NATAL)})\n",
          bloco('Melhor combinação', m), bloco('Melhor com Natal (ida até 24/12)', n),
          '## Por data\n\n' + '\n'.join(f"- {t['s']} {dia(t['d'])}: {texto_trecho(t)}" for t in scan['trechos']) + '\n']
    if maxmilhas:
        md.append('## MaxMilhas direto (site)\n\n' + '\n'.join(f'- {x}' for x in maxmilhas) + '\n')
    md.append(f"Painel: {PAINEL_URL}\n")
    open(ultimo, 'w', encoding='utf-8').write('\n'.join(md))
    return saida


def _teste():
    def scan(pp_ida, pp_volta):
        return {'status': 'ok', 'pax': 2,
                'combos': [{'ida': '2026-12-18', 'volta': '2027-01-19', 'dias': 32, 'pp': pp_ida + pp_volta, 'natal': True}],
                'trechos': [{'s': 'ida', 'd': '2026-12-18', 'pp': pp_ida, 'vend': 'CIA LATAM', 'cia': 'LATAM', 'rota': 'GRU-NAT',
                             'saida': '07:05', 'chegada': '10:25', 'tipo': 'direto', 'ppCia': pp_ida},
                            {'s': 'volta', 'd': '2027-01-19', 'pp': pp_volta, 'vend': 'maxmilhas', 'cia': 'Gol', 'rota': 'FOR-GRU',
                             'saida': '12:50', 'chegada': '16:30', 'tipo': 'direto', 'ppCia': 725, 'voos': 'FOR>GRU 2027-01-19 G3 1555'}],
                'vend': {'ida 2026-12-18': [['CIA LATAM', pp_ida]], 'volta 2027-01-19': [['maxmilhas', pp_volta], ['CIA Gol', 725]]}}
    # link idêntico, byte a byte, ao que o Google gerou para o Gol 1555 FOR->GRU de 19/01 (2 adultos)
    esperado = (b'\x08\x1c\x10\x02\x1a\x5b\x12\x0a2027-01-19\x22\x20\x0a\x03FOR\x12\x0a2027-01-19\x1a\x03GRU\x2a\x02G3\x32\x041555'
                + b''.join(bytes([0x6a, 7, 8, 1, 0x12, 3]) + c for c in (b'FOR', b'NAT', b'JDO'))
                + b''.join(bytes([0x72, 7, 8, 1, 0x12, 3]) + c for c in (b'GRU', b'CGH'))
                + bytes.fromhex('4001400148017001') + bytes.fromhex('82010b08ffffffffffffffffff01980102'))
    tfs = urllib.parse.parse_qs(urllib.parse.urlparse(link_compra('volta', '2027-01-19', 'FOR>GRU 2027-01-19 G3 1555', 2)).query)['tfs'][0]
    assert base64.urlsafe_b64decode(tfs + '=' * (-len(tfs) % 4)) == esperado
    assert link_compra('ida', '2026-12-18', '', 2) == ''
    enviados = []
    fake = lambda msg: enviados.append(msg) or 'TELEGRAM_ENVIADO'
    with tempfile.TemporaryDirectory() as d:
        assert registrar(scan(1155, 477), d, fake) == ['SEM_NOVIDADE melhor R$1.632/pessoa']   # 1ª vez, acima do alvo
        r = registrar(scan(1000, 430), d, fake)                                                 # 1.430: recorde e abaixo do alvo Natal
        assert r[0].startswith('ALERTA') and r[1] == 'TELEGRAM_ENVIADO' and len(enviados) == 1, r
        assert 'R$1.430/pessoa' in enviados[0] and 'Ida sex 18/12: LATAM GRU-NAT' in enviados[0], enviados[0]
        assert 'Comprar na MaxMilhas, R$430: https://www.google.com/travel/flights/booking?tfs=' in enviados[0], enviados[0]
        assert 'Ver na MaxMilhas: https://www.maxmilhas.com.br/' in enviados[0], enviados[0]   # ida sem número de voo
        s2 = scan(1000, 430)
        s2['trechos'][1].update({'vend': '123milhas', 'link': 'https://123milhas.com/v2/busca?x'})
        assert link_compra('volta', '2027-01-19', s2['trechos'][1]['voos'], 2) not in trecho_compra('Volta', 'volta', '2027-01-19', s2['trechos'][1], 2)
        assert 'Comprar na 123milhas, R$430: https://123milhas.com/v2/busca?x' in trecho_compra('Volta', 'volta', '2027-01-19', s2['trechos'][1], 2)
        assert registrar(scan(1000, 420), d, fake)[1:] == [] and len(enviados) == 1              # caiu só R$10: não repete
        assert registrar(scan(950, 420), d, fake)[1] == 'TELEGRAM_ENVIADO'                       # caiu R$60: avisa de novo
        rows = json.load(open(os.path.join(d, 'painel.json')))
        res = [x for x in rows if x['tipo'] == 'resumo'][0]
        assert res['natal_ida_orig'] == 'GRU' and res['natal_total'] == 2740 and len([x for x in rows if x['tipo'] == 'hist']) == 4
        dias = [x for x in rows if x['tipo'] == 'data']
        assert dias[1]['orig'] == 'FOR' and dias[1]['link'].startswith('https://www.google.com/travel/flights/booking?tfs=')
        assert dias[0]['link'].startswith('https://www.maxmilhas.com.br/') and res['max_dias'] == 36
        assert len([x for x in rows if x['tipo'] == 'vend']) == 3
        assert registrar({'status': 'erro', 'erro': 'x'}, d, fake)[0].startswith('ERRO_VARREDURA')
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    elif sys.argv[1:] == ['--configurar-telegram']:
        print(configurar_telegram())
    elif sys.argv[1:] == ['--teste-telegram']:
        print(telegram(f'Teste do monitor de passagens: o alerta está funcionando. Painel: {PAINEL_URL}'))
    else:
        print('\n'.join(registrar(json.load(open(sys.argv[1], encoding='utf-8')), maxmilhas=sys.argv[2:])))
