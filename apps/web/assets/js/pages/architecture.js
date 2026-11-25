// Mimari Harita: bölümlü SVG, sığdır/yakınlaştır, bileşen arama, detay paneli, SVG indirme.
import { h, append, clear, loadJSON, loadSVG, phaseChip, statusChip } from '../lib/dom.js';

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 2.5;

export async function renderArchitecture(view, param) {
  const [data, svg, roadmap] = await Promise.all([
    loadJSON('/content/architecture.json'),
    loadSVG('/content/architecture.svg'),
    loadJSON('/content/roadmap.json'),
  ]);

  const roadmapByComponent = new Map();
  for (const phase of roadmap.phases) {
    for (const item of phase.items) {
      for (const c of item.components || []) {
        if (!roadmapByComponent.has(c)) roadmapByComponent.set(c, []);
        roadmapByComponent.get(c).push({ ...item, phase: phase.id });
      }
    }
  }

  let zoom = 1;
  let selected = null;

  const scroll = h('div', { class: 'map-scroll', id: 'mapScroll' });
  scroll.append(svg);
  const panel = h('aside', { class: 'card detail', id: 'mapDetail', 'aria-live': 'polite', 'aria-label': 'Bileşen ayrıntısı' });
  const layout = h('div', { class: 'map-layout no-panel' });
  const zoomLabel = h('span', { class: 'muted', id: 'zoomLabel' }, '%100');
  const search = h('input', {
    class: 'input', type: 'search', id: 'mapSearch', placeholder: 'Bileşen ara (ör. kimlik, Kafka, IRE)',
    'aria-label': 'Haritada bileşen ara',
  });

  function applyZoom(next, anchor) {
    const prev = zoom;
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    const keepX = anchor ? (scroll.scrollLeft + anchor.x) / prev : 0;
    const keepY = anchor ? (scroll.scrollTop + anchor.y) / prev : 0;
    svg.setAttribute('width', String(Math.round(data.width * zoom)));
    svg.setAttribute('height', String(Math.round(data.height * zoom)));
    zoomLabel.textContent = `%${Math.round(zoom * 100)}`;
    if (anchor) {
      scroll.scrollLeft = keepX * zoom - anchor.x;
      scroll.scrollTop = keepY * zoom - anchor.y;
    }
  }

  function fit() {
    const available = scroll.clientWidth || data.width;
    applyZoom(available / data.width);
  }

  function centerOn(nodeId) {
    const g = svg.querySelector(`[data-node="${CSS.escape(nodeId)}"]`);
    if (!g) return;
    const box = g.getBBox();
    scroll.scrollTo({ top: Math.max(0, box.y * zoom - 80), left: Math.max(0, box.x * zoom - 60) });
  }

  function select(nodeId, { center = false, updateHash = true } = {}) {
    svg.querySelectorAll('.node.selected').forEach((n) => n.classList.remove('selected'));
    const node = data.nodes[nodeId];
    if (!node) {
      selected = null;
      layout.classList.add('no-panel');
      clear(panel);
      return;
    }
    selected = nodeId;
    svg.querySelector(`[data-node="${CSS.escape(nodeId)}"]`)?.classList.add('selected');
    renderPanel(nodeId, node);
    layout.classList.remove('no-panel');
    if (center) centerOn(nodeId);
    if (updateHash && location.hash !== `#/mimari/${nodeId}`) history.replaceState(null, '', `#/mimari/${nodeId}`);
  }

  function renderPanel(nodeId, node) {
    const chapter = data.chapters.find((c) => c.id === node.chapter);
    const items = roadmapByComponent.get(nodeId) || [];
    append(clear(panel), [
      h('button', { class: 'btn small close', type: 'button', onclick: closePanel, 'aria-label': 'Ayrıntıyı kapat' }, 'Kapat'),
      h('div', { class: 'meta' },
        phaseChip(node.phase, data.phases[node.phase]),
        h('span', { class: 'kind' }, `${node.kind_label} · ${chapter ? `${chapter.number} ${chapter.title}` : ''}`)),
      h('h2', { id: 'detailTitle' }, node.label),
      node.sub.length ? h('p', { class: 'muted' }, node.sub.join(' ')) : null,
      h('p', {}, node.text),
      node.tech.length ? [h('h3', {}, 'Teknoloji ve kaynak'), h('ul', {}, node.tech.map((t) => h('li', {}, t)))] : null,
      node.security.length ? [h('h3', {}, 'Güvenlik'), h('ul', {}, node.security.map((t) => h('li', {}, t)))] : null,
      h('h3', {}, 'Yol haritasında'),
      items.length
        ? items.map((it) => h('div', { class: 'rm-item' },
            statusChip(it.status, roadmap.statuses),
            h('a', { href: `#/yol-haritasi/${it.phase}/${it.id}` }, `${it.id} · ${it.title}`)))
        : h('p', { class: 'empty' }, `${data.phases[node.phase]} kapsamında; ayrı bir iş kalemi tanımlı değil.`),
    ]);
  }

  function closePanel() {
    const prev = selected;
    select(null);
    history.replaceState(null, '', '#/mimari');
    if (prev) svg.querySelector(`[data-node="${CSS.escape(prev)}"]`)?.focus();
  }

  function runSearch() {
    const q = search.value.trim().toLocaleLowerCase('tr');
    const nodes = svg.querySelectorAll('.node');
    let first = null;
    nodes.forEach((g) => {
      g.classList.remove('dim', 'hit');
      if (!q) return;
      const n = data.nodes[g.dataset.node];
      const hay = [n.label, ...n.sub, n.text, ...n.tech].join(' ').toLocaleLowerCase('tr');
      if (hay.includes(q)) {
        g.classList.add('hit');
        first = first || g.dataset.node;
      } else {
        g.classList.add('dim');
      }
    });
    if (first) centerOn(first);
  }

  svg.addEventListener('click', (ev) => {
    const g = ev.target.closest('.node');
    if (g) select(g.dataset.node);
  });
  svg.addEventListener('keydown', (ev) => {
    const g = ev.target.closest('.node');
    if (g && (ev.key === 'Enter' || ev.key === ' ')) {
      ev.preventDefault();
      select(g.dataset.node);
    }
  });
  view.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && selected) closePanel();
  });
  search.addEventListener('input', runSearch);

  const chapterNav = h('nav', { class: 'chapter-nav', 'aria-label': 'Harita bölümleri' },
    data.chapters.map((c) => h('button', {
      class: 'btn small', type: 'button', dataset: { chapter: c.id },
      onclick: () => scroll.scrollTo({ top: Math.max(0, c.y * zoom - 8), left: 0 }),
    }, h('b', {}, c.number), c.title)));

  const toolbar = h('div', { class: 'toolbar', role: 'toolbar', 'aria-label': 'Harita araçları' },
    search,
    h('span', { class: 'sep', 'aria-hidden': 'true' }),
    h('button', { class: 'btn small', type: 'button', id: 'mapFit', onclick: fit }, 'Sığdır'),
    h('button', { class: 'btn small', type: 'button', id: 'mapZoomOut', 'aria-label': 'Uzaklaştır',
      onclick: () => applyZoom(zoom / 1.2, { x: scroll.clientWidth / 2, y: scroll.clientHeight / 2 }) }, '− Uzaklaştır'),
    h('button', { class: 'btn small', type: 'button', id: 'mapZoomIn', 'aria-label': 'Yakınlaştır',
      onclick: () => applyZoom(zoom * 1.2, { x: scroll.clientWidth / 2, y: scroll.clientHeight / 2 }) }, '+ Yakınlaştır'),
    zoomLabel,
    h('span', { class: 'sep', 'aria-hidden': 'true' }),
    h('a', { class: 'btn small', id: 'mapExport', href: '/content/architecture.svg', download: 'Evren_Mimari_Harita.svg' }, 'SVG indir'));

  view.append(
    h('div', { class: 'page-head' },
      h('div', {},
        h('p', { class: 'eyebrow' }, 'Evren / Mimari'),
        h('h1', {}, 'Mimari Harita'),
        h('p', { class: 'lead' },
          'Kaynaktan karara veri akışı, kayıt sistemi, ServiceNow geçiş deseni ve güvenlik denetimleri. ',
          'Bir kutuya tıklayın veya Tab ile gelip Enter\'a basın; ayrıntı ve yol haritası bağlantısı sağda açılır.')),
      toolbar),
    layout,
  );
  layout.append(h('div', { class: 'map-frame' }, chapterNav, scroll), panel);

  // İlk yerleşim: sayfa genişliğine sığdır
  requestAnimationFrame(() => {
    fit();
    if (param) select(param, { center: true, updateHash: false });
  });

  return {
    update(next) {
      if (next) select(next, { center: true, updateHash: false });
      else select(null);
    },
  };
}
