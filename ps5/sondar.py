"""Sonda: quais fontes respondem agora e quantos consoles de PS5 cada uma traz (inclui as lojas CANDIDATAS, que não rodam).

Uso: python3 ps5/sondar.py
Não grava nada e não manda mensagem. Serve para decidir o que ligar em lojas.py: loja candidata que aparecer como OK com
consoles pode ir para LOJAS. Vale rodar de redes diferentes (GitHub, seu computador): site que barra uma pode abrir na outra.
"""
import functools, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontes   # noqa: E402
import lojas    # noqa: E402
import monitor  # noqa: E402


def sondar():
    alvos = dict(fontes.FONTES)
    alvos.update({f"(candidata) {l['nome']}": functools.partial(fontes.LEITORES[l['tipo']], l) for l in lojas.CANDIDATAS})
    guardados = {}

    def guardando(nome, f):
        def g():
            guardados[nome] = f()
            return guardados[nome]
        return g

    _, situacao = fontes.coletar({n: guardando(n, f) for n, f in alvos.items()}, orcamento=180)
    print(f"{'':6} {'fonte':30} {'itens':>5} {'consoles':>8} {'menor':>10} {'tempo':>6}  detalhe")
    for nome, s in situacao.items():
        ofertas = [o for o in guardados.get(nome, []) if 'preco' in o]
        consoles = [o for o in ofertas if monitor.avaliar(o['titulo'], o['preco'], o.get('produto_zoom'))['modelo']]
        estado = ('OK' if consoles else 'VAZIO') if s['ok'] else 'BLOQ' if 'Bloqueado' in s.get('erro', '') else 'ERRO'
        menor = monitor.brl(min(o['preco'] for o in consoles)) if consoles else ''
        print(f"{estado:6} {nome:30} {len(guardados.get(nome, [])):>5} {len(consoles):>8} {menor:>10} {s['segundos']:>5}s  "
              f"{s.get('detalhe') or s.get('erro', '')}")


if __name__ == '__main__':
    sondar()
