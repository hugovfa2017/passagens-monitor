"""Sonda: testa no GitHub, sem mandar nada ao Telegram, o que ainda não sabemos se funciona de um servidor de lá.

Rodada 4: o Google Voos abre direto a tela de compra de voos exatos (endereço montado por gvoos.url_compra)?
  - o mesmo voo com 1, 2 e 3 lugares (para dizer quanto custa viajar junto, ou comprar um lugar de cada vez);
  - ida e volta compradas juntas (mesma companhia e companhias diferentes);
e a Zupper responde à busca de ida e volta (vendedores.buscar_iv)?
Não tenta contornar bloqueio nenhum: se o site barrar, só registra.

Uso: python nuvem/sonda.py   (os voos vêm das recomendadas de ultima.json)
"""
import asyncio, json, os, re, sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, 'nuvem'))
import gvoos       # noqa: E402
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
# Resultado da rodada 4: ver o registro da execução (anotar aqui depois).
GOOGLE = 'https://www.google.com/travel/flights?hl=pt-BR&gl=BR&curr=BRL'


def casos(v, series):
    """[(nome, endereço, lugares)] a partir das recomendadas da última rodada que têm o número do voo."""
    rec = {s: r['recomendada'] for s, r in series.items() if r.get('recomendada') and r['recomendada'].get('voos')}
    perna = lambda o: (o['d'], *plano.aeroportos(v, o['sentido']), o['voos'])
    saida = []
    for s, lugares in (('v.volta', (1, 2, 3)), ('wk.volta', (1, 2)), ('wk.ida', (1, 2))):
        if s in rec:
            saida += [(f'{s} {rec[s]["voos"]} com {n} lugar(es)', gvoos.url_compra([perna(rec[s])], n), n) for n in lugares]
    for nome, ida, volta, n in (('W+K', 'wk.ida', 'wk.volta', 2), ('G dezembro', 'g.ida_dez', 'v.volta', 1), ('G janeiro', 'g.ida_jan', 'v.volta', 1)):
        if ida in rec and volta in rec:
            saida.append((f'ida e volta {nome}: {rec[ida]["voos"]} + {rec[volta]["voos"]}, {n} lugar(es)',
                          gvoos.url_compra([perna(rec[ida]), perna(rec[volta])], n, True), n))
    return saida, rec


async def main():
    from playwright.async_api import async_playwright
    v = plano.carregar()
    series = json.load(open(os.path.join(RAIZ, 'ultima.json'), encoding='utf-8'))['series']
    lista, rec = casos(v, series)
    vendedores.configurar(v)
    async with async_playwright() as p:
        opcoes = {'headless': True}
        if os.environ.get('PASSAGENS_CHROMIUM'):
            opcoes['executable_path'] = os.environ['PASSAGENS_CHROMIUM']
        if os.environ.get('HTTPS_PROXY'):
            opcoes['proxy'] = {'server': os.environ['HTTPS_PROXY']}
        nav = await p.chromium.launch(**opcoes)
        ctx = await nav.new_context(locale='pt-BR', timezone_id='America/Sao_Paulo', viewport={'width': 1280, 'height': 900})
        pg = await ctx.new_page()
        await pg.goto(GOOGLE, wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(3000)
        await pg.evaluate(open(os.path.join(RAIZ, 'varredura.js'), encoding='utf-8').read())
        print(f'== Google: {len(lista)} telas de compra abertas direto, dentro da página (como na rodada de verdade)')
        respostas = await pg.evaluate('(a) => window.__gv.comprar(a, 4)', [u for _, u, _ in lista])
        for (nome, _, n), r in zip(lista, respostas):
            r = r or {}
            vend = gvoos.ler_compra(r.get('texto') or '', n)
            print(f'-- {nome}')
            print('   vendedores (por pessoa):', ', '.join(f"{x['vend']} {x['pp']}" for x in vend) or 'nenhum', '| conta:', gvoos.melhor_vendedor(vend))
            print('   voos no endereço:', gvoos.voos_do_tfs(r.get('href') or '') or '-', '| erro:', r.get('erro') or r.get('bloqueio') or '-')
            trecho = (r.get('texto') or r.get('inicio') or '')[:1500 if 'ida e volta' in nome else 400]
            print('   texto:', trecho.replace('\n', ' | '))
        # a mesma tela aberta como página inteira, para comparar (só os casos de ida e volta e o primeiro de um trecho)
        for nome, u, n in [c for c in lista if 'ida e volta' in c[0]][:2] + lista[:1]:
            aba = await ctx.new_page()
            try:
                await aba.goto(u, wait_until='domcontentloaded', timeout=60000)
                await aba.wait_for_timeout(9000)
                txt = await aba.inner_text('body')
                ini = max(txt.find('Reserve com a '), 0)
                print(f'-- página inteira: {nome}')
                print('   vendedores:', ', '.join(f"{x['vend']} {x['pp']}" for x in gvoos.ler_compra(txt, n)) or 'nenhum', '| parou em:', aba.url[:60])
                print('   texto:', txt[ini:ini + 900].replace('\n', ' | '))
            except Exception as e:
                print(f'-- página inteira: {nome}: ERRO {type(e).__name__} {str(e)[:120]}')
            finally:
                await aba.close()
        await ctx.close()

        print('== Zupper: ida e volta juntas')
        sem = asyncio.Semaphore(1)
        for nome, ida, volta, n in (('W+K', 'wk.ida', 'wk.volta', 2), ('G dezembro', 'g.ida_dez', 'v.volta', 1)):
            if ida not in rec or volta not in rec:
                continue
            a, b = rec[ida], rec[volta]
            url = vendedores.url_zupper_iv(a['dest'], b['orig'], a['d'], b['d'], n)
            cartoes = await vendedores._cartoes(nav, url, sem)
            lidos = vendedores.ler_zupper_iv(cartoes, n)
            print(f'-- {nome}: {a["d"]} SAO>{a["dest"]} + {b["d"]} {b["orig"]}>SAO, {n} lugar(es): {len(cartoes)} cartões, {len(lidos)} lidos')
            for x in sorted(lidos, key=lambda x: x['pp'])[:4]:
                i, vt = x['pernas']['ida'], x['pernas']['volta']
                print(f"   R${x['pp']} por pessoa | ida {i['cia']} {i['orig']}>{i['dest']} {i['saida']} {'direto' if i['direto'] else 'conexão'}"
                      f" | volta {vt['cia']} {vt['orig']}>{vt['dest']} {vt['saida']} {'direto' if vt['direto'] else 'conexão'}")
            for t in cartoes[:2]:
                print('   cartão:', t[:900])
            await asyncio.sleep(2)
        await nav.close()


if __name__ == '__main__':
    asyncio.run(main())
