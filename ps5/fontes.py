"""Onde o buscador do PS5 lê preços, ofertas, cupons e leilões. Cada fonte devolve uma lista de itens e nunca derruba
a varredura: quem falhar entra na situação como "falhou" e o resto segue. Nada aqui tenta contornar bloqueio de robô.

Lojas (preço de vitrine, à vista quando a loja mostra) — tipo 'loja':
- KaBuM!      categoria de consoles PlayStation; selo "CUPOM X" do produto vira cupom; marca open box.
- Amazon      busca "playstation 5 console"; selo "Economize R$ X com cupom" vira cupom (barra o robô com frequência).
- VTEX        leitor genérico do catálogo público (lojas.LOJAS): Americanas, Fast Shop, Webcontinental, Bemol.
- JSON-LD     leitor genérico de página com dados schema.org (nenhuma loja ligada hoje; ver lojas.CANDIDATAS).
Comparador — tipo 'comparador':
- Zoom        cada PS5 da categoria com o preço de cada loja.
Ofertas postadas, já com o preço final usando o cupom — tipo 'postada' (moderadas) e 'grupo' (canais):
- Promobit    categoria PlayStation 5.           - Gatry       feed das últimas 40 ofertas.
- Telegram    canais públicos de oferta (lojas.CANAIS), pela prévia t.me/s/<canal>?q=PS5; texto livre lido por texto.py.
Cupons: páginas de cupom por loja do Promobit e cupons anunciados nos canais.
Leilões: lotes de logística reversa que citam console de PS5 (lojas.LEILOES).

Itens devolvidos pelas fontes: oferta (tem 'preco'), cupom (tem 'codigo') ou lote de leilão (tem 'lote').
"""
import functools, gzip, html, json, re, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

import lojas
import texto

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
CABECALHOS = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
              'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.5', 'Accept-Encoding': 'gzip'}
BLOQUEIO = re.compile(r'captcha|account-verification|acesso bloqueado|access denied|verifica[çc][ãa]o de seguran[çc]a', re.I)
BRT = timezone(timedelta(hours=-3))

ORCAMENTO_S = 150                 # tempo máximo de uma varredura (o monitor troca pelo valor do config.py)
PRAZO = None                      # limite (time.monotonic) da varredura em curso; depois dele nenhuma fonte começa pedido novo
DETALHE = {}                      # recado de uma fonte para a situação (ex.: "20 de 22 canais")
PULAR = set()                     # fontes que o monitor mandou pular nesta rodada (vêm barrando o robô há muito tempo)
_travas, _trava_mapa = {}, threading.Lock()


class Bloqueado(Exception):
    pass


def _trava(url):
    """Uma requisição por vez para cada site (as fontes rodam em paralelo, mas ninguém leva duas ao mesmo tempo)."""
    host = urllib.parse.urlsplit(url).netloc
    with _trava_mapa:
        return _travas.setdefault(host, threading.Lock())


def baixar(url, json_api=False, tentativas=3, dados=None, timeout=25):
    cab = dict(CABECALHOS, **({'Accept': 'application/json'} if json_api else {}))
    for i in range(tentativas):
        if PRAZO and time.monotonic() > PRAZO:
            raise TimeoutError('passou do tempo da varredura')
        try:
            with _trava(url), urllib.request.urlopen(urllib.request.Request(url, data=dados, headers=cab), timeout=timeout) as r:
                corpo = r.read()
                codificacao = r.headers.get('Content-Encoding', '').lower()
                # algumas lojas mandam gzip mesmo sem pedir; pedimos sempre e descompactamos
                if codificacao == 'gzip' or corpo[:2] == b'\x1f\x8b':
                    corpo = gzip.decompress(corpo)
                elif codificacao not in ('', 'identity') and 'Accept-Encoding' in cab:
                    cab = {k: v for k, v in cab.items() if k != 'Accept-Encoding'}   # veio em outro formato: pede sem compressão
                    raise ValueError(f'resposta comprimida em {codificacao}')
                texto_ = corpo.decode('utf-8', 'replace')
                if not json_api and BLOQUEIO.search(r.url + texto_[:3000]):
                    raise Bloqueado(f'pediu verificação anti-robô ({urllib.parse.urlsplit(r.url).netloc})')
                return texto_
        except Bloqueado:
            raise
        except Exception as e:
            if getattr(e, 'code', None) in (401, 403) or (getattr(e, 'code', None) == 503 and 'amazon' in url):
                raise Bloqueado(f'HTTP {e.code} (barrou o robô)')
            if i == tentativas - 1:
                raise
            time.sleep(1.5 * (i + 1))


def next_data(texto_):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', texto_, re.S)
    if not m:
        raise ValueError('página sem __NEXT_DATA__ (o site mudou?)')
    return json.loads(m[1])


def reais(s):
    """'R$ 4.230,57' -> 4230.57"""
    m = re.search(r'([\d.]+)(?:,(\d{1,2}))?', s.replace('\xa0', ' '))
    return float(m[1].replace('.', '') + '.' + (m[2] or '0')) if m else None


def oferta(fonte, loja, titulo, preco, link, tipo='loja', **extra):
    """tipo: loja | comparador | postada (comunidade moderada) | grupo (canal). origem: quem viu (para contar confirmações)."""
    return {'fonte': fonte, 'loja': loja, 'titulo': re.sub(r'\s+', ' ', html.unescape(titulo)).strip(), 'preco': round(preco, 2),
            'link': link, 'tipo': tipo, 'origem': fonte, 'cupom': None, 'cupom_origem': None, 'preco_sem_cupom': None,
            'obs': '', 'ressalvas': [], **extra}


def _quando(s):
    """'2026-11-13T12:30:07-0300' ou '2026-11-13T15:30:07+00:00' -> datetime com fuso"""
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z')
    except ValueError:
        return datetime.fromisoformat(s)


def _com_chave(o, chave):
    """Todos os dicts com `chave` dentro de o (as páginas guardam a lista ora direto, ora dentro de outro dict)."""
    if isinstance(o, dict):
        if chave in o:
            yield o
        else:
            for v in o.values():
                yield from _com_chave(v, chave)
    elif isinstance(o, list):
        for v in o:
            yield from _com_chave(v, chave)


# ---------------------------------------------------------------- KaBuM!
KABUM = 'https://www.kabum.com.br/gamer/playstation/consoles-playstation'


def kabum():
    dados = json.loads(next_data(baixar(KABUM))['props']['pageProps']['data'])['catalogServer']['data']
    out = []
    for p in dados:
        if not p.get('available'):
            continue
        avista = [v for v in (p.get('priceWithDiscount'), (p.get('offer') or {}).get('priceWithDiscount')) if v]
        if not avista:
            continue
        flags = p.get('flags') or {}
        o = oferta('KaBuM!', 'KaBuM!' if not flags.get('isMarketplace') else f"KaBuM! · {p.get('sellerName')}",
                   p['name'], min(avista), f"https://www.kabum.com.br/produto/{p['code']}/{p.get('friendlyName', '')}",
                   id=f"kabum-{p['code']}", obs='à vista no Pix' + (' · oferta relâmpago' if p.get('offer') else ''),
                   openbox=bool(flags.get('isOpenbox')))
        selo = (p.get('stamps') or {}).get('title', '')
        if m := re.match(r'\s*CUPOM\s+(\S+)', selo, re.I):
            o['cupom_selo'] = m[1].upper()   # o valor vem dos cupons da loja no Promobit (monitor.aplicar_selos)
        out.append(o)
    return out


# ---------------------------------------------------------------- Amazon
AMAZON = ['https://www.amazon.com.br/s?k=playstation+5+console']   # uma busca só (já traz o Pro): a Amazon barra quem pede muito


def _texto(trecho):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', trecho)))


def amazon():
    out, vistos = [], set()
    for url in AMAZON:
        pagina = baixar(url, tentativas=2)
        blocos = re.split(r'(?=<div[^>]+data-component-type="s-search-result")', pagina)[1:]
        if not blocos:
            if len(pagina) < 30000:   # página curta de "continue comprando" / verificação
                raise Bloqueado('a Amazon pediu verificação (página sem resultados)')
            raise ValueError('nenhum resultado na busca (a Amazon mudou a página?)')
        for b in blocos:
            asin = re.search(r'data-asin="(\w+)"', b)
            tit = re.search(r'<h2[^>]*aria-label="([^"]+)"', b) or re.search(r'<h2.*?<span[^>]*>(.*?)</span>', b, re.S)
            preco = re.search(r'<span class="a-price"[^>]*><span class="a-offscreen">([^<]+)</span>', b)
            if not (asin and tit and preco) or asin[1] in vistos:
                continue
            vistos.add(asin[1])
            o = oferta('Amazon', 'Amazon', re.sub(r'^(?:An[úu]ncio patrocinado\s*[–-]|Patrocinado:)\s*', '', html.unescape(tit[1])), reais(preco[1]),
                       f'https://www.amazon.com.br/dp/{asin[1]}', id=f'amazon-{asin[1]}')
            # selo de cupom da própria Amazon (ativado na página do produto, sem código)
            if c := re.search(r'Economize\s+(R\$\s*[\d.]+(?:,\d\d)?|\d+\s*%)\s+com\s+cupom', _texto(b), re.I):
                desc = reais(c[1]) if 'R$' in c[1] else o['preco'] * int(re.match(r'\d+', c[1])[0]) / 100
                o.update(preco_sem_cupom=o['preco'], preco=round(o['preco'] - desc, 2), cupom=f'cupom da página ({c[1]})',
                         cupom_origem='loja')
            out.append(o)
    return out


# ---------------------------------------------------------------- lojas VTEX (leitor genérico)
def _a_vista(co):
    """Menor preço em 1x entre as formas de pagamento (no Pix costuma haver desconto) -> (preço, forma ou '')"""
    preco = co.get('Price') or 0
    em_1x = [(i['TotalValuePlusInterestRate'], i.get('PaymentSystemName', '')) for i in co.get('Installments') or []
             if i.get('NumberOfInstallments') == 1 and i.get('TotalValuePlusInterestRate')]
    return min(em_1x) if em_1x and min(em_1x)[0] < preco - 0.5 else (preco, '')


def vtex(loja):
    """Catálogo público de uma loja VTEX. loja: dict de lojas.LOJAS (nome, base, obs opcional)."""
    slug, out = re.sub(r'\W+', '', texto.simples(loja['nome'])), {}
    for q in loja.get('consultas', ('playstation 5', 'console ps5')):
        url = f"{loja['base']}/api/catalog_system/pub/products/search?ft={urllib.parse.quote(q)}&_from=0&_to=49"
        lista = json.loads(baixar(url, json_api=True))
        for p in lista if isinstance(lista, list) else []:
            for item in (p.get('items') or [])[:1]:
                ofertas = []
                for s in item.get('sellers') or []:
                    co = s.get('commertialOffer') or {}
                    if co.get('AvailableQuantity', 0) > 0 and co.get('Price', 0) > 0:
                        ofertas.append((*_a_vista(co), s.get('sellerName') or ''))
                if not ofertas:
                    continue
                preco, forma, vendedor = min(ofertas)
                da_loja = texto.simples(vendedor)[:6] == texto.simples(loja['nome'])[:6]
                nome = loja['nome'] if da_loja else f"{loja['nome']} · {vendedor.title() if vendedor.isupper() else vendedor}"
                obs = ' · '.join(x for x in (f'à vista no {forma}' if forma else '', loja.get('obs', '')) if x)
                out[p.get('productId')] = oferta(loja['nome'], nome, p.get('productName', ''), preco, p.get('link', ''),
                                                 id=f"{slug}-{p.get('productId')}", obs=obs)
    return list(out.values())


# ---------------------------------------------------------------- lojas com dados schema.org na página (leitor genérico)
def _precos_ld(offers):
    for o in offers if isinstance(offers, list) else [offers]:
        if isinstance(o, dict) and 'OutOfStock' not in str(o.get('availability', '')):
            v = o.get('price') or o.get('lowPrice')
            try:
                yield float(str(v).replace(',', '.'))
            except (TypeError, ValueError):
                pass


def jsonld(loja):
    """Produtos com preço nos blocos <script type="application/ld+json"> das páginas de busca da loja."""
    slug, out = re.sub(r'\W+', '', texto.simples(loja['nome'])), {}
    for url in loja['urls']:
        for bloco in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', baixar(url), re.S):
            try:
                dados = json.loads(bloco)
            except ValueError:
                continue
            for p in _com_chave(dados, 'offers'):
                precos = list(_precos_ld(p['offers']))
                if p.get('name') and precos:
                    link = p.get('url') or (p['offers'].get('url') if isinstance(p['offers'], dict) else '') or url
                    out[link] = oferta(loja['nome'], loja['nome'], p['name'], min(precos), link, id=f'{slug}-{link[-60:]}',
                                       obs=loja.get('obs', ''))
    return list(out.values())


# ---------------------------------------------------------------- Zoom (comparador)
ZOOM = 'https://www.zoom.com.br'


def zoom():
    """Ofertas de cada loja para cada PS5 da categoria do Zoom (com o nome do produto no catálogo, que diz o modelo
    quando o título da loja vem cortado)."""
    st = next_data(baixar(ZOOM + '/console-de-video-game/playstation-5'))['props']['initialReduxState']
    produtos = [h for h in st['hits']['hits'] if h.get('type') == 'product']
    out = []
    for h in produtos[:20]:
        link = ZOOM + h['url']
        try:
            pst = next_data(baixar(link, tentativas=2))['props']['initialReduxState']
            lista = pst['offers'].get('offerList') or []
        except Exception:   # sem a página do produto, fica a melhor oferta da busca
            lista = []
        if not lista and h.get('price'):
            lista = [{'id': (h.get('bestOffer') or {}).get('id', h['objectId']), 'price': h['price'],
                      'sellerName': (h.get('bestOffer') or {}).get('merchantName', '?'), 'name': h['name']}]
        melhor = (h.get('bestOffer') or {}).get('merchantName')
        volta = ((h.get('cashback') or {}).get('formula') or {}).get('cashbackRate')   # cashback do Zoom na loja da melhor oferta
        for x in lista:
            if x.get('price'):
                o = oferta('Zoom', x.get('sellerName', '?'), x.get('name') or h['name'], x['price'], link, tipo='comparador',
                           id=f"zoom-{x['id']}", produto_zoom=h['name'].strip(), obs='à vista')
                if volta and x.get('sellerName') == melhor:
                    o['cashback'] = {'taxa': volta, 'por': 'Zoom'}
                out.append(o)
    return out


# ---------------------------------------------------------------- Promobit
PROMOBIT = 'https://www.promobit.com.br'
ENCERRADA = re.compile(r'FINISH|EXPIR|REJECT|CLOSED|DELETED|SOLD', re.I)


def promobit():
    """Ofertas de PS5 postadas no Promobit. O preço postado já é o final (com o cupom, quando há)."""
    pp = next_data(baixar(PROMOBIT + '/promocoes/playstation-5/s/'))['props']['pageProps']
    out = []
    for p in _com_chave(pp.get("serverOffers"), "offerTitle"):
        if p.get('offerPriceType') != 'NORMAL' or ENCERRADA.search(p.get('offerStatusName', '')):
            continue
        o = oferta('Promobit', p.get('storeName') or '?', p['offerTitle'], float(p['offerPrice']),
                   f"{PROMOBIT}/oferta/{p['offerSlug']}/", tipo='postada', id=f"promobit-{p['offerId']}",
                   publicado=p.get('offerPublished'), obs='postado no Promobit', origem='promobit')
        c = re.sub(r'\s+', ' ', p.get('offerCoupon') or '').strip()
        if re.fullmatch(r'[A-Za-z0-9_-]{3,30}', c):          # código: AEMEGA19
            o.update(cupom=c, cupom_origem='postado')
        elif re.search(r'R\$|%|\bOFF\b|cupom', c, re.I):    # cupom da página, sem código: "R$300 OFF"
            o.update(cupom=f'cupom {c} (na página)', cupom_origem='postado')
        elif c:                                             # observação: "Lojas Físicas / Parcelado"
            o['obs'] = f'postado no Promobit: {c}'
            o['ressalvas'] = [x for rx, x in ((r'f[ií]sica', 'loja física'), (r'parcelad', 'parcelado')) if re.search(rx, c, re.I)]
        out.append(o)
    return out


def promobit_encerrada(link):
    """A página da oferta no Promobit diz que ela acabou ("Oferta encerrada")? True | False; None se não deu para ler."""
    try:
        o = next_data(baixar(link, tentativas=1, timeout=15))['props']['pageProps'].get('serverOffer') or {}
    except Exception:
        return None
    return bool(ENCERRADA.search(o.get('offerStatusName') or '')) if o.get('offerStatusName') else None


# ---------------------------------------------------------------- Gatry
GATRY = 'https://gatry.com'


def gatry():
    """Feed das 40 últimas ofertas do Gatry ("Título — R$ X · Loja"). Não tem busca: filtra o que for console de PS5."""
    itens = re.findall(r'<li><a href="(/promocoes/([0-9a-f-]{36})/[^"]+)">(.*?)</a>\s*[—–-]\s*R\$\s*([\d.]+(?:,\d\d)?)\s*·\s*([^<]+)</li>',
                       baixar(GATRY + '/promocoes'))
    if not itens:
        raise ValueError('feed do Gatry sem itens (o site mudou?)')
    out = []
    for href, uid, tit, preco, loja in itens:
        titulo = texto.titulo_console(html.unescape(tit))
        if titulo:
            out.append(oferta('Gatry', texto.loja_canonica(html.unescape(loja)), titulo, reais(preco), GATRY + href, tipo='postada',
                              id=f'gatry-{uid}', obs='postado no Gatry', origem='gatry'))   # sem horário: carimbado ao ser visto
    DETALHE['Gatry'] = f'{len(itens)} ofertas no feed'
    return out


# ---------------------------------------------------------------- canais do Telegram
HORAS_TELEGRAM = 48   # posts mais velhos nem são lidos (a validade de verdade é a do config, aplicada no monitor)


def _posts_telegram(pagina):
    """[(canal/123, datetime, html do texto)] da prévia pública de um canal"""
    for m in re.split(r'(?=<div class="tgme_widget_message_wrap)', pagina)[1:]:
        post = re.search(r'data-post="([^"]+)"', m)
        hora = re.search(r'<time datetime="([^"]+)"', m)
        corpo = re.search(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', m, re.S)
        if post and hora and corpo:
            yield post[1], datetime.fromisoformat(hora[1]).astimezone(BRT), corpo[1]


def telegram(canais=None, agora=None, buscas=None):
    """Posts de PS5 dos canais públicos de oferta. Devolve ofertas (tipo 'grupo') e cupons anunciados com regra.
    buscas: termos procurados em cada canal; (None,) lê só a primeira página (os posts mais novos), como no plantão."""
    canais = lojas.CANAIS if canais is None else canais
    agora = agora or datetime.now(BRT)
    out, falhas, vistos = [], [], set()
    for c in canais:
        achou = False
        for q in buscas or lojas.BUSCAS_TELEGRAM:
            try:
                pagina = baixar(f"https://t.me/s/{c['canal']}" + (f"?q={urllib.parse.quote(q)}" if q else ''), tentativas=2)
            except TimeoutError:
                raise
            except Exception as e:
                falhas.append(f"{c['canal']}: {type(e).__name__}")
                break
            achou = achou or 'tgme_channel_info' in pagina
            for post, hora, corpo in _posts_telegram(pagina):
                if post in vistos or agora - hora > timedelta(hours=HORAS_TELEGRAM):
                    continue
                vistos.add(post)
                lido = texto.ler_post(corpo)
                publicado = hora.strftime('%Y-%m-%dT%H:%M:%S%z')
                for k, x in enumerate(lido['ofertas']):
                    o = oferta('Telegram', x['loja'], x['titulo'], x['preco'], f'https://t.me/{post}', tipo='grupo',
                               id=f'tg-{post}-{k}', publicado=publicado, origem=c.get('origem') or c['canal'].lower(),
                               canal=c['nome'], obs=f"postado no canal {c['nome']}", ressalvas=x['ressalvas'],
                               preco_sem_cupom=x['preco_sem_cupom'], preco_de=x['preco_de'])
                    if x['cupom']:
                        o.update(cupom=x['cupom'], cupom_origem='postado')
                    out.append(o)
                for x in lido['cupons']:
                    out.append({'codigo': x['codigo'], 'loja': x['loja'], 'titulo': x['titulo'], 'regra': regra_cupom(x['titulo']),
                                'validade': None, 'publicado': publicado, 'origem': f"canal {c['nome']}", 'link': f'https://t.me/{post}',
                                'ps5': x['ps5']})
        if not achou and not any(f.startswith(c['canal'] + ':') for f in falhas):
            falhas.append(f"{c['canal']}: canal não abriu")
    if canais and len(falhas) == len(canais):
        raise ValueError('nenhum canal respondeu: ' + ', '.join(falhas[:4]))
    DETALHE['Telegram'] = f"{len(canais) - len(falhas)} de {len(canais)} canais" + (f" (falharam: {', '.join(f.split(':')[0] for f in falhas)})" if falhas else '')
    return out


# ---------------------------------------------------------------- cupons
LOJAS_CUPOM = {'KaBuM!': 'kabum', 'Amazon': 'amazon', 'Magazine Luiza': 'magazine-luiza', 'Mercado Livre': 'mercado-livre',
               'Americanas': 'americanas', 'Casas Bahia': 'casas-bahia', 'Carrefour': 'carrefour', 'Shopee': 'shopee',
               'Aliexpress': 'aliexpress', 'Fast Shop': 'fast-shop'}


def regra_cupom(titulo):
    """'30% OFF acima de R$ 2.699 (até R$ 300)' -> {'pct': 30, 'minimo': 2699, 'teto': 300}; 'R$ 100 OFF' -> {'valor': 100}"""
    t = titulo.replace('\xa0', ' ')
    r = {}
    if m := re.search(r'(\d{1,2})\s*%', t):
        r['pct'] = int(m[1])
    elif m := re.search(r'R\$\s*([\d.]+(?:,\d\d)?)\s*(?:OFF|de desconto|em suas|em compras|em seus|na compra|na sua|$)', t, re.I) or \
            re.search(r'(?:desconto de|economiz\w+|ganh\w+|garanta)\s+(?:até\s+)?R\$\s*([\d.]+(?:,\d\d)?)', t, re.I):
        r['valor'] = reais(m[1])
    if m := re.search(r'acima de R\$\s*([\d.]+(?:,\d\d)?)', t, re.I):
        r['minimo'] = reais(m[1])
    if m := re.search(r'(?:at[ée]|limite(?: de)?) R\$\s*([\d.]+(?:,\d\d)?)', t, re.I):
        r['teto'] = reais(m[1])
    return r


def desconto(regra, preco):
    """Quanto o cupom tira de um preço (0 se não se aplica ou não dá para saber)."""
    if preco < regra.get('minimo', 0):
        return 0
    if 'valor' in regra:
        return regra['valor'] if regra['valor'] >= 1 else 0
    if 'pct' in regra:
        return min(preco * regra['pct'] / 100, regra.get('teto', float('inf')))
    return 0


def cupons_promobit(lojas_):
    """Cupons ativos das lojas pedidas, como o Promobit publica: [{loja, codigo, titulo, validade, regra}]"""
    agora = datetime.now(BRT)
    out, erros = [], []
    for loja in lojas_:
        slug = LOJAS_CUPOM.get(loja)
        if not slug:
            continue
        try:
            pp = next_data(baixar(f'{PROMOBIT}/cupons/loja/{slug}/', tentativas=2))['props']['pageProps']
        except urllib.error.HTTPError as e:
            if e.code != 404:
                erros.append(f'{loja}: HTTP {e.code}')
            continue
        except Exception as e:
            erros.append(f'{loja}: {type(e).__name__} {e}'[:160])
            continue
        vistos = set()
        for c in _com_chave(pp.get('serverCoupons'), 'couponTitle'):
            fim = _quando(c.get('couponUntil'))
            if c.get('storeSlug') != slug or c.get('couponStatusName') != 'APPROVED' or (fim and fim < agora):
                continue
            codigo = (c.get('couponCode') or '').strip()
            if not codigo or ' ' in codigo or codigo in vistos:   # "Resgate no link" e afins não são código
                continue
            vistos.add(codigo)
            out.append({'loja': loja, 'codigo': codigo, 'titulo': c.get('couponTitle', ''), 'validade': c.get('couponUntil'),
                        'publicado': c.get('couponPublished'), 'regra': regra_cupom(c.get('couponTitle', '')),
                        'origem': 'Promobit', 'link': f'{PROMOBIT}/cupons/loja/{slug}/'})
    return out, erros


# ---------------------------------------------------------------- leilões de logística reversa
def leiloes(sites=None):
    """Lotes que citam console de PS5 nos sites da plataforma Leiloar. Lote não é preço: é lance, com taxas e retirada."""
    sites = lojas.LEILOES if sites is None else sites
    out, falhas = {}, []
    for s in sites:
        try:
            for termo in lojas.BUSCAS_LEILAO:
                dados = urllib.parse.urlencode({'_method': 'POST', 'data[Bem][termo]': termo}).encode()
                pagina = baixar(s['base'] + '/bens/pesquisaAvancada', dados=dados, tentativas=2)
                for card in re.split(r'(?=<div class="bem-card card)', pagina)[1:]:
                    lote = re.search(r'href="/lote/(\d+)"', card)
                    desc = re.search(r'<h5 class="bem-card-descricao">\s*<a[^>]*>(.*?)</a>', card, re.S)
                    if not lote or not desc or not texto.titulo_console(_texto(desc[1])):
                        continue
                    lance = re.search(r'<div class="bem-card-lance">\s*<span>(.*?)</span>\s*<h4>\s*R\$\s*([\d.]+(?:,\d\d)?)', card, re.S)
                    fim = re.search(r'<div class="bem-encerramento">(.*?)</div>', card, re.S)
                    local = re.search(r'fa-map-marker"></i>\s*([^<]+)<', card)
                    out[f"{s['nome']}-{lote[1]}"] = {
                        'lote': f"{s['nome']}-{lote[1]}", 'origem': s['nome'], 'descricao': _texto(desc[1]).strip(),
                        'lance': reais(lance[2]) if lance else None, 'lance_tipo': _texto(lance[1]).strip().lower() if lance else '',
                        'encerra': _texto(fim[1]).strip() if fim else '', 'local': local[1].strip() if local else '',
                        'aberto': 'ABERTO' in card, 'link': f"{s['base']}/lote/{lote[1]}"}
        except TimeoutError:
            raise
        except Exception as e:
            falhas.append(f"{s['nome']}: {type(e).__name__}")
    if sites and len(falhas) == len(sites):
        raise ValueError('nenhum site de leilão respondeu: ' + ', '.join(falhas))
    DETALHE['Leilões'] = f"{len(sites) - len(falhas)} de {len(sites)} sites" + (f" (falharam: {', '.join(falhas)})" if falhas else '')
    return list(out.values())


# ---------------------------------------------------------------- todas as fontes
LEITORES = {'vtex': vtex, 'jsonld': jsonld}
FONTES = {'KaBuM!': kabum, 'Amazon': amazon,
          **{l['nome']: functools.partial(LEITORES[l['tipo']], l) for l in lojas.LOJAS},
          'Zoom': zoom, 'Promobit': promobit, 'Gatry': gatry, 'Telegram': telegram, 'Leilões': leiloes}
TIPO_FONTE = {'Zoom': 'comparador', 'Promobit': 'postada', 'Gatry': 'postada', 'Telegram': 'grupo', 'Leilões': 'leilão'}


def coletar(fontes=None, orcamento=None, paralelas=8, folga=5):
    """Roda todas as fontes em paralelo, com limite de tempo total. Devolve (itens, situação de cada fonte).
    Fonte que não terminar no prazo entra como "passou do tempo" e não segura a varredura."""
    global PRAZO
    fontes = FONTES if fontes is None else fontes
    orcamento = orcamento or ORCAMENTO_S
    PRAZO = time.monotonic() + orcamento
    DETALHE.clear()
    resultado, vagas = {}, threading.Semaphore(paralelas)

    def trabalho(nome, f):
        with vagas:
            t0 = time.time()
            try:
                itens = f()
                resultado[nome] = {'ok': True, 'itens': itens, 'segundos': round(time.time() - t0, 1)}
            except Exception as e:
                resultado[nome] = {'ok': False, 'erro': f'{type(e).__name__}: {e}'[:200], 'segundos': round(time.time() - t0, 1)}

    for nome in fontes:
        if nome in PULAR:
            resultado[nome] = {'ok': False, 'erro': 'pausada: vem barrando o robô (tenta de novo de vez em quando)', 'segundos': 0,
                               'pausada': True}
    linhas = [threading.Thread(target=trabalho, args=(nome, f), daemon=True) for nome, f in fontes.items() if nome not in PULAR]
    for t in linhas:
        t.start()
    for t in linhas:
        t.join(max(0.1, PRAZO - time.monotonic() + folga))   # folga: deixa terminar o pedido que já estava no ar
    itens, situacao = [], {}
    for nome in fontes:
        r = dict(resultado.get(nome) or {'ok': False, 'erro': 'passou do tempo da varredura', 'segundos': orcamento})
        achados = r.pop('itens', [])
        itens += achados
        situacao[nome] = {**r, **({'ofertas': len(achados)} if r['ok'] else {}), 'tipo': TIPO_FONTE.get(nome, 'loja'),
                          **({'detalhe': DETALHE[nome]} if nome in DETALHE else {})}
    PRAZO = None
    return itens, situacao
