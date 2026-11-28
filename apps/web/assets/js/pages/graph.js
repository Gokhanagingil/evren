// İlişki haritası (U7.4, ADR-0031): bir CI'ın çevresi. Bu CI'a bağımlı olanlar üstte, bağımlılıkları altta, etki
// taşımayan bağlantılar (fiziksel bağlantı, sahiplik) aynı düzeyde kesikli çizgiyle. Geçmiş bir an seçilirse ilişkiler
// o ana geri sarılır (değişim geçmişinden); düğüm adları bugünkü hâliyledir. Çizim satır içi stil kullanmaz (CSP).
import { h, s, clear, errorBox, formatDateTime, fromIstanbulInput, pill, toIstanbulInput } from '../lib/dom.js';
import { setHeader } from '../lib/shell.js';
import { backend, ciGraph, ciSearch } from '../lib/data.js';

const NODE_W = 184;
const NODE_H = 54;
const GAP_X = 26;
const GAP_Y = 84;
const PAD = 28;

const short = (text, n) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

/** Düzeylere göre yerleşim: düzey 0 merkez satırı; diğer satırlar komşularının ortalama konumuna göre sıralanır. */
export function layout(nodes, edges) {
  const rows = new Map();
  for (const n of nodes) {
    if (!rows.has(n.level)) rows.set(n.level, []);
    rows.get(n.level).push(n);
  }
  const levels = [...rows.keys()].sort((a, b) => a - b);
  const adj = new Map(nodes.map((n) => [n.sys_id, []]));
  for (const e of edges) {
    adj.get(e.parent)?.push(e.child);
    adj.get(e.child)?.push(e.parent);
  }
  const order = new Map();
  const place = (level, ref) => {
    const row = rows.get(level);
    const score = (n) => {
      const near = adj.get(n.sys_id).filter((id) => order.has(id) && ref.has(id)).map((id) => order.get(id));
      return near.length ? near.reduce((a, b) => a + b, 0) / near.length : Number.MAX_SAFE_INTEGER;
    };
    row.sort((a, b) => (b.center - a.center) || (score(a) - score(b)) || a.name.localeCompare(b.name, 'tr'));
    if (level === 0 && row.length > 1) {
      // Merkez satırın ortasında: yan bağlantılar iki yana dağılır
      const [center, ...rest] = row;
      row.splice(0, row.length, ...rest.filter((_, i) => i % 2), center, ...rest.filter((_, i) => !(i % 2)));
    }
    row.forEach((n, i) => order.set(n.sys_id, i - (row.length - 1) / 2));
  };
  const placed = new Set();
  const mark = (level) => rows.get(level).forEach((n) => placed.add(n.sys_id));
  if (rows.has(0)) { place(0, placed); mark(0); }
  for (const level of levels.filter((l) => l > 0)) { place(level, placed); mark(level); }
  for (const level of levels.filter((l) => l < 0).reverse()) { place(level, placed); mark(level); }
  const widest = Math.max(...[...rows.values()].map((r) => r.length));
  const width = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const pos = new Map();
  for (const level of levels) {
    const row = rows.get(level);
    const rowW = row.length * NODE_W + (row.length - 1) * GAP_X;
    row.forEach((n, i) => pos.set(n.sys_id, {
      x: PAD + (width - PAD * 2 - rowW) / 2 + i * (NODE_W + GAP_X),
      y: PAD + (level - levels[0]) * (NODE_H + GAP_Y),
    }));
  }
  const height = PAD * 2 + levels.length * NODE_H + (levels.length - 1) * GAP_Y;
  return { pos, width, height, levels };
}

function edgePath(a, b) {
  if (Math.abs(a.y - b.y) < 1) {
    // Aynı satır: üstten kavis
    const [l, r] = a.x < b.x ? [a, b] : [b, a];
    const x1 = l.x + NODE_W; const x2 = r.x; const y = l.y + NODE_H / 2;
    if (x2 - x1 <= GAP_X + 1) return `M${x1} ${y} L${x2} ${y}`;
    return `M${l.x + NODE_W / 2} ${l.y} C${l.x + NODE_W / 2} ${l.y - 40} ${r.x + NODE_W / 2} ${r.y - 40} ${r.x + NODE_W / 2} ${r.y}`;
  }
  const [top, bottom] = a.y < b.y ? [a, b] : [b, a];
  const x1 = top.x + NODE_W / 2; const y1 = top.y + NODE_H;
  const x2 = bottom.x + NODE_W / 2; const y2 = bottom.y;
  const my = (y1 + y2) / 2;
  return `M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}`;
}

export async function renderGraph(view, param) {
  const { mode } = await backend();
  setHeader({
    title: 'İlişki Haritası',
    sub: 'Bir CI\'ın bağımlıları ve bağımlılıkları.',
    crumbs: [['Konfigürasyon Öğeleri', '#/ci'], ['İlişki Haritası']],
  });
  if (mode !== 'api') {
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '),
      'İlişki haritası canlı API ile çalışır; ilişkiler golden record\'dan ve değişim geçmişinden gelir.'));
    return {};
  }
  const state = { id: null, at: null, depth: 2, physical: true, doc: null, selected: null };

  const search = h('input', { type: 'search', class: 'searchbar', id: 'graphSearch', placeholder: 'CI ara: ad, FQDN, IP',
    'aria-label': 'Haritası gösterilecek CI\'ı ara', autocomplete: 'off' });
  const results = h('ul', { class: 'mini', id: 'graphResults', 'aria-label': 'Arama sonuçları' });
  const depth = h('select', { class: 'compact', id: 'graphDepth', 'aria-label': 'Derinlik' },
    [1, 2, 3].map((d) => h('option', { value: String(d), selected: d === state.depth }, `${d} adım`)));
  const atInput = h('input', { type: 'datetime-local', id: 'graphAt', step: '1', 'aria-label': 'Geçmiş bir an (boş: şimdi)',
    max: toIstanbulInput(new Date()) });
  const atApply = h('button', { class: 'btn secondary small', type: 'button', id: 'graphAtApply' }, 'Bu andaki hâli');
  const atClear = h('button', { class: 'btn ghost small', type: 'button', id: 'graphAtNow' }, 'Şimdi');
  const physical = h('input', { type: 'checkbox', id: 'graphPhysical', checked: true });
  const summary = h('p', { class: 'muted small', id: 'graphSummary', 'aria-live': 'polite' });
  const scroll = h('div', { class: 'map-scroll graph-scroll', id: 'graphScroll' });
  const panel = h('aside', { class: 'panel map-detail', id: 'graphDetail', 'aria-live': 'polite', 'aria-label': 'Seçili CI' });
  const listBox = h('details', { class: 'inherited', id: 'graphList', hidden: true });
  const layoutBox = h('div', { class: 'map-layout' }, h('div', {}, scroll, listBox), panel);

  let typing = null;
  search.addEventListener('input', () => {
    clearTimeout(typing);
    typing = setTimeout(async () => {
      const q = search.value.trim();
      if (q.length < 2) { clear(results); return; }
      const res = await ciSearch({ q, limit: 8 });
      clear(results).append(...res.items.map((c) => h('li', {},
        h('a', { href: `#/iliski-haritasi/${c.sys_id}` }, c.name), h('span', { class: 'cell-sub' }, c.class_label))));
      if (!res.items.length) results.append(h('li', { class: 'muted' }, 'Eşleşen CI yok.'));
    }, 250);
  });
  const go = () => {
    const iso = fromIstanbulInput(atInput.value);
    location.hash = `#/iliski-haritasi/${state.id}${iso ? `@${iso}` : ''}`;
  };
  depth.addEventListener('change', () => { state.depth = Number(depth.value); draw(); });
  physical.addEventListener('change', () => { state.physical = physical.checked; paint(); });
  atApply.addEventListener('click', () => { if (state.id && atInput.value) go(); });
  atInput.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' && state.id && atInput.value) { ev.preventDefault(); go(); } });
  atClear.addEventListener('click', () => { atInput.value = ''; if (state.id) go(); });

  view.append(
    h('div', { class: 'map-toolbar' }, search, depth, h('span', { class: 'sep' }), h('label', { class: 'field-inline', for: 'graphAt' }, 'An'),
      atInput, atApply, atClear, h('span', { class: 'sep' }),
      h('label', { class: 'check', for: 'graphPhysical' }, physical, 'Etki taşımayan bağlantılar')),
    results,
    h('div', { class: 'legend', 'aria-label': 'Gösterim' },
      h('span', {}, h('i', { class: 'lg-dep' }), 'Bağımlılık (ok bağımlılığa doğru)'),
      h('span', {}, h('i', { class: 'lg-phys' }), 'Etki taşımayan bağlantı'),
      h('span', {}, h('i', { class: 'lg-service' }), 'Hizmet / iş uygulaması'),
      h('span', {}, h('i', { class: 'lg-center' }), 'Merkez CI'),
      h('span', {}, h('i', { class: 'lg-retired' }), 'Emekli')),
    summary, layoutBox);

  // Görünür alt küme: etkisiz kenarlar gizliyse merkezden yalnızca bağımlılık kenarlarıyla ulaşılan düğümler
  function visible() {
    const d = state.doc;
    if (state.physical) return { nodes: d.nodes, edges: d.edges };
    const edges = d.edges.filter((e) => e.impact !== 'none');
    const seen = new Set([d.center]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const e of edges) {
        if (seen.has(e.parent) !== seen.has(e.child)) { seen.add(e.parent); seen.add(e.child); grew = true; }
      }
    }
    return { nodes: d.nodes.filter((n) => seen.has(n.sys_id)), edges: edges.filter((e) => seen.has(e.parent) && seen.has(e.child)) };
  }

  function relationsOf(id, edges, byId) {
    return edges.filter((e) => e.parent === id || e.child === id).map((e) => {
      const asParent = e.parent === id;
      const other = byId.get(asParent ? e.child : e.parent);
      return { phrase: asParent ? e.parent_phrase : e.child_phrase, other, e };
    }).sort((a, b) => a.phrase.localeCompare(b.phrase, 'tr') || a.other.name.localeCompare(b.other.name, 'tr'));
  }

  function select(id) {
    state.selected = id;
    const { nodes, edges } = visible();
    const byId = new Map(nodes.map((n) => [n.sys_id, n]));
    const n = byId.get(id);
    scroll.querySelectorAll('.gnode').forEach((g) => g.classList.toggle('selected', g.dataset.id === id));
    if (!n) { clear(panel); return; }
    clear(panel).append(
      h('div', { class: 'kind' }, n.class_label),
      h('h2', {}, n.name),
      h('div', { class: 'meta' }, n.center ? pill('Merkez', 'info') : null, n.service ? pill('Hizmet katmanı', 'neutral') : null,
        n.production ? pill('Üretim', 'good') : null, n.retired ? pill('Emekli', 'warn') : null,
        n.depth ? h('span', { class: 'muted small' }, `${n.depth}. adım`) : null),
      h('h3', {}, 'İlişkileri (haritada)'),
      h('ul', { id: 'graphNodeRels' }, relationsOf(id, edges, byId).map((r) => h('li', {}, `${r.phrase} `,
        h('strong', {}, r.other.name), r.e.sources.length ? h('span', { class: 'cell-sub' }, `Bildiren: ${r.e.sources.join(', ')}`) : null))),
      h('div', { class: 'link-actions' },
        n.center ? null : h('a', { class: 'btn secondary small', id: 'graphRecenter',
          href: `#/iliski-haritasi/${n.sys_id}${state.at ? `@${state.at}` : ''}` }, 'Merkeze al'),
        h('a', { class: 'btn ghost small', href: `#/ci/${n.sys_id}` }, 'CI ayrıntısı')));
  }

  function paint() {
    const d = state.doc;
    const { nodes, edges } = visible();
    const byId = new Map(nodes.map((n) => [n.sys_id, n]));
    const { pos, width, height } = layout(nodes, edges);
    const svg = s('svg', { viewBox: `0 0 ${width} ${height}`, width, height, class: 'graph-svg', role: 'group',
      'aria-label': `İlişki haritası: ${nodes.length} CI, ${edges.length} ilişki` },
    s('defs', {}, s('marker', { id: 'graphArrow', viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7',
      orient: 'auto-start-reverse' }, s('path', { d: 'M0 0L10 5L0 10z', class: 'graph-arrowhead' }))));
    const edgeLayer = s('g', { class: 'graph-edges' });
    for (const e of edges) {
      const a = pos.get(e.parent); const b = pos.get(e.child);
      if (!a || !b) continue;
      // Ok bağımlıdan bağımlılığa: bağımlı ebeveynse yol ebeveynden çocuğa çizilir
      const [from, to] = e.dependent === e.child ? [b, a] : [a, b];
      const p = s('path', { d: edgePath(from, to), class: `gedge ${e.impact === 'none' ? 'phys' : 'dep'}`,
        'marker-end': e.impact === 'none' ? null : 'url(#graphArrow)' },
      s('title', {}, `${byId.get(e.parent).name} ${e.parent_phrase} ${byId.get(e.child).name}${e.sources.length ? ` · ${e.sources.join(', ')}` : ''}`));
      if (from.y === to.y && e.impact !== 'none') p.setAttribute('class', 'gedge dep lateral');
      edgeLayer.append(p);
    }
    const nodeLayer = s('g', { class: 'graph-nodes' });
    const sorted = [...nodes].sort((a, b) => (pos.get(a.sys_id).y - pos.get(b.sys_id).y) || (pos.get(a.sys_id).x - pos.get(b.sys_id).x));
    for (const n of sorted) {
      const { x, y } = pos.get(n.sys_id);
      const cls = ['gnode', n.center && 'center', n.service && 'service', n.retired && 'retired'].filter(Boolean).join(' ');
      const g = s('g', { class: cls, transform: `translate(${x} ${y})`, tabindex: '0', role: 'button', 'data-id': n.sys_id,
        'aria-label': `${n.name}, ${n.class_label}${n.center ? ', merkez' : ''}${n.production ? ', üretim' : ''}${n.retired ? ', emekli' : ''}` },
      s('title', {}, `${n.name} · ${n.class_label}`),
      s('rect', { width: NODE_W, height: NODE_H, rx: '9' }),
      n.production ? s('rect', { class: 'gprod', x: NODE_W - 14, y: 8, width: 6, height: 6, rx: '3' }) : null,
      s('text', { x: 12, y: 23, class: 'gname' }, short(n.name, 24)),
      s('text', { x: 12, y: 41, class: 'gclass' }, short(n.class_label, 30)));
      g.addEventListener('click', () => select(n.sys_id));
      g.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); select(n.sys_id); }
      });
      nodeLayer.append(g);
    }
    svg.append(edgeLayer, nodeLayer);
    clear(scroll).append(svg);
    // Metin alternatifi: ekran okuyucu ve kopyalama için
    listBox.hidden = false;
    clear(listBox).append(h('summary', {}, `Liste olarak (${edges.length} ilişki)`),
      edges.length ? h('ul', { class: 'rel-list', id: 'graphEdgeList' }, edges.map((e) => h('li', {},
        h('strong', {}, byId.get(e.parent).name), ` ${e.parent_phrase} `, h('strong', {}, byId.get(e.child).name),
        e.impact === 'none' ? h('span', { class: 'cell-sub' }, 'Etki taşımaz') : null)))
        : h('p', { class: 'muted small' }, 'İlişki yok.'));
    const notes = [`${nodes.length} CI, ${edges.length} ilişki`];
    if (d.at) notes.push(`${formatDateTime(d.at)} anındaki ilişkiler (adlar bugünkü hâliyle)`);
    if (d.hidden) notes.push(`okuma izniniz olmayan ${d.hidden} CI gösterilmiyor`);
    if (d.truncated) notes.push('harita sınırına ulaşıldı; derinliği azaltın');
    summary.textContent = `${notes.join(' · ')}.`;
    select(state.selected && byId.has(state.selected) ? state.selected : d.center);
  }

  async function draw() {
    clear(scroll).append(h('p', { class: 'muted small graph-loading' }, 'Yükleniyor…'));
    try {
      state.doc = await ciGraph(state.id, { depth: state.depth, at: state.at });
    } catch (err) {
      clear(scroll).append(errorBox(`Harita yüklenemedi: ${err.message}`));
      clear(panel);
      return;
    }
    if (!state.doc.existed) {
      clear(scroll).append(h('p', { class: 'notice', id: 'graphNotExisted' }, 'Bu CI seçilen anda henüz yoktu.'));
      clear(panel); clear(listBox); listBox.hidden = true; summary.textContent = '';
      return;
    }
    setHeader({ title: 'İlişki Haritası', sub: `${state.doc.name || '(adsız)'} çevresi${state.at ? ` · ${formatDateTime(state.at)}` : ''}`,
      crumbs: [['Konfigürasyon Öğeleri', '#/ci'], [state.doc.name || 'CI', `#/ci/${state.id}`], ['İlişki Haritası']] });
    paint();
  }

  async function update(p) {
    const [id, at] = (p || '').split('@');
    if (!/^[0-9a-f]{32}$/.test(id || '')) {
      state.id = null;
      clear(scroll).append(h('p', { class: 'muted', id: 'graphEmpty' }, 'Haritasını görmek istediğiniz CI\'ı yukarıdan arayın veya CI panelindeki "İlişki haritası" bağlantısını kullanın.'));
      clear(panel); clear(listBox); listBox.hidden = true; summary.textContent = '';
      return;
    }
    state.id = id;
    state.at = at && !Number.isNaN(Date.parse(at)) ? new Date(at).toISOString() : null;
    atInput.value = state.at ? toIstanbulInput(new Date(state.at)) : '';
    state.selected = null;
    clear(results);
    await draw();
  }

  await update(param);
  return { update };
}
