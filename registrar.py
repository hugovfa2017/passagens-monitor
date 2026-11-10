"""Registra uma varredura: histórico, resumo, dados do painel e mensagem no Telegram.

A ida de 2 adultos já foi comprada (o voo fica em varredura.js, CFG.alvo). Agora o monitor acompanha:
- a ida dos extras (1 a 3 adultos, a confirmar): o mesmo voo em destaque e os voos perto dele;
- a volta dos dois que já têm a ida.
Toda varredura vira uma mensagem no Telegram; ela toca só com preço abaixo do alvo ou novo menor preço.

Uso:  python3 registrar.py ultima.json
      python3 registrar.py --teste                -> auto-checagem (não envia nada)
      python3 registrar.py --configurar-telegram  -> acha o seu chat depois que você manda /start ao bot
      python3 registrar.py --teste-telegram       -> manda uma mensagem de teste
      python3 registrar.py --novo-chat            -> mostra quem mais abriu o bot (para receber os alertas também)

O token do bot fica no Keychain (serviço telegram-passagens); o id do chat em telegram_chat.txt.
No GitHub, os segredos TELEGRAM_TOKEN e TELEGRAM_CHAT_ID (vários chats separados por vírgula).
Saídas na pasta: historico.csv, ultimo.md, painel.json (o que o painel mostra).
Imprime uma linha por evento: ALERTA ..., SEM_NOVIDADE ..., ERRO_VARREDURA ..., TELEGRAM_* ...
"""
import base64, csv, html, json, os, re, subprocess, sys, tempfile, urllib.parse, urllib.request
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
PAINEL_URL = 'https://hugovfa2017.github.io/passagens-monitor/'

# "Algo muito bom" = preço por pessoa, sem mala, igual ou abaixo destes alvos (Telegram com som).
ALVO_IDA = 800     # ida dos extras (compra individual), no mesmo voo ou num voo perto (a ida comprada saiu a R$480)
ALVO_VOLTA = 450   # volta dos dois (já foi a R$424; costuma ficar perto de R$520)
QUEDA_MIN = 20     # "novo menor preço" (Telegram com som) quando cai pelo menos isso abaixo do menor já visto

EXTRAS = (1, 2, 3)   # ponytail: colunas do histórico fixas para até 3 extras (hoje só a compra individual, mesmo_1, é buscada)
CAMPOS = ['data_hora', *(f'mesmo_{n}' for n in EXTRAS), 'perto_pp', 'perto_voo', 'volta_pp', 'volta_voo', 'volta_mala_pp']
ROTULO = {**{f'mesmo_{n}': f"o mesmo voo para {n} {'pessoa' if n == 1 else 'pessoas'}" for n in EXTRAS},
          'perto_pp': 'o voo perto mais barato', 'volta_pp': 'a volta de vocês dois'}
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


def pessoas(n):
    return f"{n} {'pessoa' if n == 1 else 'pessoas'}"


def voo_txt(v):  # 'Gol 1678 · 14:05 → 17:25, direto'
    num = f" {v['voo'].split(' ')[-1]}" if v.get('voo') else ''
    return f"{v['cia']}{num} · {v['saida']} → {v['chegada']}, {v['tipo']}"


def link_ida(v, n, mala=False):
    """Link de compra do voo de ida para n adultos (nunca vazio: o Telegram recusa botão sem link)."""
    p = v['precos'][n]
    return p.get('mala_link' if mala else 'link') or link_mm(f"{v['orig']}-{v['dest']}", v['d'], n)


def link_volta(t, pax, mala=False):
    x = t['mala'] if mala else t
    return x.get('link') or link_compra('volta', t['d'], x.get('voos'), pax) or link_mm(x['rota'], t['d'], pax)


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


def _chats():
    """Quem recebe os alertas: um ou mais ids de chat (separados por vírgula ou espaço)."""
    bruto = os.environ.get('TELEGRAM_CHAT_ID', '').strip() or (open(CHAT_FILE).read().strip() if os.path.exists(CHAT_FILE) else '')
    return re.findall(r'-?\d{5,}', bruto)


def _enviar_um(token, campos):
    """None se foi; senão o motivo. O Telegram nunca derruba a varredura; sem str(e), que pode trazer o token na URL."""
    try:
        r = _api(token, 'sendMessage', campos)
        return None if r.get('ok') else r.get('description', '')[:150]
    except Exception as e:
        desc = ''
        if hasattr(e, 'read'):   # erro HTTP: o Telegram explica no corpo ("Unauthorized", "chat not found"...)
            try:
                desc = json.loads(e.read()).get('description', '')[:150]
            except Exception:
                pass
        return f"{type(e).__name__} {getattr(e, 'code', '')} {desc}".strip()


def telegram(texto, silencioso=False, botoes=(), _envio=_enviar_um):
    """Manda para todos os chats. botoes: linhas de botões com link, ex. [[{'text': 'Abrir', 'url': 'https://...'}]]
    O resultado vai para arquivos públicos (ultimo.md, painel.json): nunca põe o id do chat nele."""
    token, chats = _token(), _chats()
    if not token or not chats:
        return 'TELEGRAM_NAO_CONFIGURADO'
    falhas = []
    for i, chat in enumerate(chats, 1):
        campos = {'chat_id': chat, 'text': texto, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true',
                  'disable_notification': 'true' if silencioso else 'false'}
        if botoes:
            campos['reply_markup'] = json.dumps({'inline_keyboard': list(botoes)})
        erro = _envio(token, campos)
        if erro is not None:
            falhas.append(f"destinatário {i} de {len(chats)}: {erro}" if len(chats) > 1 else erro)
    return 'TELEGRAM_ENVIADO' if not falhas else 'TELEGRAM_FALHOU ' + ' | '.join(falhas)


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


def novos_chats():
    """Quem abriu o bot e ainda não recebe os alertas (nome e id do chat), para pôr no segredo TELEGRAM_CHAT_ID."""
    token = _token()
    if not token:
        return 'TELEGRAM_SEM_TOKEN salve o token no Keychain primeiro'
    try:
        msgs = [u['message'] for u in _api(token, 'getUpdates').get('result', []) if 'message' in u]
    except Exception as e:
        return f'TELEGRAM_FALHOU {type(e).__name__}'
    ja = set(_chats())
    novos = {str(m['chat']['id']): ' '.join(filter(None, (m['chat'].get('first_name'), m['chat'].get('last_name')))) or m['chat'].get('title', '')
             for m in msgs if str(m['chat']['id']) not in ja}
    if not novos:
        return 'NENHUM_NOVO a pessoa precisa abrir o bot no Telegram, tocar em Iniciar e mandar qualquer mensagem (vale por 24 h)'
    return '\n'.join(f'NOVO {nome}: {chat}' for chat, nome in novos.items()) + \
        f"\nSegredo TELEGRAM_CHAT_ID com todos: {','.join([*ja, *novos])}"


def registrar(scan, pasta=AQUI, enviar=telegram):
    hist, ultimo, painel = (os.path.join(pasta, f) for f in ('historico.csv', 'ultimo.md', 'painel.json'))
    # hora da varredura (não a do registro), no fuso do computador
    fim = scan.get('fim')
    agora = (datetime.fromisoformat(fim.replace('Z', '+00:00')).astimezone() if fim else datetime.now()).strftime('%Y-%m-%d %H:%M')
    # quantidade de pessoas como número (no ultima.json, por ser JSON, ela vem como texto)
    voos = [{**v, 'precos': {int(n): p for n, p in v['precos'].items()}} for v in scan.get('voos_ida') or []]
    voltas = sorted(scan.get('trechos') or [], key=lambda t: t['d'])
    if scan.get('status') != 'ok' or not (voos or voltas):
        erro = scan.get('erro') or 'nenhum voo encontrado'
        with open(ultimo, 'a', encoding='utf-8') as f:
            f.write(f"\n\nVarredura de {agora} falhou: {erro}\n")
        aviso = (f"⚠️ <b>Passagens Nordeste</b>\n<i>{quando_tg(agora)}</i>\n\nDesta vez não consegui ler os preços. "
                 f"Tento de novo daqui a 1 hora; o painel continua com a última busca boa.\n\n<i>{html.escape(erro[:120])}</i>")
        return [f"ERRO_VARREDURA {erro}", enviar(aviso, silencioso=True, botoes=[PAINEL_BOTAO])]

    pax, n_max, alvo = scan['pax'], scan.get('paxIda', EXTRAS[-1]), scan['alvo']
    mesmo = next((v for v in voos if v['mesmo']), None)
    pm = {n: p for n, p in sorted(mesmo['precos'].items())} if mesmo else {}
    perto = sorted((v for v in voos if not v['mesmo'] and n_max in v['precos']), key=lambda v: v['precos'][n_max]['pp'])
    perto_dia = {}   # o voo perto mais barato de cada data
    for v in perto:
        perto_dia.setdefault(v['d'], v)
    volta = min(voltas, key=lambda t: t['pp'], default=None)
    volta_mala = min((t for t in voltas if t.get('mala')), key=lambda t: t['mala']['pp'], default=None)
    estadia = lambda d: (datetime.strptime(d, '%Y-%m-%d') - datetime.strptime(alvo['d'], '%Y-%m-%d')).days

    # histórico (o arquivo da época de ida e volta juntas, se ainda estiver aqui, é guardado com outro nome)
    if os.path.exists(hist) and not open(hist, encoding='utf-8').readline().startswith(','.join(CAMPOS[:2])):
        os.replace(hist, os.path.join(pasta, 'historico-ida-e-volta.csv'))
    linhas = list(csv.DictReader(open(hist, encoding='utf-8'))) if os.path.exists(hist) else []
    metricas = {**{f'mesmo_{n}': pm[n]['pp'] if n in pm else None for n in EXTRAS},
                'perto_pp': perto[0]['precos'][n_max]['pp'] if perto else None, 'volta_pp': volta['pp'] if volta else None}
    menor = lambda c: min((float(r[c]) for r in linhas if r.get(c)), default=None)
    antes = {c: menor(c) for c in metricas}
    ult = linhas[-1] if linhas else {}
    nova = {'data_hora': agora, **{c: '' if v is None else v for c, v in metricas.items()},
            'perto_voo': f"{dia(perto[0]['d'])} {voo_txt(perto[0])} {perto[0]['orig']}-{perto[0]['dest']} via {perto[0]['precos'][n_max]['vend']}" if perto else '',
            'volta_voo': f"{dia(volta['d'])} {texto_trecho(volta)}" if volta else '',
            'volta_mala_pp': volta_mala['mala']['pp'] if volta_mala else ''}
    with open(hist, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if not linhas and f.tell() == 0:
            w.writeheader()
        w.writerow(nova)
    linhas.append({k: str(v) for k, v in nova.items()})

    alvo_de = lambda c: ALVO_VOLTA if c == 'volta_pp' else ALVO_IDA
    recordes = [(c, v, antes[c]) for c, v in metricas.items() if v is not None and antes[c] is not None and v <= antes[c] - QUEDA_MIN]
    abaixo = [(c, v) for c, v in metricas.items() if v is not None and v <= alvo_de(c)]
    cap = lambda t: t[:1].upper() + t[1:]
    saida = [('ALERTA ' + ' | '.join(f"{ROTULO[c]} caiu p/ {brl(v)}/pessoa (antes {brl(a)})" for c, v, a in recordes)) if recordes else
             'SEM_NOVIDADE ' + ' | '.join(f"{c} {brl(v)}" for c, v in metricas.items() if v is not None)]

    # Telegram a cada varredura; com som só quando há recorde ou preço abaixo do alvo
    if abaixo:
        topo = ("🔥 <b>Hora de comprar!</b> " + cap('; '.join(f"{ROTULO[c]} sai a {brl(v)}" + ('' if c == 'mesmo_1' else ' cada') for c, v in abaixo)) +
                ", abaixo do alvo. Preço muda rápido: confira e compre logo.")
    elif recordes:
        topo = "📉 <b>Novo menor preço!</b> " + ' '.join(
            f"{cap(ROTULO[c])} caiu {brl(a - v)} e chegou a {brl(v)}, o menor desde que comecei." for c, v, a in recordes)
    elif not ult:
        topo = "👋 Primeira busca no plano novo: a ida dos extras no voo de vocês (ou perto dele) e a volta de vocês dois."
    else:
        mudou = [(c, metricas[c] - float(ult[c])) for c in (f'mesmo_{n_max}', 'volta_pp')
                 if metricas.get(c) is not None and ult.get(c) and abs(metricas[c] - float(ult[c])) >= 1]
        topo = ("😌 Tudo igual à última busca. Sigo de olho." if not mudou else
                ('📈 ' if all(d > 0 for _, d in mudou) else '↘️ ' if all(d < 0 for _, d in mudou) else '🔄 ') +
                cap('; '.join(f"{ROTULO[c]} {'subiu' if d > 0 else 'caiu'} {brl(abs(d))}" for c, d in mudou)) +
                " desde a última busca. Sigo de olho.")

    num = alvo['voo'].split(' ')[-1]
    blocos, botoes = [], []
    if mesmo:
        ok = lambda p: ' ✅' if p['pp'] <= ALVO_IDA else ''
        if list(pm) == [1]:   # compra individual: cada extra compra a sua passagem
            p = pm[1]
            precos = (f"Por pessoa, comprando uma passagem por vez: <b>{brl(p['pp'])}</b> na {html.escape(nome_vendedor(p['vend']))}{ok(p)}\n"
                      f"<i>Se forem 3, cada um compra a sua (até {brl(3 * p['pp'])} no total, se o preço não subir no meio).</i>")
        else:
            precos = '\n'.join(f"{pessoas(n)}: <b>{brl(p['pp'])}</b>{' cada' if n > 1 else ''} na {html.escape(nome_vendedor(p['vend']))}{ok(p)}"
                               for n, p in pm.items())
        malas = [(n, p) for n, p in pm.items() if 'mala_pp' in p]
        menor_pp = min(p['pp'] for p in pm.values())
        meta = f"✅ abaixo do alvo de {brl(ALVO_IDA)}" if menor_pp <= ALVO_IDA else f"faltam {brl(menor_pp - ALVO_IDA)} para o alvo de {brl(ALVO_IDA)}"
        blocos.append(f"🎯 <b>Ida dos extras · mesmo voo de vocês</b>\n<blockquote>🛫 <b>{dia(alvo['d'])}</b> · {alvo['orig']} → {alvo['dest']}\n"
                      f"{html.escape(voo_txt(mesmo))}\n\n{precos}</blockquote>\n{meta}" +
                      (f"\n🧳 Com mala, por pessoa: " + ' · '.join(f"{n}: {brl(p['mala_pp'])}" for n, p in malas) +
                       (' <i>(mala à parte, estimada)</i>' if any(p['mala_estimado'] for _, p in malas) else '') if malas else ''))
        botoes.append([{'text': f"🎯 Mesmo voo {brl(p['pp'])}" if len(pm) == 1 else f"🎯 {n}: {brl(p['pp'])}", 'url': link_ida(mesmo, n)}
                       for n, p in pm.items()])
    else:
        blocos.append(f"🎯 <b>Ida dos extras · mesmo voo de vocês</b>\nNão achei o {alvo['cia']} {num} ({dia(alvo['d'])}, {alvo['saida']}) "
                      "à venda nesta busca. Pode ter esgotado ou o vendedor não mostrou; veja os voos perto.")
    if perto_dia:
        cada = ' cada' if n_max > 1 else ''
        linhas_p = [f"{dia(v['d'])} · {v['orig']} → {v['dest']} · {html.escape(voo_txt(v))}\n<b>{brl(v['precos'][n_max]['pp'])}</b>{cada} na "
                    f"{html.escape(nome_vendedor(v['precos'][n_max]['vend']))}" for v in sorted(perto_dia.values(), key=lambda v: (v['d'], v['saida']))]
        blocos.append(f"📍 <b>Voos perto · {'compra individual' if n_max == 1 else 'para ' + pessoas(n_max)}</b>\n<blockquote>" + '\n\n'.join(linhas_p) + "</blockquote>")
        botoes.append([{'text': f"📍 Perto {brl(perto[0]['precos'][n_max]['pp'])} ({dia(perto[0]['d'])[4:]} {perto[0]['saida']})",
                        'url': link_ida(perto[0], n_max)}])
    if volta:
        o, de = volta['rota'].split('-')
        meta = f"✅ abaixo do alvo de {brl(ALVO_VOLTA)}" if volta['pp'] <= ALVO_VOLTA else f"faltam {brl(volta['pp'] - ALVO_VOLTA)} para o alvo de {brl(ALVO_VOLTA)}"
        b = (f"🏠 <b>Volta de vocês dois · {brl(volta['pp'])}</b> por pessoa\n<blockquote>🛬 <b>{dia(volta['d'])}</b> · {o} → {de}\n"
             f"{html.escape(voo_txt(volta))} · na {html.escape(nome_vendedor(volta['vend']))}</blockquote>\n{estadia(volta['d'])} dias lá · {meta}")
        linha_b = [{'text': f"🏠 Volta {brl(volta['pp'])}", 'url': link_volta(volta, pax)}]
        if volta_mala:
            x = volta_mala['mala']
            b += (f"\n🧳 Com mala: <b>{brl(x['pp'])}</b> na {html.escape(nome_vendedor(x['vend']))} · {dia(volta_mala['d'])}, {html.escape(x['cia'])} {x['saida']}"
                  + (' <i>(mala à parte, estimada)</i>' if x['estimado'] else ''))
            linha_b.append({'text': f"🧳 Com mala {brl(x['pp'])}", 'url': link_volta(volta_mala, pax, mala=True)})
        blocos.append(b)
        botoes.append(linha_b)
    msg = (f"✈️ <b>Passagens Nordeste</b>\n<i>{quando_tg(agora)} · preço por pessoa, sem mala (exceto 🧳)</i>\n\n{topo}\n\n" +
           '\n\n'.join(blocos) + "\n\n<i>Os botões abrem o vendedor já com o voo e a quantidade de pessoas.</i>")
    status_tg = enviar(msg, silencioso=not (abaixo or recordes), botoes=botoes + [PAINEL_BOTAO])
    saida.append(status_tg)

    # painel.json: uma tabela, cada linha com seu "tipo"
    num_ou_nada = lambda v: float(v) if v not in (None, '') else None
    rows = [{'tipo': 'resumo', 'quando': agora, 'pax': pax, 'pax_ida': n_max, 'alvo_ida': ALVO_IDA, 'alvo_volta': ALVO_VOLTA,
             'voo_comprado': {**alvo, 'dia': dia(alvo['d'])}, 'alerta': status_tg,
             'menor': {c: menor(c) for c in metricas},   # já com esta varredura
             'anterior': {c: num_ou_nada(ult.get(c)) for c in metricas}, 'agora': metricas}]
    rows += [{'tipo': 'hist', 'quando': r['data_hora'], **{c: num_ou_nada(r.get(c)) for c in metricas}} for r in linhas]
    for v in voos:
        rows.append({'tipo': 'ida', **{k: v[k] for k in ('d', 'cia', 'orig', 'dest', 'saida', 'chegada', 'voo', 'mesmo')},
                     'dia': dia(v['d']), 'tipo_voo': v['tipo'],
                     'precos': {str(n): {**p, 'link': link_ida(v, n), **({'mala_link': link_ida(v, n, True)} if 'mala_pp' in p else {})}
                                for n, p in v['precos'].items()}})
    for t in voltas:
        o, de = t['rota'].split('-')
        rows.append({'tipo': 'volta', 'data': t['d'], 'dia': dia(t['d']), 'dias': estadia(t['d']), 'pp': t['pp'], 'vend': t['vend'],
                     'pp_cia': t.get('ppCia') or t['pp'], 'cia': t['cia'], 'orig': o, 'dest': de, 'saida': t['saida'],
                     'chegada': t['chegada'], 'tipo_voo': t['tipo'], 'link': link_volta(t, pax),
                     # melhor opção da data com 1 mala de 23 kg por pessoa (pode ser outro voo ou vendedor)
                     **({'mala_pp': x['pp'], 'mala_vend': x['vend'], 'mala_cia': x['cia'], 'mala_orig': x['rota'][:3],
                         'mala_dest': x['rota'][4:], 'mala_saida': x['saida'], 'mala_chegada': x['chegada'], 'mala_tipo_voo': x['tipo'],
                         'mala_estimado': x['estimado'], 'mala_link': link_volta(t, pax, mala=True)} if (x := t.get('mala')) else {})})
    json.dump(rows, open(painel, 'w', encoding='utf-8'), ensure_ascii=False)

    # ultimo.md
    md = [f"# Última varredura: {agora} (preço por pessoa, sem mala despachada salvo onde diz \"com mala\")\n",
          ('**ALERTA:** ' + saida[0][7:] + '\n') if recordes else 'Sem queda nova de preço.\n',
          f"Telegram: {status_tg} (alvos: ida dos extras {brl(ALVO_IDA)}, volta {brl(ALVO_VOLTA)})\n",
          f"## Ida dos extras: mesmo voo ({alvo['cia']} {num}, {dia(alvo['d'])}, {alvo['saida']} {alvo['orig']} -> {alvo['dest']})\n",
          '\n'.join(f"- {pessoas(n)}: {brl(p['pp'])}/pessoa via {nome_vendedor(p['vend'])} ([comprar]({link_ida(mesmo, n)}))"
                    + (f" · com mala {brl(p['mala_pp'])}" if 'mala_pp' in p else '') for n, p in pm.items()) + '\n' if mesmo else
          'Não encontrado à venda nesta busca.\n',
          f"## Ida dos extras: voos perto (para {pessoas(n_max)})\n",
          '\n'.join(f"- {dia(v['d'])} {voo_txt(v)} {v['orig']}-{v['dest']}: {brl(v['precos'][n_max]['pp'])}/pessoa via "
                    f"{nome_vendedor(v['precos'][n_max]['vend'])} ([comprar]({link_ida(v, n_max)}))" for v in perto[:10]) + '\n' if perto else 'Nenhum.\n',
          f"## Volta de vocês dois ({pax} adultos)\n",
          '\n'.join(f"- {dia(t['d'])} ({estadia(t['d'])} dias): {texto_trecho(t)}" + (f" · com mala {brl(t['mala']['pp'])}" if t.get('mala') else '')
                    for t in voltas) + '\n',
          f"Painel: {PAINEL_URL}\n"]
    open(ultimo, 'w', encoding='utf-8').write('\n'.join(md))
    return saida


def _teste():
    alvo = {'d': '2026-12-22', 'saida': '14:05', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'NAT', 'voo': 'G3 1678'}

    def scan(m1=900, m3=1283, perto=1100, volta=469, mesmo=True):
        p_m = {1: {'pp': m1, 'vend': '123milhas', 'link': 'https://123milhas.com/1', 'mala_pp': m1 + 119, 'mala_vend': '123milhas',
                   'mala_link': 'https://123milhas.com/1', 'mala_estimado': True},
               3: {'pp': m3, 'vend': 'Zupper', 'link': 'https://www.zupper.com.br/3', 'mala_pp': m3 + 100, 'mala_vend': 'Zupper',
                   'mala_link': 'https://www.zupper.com.br/3', 'mala_estimado': False}}
        voo = {'d': '2026-12-22', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'NAT', 'saida': '14:05', 'chegada': '17:25', 'tipo': 'direto',
               'voo': 'G3 1678', 'mesmo': True, 'precos': p_m}
        outros = [{'d': '2026-12-21', 'cia': 'LATAM', 'orig': 'GRU', 'dest': 'NAT', 'saida': '13:35', 'chegada': '16:55', 'tipo': 'direto',
                   'voo': '', 'mesmo': False, 'precos': {3: {'pp': perto, 'vend': 'MaxMilhas', 'link': None}}},
                  {'d': '2026-12-21', 'cia': 'Azul', 'orig': 'CGH', 'dest': 'NAT', 'saida': '21:10', 'chegada': '02:05', 'tipo': 'com escala',
                   'voo': '', 'mesmo': False, 'precos': {3: {'pp': perto + 300, 'vend': 'Zupper', 'link': 'https://www.zupper.com.br/a'}}},
                  {'d': '2026-12-23', 'cia': 'Gol', 'orig': 'GRU', 'dest': 'NAT', 'saida': '06:00', 'chegada': '09:20', 'tipo': 'direto',
                   'voo': '', 'mesmo': False, 'precos': {1: {'pp': 600, 'vend': 'Zupper', 'link': 'https://www.zupper.com.br/b'}}}]
        voltas = [{'s': 'volta', 'd': '2027-01-25', 'pp': volta, 'vend': '123milhas', 'cia': 'LATAM', 'rota': 'NAT-GRU', 'saida': '05:05',
                   'chegada': '08:40', 'tipo': 'direto', 'ppCia': None, 'link': 'https://123milhas.com/v',
                   'mala': {'pp': volta + 150, 'estimado': True, 'vend': '123milhas', 'cia': 'LATAM', 'rota': 'NAT-GRU', 'saida': '05:05',
                            'chegada': '08:40', 'tipo': 'direto', 'link': 'https://123milhas.com/v', 'voos': ''}},
                  {'s': 'volta', 'd': '2027-01-19', 'pp': 725, 'vend': 'CIA Gol', 'cia': 'Gol', 'rota': 'FOR-GRU', 'saida': '12:50',
                   'chegada': '16:30', 'tipo': 'direto', 'ppCia': 725, 'voos': 'FOR>GRU 2027-01-19 G3 1555'}]
        return {'status': 'ok', 'pax': 2, 'paxIda': 3, 'alvo': alvo, 'voos_ida': ([voo] if mesmo else []) + outros, 'trechos': voltas}

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
        # vários destinatários: manda para cada um; o resultado diz quem falhou, sem mostrar o id
        os.environ.update(TELEGRAM_TOKEN=tk, TELEGRAM_CHAT_ID=' 111111111, 222222222 ')
        foram = []
        envio = lambda token, campos: foram.append(campos['chat_id']) or (None if campos['chat_id'] == '111111111' else 'Forbidden: bot was blocked')
        r = telegram('oi', _envio=envio)
        assert foram == ['111111111', '222222222'] and r == 'TELEGRAM_FALHOU destinatário 2 de 2: Forbidden: bot was blocked', r
        assert telegram('oi', _envio=lambda t, c: None) == 'TELEGRAM_ENVIADO' and '2222' not in r
    finally:
        os.environ.pop('TELEGRAM_CHAT_ID', None)
        os.environ.pop('TELEGRAM_TOKEN', None) if antes is None else os.environ.update(TELEGRAM_TOKEN=antes)
    enviados = []
    fake = lambda msg, silencioso=False, botoes=(): enviados.append((msg, silencioso, botoes)) or 'TELEGRAM_ENVIADO'
    with tempfile.TemporaryDirectory() as d:
        # histórico antigo (ida e volta juntas) é guardado com outro nome; o novo começa do zero
        open(os.path.join(d, 'historico.csv'), 'w').write('data_hora,pax,melhor_pp\n2026-11-05 01:00,2,1499\n')
        r = registrar(scan(), d, fake)
        assert r == ['SEM_NOVIDADE mesmo_1 R$900 | mesmo_3 R$1.283 | perto_pp R$1.100 | volta_pp R$469', 'TELEGRAM_ENVIADO'], r
        assert os.path.exists(os.path.join(d, 'historico-ida-e-volta.csv'))
        msg, mudo, bt = enviados[-1]
        assert mudo and 'Primeira busca no plano novo' in msg and msg.count('<blockquote>') == msg.count('</blockquote>') == 3, msg
        assert '🎯 <b>Ida dos extras · mesmo voo de vocês</b>' in msg and '🛫 <b>ter 22/12</b> · GRU → NAT\nGol 1678 · 14:05 → 17:25, direto' in msg, msg
        assert '1 pessoa: <b>R$900</b> na 123milhas\n3 pessoas: <b>R$1.283</b> cada na Zupper</blockquote>\nfaltam R$100 para o alvo de R$800' in msg, msg
        assert '🧳 Com mala, por pessoa: 1: R$1.019 · 3: R$1.383 <i>(mala à parte, estimada)</i>' in msg, msg
        # perto: o mais barato de cada data, para 3 (o voo de 23/12 só tem preço para 1 e fica de fora)
        assert '📍 <b>Voos perto · para 3 pessoas</b>' in msg and 'seg 21/12 · GRU → NAT · LATAM · 13:35 → 16:55, direto\n<b>R$1.100</b> cada na MaxMilhas' in msg, msg
        assert 'Azul' not in msg and 'qua 23/12' not in msg, msg
        assert '🏠 <b>Volta de vocês dois · R$469</b>' in msg and '34 dias lá · faltam R$19 para o alvo de R$450' in msg, msg
        assert '🧳 Com mala: <b>R$619</b> na 123milhas · seg 25/01, LATAM 05:05 <i>(mala à parte, estimada)</i>' in msg, msg
        assert [x['text'] for x in bt[0]] == ['🎯 1: R$900', '🎯 3: R$1.283'] and bt[0][1]['url'] == 'https://www.zupper.com.br/3', bt
        assert bt[1][0]['text'] == '📍 Perto R$1.100 (21/12 13:35)' and bt[1][0]['url'].startswith('https://www.maxmilhas.com.br/'), bt
        assert '/OW/GRU/NAT/2026-12-21/3/' in bt[1][0]['url'] and [x['text'] for x in bt[2]] == ['🏠 Volta R$469', '🧳 Com mala R$619'], bt
        assert bt[-1] == PAINEL_BOTAO and all(x['url'] for linha in bt for x in linha), bt
        # o mesmo voo para 3 cai R$83: recorde, com som (ainda acima do alvo)
        r = registrar(scan(m3=1200), d, fake)
        msg, mudo, bt = enviados[-1]
        assert r[0] == 'ALERTA o mesmo voo para 3 pessoas caiu p/ R$1.200/pessoa (antes R$1.283)' and not mudo, r
        assert '📉 <b>Novo menor preço!</b> O mesmo voo para 3 pessoas caiu R$83 e chegou a R$1.200' in msg, msg
        # subiu, sem recorde nem alvo: sem som
        registrar(scan(m1=900, m3=1250, volta=480), d, fake)
        msg, mudo, _ = enviados[-1]
        assert mudo and '📈 O mesmo voo para 3 pessoas subiu R$50; a volta de vocês dois subiu R$11 desde a última busca' in msg, msg
        registrar(scan(m1=900, m3=1250, volta=480), d, fake)
        assert enviados[-1][1] and '😌 Tudo igual à última busca' in enviados[-1][0]
        # abaixo do alvo na ida para 1 e na volta: hora de comprar, com som
        registrar(scan(m1=780, m3=1250, volta=440), d, fake)
        msg, mudo, _ = enviados[-1]
        assert not mudo and ('🔥 <b>Hora de comprar!</b> O mesmo voo para 1 pessoa sai a R$780; a volta de vocês dois sai a R$440 cada, '
                             'abaixo do alvo') in msg and '1 pessoa: <b>R$780</b> na 123milhas ✅' in msg, msg
        # voo comprado sumiu da venda: avisa e mostra os voos perto
        registrar(scan(m1=900, m3=1250, volta=480, mesmo=False), d, fake)
        msg, _, bt = enviados[-1]
        assert 'Não achei o Gol 1678 (ter 22/12, 14:05) à venda nesta busca' in msg and bt[0][0]['text'].startswith('📍'), msg
        linhas = list(csv.DictReader(open(os.path.join(d, 'historico.csv'), encoding='utf-8')))
        assert len(linhas) == 6 and linhas[0]['mesmo_1'] == '900' and linhas[-1]['mesmo_3'] == '' and linhas[0]['volta_mala_pp'] == '619', linhas
        rows = json.load(open(os.path.join(d, 'painel.json')))
        res = rows[0]
        assert res['tipo'] == 'resumo' and res['voo_comprado']['dia'] == 'ter 22/12' and res['menor']['mesmo_3'] == 1200, res
        assert res['anterior']['volta_pp'] == 440 and res['agora']['mesmo_3'] is None and len([x for x in rows if x['tipo'] == 'hist']) == 6
        idas = [x for x in rows if x['tipo'] == 'ida']
        assert idas[0]['precos']['3']['link'].startswith('https://www.maxmilhas.com.br/') and idas[0]['dia'] == 'seg 21/12', idas[0]
        vs = [x for x in rows if x['tipo'] == 'volta']
        assert [x['data'] for x in vs] == ['2027-01-19', '2027-01-25'] and vs[0]['link'].startswith('https://www.google.com/travel/flights/booking'), vs
        assert vs[1]['mala_pp'] == 630 and vs[1]['dias'] == 34 and 'mala_pp' not in vs[0], vs
        md = open(os.path.join(d, 'ultimo.md'), encoding='utf-8').read()
        assert 'Não encontrado à venda' in md and '- seg 25/01 (34 dias): R$480/pessoa via 123milhas' in md, md
        s9 = json.loads(json.dumps(scan(m3=1100)))   # lido do ultima.json: chaves de texto
        assert registrar(s9, d, fake)[0].startswith('ALERTA o mesmo voo para 3 pessoas caiu p/ R$1.100'), enviados[-1]
        # compra individual (CFG.paxIda = 1): texto e botão próprios, sem "cada"
        s10 = scan()
        s10['paxIda'] = 1
        for v in s10['voos_ida']:
            v['precos'] = {1: v['precos'].get(1) or {**v['precos'][3], 'pp': v['precos'][3]['pp'] - 50}}
        with tempfile.TemporaryDirectory() as d4:
            registrar(s10, d4, fake)
            msg, _, bt = enviados[-1]
            assert 'Por pessoa, comprando uma passagem por vez: <b>R$900</b> na 123milhas\n<i>Se forem 3, cada um compra a sua (até R$2.700' in msg, msg
            assert '📍 <b>Voos perto · compra individual</b>' in msg and '<b>R$600</b> na Zupper' in msg and ' cada na' not in msg, msg
            assert bt[0] == [{'text': '🎯 Mesmo voo R$900', 'url': 'https://123milhas.com/1'}] and bt[1][0]['text'] == '📍 Perto R$600 (23/12 06:00)', bt
        assert registrar({'status': 'erro', 'erro': 'x<y'}, d, fake) == ['ERRO_VARREDURA x<y', 'TELEGRAM_ENVIADO']
        assert enviados[-1][1] and 'não consegui ler os preços' in enviados[-1][0] and '<i>x&lt;y</i>' in enviados[-1][0], enviados[-1]
    print('ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--teste']:
        _teste()
    elif sys.argv[1:] == ['--configurar-telegram']:
        print(configurar_telegram())
    elif sys.argv[1:] == ['--novo-chat']:
        print(novos_chats())
    elif sys.argv[1:] == ['--teste-telegram']:
        print(telegram(f'Teste do monitor de passagens: o alerta está funcionando. Painel: {PAINEL_URL}'))
    else:
        print('\n'.join(registrar(json.load(open(sys.argv[1], encoding='utf-8')))))
