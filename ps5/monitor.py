"""Buscador de preço do PS5 no Brasil: melhor preço de cada modelo, com cupom e sem cupom, e alerta no Telegram.

Modelos: PS5 com leitor de disco, PS5 Digital e PS5 Pro (bundles com jogo contam; acessórios não).
De onde vem cada preço e quanto dá para confiar nele (nível):
  A  preço lido direto na loja ou no comparador (KaBuM!, Amazon, lojas VTEX, Zoom)
  B  oferta postada em comunidade moderada (Promobit, Gatry) ou vista em dois ou mais grupos independentes do Telegram
  C  vista num grupo só, sem loja identificada, sem modelo no texto ou barata demais: "não confirmada"
O "melhor preço" e os recordes usam só A e B. A mesma oferta vista em várias fontes vira uma só, com "visto em".
Open box, importados e leilões ficam em seções à parte (config.SECOES), nunca misturados ao console novo.
Cupons gerais da loja aparecem como "para testar no carrinho", sem entrar na conta.

Alerta com som: melhor preço igual ou abaixo do alvo (config.py) ou menor preço já visto. Resumo diário sem som.
Telegram: bot próprio do PS5. Segredos: PS5_BOT, TELEGRAM_TOKEN_PS5 ou PS5_TOKEN (token do bot novo) e, opcional, TELEGRAM_CHAT_ID_PS5;
sem o segundo, vai para o primeiro chat de TELEGRAM_CHAT_ID. Quem manda /start ao bot passa a receber também (a lista
fica em ps5/assinantes.cif, cifrada; ver atendimento.py na raiz). PS5_ENVIAR=0 não manda nada (usado fora da branch main).

Uso:  python ps5/monitor.py                    -> varredura completa (fontes.py), grava e alerta
      python ps5/monitor.py --teste            -> auto-checagem sem internet
      python ps5/monitor.py --teste-telegram   -> manda uma mensagem de teste pelo bot do PS5
Saídas: ps5/historico.csv, ps5/estado.json, ps5/ultimo.md, docs/ps5/index.html e docs/ps5/painel.json.
"""
import csv, html, json, os, re, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
import config  # noqa: E402
import fontes  # noqa: E402
import texto   # noqa: E402

DOCS = os.path.join(RAIZ, 'docs', 'ps5')
PAINEL_URL = 'https://hugovfa2017.github.io/passagens-monitor/ps5/'
MODELOS = {'leitor': 'PS5 com leitor de disco', 'digital': 'PS5 Digital', 'pro': 'PS5 Pro'}
FAIXA = {'leitor': (2500, 8000), 'digital': (2500, 8000), 'pro': (4000, 11000)}   # fora disso é acessório, parcela ou erro
CAMPOS = ['data_hora'] + [f'{m}{s}' for m in MODELOS for s in ('', '_cupom', '_loja')]
SEMANA = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']
NOME_SECAO = {'openbox': 'open box / reembalado', 'importados': 'importado', 'usados': 'usado'}
simples = texto.simples


# ---------------------------------------------------------------- o que é um PS5 (e qual)
PS5 = re.compile(r'\bps ?5\b|playstation 5\b')
E_CONSOLE = re.compile(r'^\W*(?:novo |o )?(?:console|sony|playstation|ps ?5|bundle|pack|pacote|video ?game)\b'
                       r'|\bconsole (?:sony )?(?:playstation|ps) ?5|(?:playstation|ps) ?5 (?:\w+ ){0,3}console\b')
NAO_CONSOLE = re.compile(r'\bportal\b|reprodutor remoto|\bvr ?(?:2|ii)\b|realidade virtual|\bvolante\b|headset|\bfones?\b|'
                         r'^\W*(?:controle|jogo|game|capa|tampas?|suporte|base|drive|unidade|cabo|pelicula|skin|adesivo|case|'
                         r'bolsa|carregador|dock|ssd|hd|cartao|gift|monitor|tv|smart tv|cadeira|mesa|mochila|kit de)\b')
OPENBOX = re.compile(r'\b(?:open ?box|vitrine|mostruario|reembalad[oa]|recondicionad[oa]|refurbished|semi ?nov[oa]|quase nov[oa])\b')
USADO = re.compile(r'\b(?:usad[oa]s?|defeito|para pecas|retirada de pecas)\b')
IMPORTADO = re.compile(r'\b(?:middle east|uae|ksa|international|internacional|importad[oa]|japan(?:ese)?|japones[ae]?|'
                       r'europe(?:an)?|europeia|us version|usa version|versao americana|asia(?:n)?|hong kong|hk version|'
                       r'(?:eu|uk) version)\b')
COM_JOGO = re.compile(r'\+|bundle|pacote|\bpack\b|com \d+ jogos?|\bjogos?\b|astro bot|gran turismo|spider|god of war|gta\b|'
                      r'grand theft|wolverine|ea sports|\bfc ?\d\d\b|returnal|ratchet|ghost of|call of duty|2 controles')
ESPECIAL = re.compile(r'edicao limitada|limited edition|aniversario|anniversary|\bouro\b|\bgold\b')
LOJAS_DE_FORA = {'Aliexpress', 'Shopee', 'Temu'}   # marketplaces onde a versão vendida pode ser a importada


def modelo_de(t):
    """-> (modelo, se o texto diz o modelo com todas as letras)"""
    t = re.sub(r'jogos? digita(?:l|is)', '', t)
    if re.search(r'\bpro\b', t):
        return 'pro', True
    if 'digital' in t or re.search(r'sem (?:leitor|disco|drive)', t):
        return 'digital', True
    return 'leitor', bool(re.search(r'\bdis[ck]o?\b|leitor|fisic|standard|blu ?-?ray', t))


def avaliar(titulo, preco, produto=''):
    """-> {'modelo', 'condicao', 'motivo'}. modelo None quando não é console de PS5 (motivo diz por quê).
    condicao: novo | openbox | usado | importado.
    produto: nome no catálogo do comparador (Zoom), usado quando o título da loja vem cortado e não diz o modelo."""
    t, p = simples(titulo), simples(produto or '')
    base = t if PS5.search(t) and E_CONSOLE.search(t) else p
    if not base or not PS5.search(base) or not E_CONSOLE.search(base) or NAO_CONSOLE.search(t) or (p and NAO_CONSOLE.search(p)):
        return {'modelo': None, 'condicao': None, 'motivo': 'não é console PS5'}
    tudo = f'{t} {p}'
    condicao = 'usado' if USADO.search(tudo) else 'openbox' if OPENBOX.search(tudo) else 'importado' if IMPORTADO.search(tudo) else 'novo'
    modelo, explicito = modelo_de(t)
    if not explicito and p:
        modelo = modelo_de(p)[0]
    lo, hi = FAIXA[modelo]
    if condicao in ('usado', 'openbox'):
        lo *= 0.6                                                # usado e open box podem custar bem menos
    if not lo <= preco <= hi:
        return {'modelo': None, 'condicao': condicao, 'motivo': f'preço fora da faixa de {MODELOS[modelo]}'}
    return {'modelo': modelo, 'condicao': condicao, 'motivo': ''}


def classificar(titulo, preco, produto=''):
    """-> (modelo, motivo) só para console novo nacional; o resto volta None com o motivo."""
    a = avaliar(titulo, preco, produto)
    if a['modelo'] and a['condicao'] != 'novo':
        return None, 'importado' if a['condicao'] == 'importado' else 'usado/recondicionado'
    return a['modelo'], a['motivo']


def detalhes(titulo):
    t = simples(titulo)
    return ', '.join(x for x, ok in (('edição especial', ESPECIAL.search(t)), ('com jogo/extra', COM_JOGO.search(t))) if ok)


# ---------------------------------------------------------------- cupons
CUPOM_FORA = re.compile(r'jbl|impressora|scanner|note(?:book)?\d*|lenovo|asus|moda|beleza|livro|bebe|bebida|cozinha|\bpet\b|'
                        r'mercado\b(?! livre| pago)|\bcasa\b|esporte|fitness|automotiv|ferrament|constru|jardim|papelaria|relogio|'
                        r'bolsa|carteira|vinil|instrument|celular|smartphone|\btv\b|telona|tela gigante|cafe|arfrio|ar condicionado|'
                        r'robo|aspirador|mouse|teclado|cadeira|mesa|husky|periferi|software|antena|precisao|nintendo|saude|'
                        r'brinquedo|fralda|biscoito|lacta|sono|tablet|frete|primeira compra|app\b|selecionados|placa de video|'
                        r'processador|controle|dualsense|headset|monitor', re.I)
CUPOM_PS5 = re.compile(r'ps ?5|playstation|console|video ?games?|\bgames?\b|gamer|eletronicos|tecnologia|todo o site|site todo|'
                       r'todo site|sitetodo|todosite')


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


def cupom_plausivel(c, codigos_ps5=()):
    """O cupom pode valer para console? (fala de games/eletrônicos/site todo, veio de post sobre PS5 ou já apareceu numa
    oferta de console), desde que a regra dele não seja de outra categoria."""
    t = simples(c['titulo'] + ' ' + c['codigo'])
    return c['codigo'].upper() in codigos_ps5 or ((c.get('ps5') or bool(CUPOM_PS5.search(t))) and not CUPOM_FORA.search(t))


def cupons_para_testar(cupons, melhores, codigos_ps5=()):
    """Cupons gerais que podem valer para console, por loja, com o preço que dariam sobre a oferta sem cupom mais barata
    daquela loja (quando o buscador lê o preço dela). Não confirmados: não entram no 'melhor preço com cupom'."""
    base = {}   # a oferta sem cupom mais barata de cada loja: é nela que o cupom é testado
    for o in melhores:
        loja = loja_base(o)
        if loja not in base or o['preco'] < base[loja]['preco']:
            base[loja] = o
    out = {loja: {'pagina': f"{fontes.PROMOBIT}/cupons/loja/{fontes.LOJAS_CUPOM[loja]}/" if loja in fontes.LOJAS_CUPOM else None,
                  'cupons': []} for loja in base}
    vistos = set()
    for c in cupons:
        chave = (c['loja'], c['codigo'].upper())
        if chave in vistos or chave[1] in config.CUPONS_QUE_NAO_VALEM or not cupom_plausivel(c, codigos_ps5):
            continue
        vistos.add(chave)
        item = {k: c.get(k) for k in ('loja', 'codigo', 'titulo', 'validade', 'origem', 'link')}
        if c['loja'] in base:
            d = fontes.desconto(c['regra'], base[c['loja']]['preco'])
            if d < 30:
                continue
            item.update(desconto=round(d), preco_estimado=round(base[c['loja']]['preco'] - d, 2),
                        modelo=MODELOS[base[c['loja']]['modelo']], preco_base=base[c['loja']]['preco'])
        elif c.get('origem', '').startswith('canal') or c['codigo'].upper() in codigos_ps5:
            item.update(desconto=None, preco_estimado=None, modelo=None, preco_base=None)   # loja que o buscador não lê (ex.: ML)
        else:
            continue
        out.setdefault(c['loja'], {'pagina': None, 'cupons': []})['cupons'].append(item)
    for v in out.values():
        v['cupons'] = sorted(v['cupons'], key=lambda c: -(c['desconto'] or 0))[:5]
    return out


# ---------------------------------------------------------------- juntar a mesma oferta vista em várias fontes
ORDEM_TIPO = {'loja': 0, 'comparador': 1, 'postada': 2, 'grupo': 3}


def loja_base(o):
    """'KaBuM! · Magalu' -> 'KaBuM!'; 'Magalu' -> 'Magazine Luiza'"""
    return texto.loja_canonica(o['loja'].split(' · ')[0])


def _mesma(a, b):
    """a e b são a mesma oferta? (mesmo modelo, mesma loja, preço praticamente igual)"""
    if a['modelo'] != b['modelo'] or loja_base(a) != loja_base(b):
        return False
    de_loja = {a['tipo'], b['tipo']} <= {'loja', 'comparador'}
    if de_loja and a['fonte'] == b['fonte']:
        return False                                             # dois anúncios da mesma loja são produtos diferentes
    return abs(a['preco'] - b['preco']) <= (2 if de_loja else max(15, 0.004 * a['preco']))


def _fundida(g):
    """Grupo de ofertas iguais -> uma oferta, com nível de confiança e onde foi vista."""
    g = sorted(g, key=lambda o: (ORDEM_TIPO[o['tipo']], o.get('idade_h') if o.get('idade_h') is not None else 0))
    rep = dict(g[0])
    postadas = [o for o in g if o['tipo'] in ('postada', 'grupo')]
    tipos = {o['tipo'] for o in g}
    origens = {o['origem'] for o in postadas}
    vistos, visto_em = set(), []
    for o in g:
        if o['link'] not in vistos:
            vistos.add(o['link'])
            visto_em.append({'fonte': o.get('canal') or o['fonte'], 'link': o['link'], 'idade_h': o.get('idade_h'), 'tipo': o['tipo']})
    confianca = 'A' if tipos & {'loja', 'comparador'} else 'B' if 'postada' in tipos or len(origens) >= 2 else 'C'
    ressalvas = sorted({x for o in g for x in o.get('ressalvas') or []})
    if confianca != 'A':
        if rep.get('modelo_incerto') and all(o.get('modelo_incerto') for o in g):
            confianca, ressalvas = 'C', ressalvas + ['o post não diz o modelo']
        if loja_base(rep) == '?':
            confianca = 'C'
        if rep['preco'] < config.PISO[rep['modelo']]:
            confianca, ressalvas = 'C', ressalvas + ['preço muito abaixo do normal']
    idades = [o['idade_h'] for o in postadas if o.get('idade_h') is not None]
    citados = [o['cupom'] for o in g if o.get('cupom')]
    if citados and not rep.get('cupom'):
        codigos = [c for c in citados if not c.startswith('cupom')] or citados
        mais = max(set(codigos), key=codigos.count)
        if rep['tipo'] in ('postada', 'grupo'):
            rep.update(cupom=mais, cupom_origem='postado')
        else:
            rep['cupom_citado'] = mais                           # a loja mostra esse preço sem cupom; os grupos citam um
    rep.update(confianca=confianca, ressalvas=ressalvas, visto_em=visto_em[:8], n_vistos=len(visto_em),
               n_origens=len(origens) + len({o['fonte'] for o in g if o['tipo'] in ('loja', 'comparador')}),
               chave=f"{rep['modelo']}|{simples(loja_base(rep))}|{int(rep['preco'] // 10)}")
    if idades:
        rep['idade_h'] = min(idades)
    return rep


def fundir(ofertas):
    """Junta a mesma oferta vinda de fontes diferentes (loja + comparador + Promobit + vários canais) numa só."""
    certas = sorted((o for o in ofertas if not o.get('modelo_incerto') and loja_base(o) != '?'),
                    key=lambda o: (ORDEM_TIPO[o['tipo']], o['preco']))
    soltas = sorted((o for o in ofertas if o.get('modelo_incerto') or loja_base(o) == '?'), key=lambda o: o['preco'])
    grupos = []
    for o in certas:
        g = next((g for g in grupos if _mesma(g[0], o)), None)
        if g:
            g.append(o)
        else:
            grupos.append([o])
    for o in soltas:   # sem modelo ou sem loja no texto: entra num grupo só se houver um único candidato com o mesmo preço
        cands = [g for g in grupos if abs(g[0]['preco'] - o['preco']) <= 5 and not g[0].get('modelo_incerto')
                 and (o.get('modelo_incerto') or g[0]['modelo'] == o['modelo'])
                 and (loja_base(o) == '?' or loja_base(g[0]) == loja_base(o))]
        if cands and len({(g[0]['modelo'], loja_base(g[0])) for g in cands}) == 1:
            cands[0].append(o)
        else:
            grupos.append([o])
    return sorted((_fundida(g) for g in grupos), key=lambda o: (o['preco'], o['confianca']))


def lembrar_postadas(ofertas, estado, agora):
    """Guarda as ofertas postadas (somem da 1ª página em poucas horas) e devolve as atuais mais as lembradas ainda válidas."""
    mem = estado.pop('promobit', None) or {}
    mem.update(estado.get('postadas') or {})
    atuais = set()
    for o in ofertas:
        if o['tipo'] in ('postada', 'grupo'):
            if not o.get('publicado'):                           # Gatry não informa a hora: vale a da primeira vez em que foi vista
                o['publicado'] = (mem.get(o['id']) or {}).get('publicado') or agora.strftime('%Y-%m-%dT%H:%M:%S%z')
            mem[o['id']] = o
            atuais.add(o['id'])
    validade = lambda o: timedelta(hours=config.HORAS_OFERTA_GRUPO if o.get('tipo') == 'grupo' else config.HORAS_OFERTA_POSTADA)
    estado['postadas'] = mem = {k: o for k, o in mem.items() if agora - fontes._quando(o['publicado']) <= validade(o)}
    lembradas = [{**o, 'tipo': o.get('tipo', 'postada'), 'origem': o.get('origem', 'promobit'), 'ressalvas': o.get('ressalvas', []),
                  'obs': o.get('obs', '') + ' (saiu da lista; pode ter acabado)'} for k, o in mem.items() if k not in atuais]
    return ofertas + lembradas


def preparar(ofertas, agora):
    """Classifica cada oferta e junta as repetidas. -> (console novo, seções {openbox, importados, usados}, descartadas por motivo)"""
    novas, secoes, fora = [], {}, {}
    for o in ofertas:
        if o.get('publicado'):
            pub = fontes._quando(o['publicado'])
            limite = config.HORAS_OFERTA_GRUPO if o['tipo'] == 'grupo' else config.HORAS_OFERTA_POSTADA
            if agora - pub > timedelta(hours=limite):
                continue
            o['idade_h'] = round(max((agora - pub).total_seconds(), 0) / 3600, 1)
        a = avaliar(o['titulo'], o['preco'], o.get('produto_zoom'))
        if not a['modelo']:
            fora[a['motivo']] = fora.get(a['motivo'], 0) + 1
            continue
        condicao = 'openbox' if o.get('openbox') and a['condicao'] == 'novo' else a['condicao']
        x = {**o, 'modelo': a['modelo'], 'condicao': condicao, 'detalhes': detalhes(f"{o['titulo']} {o.get('produto_zoom', '')}"),
             'ressalvas': list(o.get('ressalvas') or [])}
        if o['tipo'] == 'grupo' and not texto.MODELO_DITO.search(simples(o['titulo'])):
            x['modelo_incerto'] = True
        if condicao == 'novo' and loja_base(x) in LOJAS_DE_FORA and o['tipo'] in ('postada', 'grupo'):
            x['ressalvas'].append('confira se é a versão nacional')
        if condicao == 'novo':
            novas.append(x)
            continue
        secao = {'openbox': 'openbox', 'importado': 'importados', 'usado': 'usados'}[condicao]
        if config.SECOES.get(secao):
            secoes.setdefault(secao, []).append(x)
        else:
            fora[NOME_SECAO[secao]] = fora.get(NOME_SECAO[secao], 0) + 1
    return fundir(novas), {k: fundir(v) for k, v in secoes.items()}, fora


def resumir(ofertas):
    """Por modelo: melhor sem cupom, melhor com cupom e o melhor dos dois (só níveis A e B), mais a melhor 'não confirmada'."""
    r = {}
    for m in MODELOS:
        dom = [o for o in ofertas if o['modelo'] == m]
        firmes = [o for o in dom if o['confianca'] in 'AB']
        sem = next((o for o in firmes if not o['cupom']), None)
        com = next((o for o in firmes if o['cupom']), None)
        # cupom que economiza quase nada só dá trabalho: fica valendo a oferta sem cupom
        vale = com and (not sem or com['preco'] <= sem['preco'] - config.ECONOMIA_MINIMA_CUPOM)
        melhor = com if vale else sem or com
        duvidosa = next((o for o in dom if o['confianca'] == 'C' and (not melhor or o['preco'] < melhor['preco'])), None)
        r[m] = {'sem': sem, 'com': com, 'melhor': melhor, 'nao_confirmada': duvidosa}
    return r


def mais_barato(resumo):
    """O PS5 novo mais barato agora, seja qual for o modelo."""
    return min((r['melhor'] for r in resumo.values() if r['melhor']), key=lambda o: o['preco'], default=None)


# ---------------------------------------------------------------- texto
def brl(n):
    return 'R$ ' + f'{n:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.').removesuffix(',00')


def quando(agora):
    return f"{SEMANA[agora.weekday()]} {agora:%d/%m, %H:%M}"


def na(loja):
    return 'no' if re.match(r'(Mercado Livre|Carrefour|Ponto|Extra|Submarino|Zoom)', loja) else 'na'


def _extras(o, html_=True):
    """Ressalvas e sinais de confiança de uma oferta, em pedaços curtos: 'à vista no Pix', 'visto em 3 lugares', 'postado há 1h'."""
    esc = html.escape if html_ else (lambda s: s)
    cod = (lambda c: f'<code>{html.escape(c)}</code>') if html_ else (lambda c: c)
    postada = o.get('tipo') in ('postada', 'grupo')
    extra = [esc(x) for x in (o.get('ressalvas') or [])]
    if not postada and o.get('obs'):
        extra.append(esc(o['obs'].split(' · ')[0]))
    elif o.get('fonte') == 'Promobit' and ':' in o.get('obs', ''):
        extra.append(esc(o['obs']))
    if o.get('cupom_citado'):
        extra.append(f"grupos citam o cupom {cod(o['cupom_citado'])}")
    if o.get('n_vistos', 1) > 1:
        extra.append(f"visto em {o['n_vistos']} lugares")
    if postada and o.get('idade_h') is not None:
        extra.append(f"postado há {o['idade_h']:.0f}h")
    return extra


def descricao(o, html_=True):
    """'R$ 3.759 no Mercado Livre com cupom MP500PS · cartão Mercado Pago · visto em 6 lugares, há 1h'"""
    esc = html.escape if html_ else (lambda s: s)
    cod = (lambda c: f'<code>{html.escape(c)}</code>') if html_ else (lambda c: c)
    partes = [f"{brl(o['preco'])} {na(o['loja'])} {esc(o['loja'])}"]
    if o['cupom']:
        partes.append(f"com {esc(o['cupom'])}" if o['cupom'].startswith('cupom') else f"com cupom {cod(o['cupom'])}")
        if o.get('preco_sem_cupom'):
            partes.append(f"(sem cupom {brl(o['preco_sem_cupom'])})")
    extra = _extras(o, html_)
    return ' '.join(partes) + (f" · {', '.join(extra)}" if extra else '')


def linha_modelo(m, r, html_=True):
    b = (lambda s: f'<b>{s}</b>') if html_ else (lambda s: f'**{s}**')
    if not r['melhor']:
        return f"{b(MODELOS[m])}: nenhuma oferta confirmada"
    s = f"{b(MODELOS[m])}: {descricao(r['melhor'], html_)}"
    if r['melhor']['cupom'] and r['sem']:
        s += f"\n   sem cupom: {brl(r['sem']['preco'])} {na(r['sem']['loja'])} {r['sem']['loja']}"
    if r.get('nao_confirmada'):
        s += f"\n   não confirmada: {descricao(r['nao_confirmada'], html_)}"
    return s


# ---------------------------------------------------------------- mensagens do Telegram
# Toda mensagem de preço começa pelo valor: é o começo do texto que aparece na notificação do celular.
ICONE = {'leitor': '💿', 'digital': '🎮', 'pro': '🚀'}
CURTO = {'leitor': 'Com leitor', 'digital': 'Digital', 'pro': 'Pro'}


def dia_mes(quando_):
    """'2026-11-18 14:30' -> '18/11'"""
    return f"{quando_[8:10]}/{quando_[5:7]}"


def loja_link(o):
    """Nome da loja como link para a oferta (texto simples quando não há link)."""
    nome = html.escape(o['loja'])
    return f'<a href="{html.escape(o["link"], quote=True)}">{nome}</a>' if o.get('link') else nome


def manchete(o, icone):
    """Primeira linha: valor, modelo e loja. '🔥 R$ 3.450 · PS5 Digital na Shopee'"""
    return f"{icone} <b>{brl(o['preco'])} · {MODELOS[o['modelo']]}</b> {na(o['loja'])} {loja_link(o)}"


def linhas_oferta(o, sem=None):
    """Cupom e ressalvas de uma oferta, uma linha para cada. sem: a melhor oferta sem cupom do modelo, para comparar."""
    linhas = []
    if o['cupom']:
        cupom = html.escape(o['cupom']) if o['cupom'].startswith('cupom') else f"cupom <code>{html.escape(o['cupom'])}</code>"
        if sem and sem is not o:
            cupom += f" · sem cupom: {brl(sem['preco'])} {na(sem['loja'])} {loja_link(sem)}"
        elif o.get('preco_sem_cupom'):
            cupom += f" · sem ele: {brl(o['preco_sem_cupom'])}"
        linhas.append(f"🏷 {cupom}")
    extras = _extras(o)
    if extras:
        linhas.append('ℹ️ ' + ' · '.join(extras))
    return linhas


def bloco_modelo(m, r, recorde=None):
    """Um modelo no resumo: preço e loja na primeira linha; depois cupom, ressalvas, distância do alvo e menor preço já visto."""
    o = r['melhor']
    if not o:
        linhas = [f"{ICONE[m]} <b>{CURTO[m]}</b> — nenhuma oferta confirmada"]
    else:
        linhas = [f"{ICONE[m]} <b>{CURTO[m]}</b> — <b>{brl(o['preco'])}</b> {na(o['loja'])} {loja_link(o)}"] + linhas_oferta(o, r['sem'])
        metas = []
        alvo = config.ALVOS.get(m)
        if alvo and m in config.MODELOS_COM_ALERTA:
            metas.append(f"✅ no alvo de {brl(alvo)}" if o['preco'] <= alvo else f"🎯 alvo {brl(alvo)} (faltam {brl(round(o['preco'] - alvo))})")
        if recorde and recorde['preco'] <= o['preco'] - 10:   # recorde centavos abaixo do preço de agora é ruído
            metas.append(f"📉 recorde {brl(recorde['preco'])} em {dia_mes(recorde['quando'])}")
        if metas:
            linhas.append(' · '.join(metas))
    d = r.get('nao_confirmada')
    if d:
        idade = f", postado há {d['idade_h']:.0f}h" if d.get('idade_h') is not None else ''
        linhas.append(f"👀 só num grupo: {brl(d['preco'])} {na(d['loja'])} {loja_link(d)} (não confirmada{idade})")
    return linhas


def texto_alerta(alertas, agora, resumo=None):
    """Começa pela oferta que disparou o aviso (primeiro as que tocam, da mais barata para a mais cara).
    No fim, uma linha com o melhor preço dos outros modelos."""
    linhas = []
    for a in sorted(alertas, key=lambda a: (not a['som'], a['oferta']['preco'])):
        o = a['oferta']
        sinal = '🎯' if a['motivo'].startswith('abaixo') else '📉' if a['motivo'].startswith('menor') else '⚠️'
        linhas += [manchete(o, '🔥' if a['som'] else '👀'), f"{sinal} {a['motivo'][:1].upper()}{a['motivo'][1:]}", *linhas_oferta(o),
                   f"<i>{html.escape(o['titulo'][:90])}</i>" + (f" ({o['detalhes']})" if o['detalhes'] else ''), '']
    avisados = {a['modelo'] for a in alertas}
    outros = [f"{ICONE[m]} {CURTO[m]} {brl(r['melhor']['preco'])} ({loja_link(r['melhor'])})"
              for m, r in (resumo or {}).items() if m not in avisados and r['melhor']]
    if outros:
        linhas += ['Outros modelos: ' + ' · '.join(outros), '']
    linhas.append('Confira o preço final no carrinho antes de pagar.')
    return '\n'.join(linhas)


def texto_resumo(resumo, agora, primeira=False, recordes=None):
    """Começa pelo PS5 novo mais barato agora; depois, um bloco por modelo."""
    barato = mais_barato(resumo)
    linhas = [manchete(barato, '🏆'), f"O PS5 novo mais barato agora · {quando(agora)}"] if barato else \
        [f"🎮 <b>PS5 hoje</b> · {quando(agora)}"]
    if primeira:
        linhas.append('Buscador do PS5 ligado! Este resumo chega uma vez por dia, sem som.')
    for m, r in resumo.items():
        linhas += [''] + bloco_modelo(m, r, (recordes or {}).get(m))
    linhas += ['', '🔔 Toca quando um modelo chega ao alvo ou ao menor preço já visto.']
    return '\n'.join(linhas)


def texto_leiloes(lotes):
    """Lotes novos de leilão. Começa pelo menor lance."""
    ordem = sorted(lotes, key=lambda l: (not l.get('lance'), l.get('lance') or 0))
    p = ordem[0]
    topo = f"🔨 <b>{brl(p['lance'])} · PS5 em leilão</b> ({html.escape(p.get('lance_tipo') or 'lance')})" if p.get('lance') else \
        '🔨 <b>PS5 em leilão</b>'
    linhas = [topo, 'Lance não é preço final: há taxas, retirada e nenhum teste.', '']
    for l in ordem[:5]:
        casa = html.escape(l['origem'])
        casa = f'<a href="{html.escape(l["link"], quote=True)}">{casa}</a>' if l.get('link') else casa
        linhas.append(f"• {casa}: {html.escape(l['descricao'][:100])}" +
                      (f" — {html.escape(l.get('lance_tipo') or 'lance')} {brl(l['lance'])}" if l.get('lance') else ''))
    return '\n'.join(linhas)


# ---------------------------------------------------------------- Telegram (bot próprio do PS5)
def _token():
    """Só o bot do PS5 (TELEGRAM_TOKEN_PS5). Nunca cai no bot das passagens."""
    achado = re.search(r'\d+:[A-Za-z0-9_-]{30,}', os.environ.get('TELEGRAM_TOKEN_PS5', ''))
    return achado[0] if achado else ''


def _chats(pasta=AQUI):
    """A lista fixa do dono (TELEGRAM_CHAT_ID_PS5, ou o primeiro chat de TELEGRAM_CHAT_ID: o número do chat é o mesmo
    para qualquer bot) e, depois dela, quem se inscreveu mandando /start ao bot do PS5 (assinantes.cif)."""
    proprio = re.findall(r'-?\d{5,}', os.environ.get('TELEGRAM_CHAT_ID_PS5', ''))
    fixos = proprio or re.findall(r'-?\d{5,}', os.environ.get('TELEGRAM_CHAT_ID', ''))[:1]
    try:
        if RAIZ not in sys.path:
            sys.path.append(RAIZ)
        import atendimento
        pelo_start = atendimento.inscritos(_token(), os.path.join(pasta, 'assinantes.cif'))
    except Exception:
        pelo_start = []
    return list(dict.fromkeys(fixos + pelo_start))


def _api(token, metodo, campos=None):
    dados = urllib.parse.urlencode(campos).encode() if campos else None
    with urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/{metodo}', data=dados), timeout=30) as r:
        return json.loads(r.read())


def telegram(texto_, silencioso=False, botoes=(), so_dono=False):
    """Nunca derruba a varredura nem escreve token ou id de chat em lugar nenhum.
    so_dono: só o primeiro chat da lista (o dono) recebe; é para aviso de manutenção, que não interessa a quem assina."""
    if os.environ.get('PS5_ENVIAR', '1') == '0':
        return 'TELEGRAM_DESLIGADO'
    token, chats = _token(), _chats()
    chats = chats[:1] if so_dono else chats
    if not token:
        return 'TELEGRAM_NAO_CONFIGURADO (falta o segredo com o token do bot do PS5: PS5_BOT)'
    if not chats:
        return 'TELEGRAM_NAO_CONFIGURADO (falta TELEGRAM_CHAT_ID_PS5 ou TELEGRAM_CHAT_ID)'
    falhas = []
    for chat in chats:
        campos = {'chat_id': chat, 'text': texto_, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true',
                  'disable_notification': 'true' if silencioso else 'false'}
        if botoes:
            campos['reply_markup'] = json.dumps({'inline_keyboard': list(botoes)})
        try:
            if not _api(token, 'sendMessage', campos).get('ok'):
                falhas.append('recusado')
        except Exception as e:   # sem str(e): pode trazer o token na URL
            codigo = getattr(e, 'code', '')
            falhas.append('o bot não pode te escrever: mande /start para o bot do PS5' if codigo in (400, 403)
                          else f"{type(e).__name__} {codigo}".strip())
    return 'TELEGRAM_ENVIADO' if not falhas else 'TELEGRAM_FALHOU ' + ', '.join(falhas)


def botao(o):
    """'💿 Com leitor · R$ 4.530,55 · Magazine Luiza' -> abre a oferta."""
    return [{'text': f"{ICONE[o['modelo']]} {CURTO[o['modelo']]} · {brl(o['preco'])} · {o['loja'].split(' · ')[0]}"[:60], 'url': o['link']}]


PAINEL_BOTAO = [{'text': '📊 Abrir o painel do PS5', 'url': PAINEL_URL}]


# ---------------------------------------------------------------- alertas e estado
def cacador(agora):
    """Modo caçador ligado? (até config.CACADOR_ATE, exclusiva, no fuso de agora)"""
    return bool(config.CACADOR_ATE) and agora.date() < config.CACADOR_ATE


def guarda_min(agora):
    """Minutos que o workflow espera desde a última rodada: 7 no modo caçador (gatilho de 10 min), 25 fora dele (rodada de 30 em 30)."""
    return 7 if cacador(agora) else 25


def limite(m):
    """Preço que faz um modelo tocar: o alvo dele ou config.AVISAR_ABAIXO, o que for maior."""
    return max(config.ALVOS.get(m, 0), config.AVISAR_ABAIXO or 0)


def decidir_alertas(resumo, estado, agora):
    """Com som: melhor preço confirmado abaixo do alvo ou menor já visto. Sem som: oferta de grupo ainda não confirmada.
    Não repete a mesma oferta (mesma loja, mesmo preço) por 24 h. Oferta só vista em post antigo não toca nem vira recorde."""
    alertas = []
    ja = estado.setdefault('alertados', {})
    estado.setdefault('menor', {})
    recente = lambda chave: chave in ja and agora - datetime.fromisoformat(ja[chave]) < timedelta(hours=24)
    for m, r in resumo.items():
        o = r['melhor']
        if o:
            recorde = estado['menor'].get(m)
            velha = o['tipo'] in ('postada', 'grupo') and (o.get('idade_h') or 0) > config.HORAS_ALERTA_POSTADA
            motivo = None
            if m in config.MODELOS_COM_ALERTA and not velha and not recente(o['chave']):
                if o['preco'] <= config.ALVOS.get(m, 0):
                    motivo = f"abaixo do alvo de {brl(config.ALVOS[m])}"
                elif o['preco'] <= (config.AVISAR_ABAIXO or 0):
                    motivo = f"abaixo de {brl(config.AVISAR_ABAIXO)}"
                elif recorde and o['preco'] <= recorde['preco'] - config.QUEDA_MINIMA:
                    motivo = f"menor preço já visto (antes: {brl(recorde['preco'])} em {dia_mes(recorde['quando'])})"
            if motivo:
                alertas.append({'modelo': m, 'motivo': motivo, 'oferta': o, 'som': True})
                ja[o['chave']] = agora.isoformat(timespec='seconds')
            if not velha and (not recorde or o['preco'] < recorde['preco']):
                estado['menor'][m] = {'preco': o['preco'], 'quando': agora.strftime('%Y-%m-%d %H:%M'), 'loja': o['loja'],
                                      'cupom': o['cupom'], 'titulo': o['titulo'], 'link': o['link']}
        d = r.get('nao_confirmada')
        if d and m in config.MODELOS_COM_ALERTA and (d.get('idade_h') or 0) <= 3 and not recente('nc|' + d['chave']) \
                and (d['preco'] <= limite(m) or (o and d['preco'] <= o['preco'] - 150)):
            alertas.append({'modelo': m, 'motivo': 'oferta de grupo ainda NÃO confirmada (confira antes de se animar)', 'oferta': d,
                            'som': cacador(agora) and d['preco'] <= limite(m)
                                   and 'o post não diz o modelo' not in (d.get('ressalvas') or [])})
            ja['nc|' + d['chave']] = agora.isoformat(timespec='seconds')
    estado['alertados'] = {k: v for k, v in ja.items() if agora - datetime.fromisoformat(v) < timedelta(days=7)}
    return alertas


# ---------------------------------------------------------------- arquivos
def ler_json(caminho, padrao):
    for c in (caminho, caminho + '.bak'):   # se a gravação foi interrompida, vale a cópia anterior
        try:
            with open(c) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return padrao


def gravar_json(caminho, dados, copia=False):
    """Grava num arquivo temporário e troca de uma vez (nunca deixa o arquivo pela metade)."""
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    if copia and os.path.exists(caminho):
        os.replace(caminho, caminho + '.bak')
    with open(caminho + '.tmp', 'w') as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
        f.write('\n')
    os.replace(caminho + '.tmp', caminho)


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


def resumo_md(resumo, agora, alertas, situacao, fora, cupons_teste, telegram_status, secoes, lotes):
    linhas = [f"# PS5 — última varredura: {agora:%Y-%m-%d %H:%M} (hora de Brasília)", '']
    barato = mais_barato(resumo)
    if barato:
        linhas += [f"**Mais barato agora:** {MODELOS[barato['modelo']]} por {descricao(barato, html_=False)}", '']
    linhas += [f"- {linha_modelo(m, r, html_=False)}" for m, r in resumo.items()]
    linhas += ['', '## Alertas', ''] + ([f"- {MODELOS[a['modelo']]}: {a['motivo']}" for a in alertas] or ['Nenhum alerta nesta varredura.'])
    linhas += ['', f"Telegram: {telegram_status}", '', '## Cupons para testar no carrinho (não confirmados para console)', '']
    linhas += [f"- {loja}: " + '; '.join(f"`{c['codigo']}` ({c['titulo'][:70]})" + (f" → {c['modelo']} de {brl(c['preco_base'])} por ~{brl(c['preco_estimado'])}" if c.get('preco_estimado') else '')
                                         for c in v['cupons']) for loja, v in cupons_teste.items() if v['cupons']] or ['Nenhum.']
    for chave, lista in secoes.items():
        linhas += ['', f"## À parte: {NOME_SECAO[chave]}", ''] + [f"- {MODELOS[o['modelo']]}: {descricao(o, html_=False)}" for o in lista[:6]]
    if lotes:
        linhas += ['', '## À parte: leilões (lance não é preço final: há taxas, retirada e nenhum teste)', '']
        linhas += [f"- {l['origem']}: {l['descricao'][:90]}" + (f" — {l['lance_tipo']} {brl(l['lance'])}" if l.get('lance') else '') +
                   (f", {l['encerra'].lower()}" if l.get('encerra') else '') + f" ({l['link']})" for l in lotes[:8]]
    linhas += ['', '## Fontes', '']
    barradas = lambda s: f" · barrada em {s['barradas_hoje']} de {s['buscas_hoje']} buscas hoje" if s.get('barradas_hoje') else ''
    linhas += [f"- {n}: " + (f"ok, {s['ofertas']} itens" + (f" ({s['detalhe']})" if s.get('detalhe') else '') if s['ok'] else f"falhou ({s['erro']})")
               + barradas(s) for n, s in situacao.items()]
    linhas += ['', 'Descartados: ' + (', '.join(f'{v} {k}' for k, v in sorted(fora.items())) or 'nenhum'), '', f'Painel: {PAINEL_URL}']
    return '\n'.join(linhas) + '\n'


def para_painel(o):
    return {k: o.get(k) for k in ('modelo', 'fonte', 'loja', 'titulo', 'preco', 'preco_sem_cupom', 'cupom', 'cupom_origem', 'link',
                                  'obs', 'detalhes', 'idade_h', 'tipo', 'condicao', 'confianca', 'ressalvas', 'visto_em', 'n_vistos',
                                  'cupom_citado')}


def gerar_painel(dados, destino=DOCS):
    gravar_json(os.path.join(destino, 'painel.json'), dados)
    with open(os.path.join(AQUI, 'painel.html')) as f:
        pagina = f.read()
    embutido = json.dumps(dados, ensure_ascii=False).replace('</', '<\\/')
    with open(os.path.join(destino, 'index.html'), 'w') as f:
        f.write(pagina.replace('/*DADOS*/null', embutido))


# ---------------------------------------------------------------- varredura
def avisos_da_fonte(nome, s, h, agora):
    """Atualiza a saúde de uma fonte (h, guardada no estado) com o resultado desta rodada (s) e devolve os avisos de
    manutenção a mandar ao dono (quase sempre nenhum).
    - Fonte quebrada (erro que não é bloqueio): avisa na 6ª falha seguida, no máximo uma vez a cada
      config.DIAS_ENTRE_AVISOS_DE_FONTE dias.
    - Bloqueio anti-robô é rotina e não avisa: a loja barra o servidor em parte das buscas e volta sozinha. Só quando a
      fonte passa um dia inteiro barrada em todas as buscas (20 ou mais) sai um aviso, com o mesmo intervalo mínimo.
    h também conta as buscas e os bloqueios do dia, para o resumo da varredura mostrar a proporção."""
    hoje, avisos = agora.strftime('%Y-%m-%d'), []
    recente = bool(h.get('avisado')) and agora - datetime.fromisoformat(h['avisado']) < timedelta(days=config.DIAS_ENTRE_AVISOS_DE_FONTE)
    if h.get('dia') != hoje:
        buscas, barradas = h.get('buscas', 0), h.get('barradas', 0)
        if h.get('dia') and buscas >= 20 and barradas == buscas and h.get('ja_funcionou') and not recente:
            avisos.append(f"⚠️ <b>Buscador do PS5</b>: a fonte {html.escape(nome)} barrou o buscador em todas as {buscas} buscas de ontem. "
                          "Sigo tentando de vez em quando.")
            recente = True
        h.update(dia=hoje, buscas=0, barradas=0)
    barrada = not s['ok'] and str(s.get('erro', '')).startswith(('Bloqueado', 'pausada'))
    h['buscas'] += 1
    h['barradas'] += barrada
    if s['ok'] and not s.get('pausada'):
        h.update(falhas=0, ja_funcionou=True)
    else:
        h['falhas'] += 1
        if h['falhas'] == 6 and h['ja_funcionou'] and not barrada and not recente:
            avisos.append(f"⚠️ <b>Buscador do PS5</b>: a fonte {html.escape(nome)} parou de responder há 6 rodadas "
                          f"({html.escape(str(s.get('erro', ''))[:80])}).")
    if avisos:
        h['avisado'] = agora.isoformat(timespec='seconds')
    return avisos


def rodar(coletar=None, buscar_cupons=None, enviar=telegram, pasta=AQUI, agora=None, painel=None, resumo_agora=False):
    agora = agora or datetime.now(fontes.BRT)
    painel = (pasta == AQUI) if painel is None else painel
    fontes.ORCAMENTO_S = config.ORCAMENTO_S
    estado = ler_json(os.path.join(pasta, 'estado.json'), {})
    itens, situacao = (coletar or fontes.coletar)()
    ofertas = [x for x in itens if 'preco' in x]
    cupons_grupos = [x for x in itens if 'codigo' in x]          # cupons anunciados nos canais
    lotes = [x for x in itens if 'lote' in x] if config.SECOES.get('leiloes') else []
    for o in ofertas:                                            # fontes de teste ou antigas podem vir sem estes campos
        o.setdefault('tipo', 'loja')
        o.setdefault('origem', o['fonte'])
    ofertas = lembrar_postadas(ofertas, estado, agora)

    lojas_vistas = {'KaBuM!'} | {loja_base(o) for o in ofertas}
    cupons, erros_cupons = (buscar_cupons or fontes.cupons_promobit)(sorted(lojas_vistas & set(fontes.LOJAS_CUPOM)))
    cupons = cupons + cupons_grupos
    aplicar_selos(ofertas, cupons)
    ps5, secoes, fora = preparar(ofertas, agora)
    resumo = resumir(ps5)
    melhores_sem = [o for o in ps5 if not o['cupom'] and o['confianca'] == 'A'][:20]
    codigos_ps5 = {o['cupom'].upper() for o in ps5 if o['cupom']} | {o['cupom_selo'] for o in ofertas if o.get('cupom_selo')} \
        | {o['cupom_citado'].upper() for o in ps5 if o.get('cupom_citado')}
    teste = cupons_para_testar(cupons, melhores_sem, codigos_ps5)

    alertas = decidir_alertas(resumo, estado, agora)
    status = []
    if alertas:
        botoes = [botao(a['oferta']) for a in alertas][:3] + [PAINEL_BOTAO]
        status.append(enviar(texto_alerta(alertas, agora, resumo), silencioso=not any(a['som'] for a in alertas), botoes=botoes))
    hora = config.RESUMO_DIARIO_HORA
    hoje = agora.strftime('%Y-%m-%d')
    primeira = 'resumo_dia' not in estado
    devido = primeira or (hora is not None and agora.hour >= hora and estado['resumo_dia'] != hoje)
    if any(r['melhor'] for r in resumo.values()) and (devido or resumo_agora):   # resumo_agora: pedido pelo botão "Run workflow"
        botoes = [botao(r['melhor']) for r in resumo.values() if r['melhor']] + [PAINEL_BOTAO]
        st = enviar(texto_resumo(resumo, agora, primeira, estado['menor']), silencioso=True, botoes=botoes)
        status.append(st)
        if devido and (st == 'TELEGRAM_ENVIADO' or st.startswith('TELEGRAM_NAO_CONFIGURADO')):   # desligado ou falha: tenta de novo depois
            estado['resumo_dia'] = hoje

    # lotes de leilão: avisa (sem som) uma vez por lote novo
    conhecidos = estado.setdefault('lotes', {})
    novos_lotes = [l for l in lotes if l['lote'] not in conhecidos and l.get('aberto', True)]
    for l in lotes:
        conhecidos[l['lote']] = hoje
    estado['lotes'] = {k: v for k, v in conhecidos.items() if v >= (agora - timedelta(days=30)).strftime('%Y-%m-%d')}
    if novos_lotes and not primeira:
        status.append(enviar(texto_leiloes(novos_lotes), silencioso=True,
                             botoes=[[{'text': f"🔨 {l['origem']}"[:40], 'url': l['link']}] for l in novos_lotes[:3]]))

    # aviso se todas as fontes falharem por 6 varreduras seguidas (o site mudou ou barrou)
    estado['falhas_seguidas'] = 0 if ps5 else estado.get('falhas_seguidas', 0) + 1
    if estado['falhas_seguidas'] == 6:
        status.append(enviar('⚠️ <b>Buscador do PS5</b>: há 6 varreduras sem achar nenhuma oferta. As lojas podem ter mudado o site.',
                             botoes=[PAINEL_BOTAO], so_dono=True))
    # saúde de cada fonte: quantas rodadas seguidas falhou (quem barra há muito tempo é tentado só de vez em quando)
    saude = estado.setdefault('saude', {})
    for nome, s in situacao.items():
        h = saude.setdefault(nome, {'falhas': 0, 'ja_funcionou': False})
        status += [enviar(t, silencioso=True, botoes=[PAINEL_BOTAO], so_dono=True) for t in avisos_da_fonte(nome, s, h, agora)]
        s['falhas_seguidas'] = h['falhas']
        s['barradas_hoje'], s['buscas_hoje'] = h['barradas'], h['buscas']
    fontes.PULAR = {n for n, h in saude.items() if h['falhas'] >= 24 and h['falhas'] % 6 != 0}
    tg = ' | '.join(status) or 'sem mensagem nesta varredura'

    gravar_historico(os.path.join(pasta, 'historico.csv'), agora, resumo)
    estado['ultima'] = agora.isoformat(timespec='seconds')
    estado['pular'] = sorted(fontes.PULAR)
    gravar_json(os.path.join(pasta, 'estado.json'), estado, copia=True)
    with open(os.path.join(pasta, 'ultimo.md'), 'w') as f:
        f.write(resumo_md(resumo, agora, alertas, situacao, fora, teste, tg, secoes, lotes))

    situacao['Cupons (Promobit)'] = {'ok': False, 'erro': '; '.join(erros_cupons)[:200], 'tipo': 'cupom'} if erros_cupons else \
        {'ok': True, 'ofertas': len(cupons), 'tipo': 'cupom'}
    barato = mais_barato(resumo)
    dados = {
        'atualizado': agora.strftime('%Y-%m-%d %H:%M'),
        'mais_barato': para_painel(barato) if barato else None,
        'modelos': [{'id': m, 'nome': MODELOS[m], 'alvo': config.ALVOS.get(m), 'alerta': m in config.MODELOS_COM_ALERTA,
                     'melhor': para_painel(r['melhor']) if r['melhor'] else None,
                     'sem': para_painel(r['sem']) if r['sem'] else None, 'com': para_painel(r['com']) if r['com'] else None,
                     'nao_confirmada': para_painel(r['nao_confirmada']) if r['nao_confirmada'] else None,
                     'recorde': estado['menor'].get(m),
                     'ofertas': [para_painel(o) for o in ps5 if o['modelo'] == m][:20]} for m, r in resumo.items()],
        'historico': ler_historico(os.path.join(pasta, 'historico.csv')),
        'cupons_teste': teste,
        'secoes': {k: [para_painel(o) for o in v[:12]] for k, v in secoes.items()},
        'leiloes': lotes[:20],
        'alertas': [{'modelo': a['modelo'], 'motivo': a['motivo'], 'som': a['som'], 'oferta': para_painel(a['oferta'])} for a in alertas],
        'fontes': situacao,
        'descartados': fora,
        'config': {'queda_minima': config.QUEDA_MINIMA, 'horas_promobit': config.HORAS_OFERTA_POSTADA,
                   'horas_grupo': config.HORAS_OFERTA_GRUPO},
    }
    if painel:
        gerar_painel(dados)
    for a in alertas:
        print(f"{'ALERTA' if a['som'] else 'CONFERIR'} {a['modelo']} {a['oferta']['preco']} {a['motivo']}")
    for m, r in resumo.items():
        print(f"MELHOR {m}: " + (descricao(r['melhor'], html_=False) if r['melhor'] else 'nada'))
        if r['nao_confirmada']:
            print(f"   NAO_CONFIRMADA {m}: " + descricao(r['nao_confirmada'], html_=False))
    for chave, lista in secoes.items():
        if lista:
            print(f"SECAO {chave}: {MODELOS[lista[0]['modelo']]} " + descricao(lista[0], html_=False))
    for l in lotes[:5]:
        print(f"LEILAO {l['origem']}: {l['descricao'][:80]}" + (f" — {l['lance_tipo']} {brl(l['lance'])}" if l.get('lance') else ''))
    print('FONTES', ' '.join(f"{n}={'ok' if s['ok'] else 'falhou'}" for n, s in situacao.items()))
    print(tg)
    return dados


# ---------------------------------------------------------------- auto-checagem
def _teste():
    import tempfile
    # a auto-checagem não depende dos valores que o dono ajusta no config.py (alvos, aviso)
    config.ALVOS = {'leitor': 3799, 'digital': 3499, 'pro': 6299}
    config.AVISAR_ABAIXO = None
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
        ('Bundle Console PlayStation 5 Slim + 2 Jogos — Modelo sem leitor', 3999.9, 'digital'),
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
        ('Luminária Controle Playstation 5 Gamer Light', 3080, None),
    ]
    for titulo, preco, esperado, *produto in casos:
        assert classificar(titulo, preco, *produto)[0] == esperado, (titulo, classificar(titulo, preco, *produto), esperado)
    assert avaliar('Sony PlayStation 5 Slim Digital Edition Console - 825GB - Middle East Version', 4369)['condicao'] == 'importado'
    assert avaliar('Console PlayStation 5 Slim Digital Open Box', 2400) == {'modelo': 'digital', 'condicao': 'openbox', 'motivo': ''}
    assert 'com jogo' in detalhes('PlayStation®5 Slim Digital 825GB – Pacote ASTRO BOT e Gran Turismo 7')
    assert detalhes('PlayStation®5 Pro') == ''

    assert fontes.regra_cupom('Cupom Mercado Livre: 30% OFF acima de R$ 2.699 (até R$ 300) em Eletrônicos') == {'pct': 30, 'minimo': 2699, 'teto': 300}
    assert fontes.regra_cupom('Use cupom KaBuM! e tenha desconto de R$ 100') == {'valor': 100}
    assert fontes.regra_cupom('O momento chegou: Aplique cupom KaBuM! e ganhe R$ 300 OFF') == {'valor': 300}
    assert fontes.regra_cupom('Garanta R$50 OFF com cupom nas compras acima de R$349') == {'valor': 50, 'minimo': 349}
    assert fontes.regra_cupom('▪️ R$500 OFF em compras acima de R$2.500 🎯 Usem o cupom: «MP500PS»') == {'valor': 500, 'minimo': 2500}
    assert fontes.desconto({'pct': 30, 'minimo': 2699, 'teto': 300}, 4000) == 300
    assert fontes.desconto({'pct': 30, 'minimo': 2699, 'teto': 300}, 2000) == 0
    assert fontes.desconto({'pct': 5}, 4000) == 200

    # ---- leitor de texto livre: posts reais dos canais (com <br> e <code> como o Telegram manda)
    def um(post):
        r = texto.ler_post(post)
        return [(o['preco'], o['cupom'], o['loja'], o['ressalvas']) for o in r['ofertas']], r
    posts = [
        ("[Mercado Livre] Console PlayStation 5 Slim Digital<br><br>👉 jogobara.to/X8XiE<br>🎟 Cupom: <code>MP500PS</code><br>"
         "• R$ 3.759,00 no cartão Mercado Pago<br>• Frete Grátis", [(3759.0, 'MP500PS', 'Mercado Livre', ['cartão Mercado Pago'])]),
        ("PS5 COM DESCONTO NA AMAZON<br><br>Console PlayStation 5 Slim Digital + 2 Jogos<br>por R$3.905,07 à vista no Pix<br>"
         "ou em até 12x de R$349,99 SEM JUROS<br>&gt;&gt; https://link.amazon/B0jbAhZlm", [(3905.07, None, 'Amazon', [])]),
        ("🔥🎮 PlayStation 5 Slim Digital (825 GB) +Astro Bot e Grand Turismo 7<br><br><s>De R$ 4.599</s><br>Por R$ 3.884,07 no Pix<br>"
         "🏷 Aplique o cupom <code>300TAO</code> (apenas para assinantes Prime!)<br><br>👉 Aproveite esta oferta na Amazon: https://tecno.click/8mg",
         [(3884.07, '300TAO', 'Amazon', ['assinante Prime'])]),
        ("[KaBuM!] Bundle Console PlayStation 5 Slim + ASTRO BOT + Gran Turismo 7<br><br>Modelo Digital<br>👉 https://jogobara.to/5qUDz<br>"
         "🎟 Cupom: <code>JOGAMUITO</code><br>• R$ 3.905,07 à vista<br>• R$ 4.199,00 em até 10x<br><br>Modelo com Leitor<br>"
         "👉 https://jogobara.to/Mk6Sz<br>🎟 Cupom: <code>JOGAMUITO</code><br>• R$ 4.463,07 à vista<br>• R$ 4.799,00 em até 10x",
         [(3905.07, 'JOGAMUITO', 'KaBuM!', []), (4463.07, 'JOGAMUITO', 'KaBuM!', [])]),
        ("PS5 PARCELADINHO<br>não fique de fora do GTA VI<br><br>PlayStation 5 Slim Digital 825GB – Pacote ASTRO BOT e Gran Turismo 7<br><br>"
         "R$ 3.999 em 12x sem juros<br>https://link.amazon/B07Kt0YMG", [(3999.0, None, 'Amazon', ['parcelado'])]),
        ("➡️ Console PlayStation 5 Slim Edição Digital 825 Gb Ps5<br><br>✅ R$ 3.759 😱😱<br>🏷 Cupom: <code>MP500PS</code><br><br>"
         "🛒 https://nerdofertas.com/o/05k405le86zl2o", [(3759.0, 'MP500PS', '?', [])]),
        ("PS5 Console PlayStation 5 Slim Disk 1TB com Leitor de Disco 1 DualSense 4K 120fps Branco<br><br>Por R$ 3.644,10 na Vivo<br><br>"
         "Ver oferta: https://promoby.me/h11n57vaphx5", [(3644.1, None, 'Vivo', [])]),
        ("💥 Console PlayStation 5 Com Leitor + 2 Jogos + Frete Grátis<br><br>✅ Por: R$4.274,90 [no pix]<br>🤏🏽 [em até 12x sem juros]<br><br>"
         "🏪 Loja: #Amazon", [(4274.9, None, 'Amazon', [])]),
        ("PlayStation 5 Pro 2TB<br>10x de R$ 649,90 sem juros — KaBuM!", [(6499.0, None, 'KaBuM!', ['parcelado'])]),
        ("PS5 Slim Digital por R$ 3.499 + R$ 350 de cashback na Americanas", [(3499.0, None, 'Americanas', [])]),
        ("Controle Sony DualSense PS5, PC, Mobile Edição Limitada Wolverine 💰 R$ 417,00 📍 KaBuM!", []),
        ("Grand Theft Auto VI - PlayStation 5<br>R$ 3.000", []),
        ("Volante Logitech G29 para PS5 e PC por R$ 2.899 na Amazon", []),
        ("Console PS5 Slim Digital de R$ 3.199 a R$ 3.499 na Shopee", []),
        ("❌ ENCERRADA — PS5 Slim Digital por R$ 2.999 no Magalu", []),
        ("Sorteio de um Console PS5 Slim Digital avaliado em R$ 3.999! Concorra", []),
    ]
    for post, esperado in posts:
        achado, r = um(post)
        assert achado == esperado, (post[:60], achado, r['motivo'])
    cup = texto.ler_post("🔥 Novos Cupons Mercado Livre Cartão Mercado Pago!<br>▪️ R$500 OFF em compras acima de R$2.500 🎯 Usem o cupom: "
                         "<code>MP500PS</code><br>🛒 Itens de PS5 e GTA VI")['cupons']
    assert [(c['codigo'], c['loja']) for c in cup] == [('MP500PS', 'Mercado Livre')], cup

    # ---- fontes simuladas (sem internet): Promobit, Gatry, VTEX e Telegram
    baixar_de_verdade = fontes.baixar

    def com_pagina(pagina, f, *a, **kw):
        fontes.baixar = lambda url, **k: pagina(url) if callable(pagina) else pagina
        try:
            return f(*a, **kw)
        finally:
            fontes.baixar = baixar_de_verdade

    pagina = {'props': {'pageProps': {'serverOffers': {'offers': [
        {'offerId': i, 'offerTitle': 'PlayStation 5 Slim Digital 825GB', 'offerPrice': 3800 + i, 'offerPriceType': 'NORMAL',
         'offerStatusName': 'APPROVED', 'storeName': 'X', 'offerSlug': f's-{i}', 'offerPublished': '2026-11-17T07:20:41-0300',
         'offerCoupon': c} for i, c in enumerate(['AEMEGA19', 'R$300 OFF', 'Lojas Físicas / Parcelado', None])]}}}}
    p = com_pagina(f'<script id="__NEXT_DATA__">{json.dumps(pagina)}</script>', fontes.promobit)
    assert [(x['cupom'], x['obs']) for x in p] == [('AEMEGA19', 'postado no Promobit'), ('cupom R$300 OFF (na página)', 'postado no Promobit'),
                                                  (None, 'postado no Promobit: Lojas Físicas / Parcelado'), (None, 'postado no Promobit')], p
    assert p[2]['ressalvas'] == ['loja física', 'parcelado'] and p[0]['tipo'] == 'postada'

    g = com_pagina('<ul><li><a href="/promocoes/a0a2aa88-d161-46f3-bcd4-c27b44ac26dd/calca">Calça Moletom</a> — R$ 63,03 · Amazon</li>'
                   '<li><a href="/promocoes/b0a2aa88-d161-46f3-bcd4-c27b44ac26dd/ps5">Console PlayStation 5 Slim Digital 825GB &#43; 2 Jogos</a>'
                   ' — R$ 3.905,07 · Mercadolivre</li></ul>', fontes.gatry)
    assert [(x['preco'], x['loja'], x['tipo']) for x in g] == [(3905.07, 'Mercado Livre', 'postada')], g

    catalogo = json.dumps([{'productId': '7', 'productName': 'Console PlayStation 5 Slim Digital 825GB', 'link': 'https://loja/ps5/p',
                            'items': [{'sellers': [{'sellerName': 'Loja Teste LTDA', 'commertialOffer': {
                                'Price': 4522.0, 'AvailableQuantity': 3, 'Installments': [
                                    {'NumberOfInstallments': 1, 'TotalValuePlusInterestRate': 4284.0, 'PaymentSystemName': 'Pix'},
                                    {'NumberOfInstallments': 10, 'TotalValuePlusInterestRate': 4522.0, 'PaymentSystemName': 'Visa'}]}},
                                {'sellerName': 'Outro', 'commertialOffer': {'Price': 4000.0, 'AvailableQuantity': 0}}]}]}])
    v = com_pagina(catalogo, fontes.vtex, {'nome': 'Loja Teste', 'base': 'https://loja'})
    assert [(x['preco'], x['loja'], x['obs'], x['id']) for x in v] == [(4284.0, 'Loja Teste', 'à vista no Pix', 'lojateste-7')], v

    hora_tg = '2026-11-20T19:03:00+00:00'
    canal_html = ('<div class="tgme_channel_info"></div><div class="tgme_widget_message_wrap"><div data-post="canalx/10"></div>'
                  '<div class="tgme_widget_message_text js-message_text" dir="auto">' + posts[0][0] + '</div>'
                  f'<time datetime="{hora_tg}"></time></div>')
    agora_tg = datetime(2026, 11, 20, 17, 0, tzinfo=fontes.BRT)
    tg = com_pagina(canal_html, fontes.telegram, [{'canal': 'canalx', 'nome': 'Canal X'}], agora_tg)
    assert [(x['preco'], x['cupom'], x['loja'], x['tipo'], x['link'], x['canal']) for x in tg if 'preco' in x] == \
        [(3759.0, 'MP500PS', 'Mercado Livre', 'grupo', 'https://t.me/canalx/10', 'Canal X')], tg

    lento = lambda: __import__('time').sleep(3) or []
    itens, sit = fontes.coletar({'rápida': lambda: [{'preco': 1}], 'barrada': lambda: (_ for _ in ()).throw(fontes.Bloqueado('HTTP 403')),
                                 'lenta': lento}, orcamento=1, folga=0)
    assert sit['rápida']['ok'] and not sit['barrada']['ok'] and 'passou do tempo' in sit['lenta']['erro'] and len(itens) == 1, sit

    # ---- varredura completa com fontes de mentira
    agora = datetime(2026, 11, 13, 10, 0, tzinfo=fontes.BRT)
    TIPOS = {'Zoom': 'comparador', 'Promobit': 'postada', 'Telegram': 'grupo'}

    def of(fonte, loja, titulo, preco, id_, **kw):
        return {**fontes.oferta(fonte, loja, titulo, preco, f'https://exemplo/{id_}', tipo=TIPOS.get(fonte, 'loja'), id=id_), **kw}

    def grupo(canal, loja, titulo, preco, minutos, **kw):
        pub = (agora + timedelta(minutes=minutos)).strftime('%Y-%m-%dT%H:%M:%S%z')
        return of('Telegram', loja, titulo, preco, f'tg-{canal}-{minutos}-{preco}', publicado=pub, origem=kw.pop('origem', canal),
                  canal=canal, **kw)

    def coleta(digital_kabum=4404.28, cupom_shopee=3991.88, extras=()):
        def f():
            return [of('KaBuM!', 'KaBuM! · Magalu', 'Console Sony Playstation 5 Slim Edição Digital, 1TB, 2 Jogos', digital_kabum,
                       'kabum-939943', cupom_selo='MAGALUPS5', obs='à vista no Pix'),
                    of('KaBuM!', 'KaBuM!', 'Console PlayStation 5 Pro Sony, SSD 2TB', 7399, 'kabum-636960', obs='à vista no Pix'),
                    of('Zoom', 'KaBuM!', 'Console PlayStation 5 Pro Sony, SSD 2TB', 7399, 'zoom-1'),
                    of('Zoom', 'Magazine Luiza', 'Console PlayStation 5 Slim Disk 1TB Físico', 4530.55, 'zoom-2'),
                    of('Zoom', 'Magazine Luiza', 'Console PlayStation 5 Slim Digital 825GB', 4131.55, 'zoom-3'),
                    of('Amazon', 'Amazon', 'Sony PlayStation 5 Slim Digital Edition Console - Middle East Version', 4369.9, 'amazon-ksa'),
                    of('KaBuM!', 'KaBuM!', 'Console PlayStation 5 Slim Digital 825GB', 3650, 'kabum-ob', openbox=True),
                    of('Promobit', 'Shopee', 'PlayStation 5 Edição Digital 825GB 1 Controle Branco Sony', cupom_shopee, 'promobit-1',
                       cupom='11DO11TECH', cupom_origem='postado', publicado='2026-11-13T08:38:00-0300', origem='promobit'),
                    of('Promobit', 'Mercado Livre', 'Unidade De Disco Playstation 5 Slim', 569.48, 'promobit-2',
                       publicado='2026-11-13T09:25:00-0300'),
                    of('Promobit', 'Aliexpress', 'PlayStation 5 Edição Digital 825GB', 3800, 'promobit-velho',
                       cupom='VELHO', cupom_origem='postado', publicado='2026-11-10T09:25:00-0300'),
                    {'lote': 'Lance360-1', 'origem': 'Lance360', 'descricao': 'VIDEO GAME PS5 825GB', 'lance': 980.0,
                     'lance_tipo': 'lance inicial', 'encerra': 'Encerramento em 2 dias', 'local': 'SOROCABA, SP', 'aberto': True,
                     'link': 'https://exemplo/lote/1'},
                    {'codigo': 'MP500PS', 'loja': 'Mercado Livre', 'titulo': 'R$500 OFF em compras acima de R$2.500 cupom MP500PS',
                     'regra': {'valor': 500, 'minimo': 2500}, 'validade': None, 'origem': 'canal PromoTop', 'link': 'https://t.me/x/1', 'ps5': True},
                    *extras], \
                {'KaBuM!': {'ok': True, 'ofertas': 2}, 'Amazon': {'ok': False, 'erro': 'Bloqueado: HTTP 503'}}
        return f

    def cupons(lojas_):
        return [{'loja': 'KaBuM!', 'codigo': 'MAGALUPS5', 'titulo': 'Use cupom KaBuM! e tenha desconto de R$ 100', 'validade': None,
                 'regra': {'valor': 100}},
                {'loja': 'KaBuM!', 'codigo': 'JBL25', 'titulo': '25% de Desconto em produtos JBL', 'validade': None, 'regra': {'pct': 25}},
                {'loja': 'Magazine Luiza', 'codigo': 'LU300', 'titulo': 'Cupom Magazine Luiza concede desconto de R$ 300',
                 'validade': None, 'regra': {'valor': 300}},
                {'loja': 'Magazine Luiza', 'codigo': 'GAMES10', 'titulo': '10% OFF em Games e Consoles no Magalu',
                 'validade': None, 'regra': {'pct': 10}},
                {'loja': 'Magazine Luiza', 'codigo': 'relampago20', 'titulo': '20% OFF em todo o site',   # testado: não vale
                 'validade': None, 'regra': {'pct': 20}}], []

    enviadas = []

    def html_do_telegram(t):
        """O Telegram recusa a mensagem inteira se o HTML tiver tag fora da lista ou mal fechada."""
        abertas = []
        for fecha, nome in re.findall(r'<(/?)([a-z]+)[^>]*>', t):
            assert nome in ('b', 'i', 'a', 'code'), f'tag que o Telegram não aceita: {nome}'
            if fecha:
                assert abertas and abertas.pop() == nome, f'tag fechada fora de ordem: {nome}'
            else:
                abertas.append(nome)
        assert not abertas, f'tag sem fechar: {abertas}'
        assert '<' not in re.sub(r'</?(?:b|i|code|a(?: href="[^"<>]*")?)>', '', t), 'sinal < sem escapar'

    def enviar(texto_, silencioso=False, botoes=(), so_dono=False):
        html_do_telegram(texto_)
        assert so_dono == texto_.startswith('⚠️ <b>Buscador do PS5</b>'), 'aviso de manutenção vai só para o dono; o resto, para todos'
        assert len(texto_) < 4096, 'o Telegram corta mensagem com mais de 4096 caracteres'
        if 'R$' in texto_ and not texto_.startswith('⚠️'):
            primeira_ = re.sub(r'<[^>]+>', '', texto_.split('\n')[0])
            assert re.match(r'^\W+ R\$ [\d.,]+ · ', primeira_), f'a notificação tem que começar pelo preço: {primeira_}'
        enviadas.append((texto_, silencioso, botoes))
        return 'TELEGRAM_ENVIADO'

    with tempfile.TemporaryDirectory() as pasta:
        d = rodar(coleta(), cupons, enviar, pasta, agora)
        dig = d['modelos'][1]
        assert dig['com']['preco'] == 3991.88 and dig['com']['cupom'] == '11DO11TECH' and dig['com']['confianca'] == 'B', dig['com']
        assert dig['sem']['preco'] == 4131.55 and dig['melhor']['cupom'] == '11DO11TECH', dig['sem']
        assert 'confira se é a versão nacional' in dig['com']['ressalvas']
        kab = next(o for o in dig['ofertas'] if o['fonte'] == 'KaBuM!')
        assert kab['preco'] == 4304.28 and kab['cupom'] == 'MAGALUPS5' and kab['preco_sem_cupom'] == 4404.28, kab
        assert not any(o['cupom'] == 'VELHO' for o in dig['ofertas']), 'oferta do Promobit velha devia sair'
        pro = d['modelos'][2]
        assert len(pro['ofertas']) == 1 and pro['ofertas'][0]['n_vistos'] == 2, 'KaBuM! direto e pelo Zoom no mesmo preço viram uma oferta só'
        assert d['mais_barato']['preco'] == 3991.88
        assert [o['preco'] for o in d['secoes']['openbox']] == [3650] and [o['preco'] for o in d['secoes']['importados']] == [4369.9]
        assert all(o['preco'] not in (3650, 4369.9) for m in d['modelos'] for o in m['ofertas']), 'open box e importado ficam fora do principal'
        assert [l['lote'] for l in d['leiloes']] == ['Lance360-1']
        teste = {loja: [c['codigo'] for c in v['cupons']] for loja, v in d['cupons_teste'].items() if v['cupons']}
        assert teste == {'Magazine Luiza': ['GAMES10'], 'KaBuM!': ['MAGALUPS5'], 'Mercado Livre': ['MP500PS']}, teste   # JBL25, LU300 e o testado ficam fora
        assert d['cupons_teste']['Magazine Luiza']['cupons'][0]['preco_estimado'] == 3718.4
        assert len(enviadas) == 1 and enviadas[0][1] and 'ligado' in enviadas[0][0], 'primeira varredura: só o resumo, sem som'
        assert enviadas[0][0].startswith('🏆 <b>R$ 3.991,88 · PS5 Digital</b> na <a href="'), 'resumo começa pelo mais barato'
        assert [b[0]['text'] for b in enviadas[0][2]][0] == '💿 Com leitor · R$ 4.530,55 · Magazine Luiza', enviadas[0][2]
        assert not d['alertas']

        # 2ª varredura, 1h depois: Digital cai para o alvo -> alerta com som
        enviadas.clear()
        d = rodar(coleta(cupom_shopee=3450), cupons, enviar, pasta, agora + timedelta(hours=1))
        assert [a['modelo'] for a in d['alertas']] == ['digital'] and 'alvo' in d['alertas'][0]['motivo'], d['alertas']
        assert len(enviadas) == 1 and not enviadas[0][1] and '11DO11TECH' in enviadas[0][0]
        assert enviadas[0][0].startswith('🔥 <b>R$ 3.450 · PS5 Digital</b> na '), 'alerta começa pelo preço que disparou'
        assert 'Outros modelos: 💿 Com leitor R$ 4.530,55' in enviadas[0][0], enviadas[0][0]

        # 3ª: mesmo preço -> não repete
        enviadas.clear()
        d = rodar(coleta(cupom_shopee=3450), cupons, enviar, pasta, agora + timedelta(hours=2))
        assert not d['alertas'] and not enviadas

        # 4ª: Pro cai R$100 (recorde, acima do alvo) -> alerta de menor preço (com o Pro ligado nos alertas)
        def coleta_pro(preco):
            def c():
                ofs, sit = coleta(cupom_shopee=3450)()
                return [dict(o, preco=preco) if '636960' in o.get('id', '') else o for o in ofs if o.get('id') != 'zoom-1'], sit
            return c
        com_alerta = config.MODELOS_COM_ALERTA
        try:
            config.MODELOS_COM_ALERTA = com_alerta + ['pro']
            d = rodar(coleta_pro(7299), cupons, enviar, pasta, agora + timedelta(hours=3))
        finally:
            config.MODELOS_COM_ALERTA = com_alerta
        assert [a['modelo'] for a in d['alertas']] == ['pro'] and 'menor preço' in d['alertas'][0]['motivo'], d['alertas']
        # com a configuração do dono (Pro fora do orçamento), novo recorde do Pro não toca
        d = rodar(coleta_pro(7199), cupons, enviar, pasta, agora + timedelta(hours=4))
        assert not d['alertas'], d['alertas']

        # dia seguinte, 9h: resumo diário sem som
        enviadas.clear()
        rodar(coleta(cupom_shopee=3450), cupons, enviar, pasta, agora + timedelta(hours=23))
        assert len(enviadas) == 1 and enviadas[0][1] and enviadas[0][0].startswith('🏆 <b>R$ 3.450 · PS5 Digital</b>'), enviadas
        assert '📉 recorde R$ 7.199 em ' in enviadas[0][0] and '🎯 alvo R$ 3.799 (faltam R$ 732)' in enviadas[0][0], enviadas[0][0]
        with open(os.path.join(pasta, 'historico.csv')) as f:
            assert len(f.read().strip().splitlines()) == 7   # uma linha a mais: a rodada do Pro fora dos alertas

    # ---- grupos do Telegram: um canal sozinho não confirma; dois canais independentes confirmam e tocam uma vez
    with tempfile.TemporaryDirectory() as pasta:
        leitor = 'Console PlayStation 5 Slim com Leitor 1TB'
        rodar(coleta(), cupons, enviar, pasta, agora)
        enviadas.clear()
        so_um = [grupo('nerdofertas', 'Mercado Livre', leitor, 3700, 50, cupom='MP500PS', cupom_origem='postado'),
                 grupo('superofertasbr', '?', leitor, 3700, 51, origem='nerdofertas')]   # mesma rede: conta como um
        d = rodar(coleta(extras=so_um), cupons, enviar, pasta, agora + timedelta(hours=1))
        lei = d['modelos'][0]
        assert lei['melhor']['preco'] == 4530.55 and lei['nao_confirmada']['preco'] == 3700 and lei['nao_confirmada']['confianca'] == 'C'
        assert lei['nao_confirmada']['n_vistos'] == 2, 'o post sem loja entra no mesmo grupo'
        # modo caçador (até config.CACADOR_ATE): não confirmada abaixo do alvo toca com som
        assert [(a['modelo'], a['som']) for a in d['alertas']] == [('leitor', True)] and not enviadas[-1][1], 'caçador: não confirmada toca'
        dois = so_um + [grupo('jogobarato', 'Mercado Livre', leitor, 3700, 55, cupom='MP500PS', cupom_origem='postado')]
        enviadas.clear()
        d = rodar(coleta(extras=dois), cupons, enviar, pasta, agora + timedelta(hours=1, minutes=30))
        lei = d['modelos'][0]
        assert lei['melhor']['preco'] == 3700 and lei['melhor']['confianca'] == 'B' and lei['melhor']['n_vistos'] == 3, lei['melhor']
        assert [(a['modelo'], a['som']) for a in d['alertas']] == [('leitor', True)] and 'MP500PS' in enviadas[-1][0] and not enviadas[-1][1]
        enviadas.clear()
        d = rodar(coleta(extras=dois), cupons, enviar, pasta, agora + timedelta(hours=2))
        assert not d['alertas'], 'a mesma oferta não toca de novo'
        # sem modelo no texto: fica não confirmada mesmo em dois canais; preço absurdo também
        vagos = [grupo('pechinchou', 'Amazon', 'PlayStation 5 Slim', 3600, 60), grupo('promotop', 'Amazon', 'PlayStation 5 Slim', 3600, 61),
                 grupo('xet', 'Shopee', 'Console PlayStation 5 Slim Digital', 1999, 62), grupo('cj', 'Shopee', 'Console PlayStation 5 Slim Digital', 1999, 63)]
        d = rodar(coleta(extras=vagos), cupons, enviar, pasta, agora + timedelta(hours=2, minutes=10))
        assert all(o['confianca'] == 'C' for m in d['modelos'] for o in m['ofertas'] if o['preco'] in (3600, 1999))
        # caçador: post que não diz o modelo ("PS5 Slim") não toca com som, só chega como aviso
        vago = lambda a: 'o post não diz o modelo' in (a['oferta'].get('ressalvas') or [])
        assert any(vago(a) and not a['som'] for a in d['alertas']) and not any(vago(a) and a['som'] for a in d['alertas']), d['alertas']
        # estado antigo (chave 'promobit') ainda carrega
        gravar_json(os.path.join(pasta, 'estado.json'), {'promobit': {'promobit-9': of('Promobit', 'Havan', 'PlayStation 5 Slim Digital 825GB',
                    3799.99, 'promobit-9', publicado='2026-11-13T09:00:00-0300')}, 'resumo_dia': '2026-11-13'})
        d = rodar(lambda: ([], {}), cupons, enviar, pasta, agora + timedelta(hours=3))
        assert d['modelos'][1]['melhor']['preco'] == 3799.99 and 'pode ter acabado' in d['modelos'][1]['melhor']['obs']

    # ---- aviso abaixo de um preço fixo (config.AVISAR_ABAIXO), além do alvo de cada modelo
    def oferta_t(preco, tipo='loja', **kw):
        return {'preco': preco, 'tipo': tipo, 'chave': f'k{preco}', 'loja': 'X', 'cupom': None, 'titulo': 't', 'link': 'l',
                'idade_h': 0, 'ressalvas': [], **kw}
    alvos = config.ALVOS
    try:
        config.ALVOS, config.AVISAR_ABAIXO = {'digital': 3299, 'leitor': 3699}, 3600
        a = decidir_alertas({'digital': {'melhor': oferta_t(3550), 'nao_confirmada': None}}, {}, agora)
        assert [(x['modelo'], x['som']) for x in a] == [('digital', True)] and 'abaixo de R$ 3.600' in a[0]['motivo'], a
        a = decidir_alertas({'digital': {'melhor': oferta_t(3250), 'nao_confirmada': None}}, {}, agora)
        assert 'abaixo do alvo de R$ 3.299' in a[0]['motivo'], a
        assert not decidir_alertas({'digital': {'melhor': oferta_t(3650), 'nao_confirmada': None}}, {}, agora)
        a = decidir_alertas({'digital': {'melhor': oferta_t(3905), 'nao_confirmada': oferta_t(3550, 'grupo')}}, {}, agora)
        assert [(x['modelo'], x['som']) for x in a] == [('digital', True)] and 'NÃO confirmada' in a[0]['motivo'], a
        config.AVISAR_ABAIXO = None   # desligado: só o alvo vale
        assert not decidir_alertas({'digital': {'melhor': oferta_t(3550), 'nao_confirmada': None}}, {}, agora)
    finally:
        config.ALVOS, config.AVISAR_ABAIXO = alvos, None

    # ---- fora do modo caçador: a mesma não confirmada volta a chegar sem som; guarda do workflow de 7 para 25 min
    assert cacador(datetime(2026, 11, 30, 23, 59, tzinfo=fontes.BRT)) and not cacador(datetime(2026, 12, 1, 0, 0, tzinfo=fontes.BRT))
    assert guarda_min(datetime(2026, 11, 27, tzinfo=fontes.BRT)) == 7 and guarda_min(datetime(2026, 12, 2, tzinfo=fontes.BRT)) == 25
    ate = config.CACADOR_ATE
    try:
        config.CACADOR_ATE = None   # desligado no config.py: sem modo caçador, sem erro
        assert not cacador(agora) and guarda_min(agora) == 25
        config.CACADOR_ATE = datetime(2026, 11, 1).date()
        with tempfile.TemporaryDirectory() as pasta:
            rodar(coleta(), cupons, enviar, pasta, agora)
            enviadas.clear()
            d = rodar(coleta(extras=so_um), cupons, enviar, pasta, agora + timedelta(hours=1))
            assert [(a['modelo'], a['som']) for a in d['alertas']] == [('leitor', False)] and enviadas[-1][1], 'fora do caçador: sem som'
    finally:
        config.CACADOR_ATE = ate

    # ---- Telegram: só o bot do PS5; sem o segredo dele, nada é enviado (nem pelo bot das passagens)
    amb = dict(os.environ)
    try:
        os.environ.update(TELEGRAM_TOKEN='123456:' + 'A' * 35, TELEGRAM_CHAT_ID='11111111, 22222222', PS5_ENVIAR='1')
        os.environ.pop('TELEGRAM_TOKEN_PS5', None)
        os.environ.pop('TELEGRAM_CHAT_ID_PS5', None)
        assert telegram('x').startswith('TELEGRAM_NAO_CONFIGURADO') and _token() == '' and _chats() == ['11111111']
        os.environ['TELEGRAM_TOKEN_PS5'] = ' token: 654321:' + 'B' * 35 + ' '
        assert _token() == '654321:' + 'B' * 35
        # quem se inscreveu pelo /start no bot do PS5 entra depois da lista fixa, sem repetir
        import atendimento
        with tempfile.TemporaryDirectory() as d:
            assert _chats(d) == ['11111111']
            atendimento.gravar_inscritos(_token(), os.path.join(d, 'assinantes.cif'), ['11111111', '33333333'])
            assert _chats(d) == ['11111111', '33333333'], _chats(d)
        os.environ['PS5_ENVIAR'] = '0'   # fora da main: não envia
        assert telegram('x') == 'TELEGRAM_DESLIGADO'
    finally:
        os.environ.clear()
        os.environ.update(amb)
    assert '&lt;b&gt;' in descricao({'preco': 1, 'loja': '<b>x', 'cupom': None, 'tipo': 'loja', 'obs': ''})
    base = {'modelo': 'digital', 'confianca': 'A', 'loja': 'Amazon', 'link': 'https://x'}
    r = resumir([dict(base, preco=3905, cupom='JOGAMUITO'), dict(base, preco=3905.07, cupom=None)])['digital']
    assert r['melhor']['cupom'] is None, 'cupom que economiza R$ 0,07 não vira o melhor preço'
    r = resumir([dict(base, preco=3800, cupom='JOGAMUITO'), dict(base, preco=3905.07, cupom=None)])['digital']
    assert r['melhor']['cupom'] == 'JOGAMUITO'
    lotes_ = [{'origem': 'HISA', 'descricao': 'Lote <PS5>', 'lance': 1500, 'lance_tipo': 'lance atual', 'link': 'https://x/2'},
              {'origem': 'Lance360', 'descricao': 'VIDEO GAME PS5', 'lance': 980, 'lance_tipo': 'lance inicial', 'link': 'https://x/1'},
              {'origem': 'Bolsa', 'descricao': 'PS5 sem lance', 'link': ''}]
    t = texto_leiloes(lotes_)
    html_do_telegram(t)
    assert t.startswith('🔨 <b>R$ 980 · PS5 em leilão</b>') and t.index('Lance360') < t.index('HISA') < t.index('Bolsa'), t
    with tempfile.TemporaryDirectory() as pasta:
        enviadas.clear()
        rodar(coleta(), cupons, enviar, pasta, agora)                                  # 1ª: resumo "ligado"
        enviadas.clear()
        rodar(coleta(), cupons, enviar, pasta, agora + timedelta(minutes=30), resumo_agora=True)
        assert len(enviadas) == 1 and enviadas[0][0].startswith('🏆') and 'ligado' not in enviadas[0][0], 'resumo pedido na hora'
        enviadas.clear()
        rodar(coleta(), cupons, enviar, pasta, agora + timedelta(hours=24))           # dia seguinte: o resumo do dia sai
        assert len(enviadas) == 1 and enviadas[0][0].startswith('🏆'), 'o pedido avulso não conta como resumo do dia'

    # ---- saúde das fontes: bloqueio anti-robô é rotina e não vira mensagem; fonte quebrada avisa o dono, no máximo a cada 3 dias
    t0 = datetime(2026, 11, 21, 8, 0, tzinfo=fontes.BRT)
    ok_, barrou, quebrou = {'ok': True, 'ofertas': 3}, {'ok': False, 'erro': 'Bloqueado: HTTP 503 (barrou o robô)'}, {'ok': False, 'erro': "KeyError: 'preco'"}

    def dia_de(h, resultados, inicio):
        """Passa uma sequência de rodadas (uma a cada 10 minutos) pela saúde h e devolve todos os avisos gerados."""
        return [a for i, r in enumerate(resultados) for a in avisos_da_fonte('Amazon', dict(r), h, inicio + timedelta(minutes=10 * i))]

    # a Amazon de verdade: funciona, é barrada 8 vezes, volta, é barrada mais 10... nenhum aviso (antes saía um a cada 6 falhas seguidas)
    h = {'falhas': 0, 'ja_funcionou': False}
    assert dia_de(h, [ok_] + [barrou] * 8 + [ok_] + [barrou] * 10 + [ok_], t0) == [], 'bloqueio que vai e volta não gera aviso'
    assert (h['buscas'], h['barradas'], h['falhas'], h['dia']) == (21, 18, 0, '2026-11-21'), h
    # fonte que quebrou (erro que não é bloqueio): um aviso na 6ª falha seguida, com o motivo; nas falhas seguintes, nada
    h = {'falhas': 0, 'ja_funcionou': True}
    avisos = dia_de(h, [quebrou] * 9, t0)
    assert avisos == ["⚠️ <b>Buscador do PS5</b>: a fonte Amazon parou de responder há 6 rodadas (KeyError: &#x27;preco&#x27;)."], avisos
    # voltou e quebrou de novo no dia seguinte: ainda dentro dos 3 dias, não repete; passado o prazo, avisa de novo
    assert dia_de(h, [ok_] + [quebrou] * 7, t0 + timedelta(days=1)) == [], 'no máximo um aviso por fonte a cada 3 dias'
    assert len(dia_de(h, [ok_] + [quebrou] * 7, t0 + timedelta(days=4))) == 1
    # fonte que nunca funcionou não avisa (ainda não entrou em serviço)
    assert dia_de({'falhas': 0, 'ja_funcionou': False}, [quebrou] * 8, t0) == []
    # um dia inteiro barrada em todas as buscas: um aviso na virada do dia; dia com alguma busca boa, ou com poucas buscas, não
    h = {'falhas': 0, 'ja_funcionou': True}
    assert dia_de(h, [barrou] * 30, t0) == []
    avisos = dia_de(h, [barrou], t0 + timedelta(days=1))
    assert avisos == ['⚠️ <b>Buscador do PS5</b>: a fonte Amazon barrou o buscador em todas as 30 buscas de ontem. Sigo tentando de vez em quando.'], avisos
    assert dia_de(h, [barrou] * 30, t0 + timedelta(days=1, minutes=10)) == [] and dia_de(h, [barrou], t0 + timedelta(days=2)) == [], 'não repete dentro de 3 dias'
    h = {'falhas': 0, 'ja_funcionou': True}
    assert dia_de(h, [barrou] * 15 + [ok_] + [barrou] * 14, t0) == [] and dia_de(h, [barrou], t0 + timedelta(days=1)) == []
    h = {'falhas': 0, 'ja_funcionou': True}
    assert dia_de(h, [barrou] * 5, t0) == [] and dia_de(h, [barrou], t0 + timedelta(days=1)) == []
    # fonte pausada (tentada só de vez em quando) conta como barrada, sem aviso
    h = {'falhas': 30, 'ja_funcionou': True}
    assert dia_de(h, [{'ok': False, 'erro': 'pausada: vem barrando o robô (tenta de novo de vez em quando)', 'pausada': True}] * 3, t0) == [] and h['barradas'] == 3 and h['falhas'] == 33
    # o resumo da varredura mostra a proporção do dia
    md = resumo_md({}, t0, [], {'Amazon': {**barrou, 'barradas_hoje': 18, 'buscas_hoje': 21}, 'KaBuM!': {**ok_, 'barradas_hoje': 0, 'buscas_hoje': 21}}, {}, {}, 'x', {}, [])
    assert '- Amazon: falhou (Bloqueado: HTTP 503 (barrou o robô)) · barrada em 18 de 21 buscas hoje\n' in md and '- KaBuM!: ok, 3 itens\n' in md, md
    print('OK: auto-checagem passou')


if __name__ == '__main__':
    if '--teste' in sys.argv:
        _teste()
    elif '--teste-telegram' in sys.argv:
        r = telegram('✅ Teste do buscador do PS5: este é o bot novo, separado do bot das passagens.', botoes=[PAINEL_BOTAO])
        print(r)
        sys.exit(0 if r == 'TELEGRAM_ENVIADO' else 1)
    else:
        rodar(resumo_agora='--resumo' in sys.argv or os.environ.get('PS5_RESUMO') == '1')
