// Güvenli Markdown → DOM (U8.2, ADR-0033): kılavuz ve teknik belgeler için. HTML dizgesi hiç üretilmez; her metin
// textContent ile yazılır. Desteklenenler: başlık (#–####), paragraf, madde ve numaralı liste (bir alt düzey),
// alıntı (not kutusu), tablo, kod bloğu, yatay çizgi; satır içinde `kod`, **kalın**, *eğik*, [bağlantı](hedef).
//
// Bağlantı hedefleri: kilavuz:<konu> → #/kilavuz/<konu>, belge:<ad> → #/belge/<ad>, #/<arayüz yolu>; https://… dış
// adresler tıklanmaz metin olarak gösterilir (arayüz dışarıya bağlantı vermez). Teknik belgelerdeki göreli .md bağlantıları belge yoluna çevrilir; başka her hedef düz metin olur.
import { h } from './dom.js';

const SAFE_EXTERNAL = /^https:\/\/[^\s]+$/;

/** Bağlantı hedefini arayüz adresine çevirir; desteklenmeyen hedefte null. ``base``: belgenin klasörü (ör. "adr"). */
export function resolveHref(href, base = '') {
  if (href.startsWith('kilavuz:')) return `#/kilavuz/${encodeURIComponent(href.slice(8))}`;
  if (href.startsWith('belge:')) return `#/belge/${href.slice(6)}`;
  if (href.startsWith('#/')) return href;
  if (SAFE_EXTERNAL.test(href)) return href;
  // Teknik belgeler arası göreli bağlantı: "adr/0031-x.md", "06-guvenlik.md", "../06-guvenlik.md", "docs/06-x.md#bolum"
  const m = /^(?:\.\/)?((?:\.\.\/)*)(?:docs\/)?((?:adr\/)?[0-9A-Za-z][0-9A-Za-z.-]*)\.md(?:#.*)?$/.exec(href);
  if (m) {
    const up = m[1].length / 3;
    const dir = base && !up ? `${base}/` : '';
    const name = `${dir}${m[2]}`.replace(/^adr\/adr\//, 'adr/');
    return /^(?:adr\/)?[0-9A-Za-z][0-9A-Za-z.-]*$/.test(name) ? `#/belge/${name}` : null;
  }
  return null;
}

/** Satır içi biçim: kod, kalın, eğik, bağlantı. Düğüm dizisi döner. */
export function inline(text, base = '') {
  const out = [];
  let buf = '';
  const flush = () => { if (buf) { out.push(document.createTextNode(buf)); buf = ''; } };
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (ch === '`') {
      const end = text.indexOf('`', i + 1);
      if (end > i) { flush(); out.push(h('code', {}, text.slice(i + 1, end))); i = end + 1; continue; }
    }
    if (ch === '*' && text[i + 1] === '*') {
      const end = text.indexOf('**', i + 2);
      if (end > i + 2) { flush(); out.push(h('strong', {}, inline(text.slice(i + 2, end), base))); i = end + 2; continue; }
    }
    if (ch === '*' && text[i + 1] && text[i + 1] !== ' ') {
      const end = text.indexOf('*', i + 1);
      if (end > i + 1 && text[end - 1] !== ' ') { flush(); out.push(h('em', {}, inline(text.slice(i + 1, end), base))); i = end + 1; continue; }
    }
    if (ch === '[') {
      const m = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(text.slice(i));
      if (m) {
        flush();
        const href = resolveHref(m[2], base);
        const label = inline(m[1], base);
        if (href && href.startsWith('https://')) {
          // Arayüz dışarıya bağlantı vermez (dom.js isSafeHref); adres okunur ve kopyalanabilir metin olarak durur
          out.push(h('span', { class: 'md-extlink' }, label, ' ', h('span', { class: 'mono cell-sub' }, `(${href})`)));
        } else if (href) {
          out.push(h('a', { href }, label));
        } else {
          out.push(h('span', { class: 'md-deadlink' }, label));
        }
        i += m[0].length;
        continue;
      }
    }
    buf += ch;
    i += 1;
  }
  flush();
  return out;
}

const slug = (text) => text.toLocaleLowerCase('tr').replace(/[^0-9a-zçğıöşü]+/g, '-').replace(/^-|-$/g, '');

function table(lines, base) {
  const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  const head = cells(lines[0]);
  const rows = lines.slice(2).map(cells);
  return h('div', { class: 'md-table' }, h('table', {},
    h('thead', {}, h('tr', {}, head.map((c) => h('th', { scope: 'col' }, inline(c, base))))),
    h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, inline(c, base))))))));
}

/**
 * Markdown metnini DOM'a çevirir. Döner: { title, nodes, headings } — ilk "# Başlık" sayfa başlığı olarak ayrılır.
 * ``base``: göreli belge bağlantıları için klasör.
 */
export function renderMarkdown(text, { base = '' } = {}) {
  const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
  const nodes = [];
  const headings = [];
  let title = null;
  let i = 0;
  const isBlank = (l) => !l || !l.trim();
  const listItem = /^(\s*)([-*]|\d+\.)\s+(.*)$/;
  while (i < lines.length) {
    const line = lines[i];
    if (isBlank(line)) { i += 1; continue; }
    const hm = /^(#{1,4})\s+(.*)$/.exec(line);
    if (hm) {
      const level = hm[1].length;
      if (level === 1 && title === null) { title = hm[2].trim(); i += 1; continue; }
      const id = slug(hm[2]);
      headings.push({ level, text: hm[2].trim(), id });
      nodes.push(h(`h${Math.min(level + 0, 4)}`, { id: `md-${id}` }, inline(hm[2].trim(), base)));
      i += 1;
      continue;
    }
    if (line.startsWith('```')) {
      const code = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) { code.push(lines[i]); i += 1; }
      i += 1;
      nodes.push(h('pre', { class: 'md-code' }, h('code', {}, code.join('\n'))));
      continue;
    }
    if (/^(-{3,}|\*{3,})\s*$/.test(line)) { nodes.push(h('hr', {})); i += 1; continue; }
    if (line.startsWith('>')) {
      const quote = [];
      while (i < lines.length && lines[i].startsWith('>')) { quote.push(lines[i].replace(/^>\s?/, '')); i += 1; }
      nodes.push(h('div', { class: 'notice md-note', role: 'note' }, inline(quote.join(' '), base)));
      continue;
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(lines[i]); i += 1; }
      nodes.push(table(rows, base));
      continue;
    }
    if (listItem.test(line)) {
      const top = listItem.exec(line);
      const ordered = /\d/.test(top[2]);
      const list = h(ordered ? 'ol' : 'ul', { class: 'md-list' });
      let last = null;
      while (i < lines.length && (listItem.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && last))) {
        const m = listItem.exec(lines[i]);
        if (m && (m[1].length < 2 || !last)) {  // girintili ilk madde de üst düzey sayılır
          last = h('li', {}, inline(m[3], base));
          list.append(last);
        } else if (m) {
          // Bir alt düzey
          let sub = last.lastElementChild;
          if (!sub || !/^(UL|OL)$/.test(sub.tagName)) { sub = h(/\d/.test(m[2]) ? 'ol' : 'ul', { class: 'md-list' }); last.append(sub); }
          sub.append(h('li', {}, inline(m[3], base)));
        } else {
          last.append(' ', ...inline(lines[i].trim(), base));
        }
        i += 1;
      }
      nodes.push(list);
      continue;
    }
    const para = [];
    while (i < lines.length && !isBlank(lines[i]) && !/^(#{1,4}\s|```|>|\s*([-*]|\d+\.)\s)/.test(lines[i])
      && !(lines[i].trim().startsWith('|'))) {
      para.push(lines[i].trim());
      i += 1;
    }
    if (!para.length) { para.push(line.trim()); i += 1; }
    nodes.push(h('p', {}, inline(para.join(' '), base)));
  }
  return { title, nodes, headings };
}
