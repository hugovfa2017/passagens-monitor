"""Registra uma rodada do monitor de passagens: mensagem por pessoa no Telegram, histórico, estado e dados do painel.

A viagem (quem viaja, datas, aeroportos, alvos) está em viagem.json; a rodada (nuvem/rodar.py) entrega, para cada
trecho de cada grupo, a opção recomendada e a mais barata. Aqui:
- a mensagem ganha um bloco por grupo; toca só quando um trecho fica abaixo do alvo ou bate o menor preço já visto;
- precos.csv guarda o melhor preço de cada data (uma linha só quando muda), para dizer quando comprar;
- estado.json guarda o que a próxima rodada precisa (última recomendada, datas já vistas, o que já tocou);
- painel.json e ultimo.md são públicos: usam só a sigla de cada grupo. Os nomes vêm do segredo NOMES_VIAGEM e só
  aparecem no Telegram.

Uso:  python3 registrar.py --teste                -> auto-checagem (não envia nada)
      python3 registrar.py --configurar-telegram  -> acha o seu chat depois que você manda /start ao bot
      python3 registrar.py --teste-telegram       -> manda uma mensagem de teste
      python3 registrar.py --novo-chat            -> mostra quem mais abriu o bot (para receber os alertas também)

O token do bot fica no Keychain (serviço telegram-passagens); o id do chat em telegram_chat.txt.
No GitHub, os segredos TELEGRAM_TOKEN e TELEGRAM_CHAT_ID (a lista fixa do dono; o primeiro chat é o dono).
Para outro celular receber, basta abrir o bot e mandar /start: o workflow "Atendimento dos bots" inscreve o chat em
poucos minutos e guarda a lista em assinantes.cif, cifrada (ver atendimento.py). /sair cancela.
PASSAGENS_ENVIAR=0 não envia nada; PASSAGENS_ENVIAR=teste manda só para o dono, sem som, marcado [TESTE].
"""
import csv, html, json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
from datetime import datetime

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(AQUI, 'nuvem'))
import plano    # noqa: E402
import selecao  # noqa: E402

PAINEL_URL = 'https://hugovfa2017.github.io/passagens-monitor/'
DICA_24H = "Com 7+ dias de antecedência, dá para desistir em até 24 h sem custo (ANAC). Prefira emissão imediata."
SEMANA = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']
CAMPOS = ['quando', 'serie', 'd', 'ne', 'pax', 'pp', 'conf']   # precos.csv
LIMITE_MENSAGEM = 3900          # o Telegram aceita 4.096 caracteres
HORAS_ENTRE_TOQUES = 6          # o mesmo trecho não toca de novo antes disso, a não ser que caia mais
PAINEL_BOTAO = [{'text': '📊 Abrir o painel', 'url': PAINEL_URL}]


def dia(d):  # '2026-12-18' -> 'sex 18/12'
    dt = datetime.strptime(d, '%Y-%m-%d')
    return f"{SEMANA[dt.weekday()]} {dt.day:02d}/{dt.month:02d}"


def brl(n):
    return 'R$' + f'{round(n):,}'.replace(',', '.')


def link_mm(orig, dest, data, pax):
    return f'https://www.maxmilhas.com.br/busca-passagens-aereas/OW/{orig}/{dest}/{data}/{pax}/0/0/EC'


def nome_vendedor(v):
    v = v.removeprefix('CIA ')
    return 'MaxMilhas' if v.lower() == 'maxmilhas' else v


def quando_tg(agora):  # '2026-11-05 00:17' -> 'qui 05/11, 00:17'
    return f"{dia(agora[:10])}, {agora[11:]}"


def janela(datas_):
    """['2026-12-22','2026-12-23','2026-12-26'] -> '22 a 23/12 e 26/12': dias seguidos viram um intervalo."""
    if not datas_:
        return 'sem datas'
    grupos, ini = [], datas_[0]
    for a, b in zip(datas_, datas_[1:] + [None]):
        if b is None or (datetime.strptime(b, '%Y-%m-%d') - datetime.strptime(a, '%Y-%m-%d')).days != 1:
            grupos.append((ini, a))
            ini = b
    curto = lambda d, mes=True: f'{d[8:10]}/{d[5:7]}' if mes else d[8:10]
    partes = [curto(a) if a == b else f"{curto(a, a[5:7] != b[5:7])} a {curto(b)}" for a, b in grupos]
    return ', '.join(partes[:-1]) + (' e ' if len(partes) > 1 else '') + partes[-1]


# ---------------------------------------------------------------- Telegram
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
    """Quem recebe os alertas: a lista fixa do dono (segredo TELEGRAM_CHAT_ID ou telegram_chat.txt) e, depois dela,
    quem se inscreveu mandando /start ao bot (assinantes.cif, mantido por atendimento.py)."""
    bruto = os.environ.get('TELEGRAM_CHAT_ID', '').strip() or (open(CHAT_FILE).read().strip() if os.path.exists(CHAT_FILE) else '')
    fixos = re.findall(r'-?\d{5,}', bruto)
    try:
        import atendimento
        pelo_start = atendimento.inscritos(_token(), os.path.join(AQUI, 'assinantes.cif'))
    except Exception:
        pelo_start = []
    return list(dict.fromkeys(fixos + pelo_start))


def _enviar_um(token, campos, api=None, espera=5):
    """None se foi; senão o motivo. O Telegram nunca derruba a varredura; sem str(e), que pode trazer o token na URL.
    Rede fora do ar ou resposta que não chega (já houve TimeoutError): espera e tenta mais uma vez."""
    api = api or _api
    for tentativa in (1, 2):
        try:
            r = api(token, 'sendMessage', campos)
            return None if r.get('ok') else r.get('description', '')[:150]
        except Exception as e:
            desc = ''
            if hasattr(e, 'read'):   # erro HTTP: o Telegram explica no corpo ("Unauthorized", "chat not found"...)
                try:
                    desc = json.loads(e.read()).get('description', '')[:150]
                except Exception:
                    pass
            elif tentativa == 1:     # sem resposta do Telegram: vale tentar de novo
                time.sleep(espera)
                continue
            return f"{type(e).__name__} {getattr(e, 'code', '')} {desc}".strip()


def telegram(texto, silencioso=False, botoes=(), _envio=_enviar_um, so_dono=False):
    """Manda para todos os chats. botoes: linhas de botões com link, ex. [[{'text': 'Abrir', 'url': 'https://...'}]]
    O resultado vai para arquivos públicos (ultimo.md, painel.json): nunca põe o id do chat nele."""
    token, chats = _token(), (_chats()[:1] if so_dono else _chats())   # so_dono: rodada de teste, só o primeiro chat
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


# ---------------------------------------------------------------- estado e arquivos
def ler_json(caminho, padrao):
    """Lê um JSON; se estiver estragado, tenta a cópia de segurança; sem nenhum dos dois, devolve o padrão."""
    for arq in (caminho, caminho + '.bak'):
        try:
            with open(arq, encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return padrao


def gravar_json(caminho, dados):
    """Grava inteiro ou não grava (arquivo temporário + troca), guardando a versão anterior em .bak."""
    with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(caminho) or '.', delete=False, encoding='utf-8') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    if os.path.exists(caminho):
        os.replace(caminho, caminho + '.bak')
    os.replace(f.name, caminho)


def ler_estado(pasta=AQUI):
    return ler_json(os.path.join(pasta, 'estado.json'), {})


def _horas(a, b):
    """Horas entre dois 'AAAA-MM-DD HH:MM'."""
    return (datetime.strptime(b, '%Y-%m-%d %H:%M') - datetime.strptime(a, '%Y-%m-%d %H:%M')).total_seconds() / 3600


def atualizar_estado(estado, scan, ts, v, agora):
    """Atualiza o estado com esta rodada e devolve (linhas novas do precos.csv, alertas, mudanças).
    alertas: [{serie, tipo: 'alvo' ou 'recorde', pp, antes, toca}]. mudanças: {serie: diferença para a recomendada anterior}."""
    sel, linhas, alertas, mudancas = v['selecao'], [], [], {}
    por_serie = estado.setdefault('series', {})
    for t in ts:
        r = scan['series'].get(t['serie'])
        if t['igual_a'] or r is None:
            continue
        e = por_serie.setdefault(t['serie'], {})
        datas_ = e.setdefault('datas', {})
        for d in r['olhadas']:
            cel = r['celulas'].get(d) or {}
            ne = min(cel, key=lambda c: (not cel[c]['conf'], cel[c]['pp'])) if cel else None
            novo = {'pp': cel[ne]['pp'], 'ne': ne, 'conf': cel[ne]['conf'], 'visto': agora} if ne else {'pp': None, 'visto': agora}
            antes = datas_.get(d) or {}
            if (novo['pp'] is None) != (antes.get('pp') is None) or (novo['pp'] and (abs(novo['pp'] - antes['pp']) >= 5 or novo.get('ne') != antes.get('ne')
                                                                                      or novo.get('conf') != antes.get('conf'))):
                linhas.append({'quando': agora, 'serie': t['serie'], 'd': d, 'ne': novo.get('ne') or '', 'pax': t['pax'],
                               'pp': novo['pp'] or '', 'conf': int(bool(novo.get('conf')))})
            datas_[d] = novo
        for d in [d for d in datas_ if d not in t['datas']]:      # datas que já passaram
            del datas_[d]
        rec = r['recomendada']
        if not rec:
            continue
        antes = e.get('rec')
        if antes and antes['pp'] != rec['pp']:
            mudancas[t['serie']] = rec['pp'] - antes['pp']
        if not antes or antes['pp'] != rec['pp'] or antes['ident'] != list(selecao.ident(rec)):
            linhas.append({'quando': agora, 'serie': t['serie'] + '#rec', 'd': rec['d'], 'ne': selecao.nordeste(rec, v), 'pax': t['pax'], 'pp': rec['pp'], 'conf': 1})
        e.setdefault('desde', agora)
        menor = e.get('min')
        tipo = None
        if t['alvo'] and rec['pp'] <= t['alvo']:
            tipo = 'alvo'
        elif menor and rec['pp'] <= menor - sel['queda_min'] and _horas(e['desde'], agora) >= sel['horas_para_recorde']:
            tipo = 'recorde'
        if tipo:
            tocou = e.get('tocou')
            toca = not tocou or rec['pp'] <= tocou['pp'] - sel['queda_min'] or _horas(tocou['quando'], agora) >= HORAS_ENTRE_TOQUES
            alertas.append({'serie': t['serie'], 'tipo': tipo, 'pp': rec['pp'], 'antes': menor, 'toca': toca})
            if toca:
                e['tocou'] = {'pp': rec['pp'], 'quando': agora}
        e['min'] = min(menor, rec['pp']) if menor else rec['pp']
        e['rec'] = {'ident': list(selecao.ident(rec)), 'd': rec['d'], 'ne': selecao.nordeste(rec, v), 'pp': rec['pp'], 'vend': rec['vend']}
    estado['quando'] = agora
    return linhas, alertas, mudancas


# ---------------------------------------------------------------- mensagem
def lugar(c, v):
    return v['aeroportos'][c]['curto']


def voo_txt(o):
    """'Gol 1555, direto' · 'Azul, 1 conexão (espera de 1h10)' · 'LATAM, 1 conexão'. O número só aparece em voo sem conexão."""
    num = ' ' + o['voos'].split(' ')[-1] if o.get('voos') and ';' not in o['voos'] else ''
    t = o['tipo']
    tipo = 'direto' if t == 'direto' else f"1 conexão (espera de {t[7:]})" if t.startswith('escala ') else '1 conexão'
    return f"{html.escape(o['cia'])}{num}, {tipo}"


def horarios(o, v):
    """'12h50 Fortaleza → 16h30 Guarulhos', com '(+1 dia)' quando chega no dia seguinte."""
    chegada = o['chegada'].replace(':', 'h') + (' (+1 dia)' if o['chegada'] < o['saida'] else '')
    return f"{o['saida'].replace(':', 'h')} {lugar(o['orig'], v)} → {chegada} {lugar(o['dest'], v)}"


def linhas_oferta(prefixo, o, v, alvo=None, cada='', nota=''):
    """As duas linhas de uma opção, sempre na mesma ordem: preço, dia e voo; embaixo, horários, aeroportos e onde comprar."""
    ok = '✅ ' if alvo and o['pp'] <= alvo else ''
    return (f"{prefixo}{ok}<b>{brl(o['pp'])}</b>{cada} · {dia(o['d'])} · {voo_txt(o)}{nota}",
            f"└ {horarios(o, v)} · {html.escape(nome_vendedor(o['vend']))}")


def bloco_do_grupo(g, ts, scan, v, estado, nomes):
    """As linhas de um grupo: [(texto, nível)]. Nível 0 fica sempre; 1 e 2 saem primeiro quando a mensagem não cabe.
    Só entra preço conferido nesta busca (tela de compra ou página do vendedor); o que não foi conferido vem avisado."""
    series, meus = scan['series'], [t for t in ts if t['grupo'] == g['id']]
    cada = ' por pessoa' if g['assentos'] > 1 else ''
    linhas = [(f"{g.get('emoji', '•')} <b>{html.escape(nomes[g['id']])}</b>" + (f" · {g['assentos']} lugares no mesmo voo" if g['assentos'] > 1 else ''), 0)]
    precos, comprado = {}, v['comprado']
    for t in meus:
        linhas.append((f"<u>{t['nome']} · {janela(t['datas'])}</u>", 0))
        if not t['datas']:
            linhas.append(('as datas já passaram', 0))
            continue
        if t['igual_a']:
            fonte = (series.get(t['igual_a']) or {}).get('recomendada')
            if fonte:
                precos[t['id']] = fonte['pp']
                dono = html.escape(nomes[t['igual_a'].split('.')[0]])
                linhas += [(f"<b>{brl(fonte['pp'])}</b> · {dia(fonte['d'])} · mesmo voo de {dono}", 0), (f"└ {horarios(fonte, v)}", 0)]
            else:
                linhas.append(('sem preço conferido nesta busca', 0))
            continue
        r = series.get(t['serie']) or {}
        rec = r.get('recomendada')
        if not rec:
            linhas.append(('sem preço conferido nesta busca', 0))
            continue
        precos[t['id']] = rec['pp']
        eh_o_comprado = t['voo_comprado'] and selecao.eh_comprado(rec, v)
        linhas += [(l, 0) for l in linhas_oferta('⭐ ', rec, v, t['alvo'], cada, ' · 🎯 é o voo já comprado' if eh_o_comprado else '')]
        if r.get('mais_barata'):
            linhas += [(l, 1) for l in linhas_oferta('💸 Mais barata: ', r['mais_barata'], v, t['alvo'], cada)]
        if t['voo_comprado'] and r.get('comprado') and not eh_o_comprado:
            linhas += [(l, 0) for l in linhas_oferta('🎯 Voo já comprado: ', r['comprado'], v, t['alvo'], cada)]
        elif t['voo_comprado'] and comprado['d'] in t['datas'] and not eh_o_comprado:
            linhas.append((f"🎯 Voo já comprado ({comprado['cia']} {comprado['voo'].split(' ')[-1]}, {dia(comprado['d'])} "
                           f"{comprado['saida'].replace(':', 'h')}): sem lugar à venda nesta busca", 0))
        if t['rodizio']:
            vistas = estado.get('series', {}).get(t['serie'], {}).get('datas', {})
            outras = sorted(((x['pp'], d) for d, x in vistas.items() if x.get('pp') and d != rec['d']))[:2]
            if outras:
                linhas.append(('Outras datas, preço ainda não conferido: ' + ' · '.join(f"{dia(d)} {brl(pp)}" for pp, d in outras), 2))
    # quem tem duas formas de voltar: quanto uma sai mais barata que a outra
    voltas = [t for t in meus if t['sentido'] == 'volta' and t['id'] in precos]
    if len(voltas) == 2 and precos[voltas[0]['id']] != precos[voltas[1]['id']]:
        a, b = sorted(voltas, key=lambda t: precos[t['id']])
        linhas.append((f"↔️ {a['nome']} sai {brl(precos[b['id']] - precos[a['id']])} mais barato que {b['nome'].lower()}", 1))
    pacs = selecao.pacotes({**series, **{f"{g['id']}.{t['id']}": series.get(t['igual_a']) for t in meus if t['igual_a']}}, g)
    if len(pacs) == 1:
        total = f" · {brl(pacs[0]['pp'] * g['assentos'])} no total" if g['assentos'] > 1 else ''
        linhas.append((f"📦 Ida + volta: <b>{brl(pacs[0]['pp'])}</b>{cada}{total}", 0))
    elif pacs:
        melhor = min(p['pp'] for p in pacs)
        linhas.append(('📦 Ida + volta: ' + ' · '.join(f"{p['nome']} " + (f"<b>{brl(p['pp'])}</b>" if p['pp'] == melhor else brl(p['pp'])) for p in pacs), 0))
    return linhas


def montar_mensagem(cabecalho, topo, blocos, rodape, limite=LIMITE_MENSAGEM):
    """Junta tudo; se passar do limite, tira primeiro o rodapé, depois as linhas de nível 2, depois as de nível 1."""
    def texto(nivel, com_rodape):
        corpo = '\n\n'.join('\n'.join(l for l, n in b if n <= nivel) for b in blocos)
        return f"{cabecalho}\n{topo}\n\n{corpo}" + (f"\n\n<i>{rodape}</i>" if com_rodape and rodape else '')
    for nivel, com_rodape in ((2, True), (2, False), (1, False), (0, False)):
        t = texto(nivel, com_rodape)
        if len(t) <= limite:
            return t
    return t[:limite - 1] + '…'


def nome_curto(serie, ts, nomes):
    t = next(t for t in ts if t['serie'] == serie)
    return f"{nomes[t['grupo']]}, {t['nome'].lower()}"


def montar_topo(alertas, mudancas, primeira, ts, nomes):
    """A linha de novidades: hora de comprar, menor preço, o que mudou, ou nada mudou."""
    alvo = [a for a in alertas if a['tipo'] == 'alvo']
    if alvo:
        alvos = {t['serie']: t['alvo'] for t in ts}
        return ('🔥 <b>Hora de comprar:</b> ' + ' · '.join(f"{html.escape(nome_curto(a['serie'], ts, nomes))} {brl(a['pp'])} (alvo {brl(alvos[a['serie']])})" for a in alvo)
                + f"\n<i>{DICA_24H}</i>")
    recordes = [a for a in alertas if a['tipo'] == 'recorde']
    if recordes:
        return '📉 <b>Menor preço já visto:</b> ' + ' · '.join(f"{html.escape(nome_curto(a['serie'], ts, nomes))} {brl(a['pp'])} (era {brl(a['antes'])})" for a in recordes)
    if primeira:
        return '👋 Primeira busca no formato novo: um bloco por pessoa.'
    if not mudancas:
        return '😌 Sem mudança desde a última busca.'
    sinais = {v_ > 0 for v_ in mudancas.values()}
    seta = '📈' if sinais == {True} else '↘️' if sinais == {False} else '↕️'
    partes = [f"{html.escape(nome_curto(s, ts, nomes))} {'+' if d > 0 else '−'}{brl(abs(d))}" for s, d in list(mudancas.items())[:5]]
    return f"{seta} " + ' · '.join(partes)


def montar_rodape(scan, v):
    ultimos = [c for c in v['volta_origens'] if v['aeroportos'][c].get('ultimo_caso')]
    partes = ['⭐ = melhor opção pela ordem ' + ', '.join(lugar(c, v) for c in v['volta_origens'] if c not in ultimos)
              + (f"; {' e '.join(lugar(c, v) for c in ultimos)} só em último caso" if ultimos else '')]
    m = scan.get('mossoro') or {}
    if m.get('buscas') and not m.get('voos'):
        partes.append('Mossoró: nenhum voo à venda')
    mala = scan.get('taxa_mala') or {}
    if mala:
        partes.append(f"🧳 mala de 23 kg: +{brl(min(mala.values()))}" + (f" a {brl(max(mala.values()))}" if len(set(mala.values())) > 1 else '') + ' por trecho')
    fontes = scan.get('fontes') or {}
    if (fontes.get('Google') or {}).get('bloqueio'):
        partes.append('o Google pediu verificação nesta busca')
    mudos = [n for n, f in fontes.items() if n != 'Google' and not f.get('com_voo')]
    if mudos:
        partes.append(', '.join(mudos) + (' não respondeu' if len(mudos) == 1 else ' não responderam'))
    return ' · '.join(partes)


def montar_botoes(v, ts, scan):
    """Uma linha de botões por grupo, um botão por trecho com recomendada (o Telegram recusa botão sem link)."""
    linhas = []
    for g in v['grupos']:
        linha = []
        for t in ts:
            rec = (scan['series'].get(t['serie']) or {}).get('recomendada') if t['grupo'] == g['id'] and not t['igual_a'] else None
            if rec:
                url = rec.get('link') or link_mm(rec['orig'], rec['dest'], rec['d'], t['pax'])
                linha.append({'text': f"{g.get('emoji', '')} {t['curto']} {dia(rec['d'])[4:]} · {brl(rec['pp'])}".strip(), 'url': url})
        if linha:
            linhas.append(linha[:3])
    return linhas + [PAINEL_BOTAO]


# ---------------------------------------------------------------- painel (público: só siglas)
def _opcao(o):
    return o and {k: o.get(k) for k in ('d', 'orig', 'dest', 'cia', 'saida', 'chegada', 'tipo', 'pp', 'vend', 'link')} | {'dia': dia(o['d'])}


def linhas_do_painel(scan, v, ts, agora, alerta):
    rows = [{'tipo': 'resumo', 'versao': 2, 'quando': agora, 'alerta': alerta, 'ordem': v['volta_origens'], 'sp': v['sp'],
             'aeroportos': {c: {'curto': a['curto'], 'selo': a.get('selo'), 'ultimo_caso': bool(a.get('ultimo_caso'))} for c, a in v['aeroportos'].items()},
             'fontes': scan.get('fontes'), 'mossoro': scan.get('mossoro'), 'taxa_mala': scan.get('taxa_mala'),
             'grupos': [{'id': g['id'], 'rotulo': g['rotulo'], 'emoji': g.get('emoji', ''), 'assentos': g['assentos']} for g in v['grupos']]}]
    for t in ts:
        r = scan['series'].get(t['igual_a'] or t['serie']) or {}
        rows.append({'tipo': 'trecho', 'serie': t['serie'], 'grupo': t['grupo'], 'nome': t['nome'], 'sentido': t['sentido'], 'janela': janela(t['datas']),
                     'pax': t['pax'], 'alvo': t['alvo'], 'igual_a': t['igual_a'], 'recomendada': _opcao(r.get('recomendada')),
                     'mais_barata': None if t['igual_a'] else _opcao(r.get('mais_barata')), 'comprado': None if t['igual_a'] else _opcao(r.get('comprado'))})
        if not t['igual_a']:
            rows += [{'tipo': 'celula', 'serie': t['serie'], 'd': d, 'dia': dia(d), 'ne': ne, 'pp': c['pp'], 'conf': c['conf']}
                     for d, cel in sorted(r.get('celulas', {}).items()) for ne, c in cel.items()]
    for g in v['grupos']:
        apelidos = {f"{g['id']}.{t['id']}": scan['series'].get(t['igual_a']) for t in ts if t['grupo'] == g['id'] and t['igual_a']}
        rows += [{'tipo': 'pacote', 'grupo': g['id'], 'id': p['id'], 'pp': p['pp'], 'ida': _opcao(p['ida']), 'volta': _opcao(p['volta'])}
                 for p in selecao.pacotes({**scan['series'], **apelidos}, g)]
    return rows


# ---------------------------------------------------------------- registro da rodada
def registrar(scan, v, pasta=AQUI, enviar=None, nomes=None, agora=None):
    """Grava histórico, estado, painel e resumo de uma rodada e manda a mensagem. Devolve as linhas para o log (sem nomes)."""
    modo = os.environ.get('PASSAGENS_ENVIAR', '1')
    if enviar is None:
        enviar = (lambda *a, **k: 'TELEGRAM_DESLIGADO') if modo == '0' else (lambda t, silencioso=False, botoes=(): telegram(
            '🧪 <b>[TESTE]</b> ' + t if modo == 'teste' else t, silencioso or modo == 'teste', botoes, so_dono=modo == 'teste'))
    fim = scan.get('fim')
    agora = agora or (datetime.fromisoformat(fim.replace('Z', '+00:00')).astimezone() if fim else datetime.now()).strftime('%Y-%m-%d %H:%M')
    ultimo, painel, precos = (os.path.join(pasta, f) for f in ('ultimo.md', 'painel.json', 'precos.csv'))
    cabecalho = f"✈️ <b>Passagens</b> · {quando_tg(agora)}\n<i>Preço por pessoa, sem mala, conferido nesta busca.</i>"
    if scan.get('status') != 'ok':
        erro = scan.get('erro') or 'nenhum voo encontrado'
        with open(ultimo, 'a', encoding='utf-8') as f:
            f.write(f"\n> Busca de {agora} falhou: {erro}\n")
        st = enviar(f"{cabecalho}\n⚠️ Nesta busca não consegui ler os preços.\n<i>{html.escape(str(erro)[:300])}</i>", True, [PAINEL_BOTAO])
        return [f'ERRO_VARREDURA {erro}', st]
    ts = plano.trechos(v, agora[:10])
    publico = plano.nomes(v)
    nomes = nomes or plano.nomes(v, os.environ.get('NOMES_VIAGEM'))
    estado = ler_estado(pasta)
    primeira = not estado.get('series')
    linhas_csv, alertas, mudancas = atualizar_estado(estado, scan, ts, v, agora)

    novo = not os.path.exists(precos)
    with open(precos, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        w.writerows(linhas_csv)

    def mensagem(n):
        blocos = [bloco_do_grupo(g, ts, scan, v, estado, n) for g in v['grupos']]
        return montar_mensagem(cabecalho, montar_topo(alertas, mudancas, primeira, ts, n), blocos, montar_rodape(scan, v))

    toca = any(a['toca'] for a in alertas)
    st = enviar(mensagem(nomes), not toca, montar_botoes(v, ts, scan))
    alerta_publico = montar_topo(alertas, mudancas, primeira, ts, publico) if alertas else None
    gravar_json(painel, linhas_do_painel(scan, v, ts, agora, alerta_publico and re.sub(r'<[^>]+>', '', alerta_publico)))
    gravar_json(os.path.join(pasta, 'estado.json'), estado)
    with open(ultimo, 'w', encoding='utf-8') as f:
        f.write(f"# Passagens: última busca em {agora}\n\n" + html.unescape(re.sub(r'<[^>]+>', '', mensagem(publico))) + f"\n\nTelegram: {st}\n\nPainel: {PAINEL_URL}\n")
    if alertas:
        saida = 'ALERTA ' + ' | '.join(f"{a['serie']} {a['tipo']} {brl(a['pp'])}" for a in alertas)
    else:
        saida = 'SEM_NOVIDADE ' + ' | '.join(f"{t['serie']} {brl(scan['series'][t['serie']]['recomendada']['pp'])}" for t in ts
                                             if not t['igual_a'] and (scan['series'].get(t['serie']) or {}).get('recomendada'))
    return [saida, st]


def _teste():
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
        # sem resposta do Telegram: tenta mais uma vez; recusa do Telegram (erro HTTP) não se repete
        chamadas = []

        def instavel(token, metodo, campos):
            chamadas.append(metodo)
            if len(chamadas) == 1:
                raise TimeoutError(f'https://api.telegram.org/bot{token}/sendMessage')
            return {'ok': True}
        assert _enviar_um(tk, {}, api=instavel, espera=0) is None and len(chamadas) == 2

        def fora(token, metodo, campos):
            chamadas.append(metodo)
            raise TimeoutError(f'https://api.telegram.org/bot{token}/sendMessage')
        assert _enviar_um(tk, {}, api=fora, espera=0) == 'TimeoutError' and len(chamadas) == 4

        class Recusa(Exception):
            code = 403
            read = lambda self: b'{"description": "Forbidden: bot was blocked by the user"}'

        def recusado(token, metodo, campos):
            chamadas.append(metodo)
            raise Recusa()
        assert _enviar_um(tk, {}, api=recusado, espera=0) == 'Recusa 403 Forbidden: bot was blocked by the user' and len(chamadas) == 5
        # quem se inscreveu pelo /start entra depois da lista fixa, sem repetir; arquivo de outro bot não abre
        import atendimento
        global AQUI
        aqui_real = AQUI
        with tempfile.TemporaryDirectory() as d:
            AQUI = d
            try:
                assert _chats() == ['111111111', '222222222']
                atendimento.gravar_inscritos(tk, os.path.join(d, 'assinantes.cif'), ['222222222', '333333333'])
                assert _chats() == ['111111111', '222222222', '333333333'], _chats()
                os.environ['TELEGRAM_TOKEN'] = '987654321:' + 'Z' * 35
                assert _chats() == ['111111111', '222222222']
            finally:
                AQUI = aqui_real
    finally:
        os.environ.pop('TELEGRAM_CHAT_ID', None)
        os.environ.pop('TELEGRAM_TOKEN', None) if antes is None else os.environ.update(TELEGRAM_TOKEN=antes)
    _teste_registro()
    print('ok')


def _teste_registro():
    assert janela(['2026-12-22', '2026-12-23', '2026-12-26', '2026-12-27', '2026-12-28']) == '22 a 23/12 e 26 a 28/12'
    assert janela(['2027-01-18']) == '18/01' and janela([]) == 'sem datas' and janela(plano.datas({'de': '2027-01-23', 'ate': '2027-03-31'})) == '23/01 a 31/03'
    assert janela(['2026-12-30', '2027-01-02', '2027-01-03']) == '30/12 e 02 a 03/01' and brl(1234.4) == 'R$1.234'

    v = plano.carregar()
    nomes = {'v': 'Ana', 'h': 'Beto', 'g': 'Caio', 'wk': 'Duda + Eva'}

    def of(d, orig, dest, saida, pp, cia='Gol', vend='CIA Gol', pax=1, sentido='volta', tipo='direto', chegada='16:30', voos=''):
        return {'sentido': sentido, 'd': d, 'pax': pax, 'orig': orig, 'dest': dest, 'cia': cia, 'saida': saida, 'chegada': chegada, 'tipo': tipo,
                'pp': pp, 'vend': vend, 'voos': voos, 'fonte': 'google', 'link': f'https://exemplo/{orig}{dest}{d}'}

    def scan(volta=612, depois=389, ida_dez=1180, ida_jan=690, comprado=1650, wk_ida=1150, wk_volta=540, recife=540):
        s = lambda rec, **mais: {'recomendada': rec, 'mais_barata': None, 'celulas': {}, 'olhadas': [], **mais}
        series = {
            'v.volta': s(of('2027-01-20', 'FOR', 'GRU', '12:50', volta, vend='maxmilhas', voos='FOR>GRU 2027-01-20 G3 1555'),
                         mais_barata=of('2027-01-18', 'REC', 'CGH', '05:05', recife, 'Azul', 'Zupper', chegada='08:40', voos='REC>VCP 2027-01-18 AD 1;VCP>CGH 2027-01-18 AD 2'),
                         celulas={'2027-01-20': {'FOR': {'pp': volta, 'conf': True}, 'REC': {'pp': 700, 'conf': False}},
                                  '2027-01-18': {'REC': {'pp': recife, 'conf': True}}, '2027-01-19': {'JDO': {'pp': 880, 'conf': False}}},
                         olhadas=['2027-01-18', '2027-01-19', '2027-01-20', '2027-01-21', '2027-01-22']),
            'h.depois': s(of('2027-02-09', 'FOR', 'GRU', '13:10', depois, 'LATAM', '123milhas'),
                          celulas={'2027-02-09': {'FOR': {'pp': depois, 'conf': True}}, '2027-02-23': {'FOR': {'pp': 402, 'conf': False}},
                                   '2027-03-10': {'JDO': {'pp': 415, 'conf': False}}},
                          olhadas=['2027-02-09', '2027-02-23', '2027-03-10']),
            'g.ida_dez': s(of('2026-12-29', 'GRU', 'FOR', '07:00', ida_dez, vend='Viaza', sentido='ida', chegada='10:30'),
                           comprado=of('2026-12-22', 'GRU', 'NAT', '14:05', comprado, sentido='ida', chegada='17:25')),
            'g.ida_jan': s(of('2027-01-06', 'VCP', 'JDO', '22:40', ida_jan, 'Azul', 'CIA Azul', sentido='ida', tipo='escala 1h10', chegada='03:05')),
            'wk.ida': s(of('2026-12-28', 'GRU', 'FOR', '14:05', wk_ida, vend='123milhas', pax=2, sentido='ida', chegada='17:25'), comprado=None),
            'wk.volta': s(of('2027-01-11', 'JDO', 'GRU', '03:20', wk_volta, 'LATAM', 'maxmilhas', pax=2, chegada='06:50')),
        }
        return {'versao': 2, 'status': 'ok', 'series': series, 'taxa_mala': {'Azul': 93, 'Gol': 136}, 'mossoro': {'buscas': 2, 'voos': 0},
                'fontes': {'Google': {'buscas': 40, 'bloqueio': None}, 'MaxMilhas': {'buscas': 9, 'com_voo': 7}, '123milhas': {'buscas': 4, 'com_voo': 0}}}

    enviados = []
    fake = lambda msg, silencioso=False, botoes=(): enviados.append((msg, silencioso, botoes)) or 'TELEGRAM_ENVIADO'
    with tempfile.TemporaryDirectory() as d:
        rodar = lambda s, quando: registrar(s, v, d, fake, nomes, quando)
        # ---- primeira rodada: sem som, um bloco por grupo, na ordem do arquivo
        r = rodar(scan(), '2026-11-20 20:17')
        assert r == ['SEM_NOVIDADE v.volta R$612 | h.depois R$389 | g.ida_dez R$1.180 | g.ida_jan R$690 | wk.ida R$1.150 | wk.volta R$540', 'TELEGRAM_ENVIADO'], r
        msg, mudo, bt = enviados[-1]
        assert mudo and msg.startswith('✈️ <b>Passagens</b> · sex 20/11, 20:17\n<i>Preço por pessoa, sem mala, conferido nesta busca.</i>\n'
                                       '👋 Primeira busca no formato novo'), msg
        assert msg.index('👩 <b>Ana</b>') < msg.index('🧍 <b>Beto</b>') < msg.index('🧑 <b>Caio</b>') < msg.index('👭 <b>Duda + Eva</b>')
        # cada opção em duas linhas, sempre na mesma ordem: preço, dia e voo; embaixo, horários, aeroportos por extenso e onde comprar
        assert ('👩 <b>Ana</b>\n<u>Volta · 18 a 22/01</u>\n'
                '⭐ <b>R$612</b> · qua 20/01 · Gol 1555, direto\n└ 12h50 Fortaleza → 16h30 Guarulhos · MaxMilhas\n'
                '💸 Mais barata: <b>R$540</b> · seg 18/01 · Azul, direto\n└ 05h05 Recife → 08h40 Congonhas · Zupper\n\n') in msg, msg
        # quem pode voltar junto ou depois: as duas possibilidades, a diferença, e o que ainda não foi conferido vem avisado
        assert ('🧍 <b>Beto</b>\n<u>Volta junto · 18 a 22/01</u>\n<b>R$612</b> · qua 20/01 · mesmo voo de Ana\n└ 12h50 Fortaleza → 16h30 Guarulhos\n'
                '<u>Volta depois · 23/01 a 31/03</u>\n⭐ <b>R$389</b> · ter 09/02 · LATAM, direto\n└ 13h10 Fortaleza → 16h30 Guarulhos · 123milhas\n'
                'Outras datas, preço ainda não conferido: ter 23/02 R$402 · qua 10/03 R$415\n'
                '↔️ Volta depois sai R$223 mais barato que volta junto\n\n') in msg, msg
        # dois cenários de ida, o voo já comprado à parte, Campinas por extenso, conexão com o tempo de espera, chegada no dia seguinte
        assert ('🧑 <b>Caio</b>\n<u>Ida em dezembro · 22 a 23/12 e 26 a 30/12</u>\n'
                '⭐ <b>R$1.180</b> · ter 29/12 · Gol, direto\n└ 07h00 Guarulhos → 10h30 Fortaleza · Viaza\n'
                '🎯 Voo já comprado: <b>R$1.650</b> · ter 22/12 · Gol, direto\n└ 14h05 Guarulhos → 17h25 Natal · Gol\n'
                '<u>Ida em janeiro · 02 a 10/01</u>\n'
                '⭐ <b>R$690</b> · qua 06/01 · Azul, 1 conexão (espera de 1h10)\n└ 22h40 Campinas (Viracopos) → 03h05 (+1 dia) Juazeiro · Azul\n'
                '<u>Volta · 18 a 22/01</u>\n<b>R$612</b> · qua 20/01 · mesmo voo de Ana\n└ 12h50 Fortaleza → 16h30 Guarulhos\n'
                '📦 Ida + volta: indo em dezembro R$1.792 · indo em janeiro <b>R$1.302</b>\n\n') in msg, msg
        # dois lugares no mesmo voo: o preço é sempre por pessoa, e o pacote mostra também o total
        assert ('👭 <b>Duda + Eva</b> · 2 lugares no mesmo voo\n<u>Ida · 22 a 23/12 e 26 a 30/12</u>\n'
                '⭐ <b>R$1.150</b> por pessoa · seg 28/12 · Gol, direto\n└ 14h05 Guarulhos → 17h25 Fortaleza · 123milhas\n'
                '🎯 Voo já comprado (Gol 1678, ter 22/12 14h05): sem lugar à venda nesta busca\n'
                '<u>Volta · 07 a 12/01</u>\n⭐ <b>R$540</b> por pessoa · seg 11/01 · LATAM, direto\n└ 03h20 Juazeiro → 06h50 Guarulhos · MaxMilhas\n'
                '📦 Ida + volta: <b>R$1.690</b> por pessoa · R$3.380 no total\n\n') in msg, msg
        assert msg.endswith('<i>⭐ = melhor opção pela ordem Fortaleza, Mossoró, Juazeiro, Natal, J. Pessoa; Recife só em último caso · Mossoró: nenhum voo à venda · '
                            '🧳 mala de 23 kg: +R$93 a R$136 por trecho · 123milhas não respondeu</i>'), msg[-260:]
        assert len(msg) < 2600, len(msg)
        assert [[x['text'] for x in l] for l in bt] == [['👩 Volta 20/01 · R$612'], ['🧍 Volta depois 09/02 · R$389'], ['🧑 Ida dez 29/12 · R$1.180', '🧑 Ida jan 06/01 · R$690'],
                                                        ['👭 Ida 28/12 · R$1.150', '👭 Volta 11/01 · R$540'], ['📊 Abrir o painel']], bt
        assert all(x['url'].startswith('https://') for l in bt for x in l)

        # ---- arquivos públicos: nenhum nome, só a sigla; histórico com uma linha por data olhada e por recomendada
        painel = json.load(open(os.path.join(d, 'painel.json'), encoding='utf-8'))
        publico = open(os.path.join(d, 'painel.json'), encoding='utf-8').read() + open(os.path.join(d, 'ultimo.md'), encoding='utf-8').read() + ' '.join(r)
        assert not any(n in publico for n in ('Ana', 'Beto', 'Caio', 'Duda', 'Eva')) and '👩 V\nVolta · 18 a 22/01' in publico and 'mesmo voo de V' in publico
        resumo = painel[0]
        assert resumo['tipo'] == 'resumo' and resumo['versao'] == 2 and resumo['ordem'][0] == 'FOR' and resumo['grupos'][3] == {'id': 'wk', 'rotulo': 'W + K', 'emoji': '👭', 'assentos': 2}
        trs = {x['serie']: x for x in painel if x['tipo'] == 'trecho'}
        assert list(trs) == ['v.volta', 'h.junto', 'h.depois', 'g.ida_dez', 'g.ida_jan', 'g.volta', 'wk.ida', 'wk.volta']
        assert trs['h.junto']['recomendada']['pp'] == 612 and trs['h.junto']['igual_a'] == 'v.volta' and trs['v.volta']['mais_barata']['orig'] == 'REC'
        assert trs['g.ida_dez']['comprado']['pp'] == 1650 and trs['v.volta']['recomendada']['dia'] == 'qua 20/01' and trs['v.volta']['janela'] == '18 a 22/01'
        cel = [x for x in painel if x['tipo'] == 'celula' and x['serie'] == 'v.volta']
        assert {(x['d'], x['ne'], x['pp'], x['conf']) for x in cel} == {('2027-01-20', 'FOR', 612, True), ('2027-01-20', 'REC', 700, False),
                                                                        ('2027-01-18', 'REC', 540, True), ('2027-01-19', 'JDO', 880, False)}
        pac = [(x['grupo'], x['id'], x['pp']) for x in painel if x['tipo'] == 'pacote']
        assert pac == [('g', 'A', 1792), ('g', 'B', 1302), ('wk', 'U', 1690)], pac
        linhas = list(csv.DictReader(open(os.path.join(d, 'precos.csv'), encoding='utf-8')))
        do_v = [(x['d'], x['ne'], x['pp'], x['conf']) for x in linhas if x['serie'] == 'v.volta']
        # de cada data olhada fica o melhor (confirmado antes de preço de lista); data olhada sem voo não gera linha na primeira vez
        assert do_v == [('2027-01-18', 'REC', '540', '1'), ('2027-01-19', 'JDO', '880', '0'), ('2027-01-20', 'FOR', '612', '1')], do_v
        assert [(x['d'], x['pp']) for x in linhas if x['serie'] == 'v.volta#rec'] == [('2027-01-20', '612')] and len(linhas) == 3 + 3 + 6
        estado = ler_estado(d)
        assert estado['series']['v.volta']['rec'] == {'ident': ['2027-01-20', 'Gol', 'FOR', 'GRU', '12:50'], 'd': '2027-01-20', 'ne': 'FOR', 'pp': 612, 'vend': 'maxmilhas'}
        assert estado['series']['v.volta']['datas']['2027-01-21'] == {'pp': None, 'visto': '2026-11-20 20:17'} and estado['series']['v.volta']['min'] == 612

    with tempfile.TemporaryDirectory() as d:
        enviados.clear()
        rodar = lambda s, quando: registrar(s, v, d, fake, nomes, quando)
        base = {}
        rodar(scan(**base), '2026-11-20 20:17')
        assert enviados[-1][1]
        # ---- nada mudou: sem som, não escreve no histórico
        n = len(open(os.path.join(d, 'precos.csv')).readlines())
        r = rodar(scan(**base), '2026-11-20 21:17')
        assert enviados[-1][1] and '😌 Sem mudança desde a última busca.' in enviados[-1][0] and len(open(os.path.join(d, 'precos.csv')).readlines()) == n
        # ---- subiu e desceu: resumo das mudanças, sem som; variação de menos de R$5 numa data não vira linha
        r = rodar(scan(**{**base, 'volta': 640, 'wk_volta': 500, 'recife': 543}), '2026-11-20 22:17')
        msg, mudo, _ = enviados[-1]
        assert mudo and '↕️ Ana, volta +R$28 · Duda + Eva, volta −R$40' in msg, msg
        novas = open(os.path.join(d, 'precos.csv')).readlines()[n:]
        assert len(novas) == 3 and novas[0].startswith('2026-11-20 22:17,v.volta,2027-01-20,FOR,1,640,1'), novas
        # ---- abaixo do alvo: toca, com a dica das 24 h; na hora seguinte, no mesmo preço, não toca de novo
        r = rodar(scan(**{**base, 'volta': 440}), '2026-11-20 23:17')
        msg, mudo, _ = enviados[-1]
        assert r[0] == 'ALERTA v.volta alvo R$440' and not mudo, r
        assert '🔥 <b>Hora de comprar:</b> Ana, volta R$440 (alvo R$450)\n<i>Com 7+ dias' in msg and '⭐ ✅ <b>R$440</b>' in msg, msg
        rodar(scan(**{**base, 'volta': 440}), '2026-11-21 00:17')
        assert enviados[-1][1] and '🔥' in enviados[-1][0]
        # cai mais R$20: toca de novo; passadas 6 horas no alvo, toca de novo
        rodar(scan(**{**base, 'volta': 418}), '2026-11-21 01:17')
        assert not enviados[-1][1]
        rodar(scan(**{**base, 'volta': 418}), '2026-11-21 05:17')
        assert enviados[-1][1]
        rodar(scan(**{**base, 'volta': 418}), '2026-11-21 07:17')
        assert not enviados[-1][1]
        # ---- menor preço já visto: só vale depois de 72 h de histórico do trecho, e precisa cair pelo menos R$20
        rodar(scan(**{**base, 'wk_ida': 1100}), '2026-11-22 10:17')
        assert '📉' not in enviados[-1][0]
        rodar(scan(**{**base, 'wk_ida': 1090}), '2026-11-23 21:17')
        assert '📉' not in enviados[-1][0]                           # caiu só R$10 abaixo do menor (1.100)
        r = rodar(scan(**{**base, 'wk_ida': 1070}), '2026-11-23 22:17')
        msg, mudo, _ = enviados[-1]
        assert r[0] == 'ALERTA wk.ida recorde R$1.070' and not mudo and '📉 <b>Menor preço já visto:</b> Duda + Eva, ida R$1.070 (era R$1.090)' in msg, (r, msg)
        # ---- trecho sem preço nesta busca: diz isso, mantém o resto; o estado antigo do trecho fica
        s = scan(**base)
        s['series']['h.depois']['recomendada'] = None
        s['series']['v.volta'].update(recomendada=None, mais_barata=None)
        rodar(s, '2026-11-24 10:17')
        msg = enviados[-1][0]
        assert '👩 <b>Ana</b>\n<u>Volta · 18 a 22/01</u>\nsem preço conferido nesta busca' in msg and '<u>Volta junto · 18 a 22/01</u>\nsem preço conferido nesta busca' in msg
        assert '<u>Volta depois · 23/01 a 31/03</u>\nsem preço conferido nesta busca' in msg and '📦 Ida + volta: <b>R$1.690</b>' in msg and 'Caio' in msg
        assert ler_estado(d)['series']['v.volta']['rec']['pp'] == 612      # a última recomendada conhecida fica guardada
        # ---- datas que já passaram saem do bloco e do estado
        rodar(scan(**base), '2026-12-29 10:17')
        msg = enviados[-1][0]
        assert '<u>Ida em dezembro · 30/12</u>' in msg and '2027-01-18' in ler_estado(d)['series']['v.volta']['datas']
        rodar(scan(**base), '2027-01-19 10:17')
        assert '2027-01-18' not in ler_estado(d)['series']['v.volta']['datas'] and '<u>Volta · 20 a 22/01</u>' in enviados[-1][0]
        # ---- rodada com erro: aviso sem som, nada mais muda
        r = registrar({'status': 'erro', 'erro': 'x<y'}, v, d, fake, nomes, '2026-11-24 11:17')
        assert r == ['ERRO_VARREDURA x<y', 'TELEGRAM_ENVIADO'] and enviados[-1][1] and '<i>x&lt;y</i>' in enviados[-1][0]

    # ---- mensagem grande demais: sai primeiro o rodapé, depois as linhas extras; nunca passa do limite
    blocos = [[('linha fixa ' * 20, 0), ('extra nível 1 ' * 20, 1), ('extra nível 2 ' * 20, 2)] for _ in range(4)]
    cheia = montar_mensagem('cabeça', 'topo', blocos, 'rodapé')
    assert 'rodapé' in cheia and 'nível 2' in cheia
    sem_rodape = montar_mensagem('cabeça', 'topo', blocos, 'rodapé', len(cheia) - 1)
    assert 'rodapé' not in sem_rodape and 'nível 2' in sem_rodape
    media = montar_mensagem('cabeça', 'topo', blocos, 'rodapé', len(sem_rodape) - 1)
    assert 'nível 2' not in media and 'nível 1' in media and len(media) < len(sem_rodape)
    curta = montar_mensagem('cabeça', 'topo', blocos, 'rodapé', len(media) - 1)
    assert 'nível 1' not in curta and 'linha fixa' in curta and len(montar_mensagem('cabeça', 'topo', blocos, 'rodapé', 300)) == 300

    # ---- modo de teste: só o dono recebe, sem som, com a marca [TESTE]; modo 0 não envia
    amb = dict(os.environ)
    try:
        os.environ.update(TELEGRAM_TOKEN='123456789:' + 'A' * 35, TELEGRAM_CHAT_ID='111111111, 222222222', PASSAGENS_ENVIAR='teste')
        foram = []
        assert telegram('oi', _envio=lambda t, c: foram.append((c['chat_id'], c['disable_notification'])), so_dono=True) == 'TELEGRAM_ENVIADO'
        assert foram == [('111111111', 'false')]
        os.environ['PASSAGENS_ENVIAR'] = '0'
        with tempfile.TemporaryDirectory() as d:
            assert registrar(scan(), v, d, None, nomes, '2026-11-20 20:17')[1] == 'TELEGRAM_DESLIGADO'
    finally:
        os.environ.clear()
        os.environ.update(amb)


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
        print(__doc__)
