"""Busca direta na MaxMilhas e na 123milhas (que o Google não mostra para servidores fora do Brasil).

Abre a página de resultados de cada vendedor e lê os cartões de voo. Só aceita voo direto
ou com 1 parada e duração total de até 5h30 (aproximação de "escala de até 2h": o cartão não
mostra o tempo de conexão). Preço é o total no Pix dividido pelos adultos.
"""
import asyncio, re

CIAS = {'gol': 'Gol', 'latam': 'LATAM', 'azul': 'Azul'}
MAX_MIN_COM_ESCALA = 330   # ponytail: duração total como proxy da escala; trocar se o cartão passar a mostrar a conexão

# JS que lê os cartões já desenhados na página (devolve texto de cada cartão)
LER_CARTOES = r"""() => [...document.querySelectorAll('button, a')]
  .filter(b => /^\s*Comprar\s*$/i.test(b.textContent || ''))
  .map(b => { let c = b; for (let i = 0; i < 14 && c.parentElement; i++) { c = c.parentElement;
      if (/\d\d:\d\d[\s\S]*\d\d:\d\d/.test(c.innerText) && /R\$/.test(c.innerText)) break; }
    return c.innerText.replace(/\n+/g, ' | '); })"""


def _min(h, m):
    return int(h) * 60 + int(m or 0)


def ler_maxmilhas(cartoes, pax):
    out = []
    for t in cartoes:
        m = re.search(r'(Gol|Latam|Azul)\s*\|\s*([A-Z]{3})\s*\|\s*(\d\d:\d\d)\s*\|\s*(\d+)h(\d*)m?\s*\|\s*(Direto|\d+ paradas?)\s*\|'
                      r'\s*([A-Z]{3})\s*\|\s*(\d\d:\d\d)[\s\S]*?Valor total\s*\|\s*R\$\s*([\d.]+),(\d\d)', t, re.I)
        if m:
            out.append({'vend': 'MaxMilhas', 'cia': CIAS[m[1].lower()], 'orig': m[2], 'saida': m[3], 'dur': _min(m[4], m[5]),
                        'direto': m[6].lower() == 'direto', 'dest': m[7], 'chegada': m[8], 'voo': None,
                        'pp': round((int(m[9].replace('.', '')) + int(m[10]) / 100) / pax)})
    return out


def ler_123milhas(cartoes, pax):
    out = []
    for t in cartoes:
        m = re.search(r'(GOL|LATAM|AZUL)\s*\|\s*voo ([A-Z0-9]{2})-(\d+)\s*\|\s*(\d\d:\d\d)\s*\|\s*([A-Z]{3})\s*\|\s*(Direto|\d+ Paradas?)\s*\|'
                      r'\s*(\d+)h\s*(\d+)m\s*\|\s*(\d\d:\d\d)\s*\|\s*([A-Z]{3})[\s\S]*?Total no Pix\s*\|\s*R\$\s*([\d.]+)', t, re.I)
        if m:
            out.append({'vend': '123milhas', 'cia': CIAS[m[1].lower()], 'voo': (m[2], m[3]), 'saida': m[4], 'orig': m[5],
                        'direto': m[6].lower() == 'direto', 'dur': _min(m[7], m[8]), 'chegada': m[9], 'dest': m[10],
                        'pp': round(int(m[11].replace('.', '')) / pax)})
    return out


def url_maxmilhas(o, d, data, pax):
    return f'https://www.maxmilhas.com.br/busca-passagens-aereas/OW/{o}/{d}/{data}/{pax}/0/0/EC'


def url_123milhas(o, d, data, pax):
    a, m, dd = data.split('-')
    return f'https://123milhas.com/v2/busca?de={o}&para={d}&adultos={pax}&criancas=0&bebes=0&ida={dd}-{m}-{a}&classe=3'


async def buscar(contexto, vendedor, sentido, data, ne, pax, sem):
    """Ofertas de um vendedor para SAO <-> aeroporto do Nordeste numa data."""
    o, d = ('SAO', ne) if sentido == 'ida' else (ne, 'SAO')
    url = (url_maxmilhas if vendedor == 'MaxMilhas' else url_123milhas)(o, d, data, pax)
    async with sem:
        pg = await contexto.new_page()
        try:
            await pg.goto(url, wait_until='domcontentloaded', timeout=60000)
            try:
                await pg.wait_for_function(LER_CARTOES + '().length > 0', timeout=45000)
            except Exception:
                return []          # sem voos ou página não carregou: o Google continua valendo
            for _ in range(3):     # a MaxMilhas desenha os cartões aos poucos
                await pg.mouse.wheel(0, 4000)
                await pg.wait_for_timeout(1200)
            cartoes = await pg.evaluate(LER_CARTOES)
        except Exception:
            return []
        finally:
            await pg.close()
    ofertas = (ler_maxmilhas if vendedor == 'MaxMilhas' else ler_123milhas)(cartoes, pax)
    validas = []
    for x in ofertas:
        if 'VCP' in (x['orig'], x['dest']):
            continue           # Viracopos fica fora (longe para o Hugo)
        if not x['direto'] and x['dur'] > MAX_MIN_COM_ESCALA:
            continue
        x['tipo'] = 'direto' if x['direto'] else 'com escala'
        # link da busca do vendedor já no aeroporto exato do voo (poucos voos na lista, o certo no topo)
        x['link'] = (url_maxmilhas if vendedor == 'MaxMilhas' else url_123milhas)(x['orig'], x['dest'], data, pax)
        validas.append(x)
    return validas


def _teste():
    mm = [' Ida - Domingo 27/12/26 | Gol | GRU | 22:10 | 2h50m | Direto | JDO | 01:00 | +1 | Detalhes | Até 12kg | Valor total | R$ 1.787,74 | -4% | Comp']
    r = ler_maxmilhas(mm, 2)[0]
    assert (r['cia'], r['orig'], r['saida'], r['dest'], r['pp'], r['direto']) == ('Gol', 'GRU', '22:10', 'JDO', 894, True), r
    t3 = ['Ida | Dom, 27/12/2026 | GOL | voo G3-1580 | 22:10 | GRU | Direto | 02h 50m | 01:00 | JDO | +1 | Detalhes | R$ | 819 | No Pix'
          ' | 2 adultos | R$ 1.905 | Valor das taxas | R$ 77 | Desconto no Pix | -R$ 267 | Total no Pix | R$ 1.714 | Comprar',
          'Ida | Dom, 27/12/2026 | AZUL | voo AD-4152 | 23:00 | GRU | 1 Parada | 15h 40m | 14:40 | JDO | Total no Pix | R$ 3.100 | Comprar']
    r = ler_123milhas(t3, 2)
    assert r[0]['voo'] == ('G3', '1580') and r[0]['pp'] == 857 and r[0]['direto'] and r[1]['dur'] == 940 and not r[1]['direto'], r
    assert url_123milhas('SAO', 'JDO', '2026-12-27', 2).endswith('ida=27-12-2026&classe=3')
    print('ok')


if __name__ == '__main__':
    _teste()
