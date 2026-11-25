// Güvenli DOM yardımcıları. HTML dizgesinden DOM üretilmez; metin her zaman textContent ile yazılır.

const SVGNS = 'http://www.w3.org/2000/svg';

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key === 'value' && 'value' in el) el.value = value;
    else if (key === 'checked' || key === 'selected' || key === 'disabled') el[key] = !!value;
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (key === 'href' && typeof value === 'string' && !isSafeHref(value)) continue;
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  append(el, children);
  return el;
}

/** SVG öğesi (grafikler ve ikonlar için). */
export function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value !== undefined && value !== null) el.setAttribute(key, String(value));
  }
  for (const c of children.flat(Infinity)) {
    if (c === undefined || c === null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function append(parent, children) {
  for (const child of [children].flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

function isSafeHref(value) {
  return value.startsWith('#') || value.startsWith('/') || value.startsWith('./');
}

const cache = new Map();

/** Aynı içerik bir oturumda bir kez indirilir (sayfalar arası geçişte yeniden yüklenmez). */
export function loadJSON(path, { fresh = false } = {}) {
  if (!fresh && cache.has(path)) return cache.get(path);
  const promise = fetch(path, { credentials: 'same-origin', cache: 'no-store' }).then((res) => {
    if (!res.ok) throw new Error(`${path} yüklenemedi (HTTP ${res.status})`);
    return res.json();
  });
  cache.set(path, promise);
  promise.catch(() => cache.delete(path));
  return promise;
}

export async function loadSVG(path) {
  const res = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
  if (!res.ok) throw new Error(`${path} yüklenemedi (HTTP ${res.status})`);
  const doc = new DOMParser().parseFromString(await res.text(), 'image/svg+xml');
  if (doc.querySelector('parsererror')) throw new Error('Mimari çizim çözümlenemedi');
  // Güvenlik: betik ve olay öznitelikleri kabul edilmez (içerik kendi kaynağımız olsa da).
  doc.querySelectorAll('script, foreignObject').forEach((n) => n.remove());
  doc.querySelectorAll('*').forEach((n) => {
    for (const attr of [...n.attributes]) if (attr.name.toLowerCase().startsWith('on')) n.removeAttribute(attr.name);
  });
  return document.importNode(doc.documentElement, true);
}

export function errorBox(message) {
  return h('div', { class: 'error', role: 'alert' }, message);
}

export const STATUS = {
  done: 'Tamamlandı',
  in_progress: 'Devam ediyor',
  planned: 'Planlandı',
  blocked: 'Engelli',
  at_risk: 'Riskli',
};

export function statusChip(status, labels = STATUS) {
  return h('span', { class: `chip st-${status}` }, labels[status] || status);
}

export function phaseChip(phase, label) {
  return h('span', { class: `chip phase-${phase}`, title: label || phase }, phase);
}

export function pill(text, tone = '') {
  return h('span', { class: `pill ${tone}`.trim() }, text);
}

export function formatDate(iso) {
  const [y, m, d] = String(iso).split('-');
  return d && m && y ? `${d}.${m}.${y.slice(0, 4)}` : String(iso);
}

const DT = new Intl.DateTimeFormat('tr-TR', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Istanbul',
});

/** GG.AA.YYYY SS:dd (İstanbul saati). */
export function formatDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : DT.format(d).replace(',', '');
}

export function formatNumber(n) {
  return Number(n).toLocaleString('tr-TR');
}

export function percent(value, digits = 0) {
  return `%${Number(value).toLocaleString('tr-TR', { maximumFractionDigits: digits, minimumFractionDigits: digits })}`;
}

export function fold(text) {
  return String(text ?? '').toLocaleLowerCase('tr');
}

/** Tablo: başlıklar sıralanabilir; satırlar klavyeyle açılabilir (Enter). */
export function dataTable({ columns, rows, onOpen, selectedId, idOf = (r) => r.id, empty = 'Kayıt yok.', label, sort }) {
  const state = sort || { key: null, dir: 1 };
  const wrap = h('div', { class: 'tablewrap' });
  function render() {
    clear(wrap);
    if (!rows.length) {
      wrap.append(h('div', { class: 'empty' }, h('strong', {}, 'Sonuç yok'), empty));
      return;
    }
    const sorted = [...rows];
    const col = columns.find((c) => c.key === state.key);
    if (col) {
      const val = col.sortValue || ((r) => r[col.key]);
      sorted.sort((a, b) => String(val(a) ?? '').localeCompare(String(val(b) ?? ''), 'tr', { numeric: true }) * state.dir);
    }
    const head = h('tr', {}, columns.map((c) => h('th', {
      scope: 'col', class: c.num ? 'num' : null,
      'aria-sort': state.key === c.key ? (state.dir > 0 ? 'ascending' : 'descending') : null,
    }, c.sortable === false ? c.label : h('button', {
      class: 'sort', type: 'button',
      onclick: () => { state.dir = state.key === c.key ? -state.dir : 1; state.key = c.key; render(); },
    }, c.label, h('span', { 'aria-hidden': 'true' }, state.key === c.key ? (state.dir > 0 ? '↑' : '↓') : '↕')))));
    const body = h('tbody', {}, sorted.map((r) => {
      const id = idOf(r);
      const tr = h('tr', {
        class: `${onOpen ? 'clickable' : ''}${selectedId && selectedId() === id ? ' selected' : ''}`,
        tabindex: onOpen ? '0' : null, dataset: { id },
        onclick: onOpen ? () => onOpen(r, tr) : null,
        onkeydown: onOpen ? (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); onOpen(r, tr); } } : null,
      }, columns.map((c) => h('td', { class: c.num ? 'num' : null }, c.render ? c.render(r) : (r[c.key] ?? '—'))));
      return tr;
    }));
    wrap.append(h('table', { 'aria-label': label || null }, h('thead', {}, head), body));
  }
  render();
  wrap.refreshSelection = () => {
    wrap.querySelectorAll('tbody tr').forEach((tr) => tr.classList.toggle('selected', !!selectedId && selectedId() === tr.dataset.id));
  };
  return wrap;
}

export function metric({ label, value, foot, tone = '', href, id }) {
  const tag = href ? 'a' : 'div';
  return h(tag, { class: `metric ${tone}`.trim(), href, id },
    h('span', { class: 'metric-label' }, label),
    h('span', { class: 'metric-number' }, String(value)),
    foot ? h('div', { class: 'metric-foot' }, foot) : null);
}

export function kv(pairs) {
  return h('dl', { class: 'kv' }, pairs.filter(Boolean).flatMap(([k, v]) => [h('dt', {}, k), h('dd', {}, v ?? '—')]));
}

export function section(title, ...children) {
  return h('div', { class: 'section' }, h('h3', {}, title), ...children);
}

export function tabs(items, active, onSelect, label) {
  const bar = h('div', { class: 'tabs', role: 'tablist', 'aria-label': label });
  for (const it of items) {
    bar.append(h('button', {
      class: 'tab', type: 'button', role: 'tab', id: `tab-${it.id}`, 'aria-selected': it.id === active ? 'true' : 'false',
      onclick: () => onSelect(it.id),
    }, it.label, it.count !== undefined ? h('span', { class: 'n' }, String(it.count)) : null));
  }
  return bar;
}
