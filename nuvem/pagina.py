"""Gera docs/index.html (GitHub Pages) a partir do mesmo painel do Claude (painel-index.html).

O painel foi escrito para o tipo Dashboard do Claude, que injeta um objeto `dash` e os tokens de cor.
Aqui um pequeno substituto de `dash` lê o painel.json ao lado da página, e os tokens são definidos
para os temas claro e escuro. Uso: python nuvem/pagina.py  (rodado pelo workflow a cada varredura)
"""
import os, shutil

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(RAIZ, 'docs')

TOPO = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<title>Passagens Nordeste</title>
<style>
  :root {
    --color-bg: #f7f8f8; --color-panel: #ffffff; --color-fg: #17201f; --color-fg-muted: #5b6766;
    --color-border-line: #dde3e2; --color-ok: #1d7a46; --color-warn: #a35f00; --color-bad: #b42318;
    --cds-chart-grid: #e7ecec; --cds-chart-muted: #b9c4c3; --cds-chart-reference: #8d9998;
    --cds-surface-popover: #ffffff; --cds-border: #d3dad9; --cds-text-secondary: #5b6766;
    --cds-shadow-popover: 0 8px 24px -12px rgba(16, 32, 30, .35);
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --color-bg: #111716; --color-panel: #18201f; --color-fg: #e7eeed; --color-fg-muted: #9aa8a6;
      --color-border-line: #2a3534; --color-ok: #5cc98a; --color-warn: #e0a64a; --color-bad: #f07167;
      --cds-chart-grid: #232d2c; --cds-chart-muted: #3d4a49; --cds-chart-reference: #6f7d7b;
      --cds-surface-popover: #1e2726; --cds-border: #33403f; --cds-text-secondary: #9aa8a6;
      --cds-shadow-popover: 0 8px 24px -12px rgba(0, 0, 0, .6);
      color-scheme: dark;
    }
  }
  :root {
    --font-anthropic-sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --font-anthropic-serif: ui-serif, Georgia, serif;
    --cds-gap-xs: 4px; --cds-gap-sm: 8px; --cds-gap-md: 12px; --cds-gap-lg: 16px; --cds-gap-xl: 24px;
    --cds-pad-xs: 4px; --cds-pad-sm: 8px; --cds-pad-md: 12px; --cds-pad-lg: 20px;
    --cds-radius: 8px; --cds-font-weight-medium: 550; --cds-dur-slow: .3s;
    --cds-font-size-caption: .8125rem; --cds-font-size-body: .9375rem; --cds-font-size-heading: 1.0625rem; --cds-font-size-title: 1.5rem;
  }
  html, body { margin: 0; background: var(--color-bg); color: var(--color-fg); font-family: var(--font-anthropic-sans); font-size: 16px; }
  #dash-root { max-width: 1240px; margin: 0 auto; padding-block: 28px 56px;
    padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
  .dash-skeleton { color: transparent !important; background: var(--color-border-line); border-radius: 4px; }
</style>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script>
  // substituto mínimo do `dash` do Claude: lê painel.json, calcula as tabelas e redesenha
  window.dash = (() => {
    const calcs = {}, draws = {}, aoCarregar = [];
    let bruto = null, status = 'loading', cache = {};
    const data = (id) => {
      if (status !== 'ok') return { status, data: [] };
      if (!(id in cache)) cache[id] = id === 'painel' ? bruto : (calcs[id] ? calcs[id].fn(...calcs[id].inputs.map((i) => data(i).data)) : []);
      return { status: 'ok', data: cache[id] };
    };
    const rodar = (nome) => document.querySelectorAll('[data-draw="' + nome + '"]').forEach((el) => {
      try { draws[nome](el); } catch (e) { console.error(nome, e); }
    });
    const tudo = () => { aoCarregar.forEach((f) => f()); Object.keys(draws).forEach(rodar); };
    return {
      colors: ['#17736c', '#8a7a52', '#4f6d8f', '#9b5b6b'],
      calc: (id, o) => { calcs[id] = o; },
      data,
      onData: (f) => { aoCarregar.push(f); },
      draw: (nome, fn) => { if (fn) draws[nome] = fn; else if (draws[nome]) rodar(nome); },
      params: () => ({}), setParams() {}, resetParams() {}, setLink() {}, refresh() {},
      async _iniciar() {
        tudo();
        try { bruto = await (await fetch('painel.json', { cache: 'no-store' })).json(); status = 'ok'; }
        catch (e) { status = 'error'; }
        cache = {}; tudo();
        let w = innerWidth;
        addEventListener('resize', () => { if (innerWidth !== w) { w = innerWidth; tudo(); } });
      },
    };
  })();
</script>
</head>
<body>
<div id="dash-root">
"""

FIM = """
</div>
<script>dash._iniciar();</script>
</body>
</html>
"""


def gerar():
    os.makedirs(DOCS, exist_ok=True)
    painel = open(os.path.join(RAIZ, 'painel-index.html'), encoding='utf-8').read()
    painel = painel.replace('<meta name="dash-one-writer">', '')
    open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8').write(TOPO + painel + FIM)
    if os.path.exists(os.path.join(RAIZ, 'painel.json')):
        shutil.copy(os.path.join(RAIZ, 'painel.json'), os.path.join(DOCS, 'painel.json'))
    open(os.path.join(DOCS, '.nojekyll'), 'w').close()   # o Pages serve os arquivos como estão


if __name__ == '__main__':
    gerar()
    print('docs/index.html e docs/painel.json gerados')
