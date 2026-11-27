// Kullanım kılavuzu ve teknik belgeler (U8.2, ADR-0033). Kılavuzun tek kaynağı docs/kilavuz/ (tools/build_guide.py →
// content/guide.json). Katmanlı: giriş sayfası kısa ve geneldir, ayrıntı konu sayfalarında, teknik belgeler
// bağlantıyla (#/belge/<ad>; oturum ister, canlı API'den gelir). Markdown güvenli işleyiciyle DOM'a çevrilir.
import { h, clear, errorBox, fold } from '../lib/dom.js';
import { setHeader } from '../lib/shell.js';
import { backend, docGet, docsList, guide } from '../lib/data.js';
import { renderMarkdown } from '../lib/markdown.js';

function onThisPage(headings) {
  const h2 = headings.filter((x) => x.level === 2);
  if (h2.length < 3) return null;
  return h('nav', { class: 'guide-toc', 'aria-label': 'Bu sayfada' }, h('span', { class: 'muted small' }, 'Bu sayfada: '),
    h2.map((x) => h('button', { class: 'linklike small', type: 'button',
      onclick: () => document.getElementById(`md-${x.id}`)?.scrollIntoView({ block: 'start' }) }, x.text)));
}

export async function renderGuide(view, param) {
  const g = await guide();
  const byId = new Map(g.topics.map((t) => [t.id, t]));
  const nav = h('nav', { class: 'guide-nav', 'aria-label': 'Kılavuz konuları' });
  const article = h('article', { class: 'guide-article panel', id: 'guideArticle', tabindex: '-1' });
  const search = h('input', { type: 'search', class: 'searchbar', id: 'guideSearch', placeholder: 'Kılavuzda ara',
    'aria-label': 'Kılavuzda ara', autocomplete: 'off', 'aria-controls': 'guideTopics' });
  const list = h('ul', { class: 'guide-topics', id: 'guideTopics' });
  nav.append(search, list);
  view.append(h('div', { class: 'guide-layout' }, nav, article));

  let current = null;
  function paintList() {
    const q = fold(search.value.trim());
    const hits = g.topics.filter((t) => !q || fold(`${t.title} ${t.summary} ${t.markdown}`).includes(q));
    clear(list).append(...hits.map((t) => h('li', {}, h('a', {
      href: `#/kilavuz/${t.id}`, class: t.id === current ? 'active' : '', 'aria-current': t.id === current ? 'page' : null,
    }, t.title, q ? h('span', { class: 'cell-sub' }, t.summary) : null))));
    if (!hits.length) list.append(h('li', { class: 'muted small' }, 'Eşleşen konu yok.'));
  }
  search.addEventListener('input', paintList);
  search.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { const a = list.querySelector('a'); if (a) { ev.preventDefault(); location.hash = a.getAttribute('href'); } }
  });

  function show(id) {
    const t = byId.get(id) || byId.get(g.start);
    current = t.id;
    const { title, nodes, headings } = renderMarkdown(t.markdown);
    setHeader({ title: title || t.title, sub: t.summary,
      crumbs: t.id === g.start ? [['Kullanım Kılavuzu']] : [['Kullanım Kılavuzu', '#/kilavuz'], [t.title]] });
    const idx = g.topics.indexOf(t);
    const prev = idx > 0 ? g.topics[idx - 1] : null;
    const next = idx < g.topics.length - 1 ? g.topics[idx + 1] : null;
    // Kılavuz taslakken (içeriğin son hâli ürün oturunca yazılır) okuyucu bunu bilir
    const draft = g.draft ? h('p', { class: 'notice small', role: 'note', id: 'guideDraft' }, h('strong', {}, 'Taslak. '),
      'Kılavuzun son hâli ürün tamamlandığında yazılacak; ekran adları ve adımlar değişebilir.') : '';
    clear(article).append(draft, onThisPage(headings) || '', ...nodes,
      h('nav', { class: 'guide-pager', 'aria-label': 'Konular arasında' },
        prev ? h('a', { href: `#/kilavuz/${prev.id}`, class: 'btn ghost small', id: 'guidePrev' }, `← ${prev.title}`) : h('span'),
        t.id !== g.start ? h('a', { href: '#/kilavuz', class: 'btn ghost small' }, 'Kılavuz başlangıcı') : h('span'),
        next ? h('a', { href: `#/kilavuz/${next.id}`, class: 'btn ghost small', id: 'guideNext' }, `${next.title} →`) : h('span')));
    paintList();
    article.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }

  const update = (p) => show(p || g.start);
  update(param);
  return { update };
}

export async function renderDoc(view, param) {
  const { mode } = await backend();
  const back = h('a', { href: '#/kilavuz/teknik', class: 'btn ghost small' }, '← Teknik belgeler');
  const article = h('article', { class: 'guide-article panel doc-article', id: 'docArticle', tabindex: '-1' });
  view.append(h('div', { class: 'toolbar' }, back), article);
  async function update(p) {
    if (mode !== 'api') {
      setHeader({ title: 'Teknik belge', crumbs: [['Kullanım Kılavuzu', '#/kilavuz'], ['Teknik belge']] });
      clear(article).append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '),
        'Teknik belgeler oturum açılmış canlı kurulumda uygulama içinde açılır. Depoda: ',
        h('span', { class: 'mono' }, `docs/${p || ''}.md`)));
      return;
    }
    try {
      if (!p) {
        const d = await docsList();
        setHeader({ title: 'Teknik belgeler', crumbs: [['Kullanım Kılavuzu', '#/kilavuz'], ['Teknik belgeler']] });
        clear(article).append(h('h2', {}, 'Belgeler'), h('ul', { class: 'md-list' }, d.docs.map((x) => h('li', {}, h('a', { href: `#/belge/${x.name}` }, x.title)))),
          h('h2', {}, 'Karar kayıtları (ADR)'), h('ul', { class: 'md-list' }, d.adrs.map((x) => h('li', {}, h('a', { href: `#/belge/${x.name}` }, x.title)))));
        return;
      }
      const d = await docGet(p);
      const { title, nodes } = renderMarkdown(d.markdown, { base: d.base });
      setHeader({ title: title || d.title, sub: 'Teknik belge', crumbs: [['Kullanım Kılavuzu', '#/kilavuz'], ['Teknik belgeler', '#/belge'], [title || d.title]] });
      clear(article).append(...nodes);
      article.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    } catch (err) {
      clear(article).append(errorBox(err.status === 404 ? 'Belge bulunamadı.' : `Belge açılamadı: ${err.message}`));
    }
  }
  await update(param);
  return { update };
}

