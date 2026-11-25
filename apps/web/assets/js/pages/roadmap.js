// Yol Haritası: faz adımları, ilerleme, durum filtresi, çıkış kriterleri, haritaya bağlantılar.
import { h, clear, loadJSON, statusChip, formatDate } from '../lib/dom.js';

const ORDER = ['done', 'in_progress', 'at_risk', 'blocked', 'planned'];

function progress(items) {
  const total = items.length || 1;
  const count = (s) => items.filter((i) => i.status === s).length;
  return { total: items.length, done: count('done'), prog: count('in_progress'), blocked: count('blocked') + count('at_risk'),
    pct: Math.round((100 * count('done')) / total) };
}

function bar(p) {
  const el = h('div', { class: 'bar', role: 'img', 'aria-label': `%${p.pct} tamamlandı` });
  const parts = [['b-done', p.done], ['b-prog', p.prog], ['b-blocked', p.blocked]];
  for (const [cls, n] of parts) {
    const seg = h('i', { class: cls });
    seg.style.width = `${(100 * n) / (p.total || 1)}%`; // CSSOM: CSP ile uyumlu
    el.append(seg);
  }
  return el;
}

export async function renderRoadmap(view, param) {
  const [rm, arch] = await Promise.all([loadJSON('/content/roadmap.json'), loadJSON('/content/architecture.json')]);
  const labels = rm.statuses;
  const all = rm.phases.flatMap((p) => p.items);
  const overall = progress(all);
  const current = rm.phases.find((p) => p.items.some((i) => i.status !== 'done')) || rm.phases.at(-1);
  let statusFilter = 'all';
  let query = '';

  const phasesBox = h('div', { id: 'phases' });
  const countLabel = h('span', { class: 'muted', id: 'filterCount' });

  function matches(item) {
    if (statusFilter !== 'all' && item.status !== statusFilter) return false;
    if (!query) return true;
    const comps = (item.components || []).map((c) => arch.nodes[c]?.label || c);
    return [item.id, item.title, item.note || '', ...comps].join(' ').toLocaleLowerCase('tr').includes(query);
  }

  function renderPhases() {
    clear(phasesBox);
    let shown = 0;
    for (const phase of rm.phases) {
      const items = phase.items.filter(matches);
      shown += items.length;
      const p = progress(phase.items);
      const metCount = phase.exit_criteria.filter((c) => c.met).length;
      phasesBox.append(h('section', { class: 'card phase', id: `faz-${phase.id}`, 'aria-labelledby': `faz-${phase.id}-t` },
        h('div', { class: 'phase-head' },
          h('div', { class: 'phase-no' }, phase.number),
          h('div', {},
            h('h2', { id: `faz-${phase.id}-t` }, `${phase.id} · ${phase.title}`),
            h('div', { class: 'window' }, phase.window),
            h('p', { class: 'lead' }, phase.goal),
            bar(p),
            h('small', { class: 'muted' }, `${p.done}/${p.total} iş kalemi tamamlandı · çıkış kriterleri ${metCount}/${phase.exit_criteria.length}`))),
        h('div', { class: 'phase-body' },
          h('div', {},
            items.length
              ? h('ul', { class: 'items' }, items.map((it) => h('li', { class: 'item', id: it.id, tabindex: '-1' },
                  h('code', {}, it.id),
                  h('div', {},
                    h('span', { class: 'title' }, it.title),
                    it.note ? h('span', { class: 'note' }, it.note) : null,
                    (it.components || []).length
                      ? h('div', { class: 'comps' }, it.components.map((c) => h('a', {
                          class: 'chip link', href: `#/mimari/${c}`, title: 'Mimari haritada göster',
                        }, arch.nodes[c]?.label || c)))
                      : null),
                  statusChip(it.status, labels))))
              : h('p', { class: 'empty' }, 'Bu filtreyle eşleşen iş kalemi yok.')),
          h('div', { class: 'side' },
            h('h3', {}, 'Çıkış kriterleri'),
            h('ul', { class: 'criteria' }, phase.exit_criteria.map((c) => h('li', { class: c.met ? 'met' : '' },
              h('span', { class: 'mark', 'aria-hidden': 'true' }, c.met ? '✓' : ''),
              h('span', {}, h('span', { class: 'sr-only' }, c.met ? 'Karşılandı: ' : 'Bekliyor: '), c.text)))),
            phase.risks?.length ? [h('h3', {}, 'Riskler'), h('ul', { class: 'risks' }, phase.risks.map((r) => h('li', {}, r)))] : null))));
    }
    countLabel.textContent = `${shown} iş kalemi gösteriliyor`;
  }

  const filterButtons = [['all', 'Tümü'], ...ORDER.map((s) => [s, labels[s]])].map(([value, label]) => {
    const n = value === 'all' ? all.length : all.filter((i) => i.status === value).length;
    return h('button', {
      class: 'btn small', type: 'button', 'aria-pressed': value === statusFilter ? 'true' : 'false', dataset: { status: value },
      onclick: (ev) => {
        statusFilter = value;
        filterButtons.forEach((b) => b.setAttribute('aria-pressed', b === ev.currentTarget ? 'true' : 'false'));
        renderPhases();
      },
    }, `${label} (${n})`);
  });
  const search = h('input', { class: 'input', type: 'search', id: 'roadmapSearch', placeholder: 'İş kalemi veya bileşen ara',
    'aria-label': 'Yol haritasında ara' });
  search.addEventListener('input', () => { query = search.value.trim().toLocaleLowerCase('tr'); renderPhases(); });

  function goTo(target) {
    if (!target) return;
    const [phaseId, itemId] = target.split('/');
    const el = document.getElementById(itemId || `faz-${phaseId}`);
    if (!el) return;
    document.querySelectorAll('.item.focused').forEach((n) => n.classList.remove('focused'));
    el.scrollIntoView({ block: 'center' });
    if (itemId) {
      el.classList.add('focused');
      el.focus({ preventScroll: true });
    }
  }

  view.append(
    h('div', { class: 'page-head' },
      h('div', {},
        h('p', { class: 'eyebrow' }, 'Evren / Yol Haritası'),
        h('h1', {}, rm.title),
        h('p', { class: 'lead' }, `Son güncelleme ${formatDate(rm.updated_at)}. ${rm.note}`))),
    h('div', { class: 'grid cols-4' },
      h('div', { class: 'card stat' }, h('b', {}, `%${overall.pct}`), h('span', {}, 'Genel ilerleme (tamamlanan iş kalemi)')),
      h('div', { class: 'card stat' }, h('b', {}, `${current.id}`), h('span', {}, `Etkin faz: ${current.title}`)),
      h('div', { class: 'card stat' }, h('b', {}, String(overall.prog)), h('span', {}, 'Devam eden iş kalemi')),
      h('div', { class: 'card stat' }, h('b', {}, String(overall.blocked)), h('span', {}, 'Engelli veya riskli iş kalemi'))),
    h('nav', { class: 'stepper', 'aria-label': 'Fazlar' }, rm.phases.map((phase) => {
      const p = progress(phase.items);
      return h('button', {
        class: `step${phase === current ? ' current' : ''}`, type: 'button',
        'aria-current': phase === current ? 'step' : null,
        onclick: () => document.getElementById(`faz-${phase.id}`)?.scrollIntoView({ block: 'start' }),
      },
        h('div', { class: 'top' }, h('span', { class: `chip phase-${phase.id}` }, phase.id), h('small', {}, `%${p.pct}`)),
        h('strong', {}, phase.title),
        h('small', {}, phase.window.split('·')[0].trim()),
        bar(p));
    })),
    h('div', { class: 'filters', role: 'group', 'aria-label': 'Durum filtresi' }, filterButtons, search, countLabel),
    phasesBox,
    h('div', { class: 'grid cols-2' },
      h('section', { class: 'card' }, h('h2', {}, 'İlkeler'), h('ul', {}, rm.principles.map((p) => h('li', {}, p)))),
      h('section', { class: 'card' }, h('h2', {}, 'Açık sorular'), h('ul', {}, rm.open_questions.map((q) => h('li', {}, q))))),
    h('p', { class: 'footnote' }, 'Kaynak: apps/web/content/roadmap.json — DURUM.md ile birlikte güncellenir.'),
  );
  renderPhases();
  requestAnimationFrame(() => goTo(param));

  return {
    update(next) {
      requestAnimationFrame(() => goTo(next));
    },
  };
}
