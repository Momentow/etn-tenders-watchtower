// Page snapshot for the agent (BrowserSession.snapshot): evaluated in the page, returns plain data.
() => {
  const all = [...document.querySelectorAll('body *')].slice(0, 6000);
  const vis = e => { const r = e.getBoundingClientRect(); if (!(r.width > 0 || r.height > 0)) return false;
                     const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
  const name = e => (e.getAttribute('aria-label') || txt(e) || e.getAttribute('title') || e.getAttribute('value') || '').slice(0, 80);
  const uniq = xs => [...new Set(xs)];
  const heads = [...document.querySelectorAll('h1,h2,h3')].filter(vis).map(e => e.tagName.toLowerCase() + ': ' + txt(e).slice(0, 100)).slice(0, 15);
  const links = [...document.querySelectorAll('a[href]')].filter(vis).map(e => [name(e), e.href]).filter(x => x[0] && !x[1].startsWith('javascript:'));
  const seen = new Set(); const ulinks = links.filter(([n, h]) => { const k = n + h; if (seen.has(k)) return false; seen.add(k); return true; });
  const buttons = uniq([...document.querySelectorAll('button,[role=button],input[type=submit],input[type=button]')].filter(vis).map(name).filter(Boolean));
  const tabs = uniq([...document.querySelectorAll('[role=tab]')].filter(vis).map(name).filter(Boolean));
  const fields = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]),select,textarea')].filter(vis).map(e => {
    const l = e.labels && e.labels[0] ? txt(e.labels[0]) : '';
    return (e.tagName.toLowerCase() + (e.type ? '[' + e.type + ']' : '') + ' ' + (l || e.getAttribute('aria-label') || e.placeholder || e.name || '')).trim();
  });
  const groups = new Map();
  for (const el of all) {
    const p = el.parentElement; if (!p) continue;
    const sig = el.tagName.toLowerCase() + (el.classList.length ? '.' + [...el.classList].slice(0, 2).join('.') : '');
    const pk = p.tagName.toLowerCase() + (p.id ? '#' + p.id : (p.classList.length ? '.' + p.classList[0] : ''));
    const key = pk + ' > ' + sig;
    const g = groups.get(key) || {n: 0, sample: ''};
    g.n++; if (!g.sample && vis(el)) g.sample = txt(el).slice(0, 100);
    groups.set(key, g);
  }
  // Nested wrappers of one card repeat with the same count and text: keep the outermost of each.
  const kept = new Set();
  const blocks = [...groups.entries()].filter(([k, g]) => g.n >= 5 && g.sample.length > 20)
    .filter(([k, g]) => { const id = g.n + '|' + g.sample.slice(0, 40); if (kept.has(id)) return false; kept.add(id); return true; })
    .sort((a, b) => b[1].n - a[1].n).slice(0, 6).map(([k, g]) => k + ' ×' + g.n + ': ' + g.sample);
  const body = txt(document.body);
  const totals = uniq(body.match(/\b\d[\d.,  ]{0,9}\s+(?:results?|resultaten|résultats?|treffer|ergebnisse|risultati|resultados|aankondigingen|ausschreibungen|consultations?|avis|tenders?|notices?|opportunities|procedures)\b/gi) || []).slice(0, 5);
  return {title: document.title, heads, links: ulinks.slice(0, 60), nlinks: ulinks.length, buttons: buttons.slice(0, 30),
          tabs: tabs.slice(0, 20), fields: fields.slice(0, 20), blocks, totals};
}
