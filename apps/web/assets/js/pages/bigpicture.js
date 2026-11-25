// Büyük resim: tek sayfalık mimari özet (tools/build_big_picture.py → content/big-picture.svg).
import { h, loadSVG } from '../lib/dom.js';
import { setHeader } from '../lib/shell.js';

export async function renderBigPicture(view) {
  const svg = await loadSVG('/content/big-picture.svg');
  svg.removeAttribute('width');
  svg.removeAttribute('height');
  svg.classList.add('bp-svg');
  setHeader({
    title: 'Büyük Resim',
    sub: 'Kaynaklar değişebilir, kurallar değişmez. Gözlemler ara katmanda anlamlanır, golden record Evren\'de tutulur, ServiceNow CMDB replika olur.',
    crumbs: [['Büyük Resim']],
    actions: [h('a', { class: 'btn secondary', href: '#/mimari' }, 'Bileşen ayrıntısı: Mimari Harita'),
      h('a', { class: 'btn secondary', href: '/content/big-picture.svg', download: 'Evren_Buyuk_Resim.svg', id: 'bpExport' }, 'SVG indir')],
  });
  view.append(
    h('div', { class: 'panel bp-frame', tabindex: '0', 'aria-label': 'Büyük resim çizimi; dar ekranda yatay kaydırın' }, svg),
    h('p', { class: 'muted small' }, 'Kaynak: tools/build_big_picture.py (fazlar roadmap.json\'dan okunur). Adım adım plan: Yol Haritası sayfası ve docs/PROJE-PLANI.md.'),
  );
}
