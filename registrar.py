"""Registra uma varredura (JSON de window.__scan): histórico, resumo, dados do painel e mensagem no Telegram.

Toda varredura vira uma mensagem no Telegram; ela toca só com preço abaixo do alvo ou novo menor preço.

Uso:  python3 registrar.py ultima.json ["linha MaxMilhas" ...]
      python3 registrar.py --teste                -> auto-checagem (não envia nada)
      python3 registrar.py --configurar-telegram  -> acha o seu chat depois que você manda /start ao bot
      python3 registrar.py --teste-telegram       -> manda uma mensagem de teste

O token do bot fica no Keychain (serviço telegram-passagens); o id do chat em telegram_chat.txt.
Saídas na pasta: historico.csv, ultimo.md, painel.json (o que o painel mostra).
Imprime uma linha por evento: ALERTA ..., SEM_NOVIDADE ..., ERRO_VARREDURA ..., TELEGRAM_* ...
"""
import base64, csv, html, json, os, re, subprocess, sys, tempfile, urllib.parse, urllib.request
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
PAINEL_URL = 'https://hugovfa2017.github.io/passagens-monitor/'

# "Algo muito bom" = preço por pessoa (ida + volta, sem mala) igual ou abaixo destes alvos.
ALVO_GERAL = 1400
ALVO_NATAL = 1450
QUEDA_MIN = 20    # "novo menor preço" (Telegram com som) quando cai pelo menos isso abaixo do menor já visto

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


def bloco_tg(emoji, titulo, c, alvo, tre, pax):
    """Uma combinação na mensagem do Telegram -> (texto, botões de compra da ida e da volta)."""
    voos, botoes = [], []
    for rotulo, s, seta in (('Ida', 'ida', '🛫'), ('Volta', 'volta', '🛬')):
        t = tre[(s, c[s])]
        o, de = t['rota'].split('-')
        voos.append(f"{seta} <b>{dia(c[s])}</b> · {o} → {de}\n"
                    f"{html.escape(t['cia'])} {t['saida']}, {t['tipo']} · {brl(t['pp'])} na {html.escape(nome_vendedor(t['vend']))}")
        botoes.append({'text': f"{emoji} {rotulo} {brl(t['pp'])}",
                       'url': t.get('link') or link_compra(s, c[s], t.get('voos'), pax) or link_mm(t['rota'], c[s], pax)})
    meta = f"✅ abaixo do alvo de {brl(alvo)}" if c['pp'] <= alvo else f"faltam {brl(c['pp'] - alvo)} para o alvo de {brl(alvo)}"
    return (f"{emoji} <b>{titulo} · {brl(c['pp'])}</b> por pessoa\n<blockquote>" + '\n\n'.join(voos) +
            f"</blockquote>\n{c['dias']} dias · {meta}"), botoes


PAINEL_BOTAO = [{'text': '📊 Abrir o painel', 'url': PAINEL_URL}]


def quando_tg(agora):  # '2026-11-05 00:17' -> 'qui 05/11, 00:17'
    return f"{dia(agora[:10])}, {agora[11:]}"


CHAT_FILE = os.path.join(AQUI, 'telegram_chat.txt')


def _token():
    # no GitHub Actions o token vem do segredo TELEGRAM_TOKEN; no Mac, do Keychain.
    # Fica só com o token (123456:ABC...), mesmo que tenha sido colado com texto ou espaço em volta.
    bruto = os.environ.get('TELEGRAM_TOKEN', '')
    if not bruto:
        try:
            bruto = subprocess.run(['security', 'find-generic-password', '-s', 'telegram-passagens', '-w'],
                                   capture_output=True, text=True).stdout
        except OSError:
            return ''
    achado = re.search(r'\d+:[A-Za-z0-9_-]{30,}', bruto)
    return achado[0] if achado else ''


def _api(token, metodo, campos=None):
    dados = urllib.parse.urlencode(campos).encode() if campos else None
    with urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/{metodo}', data=dados), timeout=30) as r:
        return json.loads(r.read().decode())


def telegram(texto, silencioso=False, botoes=()):
    """botoes: linhas de botões com link, ex. [[{'text': 'Abrir', 'url': 'https://...'}]]"""
    token = _token()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip() or (open(CHAT_FILE).read().strip() if os.path.exists(CHAT_FILE) else '')
    if not token or not chat:
        return 'TELEGRAM_NAO_CONFIGURADO'
    campos = {'chat_id': chat, 'text': texto, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true',
              'disable_notification': 'true' if silencioso else 'false'}
    if botoes:
        campos['reply_markup'] = json.dumps({'inline_keyboard': list(botoes)})
    try:
        r = _api(token, 'sendMessage', campos)
        return 'TELEGRAM_ENVIADO' if r.get('ok') else f"TELEGRAM_FALHOU {r.get('description', '')[:150]}"
    except Exception as e:  # o Telegram nunca derruba a varredura; sem str(e), que pode trazer o token na URL
        desc = ''
        if hasattr(e, 'read'):   # erro HTTP: o Telegram explica no corpo ("Unauthorized", "chat not found"...)
            try:
                desc = json.loads(e.read()).get('description', '')[:150]
            except Exception:
                pass
        return f"TELEGRAM_FALHOU {type(e).__name__} {getattr(e, 'code', '')} {desc}".strip()


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
    # hora da varredura (não a do registro), no fuso do computador
    fim = scan.get('fim')
    agora = (datetime.fromisoformat(fim.replace('Z', '+00:00')).astimezone() if fim else datetime.now()).strftime('%Y-%m-%d %H:%M')
    if scan.get('status') != 'ok' or not scan.get('combos'):
        erro = scan.get('erro') or 'sem combinações válidas'
        with open(ultimo, 'a', encoding='utf-8') as f:
            f.write(f"\n\nVarredura de {agora} falhou: {erro}\n")
        aviso = (f"⚠️ <b>Passagens Nordeste</b>\n<i>{quando_tg(agora)}</i>\n\nDesta vez não consegui ler os preços. "
                 f"Tento de novo daqui a 3 horas; o painel continua com a última busca boa.\n\n<i>{html.escape(erro[:120])}</i>")
        return [f"ERRO_VARREDURA {erro}", enviar(aviso, silencioso=True, botoes=[PAINEL_BOTAO])]

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
    # recorde: caiu pelo menos QUEDA_MIN abaixo do menor preço já visto
    alertas, recordes = [], []
    for rotulo, c, ant in (('opção com Natal', n, ant_n), ('mais barata', m, ant_m)):
        if c and ant is not None and c['pp'] <= ant - QUEDA_MIN and not any(r[1] is c for r in recordes):
            alertas.append(f"{rotulo} caiu p/ {brl(c['pp'])}/pessoa (antes {brl(ant)}): {dia(c['ida'])} -> {dia(c['volta'])}")
            recordes.append((rotulo, c, ant))
    saida.append(('ALERTA ' + ' | '.join(alertas)) if alertas else f"SEM_NOVIDADE melhor {brl(m['pp'])}/pessoa")

    # Telegram a cada varredura; com som só quando há recorde ou preço abaixo do alvo
    abaixo = (n and n['pp'] <= ALVO_NATAL) or m['pp'] <= ALVO_GERAL
    if abaixo:
        topo = "🔥 <b>Hora de comprar!</b> Achei passagem abaixo do seu alvo. Preço muda rápido, então confira e compre logo."
    elif recordes:
        topo = "📉 <b>Novo menor preço!</b> " + ' '.join(
            f"A {r} caiu {brl(ant - c['pp'])} e chegou a {brl(c['pp'])}, a menor desde que comecei." for r, c, ant in recordes)
    elif not ult:
        topo = "👋 Primeira busca registrada. A partir de agora te conto cada uma."
    else:
        dif = m['pp'] - float(ult['melhor_pp'])
        topo = ("😌 Tudo igual à última busca. Sigo de olho." if abs(dif) < 1 else
                f"📈 A mais barata subiu {brl(dif)} desde a última busca. Sigo de olho." if dif > 0 else
                f"↘️ A mais barata caiu {brl(-dif)} desde a última busca, mas ainda não é a menor que já vi.")
    if n and (n['ida'], n['volta']) == (m['ida'], m['volta']):
        blocos = [bloco_tg('🎄', 'Mais barata, e com Natal', n, ALVO_NATAL, tre, pax)]
    else:
        blocos = ([bloco_tg('🎄', 'Com Natal', n, ALVO_NATAL, tre, pax)] if n else []) + [bloco_tg('☀️', 'Mais barata', m, ALVO_GERAL, tre, pax)]
    msg = (f"✈️ <b>Passagens Nordeste</b>\n<i>{quando_tg(agora)} · {pax} adultos, sem mala</i>\n\n{topo}\n\n" +
           '\n\n'.join(b[0] for b in blocos) + "\n\n<i>Os botões abrem o vendedor já com o voo.</i>")
    status_wpp = enviar(msg, silencioso=not (abaixo or recordes), botoes=[b[1] for b in blocos] + [PAINEL_BOTAO])
    saida.append(status_wpp)

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
              'alerta': status_wpp, **grupo('geral', m), **grupo('natal', n)}
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
          f"Telegram: {status_wpp} (alvos: geral {brl(ALVO_GERAL)}, com Natal {brl(ALVO_NATAL)})\n",
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
    # token colado com texto/espaço em volta: fica só o token; sem token válido, não tenta enviar
    antes = os.environ.get('TELEGRAM_TOKEN')
    try:
        tk = '123456789:AAH-abcdefghijklmnopqrstuvwxyz_0123'
        os.environ['TELEGRAM_TOKEN'] = f' Use this token to access the HTTP API:\n{tk} \n'
        assert _token() == tk, _token()
        os.environ['TELEGRAM_TOKEN'] = 'sem token aqui'
        assert _token() == '' and telegram('x') == 'TELEGRAM_NAO_CONFIGURADO'
    finally:
        os.environ.pop('TELEGRAM_TOKEN', None) if antes is None else os.environ.update(TELEGRAM_TOKEN=antes)
    enviados = []
    fake = lambda msg, silencioso=False, botoes=(): enviados.append((msg, silencioso, botoes)) or 'TELEGRAM_ENVIADO'
    with tempfile.TemporaryDirectory() as d:
        # 1ª busca, acima do alvo: mensagem sem som, botões ida/volta + painel
        assert registrar(scan(1155, 477), d, fake) == ['SEM_NOVIDADE melhor R$1.632/pessoa', 'TELEGRAM_ENVIADO']
        msg, mudo, bt = enviados[-1]
        assert mudo and 'Primeira busca' in msg and 'Mais barata, e com Natal · R$1.632' in msg and 'faltam R$182 para o alvo de R$1.450' in msg, msg
        assert [x['text'] for x in bt[0]] == ['🎄 Ida R$1.155', '🎄 Volta R$477'] and bt[-1] == PAINEL_BOTAO, bt
        # 1.430: recorde e abaixo do alvo com Natal: com som
        r = registrar(scan(1000, 430), d, fake)
        msg, mudo, bt = enviados[-1]
        assert r[0].startswith('ALERTA') and r[1] == 'TELEGRAM_ENVIADO' and not mudo, r
        assert 'Hora de comprar' in msg and '✅ abaixo do alvo de R$1.450' in msg and '🛫 <b>sex 18/12</b> · GRU → NAT' in msg, msg
        assert 'Gol 12:50, direto · R$430 na MaxMilhas' in msg and msg.count('<blockquote>') == msg.count('</blockquote>') == 1, msg
        assert bt[0][1]['url'].startswith('https://www.google.com/travel/flights/booking?tfs=') and '&hl=pt-BR' in bt[0][1]['url'], bt
        assert bt[0][0]['url'].startswith('https://www.maxmilhas.com.br/'), bt   # ida sem número de voo
        # subiu: sem som
        registrar(scan(1100, 430), d, fake)
        assert enviados[-1][1] and 'subiu R$100 desde a última busca' in enviados[-1][0], enviados[-1]
        # vendedor com link próprio (123milhas) vence o link do Google
        s2 = scan(1000, 430)
        s2['trechos'][1].update({'vend': '123milhas', 'link': 'https://123milhas.com/v2/busca?x&y'})
        texto, bt = bloco_tg('t', 'T', s2['combos'][0], 1450, {(t['s'], t['d']): t for t in s2['trechos']}, 2)
        assert 'R$430 na 123milhas' in texto and bt[1]['url'] == 'https://123milhas.com/v2/busca?x&y', bt
        assert registrar(scan(950, 420), d, fake)[0].startswith('ALERTA') and not enviados[-1][1]  # novo recorde: com som
        assert len(enviados) == 4
        rows = json.load(open(os.path.join(d, 'painel.json')))
        res = [x for x in rows if x['tipo'] == 'resumo'][0]
        assert res['natal_ida_orig'] == 'GRU' and res['natal_total'] == 2740 and len([x for x in rows if x['tipo'] == 'hist']) == 4
        dias = [x for x in rows if x['tipo'] == 'data']
        assert dias[1]['orig'] == 'FOR' and dias[1]['link'].startswith('https://www.google.com/travel/flights/booking?tfs=')
        assert dias[0]['link'].startswith('https://www.maxmilhas.com.br/') and res['max_dias'] == 36
        assert len([x for x in rows if x['tipo'] == 'vend']) == 3
        assert registrar({'status': 'erro', 'erro': 'x<y'}, d, fake) == ['ERRO_VARREDURA x<y', 'TELEGRAM_ENVIADO']
        assert enviados[-1][1] and 'não consegui ler os preços' in enviados[-1][0] and '<i>x&lt;y</i>' in enviados[-1][0], enviados[-1]
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
