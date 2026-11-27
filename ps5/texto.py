"""Lê oferta de PS5 escrita em texto livre: post de grupo do Telegram, linha do feed do Gatry.

`ler_post(html)` devolve {'ofertas': [...], 'cupons': [...], 'motivo': ''}. Cada oferta: titulo, preco, preco_de,
preco_sem_cupom, cupom, loja, ressalvas, parcelado. Função pura (sem rede), calibrada em posts reais dos canais.

Como decide o preço:
- "à vista", "no Pix" ou preço solto              -> preço final
- "R$ X em até 10x", "Parcelado"                  -> total parcelado: só vale se não houver preço à vista (ressalva)
- "12x de R$ 350"                                 -> parcela: nunca é o preço (sem outro preço, vira 12 x 350, com ressalva)
- "De R$ X" ou riscado                            -> preço antigo
- "R$ 500 OFF", "acima de R$ 2.500", cashback     -> não são preço
Descarta: oferta encerrada/esgotada, sorteio, faixa "de R$ X a R$ Y" e post sem console de PS5 no título.
"""
import html, re, unicodedata
from datetime import timedelta

FAIXA_GERAL = (2000, 12000)   # console novo; a faixa por modelo é conferida depois, em monitor.avaliar


def simples(t):
    """minúsculas, sem acento, 'PlayStation®5' -> 'playstation 5'"""
    t = unicodedata.normalize('NFKD', t.replace('®', ' ').replace('™', ' ')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'playstation\s*5', 'playstation 5', t)).strip()


PS5 = re.compile(r'\bps ?5\b|playstation 5\b')
INICIO_CONSOLE = re.compile(r'\b(?:console|playstation|ps ?5|bundle|pacote|pack|video ?game)\b')
# antes da menção ao PS5, estas palavras dizem que o produto é outra coisa ("Controle DualSense PS5", "Volante para PS5")
ACESSORIO = re.compile(r'controle|dualsense|\bjogos?\b|\bgames?\b|volante|headset|\bfones?\b|\bcapa|tampa|suporte|\bbase\b|drive|'
                       r'unidade|\bcabo|pelicula|skin|\bssd\b|\bhd\b|cartao|gift|monitor|\btv\b|cadeira|portal|\bvr\b|vr2|pulse|'
                       r'camera|carregador|dock|teclado|mouse|cooler|ventoinha|adesivo|\bcase\b|bolsa|mochila|headphone|caixa de som|'
                       r'\bpara\b|compativel|grand theft|gta|ea sports|\bfc ?\d\d\b|spider|god of war|edicao standard|midia fisica')
MODELO_DITO = re.compile(r'\bpro\b|digital|sem (?:leitor|disco|drive)|\bdis[ck]o?\b|leitor|fisic|standard|blu ?-?ray')
SUBTITULO = re.compile(r'^\W*(?:modelo\s+|versao\s+|edicao\s+)?(digital|sem leitor|com leitor(?: de discos?)?|com disco|dis[ck]\b|'
                       r'midia fisica|standard|pro)\b')
ENCERRADA = re.compile(r'\b(?:encerrad[ao]|esgotad[ao]|esgotou|acabou|expirad[ao]|expirou|indisponivel)\b')
NAO_E_OFERTA = re.compile(r'\b(?:sorteio|rifa|consorcio|concorra)\b')
FAIXA_DE_PRECO = re.compile(r'r\$ ?[\d.,]+\s*(?:a|ate|-|–)\s*r\$')
DINHEIRO = re.compile(r'R\$\s?(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?(?!\d)')

# loja: nome canônico (o mesmo que as fontes de loja usam) e como aparece nos posts
ALIAS = [(r'kabum', 'KaBuM!'), (r'amazon', 'Amazon'), (r'mercado ?livre|\bmeli\b|\bml\b', 'Mercado Livre'),
         (r'magalu|magazine ?luiza', 'Magazine Luiza'), (r'casas ?bahia', 'Casas Bahia'), (r'americanas', 'Americanas'),
         (r'shopee', 'Shopee'), (r'ali ?express', 'Aliexpress'), (r'fast ?shop', 'Fast Shop'), (r'carrefour', 'Carrefour'),
         (r'ponto ?frio', 'Ponto'), (r'havan', 'Havan'), (r"sam'?s club", "Sam's Club"), (r'webcontinental', 'Webcontinental'),
         (r'submarino', 'Submarino'), (r'pichau', 'Pichau'), (r'terabyte', 'Terabyte'), (r'girafa', 'Girafa'),
         (r'tiktok', 'TikTok Shop'), (r'\bna vivo\b|vivo store|loja vivo|^vivo$', 'Vivo'), (r'\btemu\b', 'Temu'),
         (r'trocafy', 'Trocafy'), (r'kalunga', 'Kalunga'), (r'bemol', 'Bemol'), (r'gazin', 'Gazin'), (r'colombo', 'Colombo')]
ENCURTADOR = {'amzn.to': 'Amazon', 'link.amazon': 'Amazon', 'amazon.com.br': 'Amazon', 'a.co': 'Amazon',
              'meli.la': 'Mercado Livre', 'mercadolivre.com': 'Mercado Livre', 'mercadolivre.com.br': 'Mercado Livre',
              's.shopee.com.br': 'Shopee', 'shopee.com.br': 'Shopee', 's.click.aliexpress.com': 'Aliexpress',
              'pt.aliexpress.com': 'Aliexpress', 'magazineluiza.com.br': 'Magazine Luiza', 'magalu.com': 'Magazine Luiza',
              'kabum.com.br': 'KaBuM!', 'casasbahia.com.br': 'Casas Bahia', 'americanas.com.br': 'Americanas'}
NAO_E_CODIGO = {'AMAZON', 'PRIME', 'PIX', 'MERCADO', 'LIVRE', 'MAGALU', 'KABUM', 'SHOPEE', 'VALIDO', 'EXCLUSIVO', 'RESGATE',
                'DISPONIVEL', 'LIBERADO', 'ATIVO', 'NOVO', 'AQUI', 'ABAIXO', 'LINK', 'OFERTA', 'DESCONTO', 'CUPOM', 'CUPONS'}
RESSALVAS = [(r'mercado pago', 'cartão Mercado Pago'),
             (r'assinantes? (?:amazon )?prime|exclusiv[ao] (?:para )?prime|membros prime|antecipado prime', 'assinante Prime'),
             (r'apenas no app|so no app|somente no app|pelo app|no aplicativo|exclusiv[ao] (?:do|no) app', 'só no app'),
             (r'a partir de r\$', 'a partir de'), (r'algumas regioes|\bregional\b', 'só em algumas regiões'),
             (r'lojas? fisicas?', 'loja física'), (r'primeira compra|novos (?:usuarios|clientes)', 'primeira compra'),
             (r'cartao (?:da loja|magalu|casas bahia|americanas|carrefour)', 'cartão da loja')]


def linhas_de(h):
    """HTML (ou texto) do post -> (linhas, links). <code>x</code> vira «x»; riscado vira ~x~."""
    links = re.findall(r'href="([^"]+)"', h)
    h = re.sub(r'<br\s*/?>', '\n', h)
    h = re.sub(r'<code>(.*?)</code>', lambda m: '«' + m[1] + '»', h, flags=re.S)
    h = re.sub(r'<(s|del|strike)>(.*?)</\1>', lambda m: '~' + m[2] + '~', h, flags=re.S)
    t = html.unescape(re.sub(r'<[^>]+>', '', h)).replace('\xa0', ' ').replace('⁠', '').replace('‍', '')
    linhas = [re.sub(r'[ \t]+', ' ', l).strip() for l in t.split('\n')]
    return [l for l in linhas if l], links + re.findall(r'https?://\S+', t)


def titulo_console(linha):
    """A linha nomeia um console de PS5? Devolve o título (a partir de 'Console', 'PlayStation', 'PS5', 'Bundle'...) ou None."""
    l = re.sub(r'^\s*\[[^\]]{2,25}\]\s*', '', linha)            # "[KaBuM!] Console..." -> "Console..."
    l = re.sub(r'\s*(?:[—–-]\s*)?R\$.*$', '', l).split(' | ')[0]  # corta no primeiro preço e em " | chamada"
    s = simples(l)
    ps5, m = PS5.search(s), INICIO_CONSOLE.search(s)
    if not ps5 or not m or m.start() > ps5.start():
        return None
    acessorio = ACESSORIO.search(s[:ps5.start()])
    if acessorio and acessorio.start() < m.start():              # "Controle para PS5": outra coisa. "Bundle GTA VI + PS5": console
        return None
    # acha no texto original a palavra em que o título começa ("Console", "PlayStation®5", "PS5", "Bundle")
    palavra = s[m.start():m.end()].split()[0]
    for cand in re.finditer(r'[A-Za-zÀ-ÿ0-9][\w®™]*', l):
        if simples(cand[0]).startswith(palavra[:4]):
            return l[cand.start():].strip(' :-–—•*')
    return l.strip(' :-–—•*')


def _papel(antes, depois, riscado):
    """O que é este valor em R$? final | parcelado | parcela | de | desconto | minimo | cashback"""
    a, d = simples(antes)[-45:], simples(depois)[:45]
    if re.search(r'\d{1,2} ?x(?: de)? ?$', a):
        return 'parcela'
    if riscado or (re.search(r'(?:^|\W)(?:de|era|antes|estava|custava|caiu de)[: ]*$', a) and not re.search(r'a partir de[: ]*$', a)):
        return 'de'
    if re.match(r' ?(?:de )?(?:cashback|de volta)', d) or re.search(r'(?:cashback de|volta de|receba) ?$', a):
        return 'cashback'
    if re.match(r' ?(?:de )?(?:off\b|de desconto|desconto)', d) or re.search(r'(?:cupom de|desconto de|economize|menos) ?$', a):
        return 'desconto'
    if re.search(r'(?:acima de|compras de|minimo de|a cada) ?$', a):
        return 'minimo'
    if re.match(r' ?[\[(]?(?:em |ou )?(?:ate )?\d{1,2} ?x|^ ?[\[(]?parcelad|^ ?sem juros', d):
        return 'parcelado'
    return 'final'


def _precos(linha):
    """[(valor, papel, n_parcelas)] de uma linha"""
    out, riscados = [], [(m.start(), m.end()) for m in re.finditer(r'~[^~]*~', linha)]
    for m in DINHEIRO.finditer(linha):
        valor = float(m[1].replace('.', '') + '.' + (m[2] or '0'))
        risc = any(a <= m.start() < b for a, b in riscados)
        papel = _papel(linha[:m.start()], linha[m.end():], risc)
        n = re.search(r'(\d{1,2}) ?x(?: de)? ?$', simples(linha[:m.start()]))
        out.append((valor, papel, int(n[1]) if n else None))
    return out


def _cupom(linhas):
    for l in linhas:
        for c in re.findall(r'«([^»]{3,30})»', l):
            c = c.strip()
            if re.fullmatch(r'[A-Za-z0-9_-]{4,25}', c) and re.search(r'[A-Za-z]', c) and c.upper() not in NAO_E_CODIGO:
                return c.upper()
    for l in linhas:
        m = re.search(r'[Cc][Uu][Pp][Oo][Mm]\s*:?\s*([A-Z0-9][A-Z0-9_-]{3,24})(?![\w-])', l)
        if m and m[1] not in NAO_E_CODIGO and (re.search(r'\d', m[1]) or len(m[1]) >= 6):
            return m[1]
    if any(re.search(r'resgate o cupom|cupom na pagina|ative o cupom|cupom da pagina|resgate o cupom', simples(l)) for l in linhas):
        return 'cupom (na página)'
    return None


def loja_canonica(nome):
    """'Magalu' -> 'Magazine Luiza'; nome desconhecido volta como veio"""
    s = simples(nome)
    return next((canon for rx, canon in ALIAS if re.search(rx, s)), nome.strip())


def _loja(linhas, links):
    texto = '\n'.join(linhas)
    # rótulos explícitos: "[KaBuM!] ..." (1ª linha), "Loja: #Amazon", "📍 KaBuM!", "Achado Kabum"
    rotulo = re.match(r'\s*\[([^\]]{2,25})\]', linhas[0]) if linhas else None
    for m in (rotulo, re.search(r'Loja:\s*#?([\w!\' ]{2,25})', texto), re.search(r'📍\s*([\w!\' ]{2,25})', texto),
              re.search(r'Achado\s+([\w!\']{2,20})', texto)):
        if m:
            return loja_canonica(m[1])
    for u in links:                                              # domínio do link (sem seguir o redirecionamento)
        host = re.sub(r'^https?://(?:www\.)?', '', u).split('/')[0].lower()
        if host in ENCURTADOR:
            return ENCURTADOR[host]
    s = simples(texto)
    achados = sorted((m.start(), canon) for rx, canon in ALIAS for m in [re.search(rx, s)] if m)
    if achados:
        return achados[0][1]
    return '?'


def _ressalvas(linhas):
    s = simples(' '.join(linhas))
    return [nome for rx, nome in RESSALVAS if re.search(rx, s)]


def _oferta_do_bloco(titulo, linhas, linha_titulo, todas, links):
    """Um bloco (título + linhas até o próximo subtítulo) -> oferta ou None"""
    na_linha = [p for p in _precos(linha_titulo) if p[1] in ('final', 'parcelado')] if linha_titulo else []
    fonte = [linha_titulo] if na_linha else linhas               # preço na própria linha do título manda
    if any(FAIXA_DE_PRECO.search(simples(l)) for l in fonte):
        return None
    precos = [p for l in fonte for p in _precos(l)]
    em_faixa = lambda v: FAIXA_GERAL[0] <= v <= FAIXA_GERAL[1]
    finais = sorted({v for v, papel, _ in precos if papel == 'final' and em_faixa(v)})
    parcelados = sorted({v for v, papel, _ in precos if papel == 'parcelado' and em_faixa(v)})
    parcelas = sorted({round(v * n, 2) for v, papel, n in precos if papel == 'parcela' and n and em_faixa(v * n)})
    antigos = [v for v, papel, _ in precos if papel == 'de' and em_faixa(v)]
    ressalvas = _ressalvas(linhas)
    cupom = _cupom(linhas) or _cupom(todas)
    if finais:
        preco = finais[0]
    elif parcelados or parcelas:
        preco = (parcelados or parcelas)[0]
        ressalvas.append('parcelado')
    else:
        return None
    loja = _loja(linhas, []) if linhas is not todas else '?'      # bloco de um modelo pode nomear a própria loja
    o = {'titulo': titulo, 'preco': preco, 'preco_de': max(antigos) if antigos else None, 'preco_sem_cupom': None,
         'cupom': cupom, 'loja': loja if loja != '?' else _loja(todas, links), 'ressalvas': ressalvas, 'parcelado': not finais}
    if len(finais) > 1:
        if cupom:
            o['preco_sem_cupom'] = finais[-1]
        else:
            o['ressalvas'].append('post com mais de um preço')
    return o


def ler_post(h):
    linhas, links = linhas_de(h)
    s = simples(' '.join(linhas))
    cupons = _cupons_do_post(linhas, links)
    if not PS5.search(s):
        return {'ofertas': [], 'cupons': cupons, 'motivo': 'não fala de PS5'}
    if ENCERRADA.search(s):
        return {'ofertas': [], 'cupons': [], 'motivo': 'encerrada'}
    if NAO_E_OFERTA.search(s):
        return {'ofertas': [], 'cupons': cupons, 'motivo': 'sorteio'}
    candidatas = [(i, t) for i, l in enumerate(linhas) for t in [titulo_console(l)] if t]
    if not candidatas:
        return {'ofertas': [], 'cupons': cupons, 'motivo': 'não é console'}
    # melhor título: o que diz o modelo; depois o que diz "console"; depois o que não é grito em maiúsculas
    nota = lambda it: (not MODELO_DITO.search(simples(it[1])), 'console' not in simples(it[1]), it[1].isupper(), it[0])
    i_tit, titulo = min(candidatas, key=nota)
    curta = lambda l: len(re.sub(r'\s*(?:[—–:-]\s*)?R\$.*$', '', l)) <= 30          # "Modelo com Leitor", "Digital R$ 3.299"
    subs = [i for i, l in enumerate(linhas) if i != i_tit and curta(l) and SUBTITULO.match(simples(l)) and not PS5.search(simples(l))]
    ofertas = []
    if subs:                                                     # "Modelo Digital ... / Modelo com Leitor ..."
        for k, i in enumerate(subs):
            fim = subs[k + 1] if k + 1 < len(subs) else len(linhas)
            sub = re.sub(r'\s*(?:[—–:-]\s*)?R\$.*$', '', linhas[i]).strip(' :-–—•*👉')
            o = _oferta_do_bloco(f'{titulo} — {sub}', linhas[i:fim], linhas[i], linhas, links)
            if o:
                ofertas.append(o)
    if not ofertas:
        o = _oferta_do_bloco(titulo, linhas, linhas[i_tit], linhas, links)
        if o:
            ofertas.append(o)
    return {'ofertas': ofertas, 'cupons': cupons, 'motivo': '' if ofertas else 'sem preço de console'}


# ---------------------------------------------------------------- anúncio de cupom com hora marcada
# Os canais avisam antes: "🔥 Às 18h: CUPOM DE 4 DÍGITOS no Mercado Livre!" (25/11/2026, 17h15; às 18h04 já tinha
# esgotado). ler_anuncio acha esse aviso para o plantão poder avisar antes e lembrar na hora.
LOJAS_PS5 = {'Mercado Livre', 'Amazon', 'Magazine Luiza', 'KaBuM!', 'Casas Bahia', 'Americanas', 'Shopee', 'Carrefour',
             'Fast Shop', 'Ponto', 'Submarino'}
HORA = re.compile(r'\b([01]?\d|2[0-3]) ?h ?([0-5]\d)?\b|\b([01]?\d|2[0-3]):([0-5]\d)\b|(meia[- ]noite)')
SO_USO = re.compile(r'(?:\bus(?:ar|e|o)\b|utiliz\w*|valid\w*|\bate\b)\W*(?:\w+\W+){0,3}$')   # "para usar a partir da", "válidos até"
OUTRA_CATEGORIA = re.compile(r'\bmoda\b|beleza|decoracao|supermercado|\bpet\b|farmacia|\blivros?\b|moveis|\bbebes?\b|'
                             r'brinquedo|\bcasa\b|perfum|calcad|roupa|ferrament|automotiv|esporte')
PRECO_REF = 3800      # para comparar cupom em % com cupom em reais (um PS5 custa por volta disso)


def _valor(s):
    """Quanto o cupom tira de um PS5, em reais, e como escrever: (valor, 'R$ 1.000') | (valor, '35% OFF') | (None, None)"""
    reais = [int(m[1].replace('.', '')) for m in re.finditer(r'r\$ ?(\d{1,3}(?:\.\d{3})+|\d+)(?:,\d\d)?\s*(?:de\s+)?(?:off|desconto)', s)]
    reais += [int(m[1].replace('.', '')) for m in re.finditer(r'cupo\w* de (?:mais (?:ou igual )?(?:de )?|ate )?r\$ ?(\d{1,3}(?:\.\d{3})+|\d+)', s)]
    reais += [10 ** (int(m[1]) - 1) for m in re.finditer(r'\b([3-5]) digitos\b', s)] + [1000 for _ in re.finditer(r'\bmil reais\b', s)]
    if reais:
        v = max(reais)
        return v, 'R$ ' + f'{v:,}'.replace(',', '.')
    pct = [int(m[1]) for m in re.finditer(r'\b(\d{1,2}) ?% ?(?:de )?(?:off|desconto)', s)]
    if not pct:
        return None, None
    teto = [int(m[1]) for m in re.finditer(r'limit\w*(?: de| a| em)? (?:r\$ ?)?(\d+)', s)]
    v = round(max(pct) * PRECO_REF / 100)
    return (min(v, max(teto)) if teto else v), f"{max(pct)}% OFF" + (f" (até R$ {max(teto)})" if teto else '')


def ler_anuncio(h, publicado, minimo=150):
    """Post que anuncia cupom (ou oferta relâmpago) com hora marcada, numa loja que vende PS5, que tira pelo menos `minimo`
    reais de um console: {'loja', 'quando' (iso), 'horas' ['18h', ...], 'desconto', 'valor', 'ressalvas', 'resumo', 'destino'}
    ou None. publicado: datetime do post. Hora já passada, hora de "usar a partir de" e post de "liberado"/"esgotado" não contam."""
    linhas, links = linhas_de(h)
    s = simples(' '.join(linhas))
    if not re.search(r'cupo|relampago', s) or re.search(r'esgotad|encerrad|acabou|expirou', s) or OUTRA_CATEGORIA.search(s):
        return None
    loja = _loja(linhas, links)
    if loja not in LOJAS_PS5:
        return None
    valor, desconto = _valor(s)
    if not valor or valor < minimo:
        return None
    liberado = bool(re.search(r'\bliberad[oa]s?\b|resgate agora|ja liberou', s))
    futuras = []
    for m in HORA.finditer(s):
        if SO_USO.search(s[max(0, m.start() - 40):m.start()]):
            continue
        hh, mm = (0, 0) if m[5] else (int(m[1] or m[3]), int(m[2] or m[4] or 0))
        quando = publicado.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if quando <= publicado and (hh == 0 or re.search(r'\bamanha\b', s)):
            quando += timedelta(days=1)
        antecedencia = (quando - publicado).total_seconds() / 60
        if antecedencia > (10 if liberado else 0) and antecedencia <= 18 * 60 and quando not in futuras:
            futuras.append(quando)
    if not futuras:
        return None
    futuras.sort()
    hora_txt = lambda q: f"{q.hour}h" + (f"{q.minute:02d}" if q.minute else '')
    return {'loja': loja, 'quando': futuras[0].isoformat(timespec='minutes'), 'horas': [hora_txt(q) for q in futuras],
            'desconto': desconto, 'valor': valor, 'ressalvas': _ressalvas(linhas), 'resumo': re.sub(r'\s+', ' ', ' '.join(linhas))[:200],
            'destino': next((u for u in links if re.match(r'https?://', u) and 't.me/' not in u), None)}


def _cupons_do_post(linhas, links):
    """Cupons anunciados com regra ("R$500 OFF em compras acima de R$2.500 — cupom MP500PS"): [{codigo, titulo, loja, ps5}]"""
    out, s_todo = [], simples(' '.join(linhas))
    # a descrição do cupom ("📌 40% OFF em produtos selecionados", "28% OFF — Suporte para Monitor") vem nas linhas de cima,
    # antes da lista de produtos de exemplo ("Já ficou barato com ele agora:") e do primeiro link
    fim = next((i for i, l in enumerate(linhas) if re.search(r'https?://|ficou barato|exemplos?\b|aproveite em|produtos? com', l, re.I)),
               len(linhas))
    descricao = re.sub(r'\s+', ' ', ' '.join(linhas[:fim]))[:220]
    for i, l in enumerate(linhas):
        codigo = _cupom([l])
        if not codigo or codigo.startswith('cupom'):
            continue
        regra = next((x for x in (l, linhas[i - 1] if i else '') if re.search(r'(?:R\$\s?[\d.,]+|\d{1,2}\s?%)\s*(?:de\s+)?(?:OFF|desconto)', x, re.I)), None)
        if regra:
            out.append({'codigo': codigo, 'titulo': re.sub(r'\s+', ' ', regra if regra is l else f'{regra} {l}')[:160],
                        'descricao': descricao, 'loja': _loja(linhas, links),
                        'ps5': bool(re.search(r'\bps ?5\b|playstation|console|games?\b', s_todo))})
    return out
