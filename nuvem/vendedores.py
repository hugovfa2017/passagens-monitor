"""Busca direta na MaxMilhas, na 123milhas e na Zupper (que o Google não mostra para servidores fora do Brasil).

Abre a página de resultados de cada vendedor e lê os cartões de voo. Só aceita voo direto
ou com 1 parada e duração total de até 5h30 (aproximação de "escala de até 2h": o cartão não
mostra o tempo de conexão). Preço é o total a pagar (na 123milhas, no Pix) dividido pelos adultos.
"""
import asyncio, json, re, urllib.parse

CIAS = {'gol': 'Gol', 'latam': 'LATAM', 'azul': 'Azul'}
MAX_MIN_COM_ESCALA = 330   # ponytail: duração total como proxy da escala; trocar se o cartão passar a mostrar a conexão

# JS que lê os cartões já desenhados na página (devolve texto de cada cartão + nome do arquivo do logo da cia,
# porque a Zupper só mostra a companhia como imagem)
LER_CARTOES = r"""() => [...document.querySelectorAll('button, a')]
  .filter(b => /^\s*(Comprar|Ir para pagamento)\s*$/i.test(b.textContent || ''))
  .map(b => { let c = b; for (let i = 0; i < 14 && c.parentElement; i++) { c = c.parentElement;
      if (/\d\d:\d\d[\s\S]*\d\d:\d\d/.test(c.innerText) && /R\$/.test(c.innerText)) break; }
    const logos = [...c.querySelectorAll('img')].map(i => (i.src || '').split('/').pop()).filter(n => n && !/App-Icon|rounded|\.svg/.test(n));
    return c.innerText.replace(/\n+/g, ' | ') + ' | LOGO:' + [...new Set(logos)].join(','); })"""


def _min(h, m):
    return int(h) * 60 + int(m or 0)


def ler_maxmilhas(cartoes, pax):
    out = []
    for t in cartoes:
        m = re.search(r'(Gol|Latam|Azul)[^|]*\|\s*([A-Z]{3})\s*\|\s*(\d\d:\d\d)\s*\|\s*(\d+)h(\d*)m?\s*\|\s*(Direto|\d+ paradas?)\s*\|'
                      r'\s*([A-Z]{3})\s*\|\s*(\d\d:\d\d)[\s\S]*?Valor total\s*\|\s*R\$\s*([\d.]+),(\d\d)', t, re.I)
        if m:
            out.append({'vend': 'MaxMilhas', 'cia': CIAS[m[1].lower()], 'orig': m[2], 'saida': m[3], 'dur': _min(m[4], m[5]),
                        'direto': m[6].lower() == 'direto', 'dest': m[7], 'chegada': m[8], 'voo': None,
                        'pp': round((int(m[9].replace('.', '')) + int(m[10]) / 100) / pax)})
    return out


def ler_123milhas(cartoes, pax):
    out = []
    for t in cartoes:
        m = re.search(r'(GOL|LATAM|AZUL)[^|]*\|\s*voo ([A-Z0-9]{2})-(\d+)\s*\|\s*(\d\d:\d\d)\s*\|\s*([A-Z]{3})\s*\|\s*(Direto|\d+ Paradas?)\s*\|'
                      r'\s*(\d+)h\s*(\d+)m\s*\|\s*(\d\d:\d\d)\s*\|\s*([A-Z]{3})[\s\S]*?Total no Pix\s*\|\s*R\$\s*([\d.]+)', t, re.I)
        if m:
            out.append({'vend': '123milhas', 'cia': CIAS[m[1].lower()], 'voo': (m[2], m[3]), 'saida': m[4], 'orig': m[5],
                        'direto': m[6].lower() == 'direto', 'dur': _min(m[7], m[8]), 'chegada': m[9], 'dest': m[10],
                        'pp': round(int(m[11].replace('.', '')) / pax)})
    return out


def _cia_logo(nome):  # a Zupper só mostra a companhia como imagem: G3...png, LA...png, logo_azul...png
    return 'Gol' if re.match(r'G3|gol', nome, re.I) else 'LATAM' if re.match(r'LA|JJ|latam', nome, re.I) else \
        'Azul' if re.search(r'azul|^AD', nome, re.I) else None


OPCAO_Z = re.compile(r'Saída\s*\|\s*(\d\d:\d\d)\s*\|\s*Chegada\s*\|\s*(\d\d:\d\d)[^|]*\|\s*Duração\s*\|\s*(\d+)h(\d*)m?\s*\|\s*Voo (Direto|com conex)')
TOTAL_Z = re.compile(r'TOTAL A PAGAR\s*\|\s*R\$\s*([\d.]+),(\d\d)')


def _perna_zupper(parte, cia):
    """Um trecho do cartão da Zupper (pode listar vários horários pelo mesmo preço): fica com o melhor aceitável."""
    m = re.search(r'De:\s*([A-Z]{3})[\s\S]*?Para:\s*([A-Z]{3})', parte)
    if not m or not cia:
        return None
    ops = [{'cia': cia, 'orig': m[1], 'dest': m[2], 'saida': o[1], 'chegada': o[2], 'dur': _min(o[3], o[4]),
            'direto': o[5] == 'Direto', 'voo': None} for o in OPCAO_Z.finditer(parte)]
    boas = [x for x in ops if x['direto'] or x['dur'] <= MAX_MIN_COM_ESCALA]
    return min(boas or ops, key=lambda x: (not x['direto'], x['dur']), default=None)   # nenhuma boa: o filtro descarta depois


def _logos(t):
    return (re.search(r'LOGO:(\S*)', t) or [''] * 2)[1].split(',')


def _total(t, pax):
    m = TOTAL_Z.search(t)
    return round((int(m[1].replace('.', '')) + int(m[2]) / 100) / pax) if m else None


def ler_zupper(cartoes, pax):
    out = []
    for t in cartoes:
        perna, pp = _perna_zupper(t, _cia_logo(_logos(t)[0])), _total(t, pax)
        if perna and pp:
            out.append({**perna, 'vend': 'Zupper', 'pp': pp})
    return out


def ler_zupper_iv(cartoes, pax):
    """Cartões da busca de ida e volta: o preço é do par; cada trecho com seu voo (logos na ordem ida, volta)."""
    out = []
    for t in cartoes:
        if ' | Volta | ' not in t:
            continue
        a, b = t.split(' | Volta | ', 1)
        lg = _logos(t)
        ida, volta, pp = _perna_zupper(a, _cia_logo(lg[0])), _perna_zupper(b, _cia_logo(lg[-1])), _total(t, pax)
        if ida and volta and pp:
            out.append({'vend': 'Zupper', 'pp': pp, 'pernas': {'ida': ida, 'volta': volta}})
    return out


def url_maxmilhas(o, d, data, pax):
    return f'https://www.maxmilhas.com.br/busca-passagens-aereas/OW/{o}/{d}/{data}/{pax}/0/0/EC'


def url_123milhas(o, d, data, pax):
    a, m, dd = data.split('-')
    return f'https://123milhas.com/v2/busca?de={o}&para={d}&adultos={pax}&criancas=0&bebes=0&ida={dd}-{m}-{a}&classe=3'


def url_zupper(o, d, data, pax):
    trecho = urllib.parse.quote(json.dumps([{'originAirport': o, 'destinationAirport': d, 'departureDate': data}], separators=(',', ':')))
    return (f'https://www.zupper.com.br/resultados?type=oneWay&adultQty={pax}&childrenQty=0&infantQty=0'
            f'&onlyBusinessClass=false&slices={trecho}')


def url_zupper_iv(ne_ida, ne_volta, ida, volta, pax):
    trechos = [{'originAirport': 'SAO', 'destinationAirport': ne_ida, 'departureDate': ida},
               {'originAirport': ne_volta, 'destinationAirport': 'SAO', 'departureDate': volta}]
    return (f'https://www.zupper.com.br/resultados?type=roundTrip&adultQty={pax}&childrenQty=0&infantQty=0'
            f'&onlyBusinessClass=false&slices={urllib.parse.quote(json.dumps(trechos, separators=(",", ":")))}')


URL = {'MaxMilhas': url_maxmilhas, '123milhas': url_123milhas, 'Zupper': url_zupper}
LER = {'MaxMilhas': ler_maxmilhas, '123milhas': ler_123milhas, 'Zupper': ler_zupper}


async def _cartoes(navegador, url, sem):
    """Abre a página de resultados e devolve o texto de cada cartão de voo ([] se não carregar)."""
    async with sem:
        # uma sessão por busca: a 123milhas não devolve nada a duas buscas abertas na mesma sessão
        ctx = await navegador.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
        pg = await ctx.new_page()
        try:
            await pg.goto(url, wait_until='domcontentloaded', timeout=60000)
            try:
                await pg.wait_for_function(f'() => ({LER_CARTOES})().length > 0', timeout=45000)
            except Exception:
                return []          # sem voos ou página não carregou: o Google continua valendo
            await pg.wait_for_timeout(3000)   # deixa os preços chegarem
            # a MaxMilhas só desenha os cartões visíveis e apaga os que saem da tela: lê a cada rolagem e junta
            vistos = {}
            for _ in range(5):
                for t in await pg.evaluate(LER_CARTOES):
                    vistos[t] = True
                await pg.mouse.wheel(0, 1500)
                await pg.wait_for_timeout(1200)
            return list(vistos)
        except Exception:
            return []
        finally:
            await ctx.close()


def _aceita(x):
    """Sem Viracopos (longe para o Hugo); escala só se a viagem toda couber em MAX_MIN_COM_ESCALA."""
    if 'VCP' in (x['orig'], x['dest']) or (not x['direto'] and x['dur'] > MAX_MIN_COM_ESCALA):
        return False
    x['tipo'] = 'direto' if x['direto'] else 'com escala'
    return True


async def buscar(navegador, vendedor, sentido, data, ne, pax, sem):
    """Ofertas de um vendedor para SAO <-> aeroporto do Nordeste numa data."""
    o, d = ('SAO', ne) if sentido == 'ida' else (ne, 'SAO')
    validas = [x for x in LER[vendedor](await _cartoes(navegador, URL[vendedor](o, d, data, pax), sem), pax) if _aceita(x)]
    for x in validas:
        # link da busca do vendedor já no aeroporto exato do voo (poucos voos na lista, o certo no topo)
        x['link'] = URL[vendedor](x['orig'], x['dest'], data, pax)
    return validas


async def buscar_iv(navegador, ida, volta, ne_ida, ne_volta, pax, sem):
    """Ida e volta compradas juntas na Zupper (às vezes sai mais barato que dois trechos separados)."""
    url = url_zupper_iv(ne_ida, ne_volta, ida, volta, pax)
    validas = [x for x in ler_zupper_iv(await _cartoes(navegador, url, sem), pax) if _aceita(x['pernas']['ida']) and _aceita(x['pernas']['volta'])]
    for x in validas:
        x['link'] = url
    return validas


def _teste():
    mm = [' Ida - Domingo 27/12/26 | Gol | GRU | 22:10 | 2h50m | Direto | JDO | 01:00 | +1 | Detalhes | Até 12kg | Valor total | R$ 1.787,74 | -4% | Comp']
    assert ler_maxmilhas([mm[0].replace('| Gol |', '| Latam Airlines |')], 2)[0]['cia'] == 'LATAM'
    r = ler_maxmilhas(mm, 2)[0]
    assert (r['cia'], r['orig'], r['saida'], r['dest'], r['pp'], r['direto']) == ('Gol', 'GRU', '22:10', 'JDO', 894, True), r
    t3 = ['Ida | Dom, 27/12/2026 | GOL | voo G3-1580 | 22:10 | GRU | Direto | 02h 50m | 01:00 | JDO | +1 | Detalhes | R$ | 819 | No Pix'
          ' | 2 adultos | R$ 1.905 | Valor das taxas | R$ 77 | Desconto no Pix | -R$ 267 | Total no Pix | R$ 1.714 | Comprar',
          'Ida | Dom, 27/12/2026 | AZUL | voo AD-4152 | 23:00 | GRU | 1 Parada | 15h 40m | 14:40 | JDO | Total no Pix | R$ 3.100 | Comprar',
          'Ida | Qui, 24/12/2026 | LATAM Airlines | voo LA-3440 | 13:35 | GRU | Direto | 03h 20m | 16:55 | NAT | Inclui uma mochila | Total no Pix | R$ 2.310 | Comprar']
    r = ler_123milhas(t3, 2)
    assert r[0]['voo'] == ('G3', '1580') and r[0]['pp'] == 857 and r[0]['direto'] and r[1]['dur'] == 940 and not r[1]['direto'], r
    assert (r[2]['cia'], r[2]['voo'], r[2]['dest'], r[2]['pp']) == ('LATAM', ('LA', '3440'), 'NAT', 1155), r
    assert url_123milhas('SAO', 'JDO', '2026-12-27', 2).endswith('ida=27-12-2026&classe=3')
    # Zupper: textos reais (cia só no nome do arquivo do logo)
    zp = ['Ida | De: GRU - Sao Paulo  -  Para: JDO - Juazeiro Do Norte | Saída | 22:10 | Chegada | 01:00 +1 | Duração | 2h50m | Voo Direto'
          ' | Detalhes  | Melhor Preço | Light | R$ | 1.855,44 | Tarifa por adulto | R$ 890,97 | TOTAL A PAGAR | R$ 1.855,44 | Ir para pagamento'
          ' | LOGO:G35ff4fe5f1e26d.png',
          'Ida | De: CGH - Sao Paulo  -  Para: JDO - Juazeiro Do Norte | Saída | 14:25 | Chegada | 14:40 +1 | Duração | 24h15m'
          ' | Voo com conexões | Detalhes  | Azul | R$ | 2.034,98 | TOTAL A PAGAR | R$ 2.034,98 | LOGO:logo_azul-PNG-0162bb4daa0efff.png',
          'Ida | De: GRU - Sao Paulo  -  Para: JDO - Juazeiro Do Norte | Saída | 07:45 | Chegada | 10:40 | Duração | 2h55m | Voo Direto'
          ' | TOTAL A PAGAR | R$ 2.460,26 | LOGO:LA5ff4fe43c4a2b.png']
    z = ler_zupper(zp, 2)
    assert [(x['cia'], x['orig'], x['saida'], x['pp'], x['direto'], x['dur']) for x in z] == \
        [('Gol', 'GRU', '22:10', 928, True, 170), ('Azul', 'CGH', '14:25', 1017, False, 1455), ('LATAM', 'GRU', '07:45', 1230, True, 175)], z
    assert '%22originAirport%22%3A%22SAO%22' in url_zupper('SAO', 'JDO', '2026-12-27', 2)
    assert ler_maxmilhas([mm[0] + ' | LOGO:x.png'], 2) and LER['Zupper'] is ler_zupper
    # Zupper ida e volta (texto real): vários horários por trecho, cias diferentes; fica o direto
    iv = ['Ida | De: GRU - Sao Paulo  -  Para: FOR - Fortaleza | Saída | 07:00 | Chegada | 10:25 | Duração | 3h25m | Voo Direto | Detalhes '
          ' | Volta | De: JDO - Juazeiro Do Norte  -  Para: GRU - Sao Paulo | Saída | 15:10 | Chegada | 23:50 | Duração | 8h40m'
          ' | Voo com conexão | Detalhes  | Saída | 04:05 | Chegada | 07:15 | Duração | 3h10m | Voo Direto | Detalhes '
          ' | TOTAL A PAGAR | R$ 2.975,00 | Ir para pagamento | LOGO:LA5ff4fe43c4a2b.png,G35ff4fe5f1e26d.png',
          'Ida | De: VCP - Campinas/Sao Paulo  -  Para: FOR - Fortaleza | Saída | 12:40 | Chegada | 17:45 | Duração | 5h5m'
          ' | Voo com conexão | Volta | De: JDO - Juazeiro Do Norte  -  Para: GRU - Sao Paulo | Saída | 04:05 | Chegada | 07:15'
          ' | Duração | 3h10m | Voo Direto | TOTAL A PAGAR | R$ 2.500,00 | LOGO:logo_azul-PNG-0162bb4daa0efff.png']
    r = ler_zupper_iv(iv, 2)
    assert len(r) == 2 and r[0]['pp'] == 1488, r
    ri, rv = r[0]['pernas']['ida'], r[0]['pernas']['volta']
    assert (ri['cia'], ri['orig'], ri['dest'], ri['saida']) == ('LATAM', 'GRU', 'FOR', '07:00'), r[0]
    assert (rv['cia'], rv['saida'], rv['direto']) == ('Gol', '04:05', True), r[0]
    assert _aceita(ri) and _aceita(rv) and not _aceita(r[1]['pernas']['ida'])   # ida por Viracopos fica fora
    assert ler_zupper([iv[0].split(' | Volta | ')[0] + ' | TOTAL A PAGAR | R$ 900,00 | LOGO:G3x.png'], 2)[0]['cia'] == 'Gol'
    u = url_zupper_iv('FOR', 'JDO', '2026-12-24', '2027-01-20', 2)
    assert 'type=roundTrip' in u and '%22originAirport%22%3A%22JDO%22' in u and '%22destinationAirport%22%3A%22FOR%22' in u
    print('ok')


if __name__ == '__main__':
    _teste()
