"""Onde o monitor do PS5 lê os preços. Cada fonte devolve uma lista de ofertas (dicts) e nunca derruba a varredura:
quem falhar entra em `erros` e o resto segue.

Sem cupom (preço da vitrine, à vista quando a loja mostra):
- KaBuM!     página da categoria de consoles PlayStation (dados da própria página). O selo "CUPOM X" do produto vira cupom.
- Amazon     busca "playstation 5 console" e "playstation 5 pro". O selo "Economize R$ X com cupom" vira cupom.
- Americanas catálogo público da loja (VTEX); inclui vendedores parceiros, como a Magazine Luiza.
- Zoom       comparador: cada PS5 da categoria, com o preço de cada loja (Magalu, Fast Shop, Webcontinental...).
Com cupom:
- Promobit   ofertas de PS5 postadas pela comunidade, já com o preço final usando o cupom (Mercado Livre, Shopee, Magalu...),
             e os cupons ativos de cada loja.

Mercado Livre, Magazine Luiza, Casas Bahia, Carrefour e Pelando barram robôs; não tentamos contornar (as ofertas deles
chegam pelo Promobit e pelo Zoom).
"""
import gzip, html, json, re, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'
CABECALHOS = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
              'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.5', 'Accept-Encoding': 'gzip'}
BLOQUEIO = re.compile(r'captcha|account-verification|acesso bloqueado|access denied|verifica[çc][ãa]o de seguran[çc]a', re.I)
BRT = timezone(timedelta(hours=-3))


class Bloqueado(Exception):
    pass


def baixar(url, json_api=False, tentativas=3):
    cab = dict(CABECALHOS, **({'Accept': 'application/json'} if json_api else {}))
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=cab), timeout=40) as r:
                corpo = r.read()
                # a KaBuM! às vezes manda gzip mesmo sem pedir; pedimos sempre e descompactamos
                if r.headers.get('Content-Encoding', '').lower() == 'gzip' or corpo[:2] == b'\x1f\x8b':
                    corpo = gzip.decompress(corpo)
                texto = corpo.decode('utf-8', 'replace')
                if not json_api and BLOQUEIO.search(r.url + texto[:3000]):
                    raise Bloqueado(f'pediu verificação anti-robô ({urllib.parse.urlsplit(r.url).netloc})')
                return texto
        except Bloqueado:
            raise
        except Exception as e:
            if getattr(e, 'code', None) in (401, 403) or (getattr(e, 'code', None) == 503 and 'amazon' in url):
                raise Bloqueado(f'HTTP {e.code} (barrou o robô)')
            if i == tentativas - 1:
                raise
            time.sleep(3 * (i + 1))


def next_data(texto):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', texto, re.S)
    if not m:
        raise ValueError('página sem __NEXT_DATA__ (o site mudou?)')
    return json.loads(m[1])


def reais(s):
    """'R$ 4.230,57' -> 4230.57"""
    m = re.search(r'([\d.]+)(?:,(\d{1,2}))?', s.replace('\xa0', ' '))
    return float(m[1].replace('.', '') + '.' + (m[2] or '0')) if m else None


def oferta(fonte, loja, titulo, preco, link, **extra):
    return {'fonte': fonte, 'loja': loja, 'titulo': re.sub(r'\s+', ' ', html.unescape(titulo)).strip(), 'preco': round(preco, 2),
            'link': link, 'cupom': None, 'cupom_origem': None, 'preco_sem_cupom': None, 'obs': '', **extra}


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
        o = oferta('KaBuM!', 'KaBuM!' if not (p.get('flags') or {}).get('isMarketplace') else f"KaBuM! · {p.get('sellerName')}",
                   p['name'], min(avista), f"https://www.kabum.com.br/produto/{p['code']}/{p.get('friendlyName', '')}",
                   id=f"kabum-{p['code']}", obs='à vista no Pix' + (' · oferta relâmpago' if p.get('offer') else ''))
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
        pagina = baixar(url)
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


# ---------------------------------------------------------------- Americanas (VTEX)
AMERICANAS = 'https://www.americanas.com.br/api/catalog_system/pub/products/search?ft={q}&_from={a}&_to={b}'


def americanas():
    out = []
    for q in ('playstation 5 console', 'console ps5'):
        for a in (0, 50):
            lista = json.loads(baixar(AMERICANAS.format(q=urllib.parse.quote(q), a=a, b=a + 49), json_api=True))
            for p in lista:
                for item in p.get('items', [])[:1]:
                    ofertas = [(s['commertialOffer']['Price'], s['sellerName']) for s in item.get('sellers', [])
                               if s['commertialOffer'].get('AvailableQuantity', 0) > 0 and s['commertialOffer'].get('Price', 0) > 0]
                    if ofertas:
                        preco, vendedor = min(ofertas)
                        loja = 'Americanas' if vendedor.upper().startswith('AMERICANAS') else f'Americanas · {vendedor}'
                        out.append(oferta('Americanas', loja, p['productName'], preco, p.get('link', ''), id=f"americanas-{p['productId']}"))
            if len(lista) < 50:
                break
    return list({o['id']: o for o in out}.values())


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
            pst = next_data(baixar(link))['props']['initialReduxState']
            lista = pst['offers'].get('offerList') or []
        except Exception:   # sem a página do produto, fica a melhor oferta da busca
            lista = []
        if not lista and h.get('price'):
            lista = [{'id': (h.get('bestOffer') or {}).get('id', h['objectId']), 'price': h['price'],
                      'sellerName': (h.get('bestOffer') or {}).get('merchantName', '?'), 'name': h['name']}]
        for x in lista:
            if x.get('price'):
                out.append(oferta('Zoom', x.get('sellerName', '?'), x.get('name') or h['name'], x['price'], link,
                                  id=f"zoom-{x['id']}", produto_zoom=h['name'].strip(), obs='à vista'))
    return out


# ---------------------------------------------------------------- Promobit
PROMOBIT = 'https://www.promobit.com.br'
ENCERRADA = re.compile(r'FINISH|EXPIR|REJECT|CLOSED|DELETED|SOLD', re.I)


def _quando(s):
    """'2026-11-13T12:30:07-0300' -> datetime com fuso"""
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z') if s else None


def promobit():
    """Ofertas de PS5 postadas no Promobit. O preço postado já é o final (com o cupom, quando há)."""
    pp = next_data(baixar(PROMOBIT + '/promocoes/playstation-5/s/'))['props']['pageProps']
    out = []
    for p in _com_chave(pp.get("serverOffers"), "offerTitle"):
        if p.get('offerPriceType') != 'NORMAL' or ENCERRADA.search(p.get('offerStatusName', '')):
            continue
        o = oferta('Promobit', p.get('storeName') or '?', p['offerTitle'], float(p['offerPrice']),
                   f"{PROMOBIT}/oferta/{p['offerSlug']}/", id=f"promobit-{p['offerId']}", publicado=p.get('offerPublished'),
                   obs='postado no Promobit')
        c = re.sub(r'\s+', ' ', p.get('offerCoupon') or '').strip()
        if re.fullmatch(r'[A-Za-z0-9_-]{3,30}', c):          # código: AEMEGA19
            o.update(cupom=c, cupom_origem='postado')
        elif re.search(r'R\$|%|\bOFF\b|cupom', c, re.I):    # cupom da página, sem código: "R$300 OFF"
            o.update(cupom=f'cupom {c} (na página)', cupom_origem='postado')
        elif c:                                             # observação: "Lojas Físicas / Parcelado"
            o['obs'] = f'postado no Promobit: {c}'
        out.append(o)
    return out


LOJAS_CUPOM = {'KaBuM!': 'kabum', 'Amazon': 'amazon', 'Magazine Luiza': 'magazine-luiza', 'Mercado Livre': 'mercado-livre',
               'Americanas': 'americanas', 'Casas Bahia': 'casas-bahia', 'Carrefour': 'carrefour', 'Shopee': 'shopee',
               'Aliexpress': 'aliexpress'}


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
    if m := re.search(r'at[ée] R\$\s*([\d.]+(?:,\d\d)?)\)', t, re.I):
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


def _com_chave(o, chave):
    """Todos os dicts com `chave` dentro de o (a lista de cupons vem como lista ou dentro de um dict, conforme a página)."""
    if isinstance(o, dict):
        if chave in o:
            yield o
        else:
            for v in o.values():
                yield from _com_chave(v, chave)
    elif isinstance(o, list):
        for v in o:
            yield from _com_chave(v, chave)


def cupons_promobit(lojas):
    """Cupons ativos das lojas pedidas, como o Promobit publica: [{loja, codigo, titulo, validade, regra}]"""
    agora = datetime.now(BRT)
    out, erros = [], []
    for loja in lojas:
        slug = LOJAS_CUPOM.get(loja)
        if not slug:
            continue
        try:
            pp = next_data(baixar(f'{PROMOBIT}/cupons/loja/{slug}/'))['props']['pageProps']
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
                        'publicado': c.get('couponPublished'), 'regra': regra_cupom(c.get('couponTitle', ''))})
    return out, erros


FONTES = {'KaBuM!': kabum, 'Amazon': amazon, 'Americanas': americanas, 'Zoom': zoom, 'Promobit': promobit}


def coletar(fontes=FONTES):
    """Roda todas as fontes. Devolve (ofertas, situação de cada fonte)."""
    ofertas, situacao = [], {}
    for nome, f in fontes.items():
        t0 = time.time()
        try:
            achadas = f()
            ofertas += achadas
            situacao[nome] = {'ok': True, 'ofertas': len(achadas), 'segundos': round(time.time() - t0, 1)}
        except Exception as e:
            situacao[nome] = {'ok': False, 'erro': f'{type(e).__name__}: {e}'[:200], 'segundos': round(time.time() - t0, 1)}
    return ofertas, situacao
