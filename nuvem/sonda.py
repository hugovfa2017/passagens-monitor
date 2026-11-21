"""Sonda: testa no GitHub, sem mandar nada ao Telegram, o que ainda não sabemos se funciona de um servidor de lá.

Rodada 5:
  - quanto tempo cada vendedor leva para mostrar os cartões e se rolar a página traz cartões novos (para encurtar a
    espera de quem desenha tudo de uma vez), com e sem as imagens bloqueadas;
  - fontes novas: abrem a página de resultados para um navegador do GitHub?
Não tenta contornar bloqueio nenhum: se o site barrar, só registra.

Uso: python nuvem/sonda.py
"""
import asyncio, os, re, sys, time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, 'nuvem'))
import plano       # noqa: E402
import vendedores  # noqa: E402

# Rodada 1-2 (05/11/2026): funcionam MaxMilhas, 123milhas, Zupper; bloqueiam Decolar, Skyscanner, Kayak, Kiwi, Expedia,
# Trip.com, LATAM, Gol; Booking tem desafio anti-robô; Smiles exige login.
# Rodada 3: primeiro só a página inicial de cada candidato, para ver quem deixa um navegador do GitHub entrar.
# Resultado da rodada 3 (20/11/2026, só a página inicial): abrem Gotogate, Mytrip, Passagens Promo, Voopter (já com
# preços na página) e Momondo; barram Azul (Access Denied), ViajaNet e Viajala (403); kiss&fly, Hurb e Submarino Viagens
# não têm mais endereço. Viaza e Vai de Promo já estão em produção (nuvem/vendedores.py).
# Sondagem do Google Voos no mesmo dia: Viracopos aparece normalmente; a busca de ida e volta (dois trechos) abre;
# não há nenhum voo para Mossoró (MVF), nem com conexão, nem saindo do Recife.
# Resultado da rodada 4 (21/11/2026):
#   - tela de compra aberta direto pelo número do voo: funciona com 1, 2 e 3 lugares; às vezes responde "Algo deu errado"
#     e abre na tentativa seguinte (varredura.js tenta até três vezes);
#   - ida e volta juntas no Google: não serve. Mesma companhia, voltando por outro aeroporto: saiu mais cara que os dois
#     trechos separados e só "ligue para reservar"; companhias diferentes: "itinerário não está mais disponível";
#   - Zupper ida e volta: responde (15 cartões por busca) e o par dos mesmos voos saiu R$126 a R$143 por pessoa mais
#     barato que os dois trechos comprados separados nos outros vendedores.
# Resultado da rodada 5 (21/11/2026):
#   - MaxMilhas, Zupper e Vai de Promo trazem voos novos a cada rolagem (o menor preço já aparece 1 s depois do primeiro
#     cartão); a 123milhas mostra os mesmos 10 cartões do começo ao fim: não precisa rolar (vendedores.ROLAGENS);
#   - bloquear as imagens não vale: na MaxMilhas a leitura passou a achar menos voos;
#   - fontes novas: Momondo responde com a página "o que é um bot", eDestinos com 403 e a CVC com a verificação da
#     Cloudflare (ficam de fora: não contornamos bloqueio); Voopter e Gotogate abrem só a página inicial, a busca é por
#     formulário, e o que venderiam já chega por outro caminho (os mesmos vendedores, ou agência de fora).
DATA, NE, PAX = '2026-12-28', 'FOR', 1
PESADOS = re.compile(r'\.(png|jpe?g|gif|webp|svg|ico|woff2?|ttf|otf|mp4|webm)(\?|$)', re.I)
BLOQUEIO = r'captcha|datadome|access denied|acesso negado|forbidden|blocked|bloquead|verify you are|verifique|robot|robô|cloudflare|press & hold|pressione e segure|humano'
NOVAS = {
    'Momondo': f'https://www.momondo.com.br/flight-search/SAO-{NE}/{DATA}?sort=price_a',
    'eDestinos': f'https://www.edestinos.com.br/flights/select/oneway/mp/sao/ap/{NE.lower()}?departureDate={DATA}&pa={PAX}&py=0&pc=0&pi=0&sc=economy',
    'CVC': f'https://www.cvc.com.br/passagens/v2/search/SAO/{NE}?Date1={DATA}&ADT={PAX}&CHD=0&INF=0&CLA=all',
    'Voopter': 'https://www.voopter.com.br/',
    'Gotogate': 'https://br.gotogate.com/',
}


async def medir(nav, vendedor, bloquear):
    """Uma busca num vendedor, anotando quando os cartões aparecem e quantos há depois de cada espera e de cada rolagem."""
    url, casa = vendedores.URL[vendedor]('SAO', NE, DATA, PAX), vendedores.CASA.get(vendedor)
    ctx = await nav.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
    if bloquear:
        await ctx.route(PESADOS, lambda rota: rota.abort())
    pg = await ctx.new_page()
    t0 = time.monotonic()
    agora = lambda: round(time.monotonic() - t0, 1)
    try:
        if casa:
            await pg.goto(casa, wait_until='domcontentloaded', timeout=60000)
            await pg.wait_for_timeout(3000)
        await pg.goto(url, wait_until='domcontentloaded', timeout=60000)
        try:
            await pg.wait_for_function(f'() => ({vendedores.LER_CARTOES})().length > 0', timeout=45000)
        except Exception:
            print(f'-- {vendedor} (imagens {"bloqueadas" if bloquear else "normais"}): nenhum cartão em {agora()}s')
            return
        vistos, passos = {}, []

        async def contar(rotulo):
            for t in await pg.evaluate(vendedores.LER_CARTOES):
                vistos[t] = True
            lidos = [x for x in vendedores.LER[vendedor](list(vistos), PAX) if vendedores._aceita(x)]
            passos.append(f'{rotulo}: {agora()}s, {len(vistos)} cartões, {len(lidos)} voos válidos, menor R${min((x["pp"] for x in lidos), default=0)}')

        await contar('primeiro cartão')
        for espera in (1000, 2000):
            await pg.wait_for_timeout(espera)
            await contar(f'+{espera // 1000}s')
        for n in range(1, 6):
            await pg.mouse.wheel(0, 1500)
            await pg.wait_for_timeout(1200)
            await contar(f'rolagem {n}')
        print(f'-- {vendedor} (imagens {"bloqueadas" if bloquear else "normais"}):')
        for linha in passos:
            print('   ' + linha)
    except Exception as e:
        print(f'-- {vendedor}: ERRO {type(e).__name__} {str(e)[:120]}')
    finally:
        await ctx.close()


async def nova(nav, nome, url):
    pg = await nav.new_page(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
    try:
        resp = await pg.goto(url, wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(12000)
        txt = await pg.inner_text('body')
        precos = re.findall(r'R\$\s?[\d.]+(?:,\d\d)?', txt)
        print(f'== {nome}: HTTP {resp.status if resp else "?"} | título: {(await pg.title())[:80]} | parou em: {pg.url[:110]}')
        print(f'   preços na página: {len(precos)} {precos[:6]} | sinal de bloqueio: {bool(re.search(BLOQUEIO, txt[:3000], re.I))}')
        ini = txt.find('R$')
        print('   início:', txt[:300].replace('\n', ' | '))
        if ini > 0:
            print('   perto do primeiro preço:', txt[max(ini - 400, 0):ini + 300].replace('\n', ' | '))
        links = await pg.evaluate("() => [...new Set([...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => /passag|voo|flight|air|busca|search/i.test(h)))].slice(0, 12)")
        print('   links de busca:', links)
    except Exception as e:
        print(f'== {nome}: ERRO {type(e).__name__} {str(e)[:120]}')
    finally:
        await pg.close()


async def main():
    from playwright.async_api import async_playwright
    vendedores.configurar(plano.carregar())
    async with async_playwright() as p:
        opcoes = {'headless': True}
        if os.environ.get('PASSAGENS_CHROMIUM'):
            opcoes['executable_path'] = os.environ['PASSAGENS_CHROMIUM']
        if os.environ.get('HTTPS_PROXY'):
            opcoes['proxy'] = {'server': os.environ['HTTPS_PROXY']}
        nav = await p.chromium.launch(**opcoes)
        print(f'== vendedores: SAO -> {NE} em {DATA}, {PAX} lugar; um vendedor por vez, primeiro com as imagens normais')
        for bloquear in (False, True):
            await asyncio.gather(*(medir(nav, x, bloquear) for x in vendedores.URL))
            await asyncio.sleep(3)
        for nome, url in NOVAS.items():
            await nova(nav, nome, url)
        await nav.close()


if __name__ == '__main__':
    asyncio.run(main())
