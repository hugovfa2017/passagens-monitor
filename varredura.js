// Carregador do Google Voos. Abre buscas em quadros escondidos (iframes) e devolve o texto bruto de cada uma.
// Não decide nada: endereços, leitura e escolha ficam em nuvem/gvoos.py e nuvem/selecao.py, que têm teste.
//
// Uso (nuvem/rodar.py injeta este arquivo numa aba já aberta em https://www.google.com/travel/flights):
//   window.__gv.listar(enderecos, paralelo)  -> [{rotulos: [...]}]   texto de cada voo da lista de resultados
//   window.__gv.validar(itens, paralelo)     -> [{texto, href}]      tela de compra do voo cujo rótulo contém item.chave
//   window.__gv.comprar(enderecos, paralelo) -> [{texto, href}]      tela de compra aberta direto pelo endereço dos voos exatos
// Por que valida na tela de compra: o preço da lista pode estar velho (já vimos R$592 na lista virar R$2.550 na
// compra). Só conta o preço da tela de compra.
// Se o Google pedir verificação em qualquer quadro, tudo para ({bloqueio: ...}); não tentamos contornar.
window.__gv = (() => {
  const BLOQUEIO = /unusual traffic|tráfego incomum|captcha|not a robot|Antes de continuar|Before you continue/i;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let parou = null;
  const rotulos = doc => [...doc.querySelectorAll('[aria-label^="A partir de"]')].map(e => e.getAttribute('aria-label'));

  // abre a busca e espera a lista parar de crescer (ou desiste se, depois de `vazio` segundos, não veio nenhum voo)
  const abrir = async (u, vazio) => {
    const f = document.createElement('iframe');
    f.style.cssText = 'width:1200px;height:900px;position:fixed;left:-5000px;top:0';
    f.src = u; document.body.appendChild(f);
    let antes = -1, parado = 0;   // a lista chega em partes (principais voos, depois os outros): só vale quando para de crescer
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      const doc = f.contentDocument;
      if (!doc || !doc.body) continue;
      if (i >= 2 && BLOQUEIO.test((doc.body.innerText || '').slice(0, 3000))) { parou = (doc.body.innerText || '').slice(0, 200); break; }
      const n = rotulos(doc).length;
      parado = n > 0 && n === antes ? parado + 1 : 0;
      if (parado >= 2 && i >= 3) break;
      if (n === 0 && i >= vazio) break;
      antes = n;
    }
    return f;
  };

  const fila = async (itens, paralelo, fn) => {
    const out = []; let i = 0;
    await Promise.all(Array.from({ length: paralelo }, async () => {
      while (i < itens.length) {
        const k = i++;
        if (parou) { out[k] = { bloqueio: parou }; continue; }
        try { out[k] = await fn(itens[k]); } catch (e) { out[k] = { erro: String(e).slice(0, 200) }; }
      }
    }));
    return out;
  };

  const listar = (enderecos, paralelo) => fila(enderecos, paralelo, async u => {
    const f = await abrir(u, 12);
    try { return parou ? { bloqueio: parou } : { rotulos: rotulos(f.contentDocument) }; } finally { f.remove(); }
  });

  // espera a tela de compra mostrar os vendedores ("Reserve com a ...") e devolve o texto e o endereço (que traz o número de cada voo)
  const lerCompra = async f => {
    let antes = -1, n = 0, texto = '';
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      const doc = f.contentDocument;
      if (!doc || !doc.body) continue;
      const mais = [...doc.querySelectorAll('div')].find(b => !b.children.length && /^Mais \d+ opç/.test((b.textContent || '').trim()));
      if (mais) mais.click();
      texto = doc.body.innerText || '';
      if (i >= 2 && BLOQUEIO.test(texto.slice(0, 3000))) { parou = texto.slice(0, 200); return { bloqueio: parou }; }
      n = (texto.match(/Reserve com a /g) || []).length;
      if (n && n === antes) break;
      if (!n && i >= 14) break;   // 15 segundos sem nenhum vendedor: a tela não vai abrir
      antes = n;
    }
    const ini = texto.indexOf('Reserve com a ');
    return n ? { texto: texto.slice(ini, ini + 6000), href: f.contentWindow.location.href }
             : { erro: 'tela de compra sem vendedores', inicio: texto.slice(0, 300) };
  };

  // clica no voo da lista e lê a tela de compra
  const validar = (itens, paralelo) => fila(itens, paralelo, async it => {
    const f = await abrir(it.url, 12);
    try {
      if (parou) return { bloqueio: parou };
      // o voo pode estar na parte da lista que chega depois: procura por mais alguns segundos antes de desistir
      const achar = () => [...f.contentDocument.querySelectorAll('[aria-label^="A partir de"]')].find(e => e.getAttribute('aria-label').includes(it.chave));
      let el = achar();
      for (let i = 0; i < 8 && !el; i++) { await sleep(1000); el = achar(); }
      if (!el) return { erro: 'voo não está mais na lista' };
      el.click();
      return await lerCompra(f);
    } finally { f.remove(); }
  });

  // abre direto a tela de compra de voos exatos (endereço de gvoos.url_compra): outro número de lugares, ou ida e volta juntas
  const comprar = (enderecos, paralelo) => fila(enderecos, paralelo, async u => {
    const f = document.createElement('iframe');
    f.style.cssText = 'width:1200px;height:900px;position:fixed;left:-5000px;top:0';
    f.src = u; document.body.appendChild(f);
    try { return await lerCompra(f); } finally { f.remove(); }
  });

  return { listar, validar, comprar, bloqueio: () => parou };
})();
