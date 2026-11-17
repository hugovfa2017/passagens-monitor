"""Monitor de preço do PS5 no Brasil: melhor preço de cada modelo, com cupom e sem cupom, e alerta no Telegram.

Modelos: PS5 com leitor de disco, PS5 Digital e PS5 Pro (bundles com jogo contam; usados, acessórios e importados não).
"Com cupom" só conta preço confirmado: oferta postada no Promobit com o preço final, selo de cupom no próprio produto
(KaBuM!, com o valor tirado da lista de cupons da loja) ou selo "Economize R$ X com cupom" da Amazon. Cupons gerais
da loja aparecem no painel como "para testar no carrinho", sem entrar na conta.

Alerta com som: melhor preço igual ou abaixo do alvo (config.py) ou menor preço já visto. Resumo diário sem som.
Segredos: TELEGRAM_TOKEN (o mesmo bot das passagens) e TELEGRAM_CHAT_ID_PS5; sem ele, vai só para o primeiro chat
de TELEGRAM_CHAT_ID. PS5_ENVIAR=0 não manda nada (usado fora da branch main).

Uso:  python ps5/monitor.py            -> varredura completa (fontes.py), grava e alerta
      python ps5/monitor.py --teste    -> auto-checagem sem internet
Saídas: ps5/historico.csv, ps5/estado.json, ps5/ultimo.md, docs/ps5/index.html e docs/ps5/painel.json.
"""
import csv, html, json, os, re, sys, unicodedata, urllib.parse, urllib.request
from datetime import datetime, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
import config  # noqa: E402
import fontes  # noqa: E402

DOCS = os.path.join(RAIZ, 'docs', 'ps5')
PAINEL_URL = 'https://hugovfa2017.github.io/passagens-monitor/ps5/'
MODELOS = {'leitor': 'PS5 com leitor de disco', 'digital': 'PS5 Digital', 'pro': 'PS5 Pro'}
FAIXA = {'leitor': (2500, 8000), 'digital': (2500, 8000), 'pro': (4000, 11000)}   # fora disso é acessório, parcela ou erro
CAMPOS = ['data_hora'] + [f'{m}{s}' for m in MODELOS for s in ('', '_cupom', '_loja')]
SEMANA = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']


# ---------------------------------------------------------------- o que é um PS5 (e qual)
def simples(t):
    t = unicodedata.normalize('NFKD', t.replace('®', ' ').replace('™', ' ')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'playstation\s*5', 'playstation 5', t)).strip()


PS5 = re.compile(r'\bps ?5\b|playstation 5\b')
E_CONSOLE = re.compile(r'^\W*(?:novo |o )?(?:console|sony|playstation|ps ?5|bundle|pack|pacote|video ?game)\b'
                       r'|\bconsole (?:sony )?(?:playstation|ps) ?5|(?:playstation|ps) ?5 (?:\w+ ){0,3}console\b')
NAO_CONSOLE = re.compile(r'\bportal\b|reprodutor remoto|\bvr ?(?:2|ii)\b|realidade virtual|\bvolante\b|headset|\bfones?\b|'
                         r'^\W*(?:controle|jogo|game|capa|tampas?|suporte|base|drive|unidade|cabo|pelicula|skin|adesivo|case|'
                         r'bolsa|carregador|dock|ssd|hd|cartao|gift|monitor|tv|smart tv|cadeira|mesa|mochila|kit de)\b')
USADO = re.compile(r'\b(?:usad[oa]s?|semi ?nov[oa]|recondicionad[oa]|refurbished|vitrine|open ?box|mostruario|defeito|'
                   r'para pecas|retirada de pecas)\b')
IMPORTADO = re.compile(r'\b(?:middle east|uae|ksa|international|internacional|importad[oa]|japan(?:ese)?|japones[ae]?|'
                       r'europe(?:an)?|europeia|us version|usa version|versao americana|asia(?:n)?|hong kong|hk version|'
                       r'(?:eu|uk) version)\b')
COM_JOGO = re.compile(r'\+|bundle|pacote|\bpack\b|com \d+ jogos?|\bjogos?\b|astro bot|gran turismo|spider|god of war|gta\b|'
                      r'grand theft|wolverine|ea sports|\bfc ?\d\d\b|returnal|ratchet|ghost of|call of duty|2 controles')
ESPECIAL = re.compile(r'edicao limitada|limited edition|aniversario|anniversary|\bouro\b|\bgold\b')


def modelo_de(t):
    """-> (modelo, se o texto diz o modelo com todas as letras)"""
    t = re.sub(r'jogos? digita(?:l|is)', '', t)
    if re.search(r'\bpro\b', t):
        return 'pro', True
    if 'digital' in t:
        return 'digital', True
    return 'leitor', bool(re.search(r'\bdis[ck]o?\b|leitor|fisic|standard|blu ?-?ray', t))


def classificar(titulo, preco, produto=''):
    """-> (modelo, motivo). modelo None quando não é um PS5 novo à venda no Brasil (motivo diz por quê).
    produto: nome no catálogo do comparador (Zoom), usado quando o título da loja vem cortado e não diz o modelo."""
    t, p = simples(titulo), simples(produto or '')
    base = t if PS5.search(t) and E_CONSOLE.search(t) else p
    if not base or not PS5.search(base) or not E_CONSOLE.search(base) or NAO_CONSOLE.search(t) or (p and NAO_CONSOLE.search(p)):
        return None, 'não é console PS5'
    if USADO.search(f'{t} {p}'):
        return None, 'usado/recondicionado'
    if IMPORTADO.search(f'{t} {p}') and not config.INCLUIR_IMPORTADOS:
        return None, 'importado'
    modelo, explicito = modelo_de(t)
    if not explicito and p:
        modelo = modelo_de(p)[0]
    lo, hi = FAIXA[modelo]
    if not lo <= preco <= hi:
        return None, f'preço fora da faixa de {MODELOS[modelo]}'
    return modelo, ''


def detalhes(titulo):
    t = simples(titulo)
    return ', '.join(x for x, ok in (('edição especial', ESPECIAL.search(t)), ('com jogo/extra', COM_JOGO.search(t))) if ok)


# ---------------------------------------------------------------- cupons
CUPOM_FORA = re.compile(r'jbl|impressora|scanner|note(?:book)?\d*|lenovo|asus|moda|beleza|livro|bebe|bebida|cozinha|\bpet\b|'
                        r'mercado|\bcasa\b|esporte|fitness|automotiv|ferrament|constru|jardim|papelaria|relogio|bolsa|carteira|'
                        r'vinil|instrument|celular|smartphone|\btv\b|telona|tela gigante|cafe|arfrio|ar condicionado|robo|'
                        r'aspirador|mouse|teclado|cadeira|mesa|husky|periferi|software|antena|precisao|nintendo|saude|'
                        r'brinquedo|fralda|biscoito|lacta|sono|tablet|frete|primeira compra|app\b|selecionados', re.I)


def aplicar_selos(ofertas, cupons):
    """KaBuM!: o selo 'CUPOM X' do produto vira preço com cupom, usando o valor do cupom X publicado no Promobit."""
    regras = {(c['loja'], c['codigo'].upper()): c['regra'] for c in cupons}
    for o in ofertas:
        cod = o.get('cupom_selo')
        if not cod:
            continue
        r = regras.get(('KaBuM!', cod))
        d = fontes.desconto(r, o['preco']) if r else 0
        if d:
            o.update(preco_sem_cupom=o['preco'], preco=round(o['preco'] - d, 2), cupom=cod, cupom_origem='loja')
        else:
            o['obs'] = (o['obs'] + ' · ' if o['obs'] else '') + f'selo do produto: cupom {cod} (valor não encontrado)'


CUPOM_PS5 = re.compile(r'ps ?5|playstation|console|video ?games?|\bgames?\b|gamer|eletronicos|tecnologia|todo o site|site todo|todo site')


def cupons_para_testar(cupons, melhores, codigos_ps5=()):
    """Cupons gerais das lojas das melhores ofertas sem cupom que podem valer para console (games, eletrônicos, site todo,
    ou já vistos em oferta de PS5), com o preço que dariam. Não confirmados: não entram no 'melhor preço com cupom'."""
    base = {}   # a oferta sem cupom mais barata de cada loja: é nela que o cupom é testado
    for o in melhores:
        loja = o['loja'].split(' · ')[0]
        if loja not in base or o['preco'] < base[loja]['preco']:
            base[loja] = o
    preco_loja = {loja: o['preco'] for loja, o in base.items()}
    out = {loja: {'pagina': f"{fontes.PROMOBIT}/cupons/loja/{fontes.LOJAS_CUPOM[loja]}/", 'cupons': []}
           for loja in preco_loja if loja in fontes.LOJAS_CUPOM}
    for c in cupons:
        texto = simples(c['titulo'] + ' ' + c['codigo'])
        if c['loja'] not in out or CUPOM_FORA.search(texto) or not (CUPOM_PS5.search(texto) or c['codigo'].upper() in codigos_ps5):
            continue
        d = fontes.desconto(c['regra'], preco_loja[c['loja']])
        if d >= 30:
            out[c['loja']]['cupons'].append({**c, 'desconto': round(d), 'preco_estimado': round(preco_loja[c['loja']] - d, 2),
                                             'modelo': MODELOS[base[c['loja']]['modelo']], 'preco_base': preco_loja[c['loja']]})
    for v in out.values():
        v['cupons'] = sorted(v['cupons'], key=lambda c: -c['desconto'])[:5]
    return out


# ---------------------------------------------------------------- juntar
PRIORIDADE = {'KaBuM!': 0, 'Amazon': 0, 'Americanas': 0, 'Promobit': 1, 'Zoom': 2}


def preparar(ofertas, agora, memoria_promobit):
    """Classifica, junta com as ofertas do Promobit ainda válidas e tira repetidas. -> (ofertas de PS5, descartadas por motivo)"""
    validade = timedelta(hours=config.HORAS_OFERTA_PROMOBIT)
    atuais = {o['id'] for o in ofertas}
    for o in memoria_promobit.values():   # oferta postada que saiu da 1ª página do Promobit, mas ainda é recente
        if o['id'] not in atuais:
            ofertas.append({**o, 'obs': 'postado no Promobit (saiu da lista; pode ter acabado)'})
    ps5, fora = [], {}
    for o in ofertas:
        if o.get('publicado'):
            pub = fontes._quando(o['publicado'])
            if agora - pub > validade:
                continue
            o['idade_h'] = round((agora - pub).total_seconds() / 3600, 1)
        modelo, motivo = classificar(o['titulo'], o['preco'], o.get('produto_zoom'))
        if not modelo:
            fora[motivo] = fora.get(motivo, 0) + 1
            continue
        ps5.append({**o, 'modelo': modelo, 'detalhes': detalhes(f"{o['titulo']} {o.get('produto_zoom', '')}")})
    ps5.sort(key=lambda o: (o['preco'], PRIORIDADE.get(o['fonte'], 3)))
    unicas, vistas = [], set()
    for o in ps5:   # a mesma loja com o mesmo preço vinda de duas fontes (ex.: KaBuM! direto e pelo Zoom)
        chave = (o['modelo'], o['loja'].split(' · ')[0].lower(), round(o['preco']), bool(o['cupom']))
        if chave not in vistas:
            vistas.add(chave)
            unicas.append(o)
    return unicas, fora


def resumir(ofertas):
    """Por modelo: melhor sem cupom, melhor com cupom e o melhor dos dois."""
    r = {}
    for m in MODELOS:
        dom = [o for o in ofertas if o['modelo'] == m]
        sem = next((o for o in dom if not o['cupom']), None)
        com = next((o for o in dom if o['cupom']), None)
        r[m] = {'sem': sem, 'com': com, 'melhor': min((x for x in (sem, com) if x), key=lambda o: o['preco'], default=None)}
    return r


# ---------------------------------------------------------------- texto
def brl(n):
    return 'R$ ' + f'{n:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.').removesuffix(',00')


def quando(agora):
    return f"{SEMANA[agora.weekday()]} {agora:%d/%m, %H:%M}"


def na(loja):
    return 'no' if re.match(r'(Mercado Livre|Carrefour|Ponto|Extra|Submarino|Zoom)', loja) else 'na'


def descricao(o, html_=True):
    """'R$ 3.945,79 na Aliexpress com cupom AEMEGA19 (postado há 5h)'"""
    cod = (lambda c: f'<code>{html.escape(c)}</code>') if html_ else (lambda c: c)
    partes = [f"{brl(o['preco'])} {na(o['loja'])} {o['loja']}"]
    if o['cupom']:
        partes.append(f"com {o['cupom']}" if o['cupom'].startswith('cupom') else f"com cupom {cod(o['cupom'])}")
        if o.get('preco_sem_cupom'):
            partes.append(f"(sem cupom {brl(o['preco_sem_cupom'])})")
    extra = [x for x in (o.get('obs', '').split(' · ')[0] if o['fonte'] != 'Promobit' or ':' in o.get('obs', '') else '',
                         f"postado há {o['idade_h']:.0f}h" if o.get('idade_h') is not None else '') if x]
    return ' '.join(partes) + (f" · {', '.join(extra)}" if extra else '')


def linha_modelo(m, r, html_=True):
    b = (lambda s: f'<b>{s}</b>') if html_ else (lambda s: f'**{s}**')
    if not r['melhor']:
        return f"{b(MODELOS[m])}: nenhuma oferta encontrada"
    s = f"{b(MODELOS[m])}: {descricao(r['melhor'], html_)}"
    if r['melhor']['cupom'] and r['sem']:
        s += f"\n   sem cupom: {brl(r['sem']['preco'])} {na(r['sem']['loja'])} {r['sem']['loja']}"
    return s


# ---------------------------------------------------------------- Telegram
def _token():
    achado = re.search(r'\d+:[A-Za-z0-9_-]{30,}', os.environ.get('TELEGRAM_TOKEN', ''))
    return achado[0] if achado else ''


def _chats():
    """Só o dono por padrão: TELEGRAM_CHAT_ID_PS5, ou o primeiro chat de TELEGRAM_CHAT_ID (que também tem os viajantes)."""
    proprio = re.findall(r'-?\d{5,}', os.environ.get('TELEGRAM_CHAT_ID_PS5', ''))
    return proprio or re.findall(r'-?\d{5,}', os.environ.get('TELEGRAM_CHAT_ID', ''))[:1]


def telegram(texto, silencioso=False, botoes=()):
    if os.environ.get('PS5_ENVIAR', '1') == '0':
        return 'TELEGRAM_DESLIGADO'
    token, chats = _token(), _chats()
    if not token or not chats:
        return 'TELEGRAM_NAO_CONFIGURADO'
    falhas = []
    for chat in chats:
        campos = {'chat_id': chat, 'text': texto, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true',
                  'disable_notification': 'true' if silencioso else 'false'}
        if botoes:
            campos['reply_markup'] = json.dumps({'inline_keyboard': list(botoes)})
        try:
            req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=urllib.parse.urlencode(campos).encode())
            with urllib.request.urlopen(req, timeout=30) as r:
                if not json.loads(r.read()).get('ok'):
                    falhas.append('recusado')
        except Exception as e:   # sem str(e): pode trazer o token na URL
            falhas.append(f"{type(e).__name__} {getattr(e, 'code', '')}".strip())
    return 'TELEGRAM_ENVIADO' if not falhas else 'TELEGRAM_FALHOU ' + ', '.join(falhas)


def botao(o):
    via = ' (Promobit)' if o['fonte'] == 'Promobit' else ' (Zoom)' if o['fonte'] == 'Zoom' else ''
    return [{'text': f"🛒 {MODELOS[o['modelo']].removeprefix('PS5 ')}: {o['loja'].split(' · ')[0]}{via}"[:60], 'url': o['link']}]


PAINEL_BOTAO = [{'text': '📊 Abrir o painel do PS5', 'url': PAINEL_URL}]


# ---------------------------------------------------------------- alertas e estado
def decidir_alertas(resumo, estado, agora):
    """Alerta com som: abaixo do alvo ou menor preço já visto. Não repete a mesma oferta no mesmo preço por 24 h."""
    alertas = []
    ja = estado.setdefault('alertados', {})
    for m, r in resumo.items():
        o = r['melhor']
        if not o:
            continue
        recorde = estado.setdefault('menor', {}).get(m)
        chave = f"{o['id']}|{round(o['preco'])}"
        recente = chave in ja and agora - datetime.fromisoformat(ja[chave]) < timedelta(hours=24)
        motivo = None
        if m in config.MODELOS_COM_ALERTA and not recente:
            if o['preco'] <= config.ALVOS.get(m, 0):
                motivo = f"abaixo do alvo de {brl(config.ALVOS[m])}"
            elif recorde and o['preco'] <= recorde['preco'] - config.QUEDA_MINIMA:
                motivo = f"menor preço já visto (antes: {brl(recorde['preco'])} em {recorde['quando'][:10]})"
        if motivo:
            alertas.append({'modelo': m, 'motivo': motivo, 'oferta': o})
            ja[chave] = agora.isoformat(timespec='seconds')
        if not recorde or o['preco'] < recorde['preco']:
            estado['menor'][m] = {'preco': o['preco'], 'quando': agora.strftime('%Y-%m-%d %H:%M'), 'loja': o['loja'],
                                  'cupom': o['cupom'], 'titulo': o['titulo'], 'link': o['link']}
    estado['alertados'] = {k: v for k, v in ja.items() if agora - datetime.fromisoformat(v) < timedelta(days=7)}
    return alertas


def texto_alerta(alertas, agora):
    linhas = [f"🎮 <b>PS5: preço bom!</b> ({quando(agora)})", '']
    for a in alertas:
        o = a['oferta']
        linhas += [f"<b>{MODELOS[a['modelo']]}</b> — {a['motivo']}", descricao(o),
                   f"<i>{html.escape(o['titulo'][:120])}</i>" + (f" ({o['detalhes']})" if o['detalhes'] else ''), '']
    linhas.append('Confira o preço final no carrinho antes de pagar.')
    return '\n'.join(linhas)


def texto_resumo(resumo, agora, primeira=False):
    topo = '🎮 <b>Monitor do PS5 ligado!</b>' if primeira else f"🎮 <b>PS5 hoje</b> ({quando(agora)})"
    corpo = '\n'.join(linha_modelo(m, r) for m, r in resumo.items())
    alvos = ' · '.join(f"{MODELOS[m].removeprefix('PS5 ')} {brl(v)}" for m, v in config.ALVOS.items() if m in config.MODELOS_COM_ALERTA)
    fim = f"\n\nAviso com som quando chegar ao alvo ({alvos}) ou ao menor preço já visto."
    return f"{topo}\n\n{corpo}{fim}"


# ---------------------------------------------------------------- arquivos
def ler_json(caminho, padrao):
    try:
        with open(caminho) as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_json(caminho, dados):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, 'w') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
        f.write('\n')


def gravar_historico(caminho, agora, resumo):
    linha = {'data_hora': agora.strftime('%Y-%m-%d %H:%M')}
    for m, r in resumo.items():
        linha[m] = round(r['sem']['preco'], 2) if r['sem'] else ''
        linha[f'{m}_cupom'] = round(r['com']['preco'], 2) if r['com'] else ''
        linha[f'{m}_loja'] = r['melhor']['loja'] if r['melhor'] else ''
    novo = not os.path.exists(caminho)
    with open(caminho, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        if novo:
            w.writeheader()
        w.writerow(linha)


def ler_historico(caminho, dias=45):
    if not os.path.exists(caminho):
        return []
    with open(caminho, newline='') as f:
        linhas = list(csv.DictReader(f))
    limite = (datetime.now(fontes.BRT) - timedelta(days=dias)).strftime('%Y-%m-%d')
    return [x for x in linhas if x['data_hora'] >= limite]


def resumo_md(resumo, agora, alertas, situacao, fora, cupons_teste, telegram_status):
    linhas = [f"# PS5 — última varredura: {agora:%Y-%m-%d %H:%M} (hora de Brasília)", '']
    linhas += [f"- {linha_modelo(m, r, html_=False)}" for m, r in resumo.items()]
    linhas += ['', '## Alertas', ''] + ([f"- {MODELOS[a['modelo']]}: {a['motivo']}" for a in alertas] or ['Nenhum alerta nesta varredura.'])
    linhas += ['', f"Telegram: {telegram_status}", '', '## Cupons da loja para testar no carrinho (não confirmados para console)', '']
    linhas += [f"- {loja}: " + '; '.join(f"`{c['codigo']}` ({c['titulo'][:70]}) → {c['modelo']} de {brl(c['preco_base'])} por ~{brl(c['preco_estimado'])}" for c in v['cupons'])
               for loja, v in cupons_teste.items() if v['cupons']] or ['Nenhum.']
    linhas += ['', '## Fontes', '']
    linhas += [f"- {n}: " + (f"ok, {s['ofertas']} anúncios" if s['ok'] else f"falhou ({s['erro']})") for n, s in situacao.items()]
    linhas += ['', 'Descartados: ' + (', '.join(f'{v} {k}' for k, v in sorted(fora.items())) or 'nenhum'), '', f'Painel: {PAINEL_URL}']
    return '\n'.join(linhas) + '\n'


def para_painel(o):
    return {k: o.get(k) for k in ('modelo', 'fonte', 'loja', 'titulo', 'preco', 'preco_sem_cupom', 'cupom', 'cupom_origem', 'link',
                                  'obs', 'detalhes', 'idade_h')}


def gerar_painel(dados):
    gravar_json(os.path.join(DOCS, 'painel.json'), dados)
    with open(os.path.join(AQUI, 'painel.html')) as f:
        pagina = f.read()
    embutido = json.dumps(dados, ensure_ascii=False).replace('</', '<\\/')
    with open(os.path.join(DOCS, 'index.html'), 'w') as f:
        f.write(pagina.replace('/*DADOS*/null', embutido))


# ---------------------------------------------------------------- varredura
def rodar(coletar=fontes.coletar, buscar_cupons=fontes.cupons_promobit, enviar=telegram, pasta=AQUI, agora=None):
    agora = agora or datetime.now(fontes.BRT)
    estado = ler_json(os.path.join(pasta, 'estado.json'), {})
    ofertas, situacao = coletar()

    # Promobit: guarda as ofertas postadas (o preço com cupom some da 1ª página em poucas horas)
    mem = estado.get('promobit', {})
    for o in ofertas:
        if o['fonte'] == 'Promobit':
            mem[o['id']] = o
    validade = timedelta(hours=config.HORAS_OFERTA_PROMOBIT)
    estado['promobit'] = mem = {k: o for k, o in mem.items() if agora - fontes._quando(o['publicado']) <= validade}

    lojas = {'KaBuM!'} | {o['loja'].split(' · ')[0] for o in ofertas}
    cupons, erros_cupons = buscar_cupons(sorted(lojas & set(fontes.LOJAS_CUPOM)))
    aplicar_selos(ofertas, cupons)
    ps5, fora = preparar(ofertas, agora, mem)
    resumo = resumir(ps5)
    melhores_sem = [o for o in ps5 if not o['cupom']][:15]
    codigos_ps5 = {o['cupom'].upper() for o in ps5 if o['cupom']} | {o['cupom_selo'] for o in ofertas if o.get('cupom_selo')}
    teste = cupons_para_testar(cupons, melhores_sem, codigos_ps5)

    alertas = decidir_alertas(resumo, estado, agora)
    status = []
    if alertas:
        botoes = [botao(a['oferta']) for a in alertas][:3] + [PAINEL_BOTAO]
        status.append(enviar(texto_alerta(alertas, agora), botoes=botoes))
    hora = config.RESUMO_DIARIO_HORA
    hoje = agora.strftime('%Y-%m-%d')
    primeira = 'resumo_dia' not in estado
    if any(r['melhor'] for r in resumo.values()) and (primeira or (hora is not None and agora.hour >= hora and estado['resumo_dia'] != hoje)):
        botoes = [botao(r['melhor']) for r in resumo.values() if r['melhor']] + [PAINEL_BOTAO]
        st = enviar(texto_resumo(resumo, agora, primeira), silencioso=True, botoes=botoes)
        status.append(st)
        if st in ('TELEGRAM_ENVIADO', 'TELEGRAM_NAO_CONFIGURADO'):   # desligado (fora da main) ou falha: tenta de novo depois
            estado['resumo_dia'] = hoje

    # aviso se todas as fontes falharem por 6 varreduras seguidas (o site mudou ou barrou)
    estado['falhas_seguidas'] = 0 if ps5 else estado.get('falhas_seguidas', 0) + 1
    if estado['falhas_seguidas'] == 6:
        status.append(enviar('⚠️ <b>Monitor do PS5</b>: há 6 varreduras sem achar nenhuma oferta. As lojas podem ter mudado o site.',
                             botoes=[PAINEL_BOTAO]))
    tg = ' | '.join(status) or 'sem mensagem nesta varredura'

    gravar_historico(os.path.join(pasta, 'historico.csv'), agora, resumo)
    estado['ultima'] = agora.isoformat(timespec='seconds')
    gravar_json(os.path.join(pasta, 'estado.json'), estado)
    with open(os.path.join(pasta, 'ultimo.md'), 'w') as f:
        f.write(resumo_md(resumo, agora, alertas, situacao, fora, teste, tg))

    if erros_cupons:
        situacao['Cupons (Promobit)'] = {'ok': False, 'erro': '; '.join(erros_cupons)[:200]}
    else:
        situacao['Cupons (Promobit)'] = {'ok': True, 'ofertas': len(cupons)}
    dados = {
        'atualizado': agora.strftime('%Y-%m-%d %H:%M'),
        'modelos': [{'id': m, 'nome': MODELOS[m], 'alvo': config.ALVOS.get(m), 'alerta': m in config.MODELOS_COM_ALERTA,
                     'melhor': para_painel(r['melhor']) if r['melhor'] else None,
                     'sem': para_painel(r['sem']) if r['sem'] else None, 'com': para_painel(r['com']) if r['com'] else None,
                     'recorde': estado['menor'].get(m),
                     'ofertas': [para_painel(o) for o in ps5 if o['modelo'] == m][:15]} for m, r in resumo.items()],
        'historico': ler_historico(os.path.join(pasta, 'historico.csv')),
        'cupons_teste': teste,
        'alertas': [{'modelo': a['modelo'], 'motivo': a['motivo'], 'oferta': para_painel(a['oferta'])} for a in alertas],
        'fontes': situacao,
        'descartados': fora,
        'config': {'queda_minima': config.QUEDA_MINIMA, 'horas_promobit': config.HORAS_OFERTA_PROMOBIT},
    }
    if pasta == AQUI:
        gerar_painel(dados)
    for a in alertas:
        print(f"ALERTA {a['modelo']} {a['oferta']['preco']} {a['motivo']}")
    for m, r in resumo.items():
        print(f"MELHOR {m}: " + (descricao(r['melhor'], html_=False) if r['melhor'] else 'nada'))
    print('FONTES', ' '.join(f"{n}={'ok' if s['ok'] else 'falhou'}" for n, s in situacao.items()))
    print(tg)
    return dados


# ---------------------------------------------------------------- auto-checagem
def _teste():
    import tempfile
    casos = [
        ('Console Sony PlayStation 5, SSD 825GB, Controle Sem Fio DualSense + 2 Jogos Digitais, Edição Digital', 4364, 'digital'),
        ('Console Sony PlayStation 5, Com Leitor de Discos, SSD 1TB, Controle Sem Fio DualSense + 2 Jogos Digitais', 5114, 'leitor'),
        ('Console PlayStation 5 Pro Sony, SSD 2TB, Com Controle Sem Fio DualSense, Branco', 7399, 'pro'),
        ('PlayStation®5 Slim Digital 825GB – Pacote ASTRO BOT e Gran Turismo 7', 4230, 'digital'),
        ('PlayStation®5 Pro', 7399, 'pro'),
        ('Stea PlayStation®5 console (slim)', 4749, 'leitor'),
        ('Pack do console PlayStation®5 – Edição Limitada Ouro - Ghost of Yōtei', 6364, 'leitor'),
        ('Bundle GTA VI + PlayStation 5 Slim Digital 825 GB - Pacote Astro Bot e Gran Turismo 7', 4908, 'digital'),
        ('Novo Console Playstation PS5', 4399, 'leitor'),
        ('Console Sony PlayStation 5, SSD 825GB, Controle Sem Fio DualSense + 2', 4276, 'digital',
         'Playstation 5 Slim Edição Digital 825GB Bundle ASTRO BOT + Gran Turismo 7'),
        ('PlayStation 5 Pro', 8250, 'pro', 'PlayStation 5 825GB'),
        ('PlayStation 5 Edição Digital 825GB 1 Controle Branco Sony', 3945, 'digital'),
        ('Playstation Portal Para Console Ps5 Reprodutor Remoto Tela 8\' Fhd', 1799, None),
        ('Unidade De Disco Playstation 5 Slim Cor Branco', 569, None),
        ('Drive de Disco para Console PlayStation®5 Slim e Pro', 607, None),
        ('Tampas do console PlayStation®5 Slim - Graphite Gray', 499, None),
        ('Grand Theft Auto VI - PlayStation 5', 418, None),
        ('Controle sem fio PlayStation DualSense™ – Branco', 416, None),
        ('Console Playstation 4 1 Tb Edição Limitada Days Of Play - Cinza', 3599, None),
        ('Sony PlayStation 5 Slim Digital Edition Console - 825GB - Middle East Version', 4369, None),
        ('PlayStation 5 Disc Console (New 2023 Slim Model)- UAE Version', 4999, None),
        ('Console PlayStation 5 Slim Usado com 1 controle', 2900, None),
        ('Monitor Gamer Sony INZONE M3 27" compatível com PS5', 3200, None),
        ('PlayStation VR2 para PS5', 3299, None),
        ('Console PlayStation 5 Slim', 450, None),
    ]
    for titulo, preco, esperado, *produto in casos:
        assert classificar(titulo, preco, *produto)[0] == esperado, (titulo, classificar(titulo, preco, *produto), esperado)
    assert 'com jogo' in detalhes('PlayStation®5 Slim Digital 825GB – Pacote ASTRO BOT e Gran Turismo 7')
    assert detalhes('PlayStation®5 Pro') == ''

    assert fontes.regra_cupom('Cupom Mercado Livre: 30% OFF acima de R$ 2.699 (até R$ 300) em Eletrônicos') == {'pct': 30, 'minimo': 2699, 'teto': 300}
    assert fontes.regra_cupom('Use cupom KaBuM! e tenha desconto de R$ 100') == {'valor': 100}
    assert fontes.regra_cupom('O momento chegou: Aplique cupom KaBuM! e ganhe R$ 300 OFF') == {'valor': 300}
    assert fontes.regra_cupom('Garanta R$50 OFF com cupom nas compras acima de R$349') == {'valor': 50, 'minimo': 349}
    assert fontes.desconto({'pct': 30, 'minimo': 2699, 'teto': 300}, 4000) == 300
    assert fontes.desconto({'pct': 30, 'minimo': 2699, 'teto': 300}, 2000) == 0
    assert fontes.desconto({'pct': 5}, 4000) == 200

    pagina = {'props': {'pageProps': {'serverOffers': {'offers': [
        {'offerId': i, 'offerTitle': 'PlayStation 5 Slim Digital 825GB', 'offerPrice': 3800 + i, 'offerPriceType': 'NORMAL',
         'offerStatusName': 'APPROVED', 'storeName': 'X', 'offerSlug': f's-{i}', 'offerPublished': '2026-11-17T07:20:41-0300',
         'offerCoupon': c} for i, c in enumerate(['AEMEGA19', 'R$300 OFF', 'Lojas Físicas / Parcelado', None])]}}}}
    baixar, fontes.baixar = fontes.baixar, lambda url, **kw: f'<script id="__NEXT_DATA__">{json.dumps(pagina)}</script>'
    try:
        p = fontes.promobit()
    finally:
        fontes.baixar = baixar
    assert [(x['cupom'], x['obs']) for x in p] == [('AEMEGA19', 'postado no Promobit'), ('cupom R$300 OFF (na página)', 'postado no Promobit'),
                                                  (None, 'postado no Promobit: Lojas Físicas / Parcelado'), (None, 'postado no Promobit')], p

    agora = datetime(2026, 11, 13, 10, 0, tzinfo=fontes.BRT)

    def of(fonte, loja, titulo, preco, id_, **kw):
        return {**fontes.oferta(fonte, loja, titulo, preco, f'https://exemplo/{id_}', id=id_), **kw}

    def coleta(digital_kabum=4404.28, cupom_shopee=3991.88):
        def f():
            return [of('KaBuM!', 'KaBuM! · Magalu', 'Console Sony Playstation 5 Slim Edição Digital, 1TB, 2 Jogos', digital_kabum,
                       'kabum-939943', cupom_selo='MAGALUPS5', obs='à vista no Pix'),
                    of('KaBuM!', 'KaBuM!', 'Console PlayStation 5 Pro Sony, SSD 2TB', 7399, 'kabum-636960', obs='à vista no Pix'),
                    of('Zoom', 'KaBuM!', 'Console PlayStation 5 Pro Sony, SSD 2TB', 7399, 'zoom-1'),
                    of('Zoom', 'Magazine Luiza', 'Console PlayStation 5 Slim Disk 1TB Físico', 4530.55, 'zoom-2'),
                    of('Zoom', 'Magazine Luiza', 'Console PlayStation 5 Slim Digital 825GB', 4131.55, 'zoom-3'),
                    of('Promobit', 'Shopee', 'PlayStation 5 Edição Digital 825GB 1 Controle Branco Sony', cupom_shopee, 'promobit-1',
                       cupom='11DO11TECH', cupom_origem='postado', publicado='2026-11-13T08:38:00-0300'),
                    of('Promobit', 'Mercado Livre', 'Unidade De Disco Playstation 5 Slim', 569.48, 'promobit-2',
                       publicado='2026-11-13T09:25:00-0300'),
                    of('Promobit', 'Aliexpress', 'PlayStation 5 Edição Digital 825GB', 3800, 'promobit-velho',
                       cupom='VELHO', cupom_origem='postado', publicado='2026-11-10T09:25:00-0300')], \
                {'KaBuM!': {'ok': True, 'ofertas': 2}, 'Amazon': {'ok': False, 'erro': 'Bloqueado: HTTP 503'}}
        return f

    def cupons(lojas):
        return [{'loja': 'KaBuM!', 'codigo': 'MAGALUPS5', 'titulo': 'Use cupom KaBuM! e tenha desconto de R$ 100', 'validade': None,
                 'regra': {'valor': 100}},
                {'loja': 'KaBuM!', 'codigo': 'JBL25', 'titulo': '25% de Desconto em produtos JBL', 'validade': None, 'regra': {'pct': 25}},
                {'loja': 'Magazine Luiza', 'codigo': 'LU300', 'titulo': 'Cupom Magazine Luiza concede desconto de R$ 300',
                 'validade': None, 'regra': {'valor': 300}},
                {'loja': 'Magazine Luiza', 'codigo': 'GAMES10', 'titulo': '10% OFF em Games e Consoles no Magalu',
                 'validade': None, 'regra': {'pct': 10}}], []

    enviadas = []

    def enviar(texto, silencioso=False, botoes=()):
        enviadas.append((texto, silencioso, botoes))
        return 'TELEGRAM_ENVIADO'

    with tempfile.TemporaryDirectory() as pasta:
        d = rodar(coleta(), cupons, enviar, pasta, agora)
        dig = d['modelos'][1]
        assert dig['com']['preco'] == 3991.88 and dig['com']['cupom'] == '11DO11TECH', dig['com']
        assert dig['sem']['preco'] == 4131.55 and dig['melhor']['cupom'] == '11DO11TECH', dig['sem']
        kab = next(o for o in dig['ofertas'] if o['fonte'] == 'KaBuM!')
        assert kab['preco'] == 4304.28 and kab['cupom'] == 'MAGALUPS5' and kab['preco_sem_cupom'] == 4404.28, kab
        assert not any(o['cupom'] == 'VELHO' for o in dig['ofertas']), 'oferta do Promobit velha devia sair'
        pro = d['modelos'][2]
        assert len(pro['ofertas']) == 1, 'KaBuM! direto e pelo Zoom no mesmo preço viram uma oferta só'
        assert {loja: [c['codigo'] for c in v['cupons']] for loja, v in d['cupons_teste'].items()} == \
            {'Magazine Luiza': ['GAMES10'], 'KaBuM!': ['MAGALUPS5']}, d['cupons_teste']   # JBL25 é só JBL; LU300 é genérico
        assert d['cupons_teste']['Magazine Luiza']['cupons'][0]['preco_estimado'] == 3718.4
        assert len(enviadas) == 1 and enviadas[0][1] and 'ligado' in enviadas[0][0], 'primeira varredura: só o resumo, sem som'
        assert not d['alertas']

        # 2ª varredura, 1h depois: Digital cai para o alvo -> alerta com som
        enviadas.clear()
        d = rodar(coleta(cupom_shopee=3450), cupons, enviar, pasta, agora + timedelta(hours=1))
        assert [a['modelo'] for a in d['alertas']] == ['digital'] and 'alvo' in d['alertas'][0]['motivo'], d['alertas']
        assert len(enviadas) == 1 and not enviadas[0][1] and '11DO11TECH' in enviadas[0][0]

        # 3ª: mesmo preço -> não repete
        enviadas.clear()
        d = rodar(coleta(cupom_shopee=3450), cupons, enviar, pasta, agora + timedelta(hours=2))
        assert not d['alertas'] and not enviadas

        # 4ª: Pro cai R$100 (recorde, acima do alvo) -> alerta de menor preço
        def coleta_pro():
            ofs, sit = coleta(cupom_shopee=3450)()
            return [dict(o, preco=7299) if '636960' in o['id'] else o for o in ofs if o['id'] != 'zoom-1'], sit
        d = rodar(coleta_pro, cupons, enviar, pasta, agora + timedelta(hours=3))
        assert [a['modelo'] for a in d['alertas']] == ['pro'] and 'menor preço' in d['alertas'][0]['motivo'], d['alertas']

        # dia seguinte, 9h: resumo diário sem som
        enviadas.clear()
        rodar(coleta(cupom_shopee=3450), cupons, enviar, pasta, agora + timedelta(hours=23))
        assert len(enviadas) == 1 and enviadas[0][1] and 'PS5 hoje' in enviadas[0][0], enviadas
        with open(os.path.join(pasta, 'historico.csv')) as f:
            assert len(f.read().strip().splitlines()) == 6
    print('OK: auto-checagem passou')


if __name__ == '__main__':
    if '--teste' in sys.argv:
        _teste()
    else:
        rodar()
