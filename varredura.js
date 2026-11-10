// Varredura de passagens no Google Voos: ida SP -> Natal dos extras (perto do voo que já foi comprado)
// e volta Nordeste -> SP dos dois que já têm a ida.
// Uso: numa aba em https://www.google.com/travel/flights, cole este arquivo inteiro no javascript_tool.
// Roda em segundo plano (~2 min). Pronto quando window.__scan.status for 'ok' ou 'erro';
// o resultado fica em window.__json (leia em pedaços: window.__json.slice(0,900), ...).
//
// Por que valida na tela de compra: o preço da lista do Google pode estar velho
// (já vimos R$592 na lista virar R$2.550 na compra). Só conta o preço da tela de compra.
(() => {
  const CFG = {
    // ida dos extras (ainda a confirmar): o mesmo voo comprado e outras opções perto, em Natal e região
    alvo: '2026-12-22 14:05 Gol GRU-NAT G3 1678',   // data, saída, companhia, rota, número do voo
    paxIda: 1,                      // compra individual: cada extra compra a sua (busca para 1 adulto; 3 = busca 1, 2 e 3)
    ida: ['2026-12-19','2026-12-20','2026-12-21','2026-12-22','2026-12-23','2026-12-24'],
    // aeroportos do Nordeste: chegada dos extras e saída da volta (João Pessoa e Recife ficam a 2h30 e 4h de carro de Natal)
    nordeste: ['NAT','JPA','REC','FOR','JDO'],
    // volta dos dois que já têm a ida (estadia de 27 a 39 dias a partir de 22/12, até 26/01)
    pax: 2,
    volta: ['2027-01-18','2027-01-19','2027-01-20','2027-01-21','2027-01-22','2027-01-23','2027-01-24','2027-01-25','2027-01-26'],
    maxEscalaMin: 120,              // só direto ou 1 escala de até 2h
    candidatosPorData: 2,           // quantos voos da lista validar por data
    paralelo: 5,
    // vendedores que entram no ranking (os outros aparecem, mas não contam)
    confiaveis: /^(CIA |maxmilhas|123milhas|zupper|decolar|booking)/i,
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const hx = s => [...s].map(c => c.charCodeAt(0).toString(16).padStart(2, '0')).join('');
  const ap = (tag, c) => tag + '0708011203' + hx(c);
  const paxDe = dir => dir === 'ida' ? CFG.paxIda : CFG.pax;
  const [aData, aSaida, aCia] = CFG.alvo.split(' ');
  const tfs = (dir, date) => {
    const O = dir === 'ida' ? ['GRU', 'CGH'] : CFG.nordeste;
    const D = dir === 'ida' ? CFG.nordeste : ['GRU', 'CGH'];
    const seg = '120a' + hx(date) + O.map(c => ap('6a', c)).join('') + D.map(c => ap('72', c)).join('');
    const h = '081c1002' + '1a' + (seg.length / 2).toString(16).padStart(2, '0') + seg +
      '4001'.repeat(paxDe(dir)) + '4801700182010b08ffffffffffffffffff01980102';
    return btoa(h.match(/../g).map(x => String.fromCharCode(parseInt(x, 16))).join(''))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  const url = (dir, d) => `https://www.google.com/travel/flights/search?tfs=${tfs(dir, d)}&tfu=EgoIABAAGAAgAigB&hl=pt-BR&gl=BR&curr=BRL`;
  const code = s => /Natal/.test(s) ? 'NAT' : /Fortaleza/.test(s) ? 'FOR' : /Juazeiro/.test(s) ? 'JDO' : /Jo[aã]o Pessoa|Castro Pinto/.test(s) ? 'JPA' :
    /Recife|Guararapes/.test(s) ? 'REC' : /Guarulhos/.test(s) ? 'GRU' : /Congonhas/.test(s) ? 'CGH' : s.slice(0, 12);
  const mins = s => { const h = s.match(/(\d+)h/), m = s.match(/(\d+) min/); return (h ? +h[1] * 60 : 0) + (m ? +m[1] : 0); };

  // voos da lista (preço pode estar velho), só diretos ou escala curta
  const parse = (doc, dir) => {
    const seen = new Set();
    return [...doc.querySelectorAll('[aria-label^="A partir de"]')].map(el => {
      const t = el.getAttribute('aria-label');
      const p = t.match(/A partir de ([\d.]+) Reais/), a = t.match(/Voo (direto )?d[ae] (.+?)(?: com (\d+) paradas?)?\./);
      const s = t.match(/Sai do aeroporto (.+?) às (\d\d:\d\d) do dia .+? e chega no aeroporto (.+?) às (\d\d:\d\d)/);
      if (!p || !a || !s) return null;
      const lays = [...t.matchAll(/Parada \(\d de \d\) de (.+?) no aeroporto/g)].map(x => mins(x[1]));
      const ok = !!a[1] || (lays.length === 1 && lays[0] <= CFG.maxEscalaMin);
      const tipo = a[1] ? 'direto' : `escala ${Math.floor(lays[0] / 60)}h${String(lays[0] % 60).padStart(2, '0')}`;
      const cia = a[2].replace(/ ?Operado.*/, '');
      const v = { listaPP: +p[1].replace(/\./g, '') / paxDe(dir), cia, rota: `${code(s[1])}-${code(s[3])}`, saida: s[2], chegada: s[4], tipo, ok };
      v.voo = `${cia} ${v.rota} ${v.saida}-${v.chegada} ${tipo}`;
      return v;
    }).filter(v => v && v.ok && !seen.has(v.voo) && seen.add(v.voo)).sort((x, y) => x.listaPP - y.listaPP);
  };

  const load = async u => {
    const f = document.createElement('iframe');
    f.style.cssText = 'width:1200px;height:900px;position:fixed;left:-5000px;top:0';
    f.src = u; document.body.appendChild(f);
    let antes = -1;  // pronto quando a lista de voos para de crescer
    for (let i = 0; i < 30; i++) {
      await sleep(1000);
      const n = f.contentDocument ? f.contentDocument.querySelectorAll('[aria-label^="A partir de"]').length : 0;
      if (n > 3 && n === antes) break;
      antes = n;
    }
    return f;
  };

  // abre a tela de compra do voo e lê o preço de cada vendedor (por pessoa)
  const validar = async (dir, d, voo) => {
    const f = await load(url(dir, d));
    try {
      const el = [...f.contentDocument.querySelectorAll('[aria-label^="A partir de"]')].find(e => {
        const t = e.getAttribute('aria-label');
        // mesma saída, chegada, origem e companhia (já houve dois voos às 04:55 do mesmo aeroporto)
        return t.includes('às ' + voo.saida) && t.includes('às ' + voo.chegada) && t.includes(voo.cia) &&
          code((t.match(/Sai do aeroporto (.+?) às/) || [, ''])[1]) === voo.rota.slice(0, 3);
      });
      if (!el) return null;
      el.click();
      let antes = -1, vend = [];
      for (let i = 0; i < 30; i++) {
        await sleep(1000);
        const doc = f.contentDocument;
        const mais = [...doc.querySelectorAll('div')].find(b => !b.children.length && /^Mais \d+ opç/.test((b.textContent || '').trim()));
        if (mais) mais.click();
        vend = [...doc.body.innerText.matchAll(/Reserve com a (.+?)(Companhia aérea)?\nR\$[\s\u00a0]([\d.]+)/g)]
          .map(m => ({ vendedor: (m[2] ? 'CIA ' : '') + m[1], pp: Math.round(+m[3].replace(/\./g, '') / paxDe(dir)) }));
        if (vend.length && vend.length === antes) break;
        antes = vend.length;
      }
      vend.sort((x, y) => x.pp - y.pp);
      const conf = vend.filter(v => CFG.confiaveis.test(v.vendedor));
      // número de cada voo, lido do link da tela de compra (o registrador remonta o link com isso)
      let voos = '';
      try {
        const b = atob(new URL(f.contentWindow.location.href).searchParams.get('tfs').replace(/-/g, '+').replace(/_/g, '/'));
        voos = [...b.matchAll(/\x0a\x03([A-Z]{3})\x12\x0a(\d{4}-\d\d-\d\d)\x1a\x03([A-Z]{3})\x2a\x02([A-Z0-9]{2})\x32[\s\S](\d+)/g)]
          .map(m => `${m[1]}>${m[3]} ${m[2]} ${m[4]} ${m[5]}`).join(';');
      } catch (e) { /* sem número: o painel mostra só o link da MaxMilhas */ }
      return vend.length ? { ...voo, voos, vendedores: vend, melhor: conf[0] || null, naCia: vend.find(v => v.vendedor.startsWith('CIA ')) || null } : null;
    } catch (e) { return null; } finally { f.remove(); }
  };

  const pool = async (itens, fn) => {
    const out = []; let i = 0;
    await Promise.all(Array.from({ length: CFG.paralelo }, async () => { while (i < itens.length) { const k = i++; out[k] = await fn(itens[k]); } }));
    return out;
  };

  window.__scan = { status: 'rodando', inicio: new Date().toISOString(), pax: CFG.pax, progresso: 'lista' };
  (async () => {
    try {
      const datas = [...CFG.ida.map(d => ['ida', d]), ...CFG.volta.map(d => ['volta', d])];
      // 1) lista de cada data -> candidatos
      const cands = await pool(datas, async ([dir, d]) => {
        const f = await load(url(dir, d));
        const todos = parse(f.contentDocument, dir), c = todos.slice(0, CFG.candidatosPorData);
        // o voo que já foi comprado é sempre validado, mesmo que não esteja entre os mais baratos
        const alvo = todos.find(v => dir === 'ida' && d === aData && v.saida === aSaida && v.cia.startsWith(aCia));
        if (alvo && !c.includes(alvo)) c.push(alvo);
        f.remove();
        return c.map(v => [dir, d, v]);
      });
      // 2) valida cada candidato na tela de compra
      window.__scan.progresso = 'validando';
      const val = await pool(cands.flat(), ([dir, d, v]) => validar(dir, d, v).then(r => r && { dir, d, ...r }));
      const ok = val.filter(r => r && r.melhor), best = {};
      for (const r of ok.filter(r => r.dir === 'volta')) if (!best[r.d] || r.melhor.pp < best[r.d].melhor.pp) best[r.d] = r;
      const linha = r => ({ d: r.d, pp: r.melhor.pp, vend: r.melhor.vendedor, cia: r.cia, rota: r.rota, saida: r.saida, chegada: r.chegada,
        tipo: r.tipo, ppCia: r.naCia ? r.naCia.pp : null, voos: r.voos });
      // volta: o melhor voo de cada data (2 adultos); ida: cada voo validado (preço para paxIda adultos)
      const trechos = CFG.volta.filter(d => best[d]).map(d => ({ s: 'volta', ...linha(best[d]) }));
      const idas = ok.filter(r => r.dir === 'ida').map(linha);
      const vend = {};
      for (const r of Object.values(best)) vend[`volta ${r.d}`] = r.vendedores.slice(0, 8).map(v => [v.vendedor, v.pp]);
      const achou = trechos.length + idas.length;
      window.__scan = {
        status: achou ? 'ok' : 'erro', erro: achou ? undefined : 'nenhum voo validado',
        inicio: window.__scan.inicio, fim: new Date().toISOString(), pax: CFG.pax, paxIda: CFG.paxIda, trechos, idas, vend,
      };
    } catch (e) { window.__scan = { status: 'erro', erro: String(e), progresso: window.__scan.progresso }; }
    // o javascript_tool corta respostas em ~1000 caracteres: leia window.__json em pedaços de 900
    window.__json = JSON.stringify(window.__scan);
    window.__partes = Math.ceil(window.__json.length / 900);
  })();
  return 'varredura iniciada';
})();
