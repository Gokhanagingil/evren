// Büyük resim: A3 tek sayfalık mimari özet (tools/build_big_picture.py → content/big-picture.svg) ve bileşen durumu
// (content/components.json: her kutunun amacı, teknolojisi, yüzdesi, var olanı, eksiği ve plan bağı).
import { h, loadJSON, loadSVG } from '../lib/dom.js';
import { setHeader } from '../lib/shell.js';

function tone(pct) {
  return pct >= 75 ? 'good' : pct >= 40 ? 'warn' : 'bad';
}

function bar(pct) {
  const el = h('div', { class: `bar comp-bar ${tone(pct)}`, role: 'img', 'aria-label': `%${pct} tamamlandı` });
  const seg = h('i', {});
  seg.style.width = `${pct}%`; // CSSOM: CSP ile uyumlu
  el.append(seg);
  return el;
}

function average(items) {
  const vals = items.filter((it) => !it.same_as && !it.optional).map((it) => it.pct);
  return vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : 0;
}

function groupTable(g, onlyGaps) {
  const rows = g.items.filter((it) => !onlyGaps || it.pct < 100);
  return h('section', { class: 'panel comp-group', 'aria-labelledby': `cg-${g.id}` },
    h('div', { class: 'comp-head' },
      h('h2', { id: `cg-${g.id}` }, g.title),
      h('strong', { class: `comp-pct ${tone(average(g.items))}` }, `%${average(g.items)}`)),
    h('div', { class: 'tablewrap' }, h('table', { class: 'comp-table', 'aria-label': `${g.title} bileşenleri` },
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Bileşen ve görevi'), h('th', { scope: 'col' }, 'Teknoloji'),
        h('th', { scope: 'col' }, 'Tamamlanma'),
        h('th', { scope: 'col' }, 'Var olan'), h('th', { scope: 'col' }, 'Eksik'), h('th', { scope: 'col' }, 'Plan'))),
      h('tbody', {}, ...rows.map((it) => h('tr', { id: `comp-${it.id}` },
        h('th', { scope: 'row' }, it.label, h('span', { class: 'cell-sub comp-purpose' }, it.purpose || '')),
        h('td', { class: 'comp-tech' }, it.tech || '—'),
        h('td', { class: 'comp-cell' }, h('span', { class: `comp-pct ${tone(it.pct)}` }, `%${it.pct}`), bar(it.pct)),
        h('td', {}, it.done || '—'),
        h('td', {}, it.missing || '—'),
        h('td', { class: 'comp-plan' }, ...(it.plan.length
          ? it.plan.map((ref) => h('a', { class: 'pill', href: '#/yol-haritasi' }, ref))
          : [h('span', { class: 'muted' }, 'tamam')]))))))));
}

export async function renderBigPicture(view) {
  const [svg, comps] = await Promise.all([
    loadSVG('/content/big-picture.svg'),
    loadJSON('/content/components.json'),
  ]);
  svg.removeAttribute('width');
  svg.removeAttribute('height');
  svg.classList.add('bp-svg');
  setHeader({
    title: 'Büyük Resim',
    sub: 'Kaynaklar değişebilir, kurallar değişmez. Gözlemler ara katmanda anlamlanır, golden record Evren\'de tutulur, ServiceNow CMDB replika olur.',
    crumbs: [['Büyük Resim']],
    actions: [h('a', { class: 'btn secondary', href: '#/mimari' }, 'Bileşen ayrıntısı: Mimari Harita'),
      h('a', { class: 'btn secondary', href: '/content/big-picture.svg', download: 'Evren_Buyuk_Resim_A3.svg', id: 'bpExport',
        title: 'A3 yatay (420 × 297 mm); tarayıcıdan veya vektör düzenleyiciden yazdırılabilir' }, 'A3 çizimi indir (SVG)')],
  });
  const all = comps.groups.flatMap((g) => g.items);
  const gaps = all.filter((it) => it.pct < 100 && !it.same_as).length;
  const list = h('div', { id: 'compGroups' });
  const toggle = h('input', { type: 'checkbox', id: 'compOnlyGaps' });
  const paint = () => {
    list.replaceChildren(...comps.groups.map((g) => groupTable(g, toggle.checked)));
  };
  toggle.addEventListener('change', paint);
  paint();
  view.append(
    h('div', { class: 'panel bp-frame', tabindex: '0', 'aria-label': 'Büyük resim çizimi; dar ekranda yatay kaydırın' }, svg),
    h('section', { class: 'comp-intro', 'aria-labelledby': 'compTitle' },
      h('h2', { id: 'compTitle' }, `Bileşen durumu · genel %${average(all)}`),
      h('p', { class: 'muted' }, `${comps.scale} ${comps.rule}`),
      h('label', { class: 'check' }, toggle, ` Yalnızca eksiği olanlar (${gaps} bileşen)`)),
    list,
    h('p', { class: 'muted small' }, `Kaynak: apps/web/content/components.json (güncelleme ${comps.updated_at}) ve tools/build_big_picture.py. Adım adım plan: Yol Haritası sayfası ve docs/PROJE-PLANI.md.`),
  );
}
