// Uygulama kabuğu servisleri: sayfa başlığı, sağ detay paneli, iletişim kutusu, arama paleti, bildirim.
// GRC Platformu (tedarik) düzeniyle aynı: beyaz kenar menü, üst çubukta Ctrl+K, sağda detay paneli.
import { h, append, clear, fold } from './dom.js';

const $ = (id) => document.getElementById(id);

let detailState = null; // { onClose, opener }
let overlay = null; // { el, close }

export function setHeader({ title, sub = '', crumbs = [], actions = [] }) {
  $('pageTitle').textContent = title;
  append(clear($('pageSub')), sub);
  const c = clear($('crumbs'));
  const trail = [['Evren', '#/genel-bakis'], ...crumbs];
  trail.forEach(([label, href], i) => {
    if (i) c.append(' / ');
    c.append(href && i < trail.length - 1 ? h('a', { href }, label) : h('span', {}, label));
  });
  append(clear($('topActions')), actions);
  document.title = `${title} · Evren CMDB`;
}

export function openDetail({ kicker = '', title, body, footer = [], onClose, opener }) {
  const panel = $('detail');
  const wasOpen = panel.classList.contains('open');
  detailState = { onClose, opener: opener || (wasOpen ? detailState?.opener : document.activeElement) };
  $('detailKicker').textContent = kicker;
  $('detailTitle').textContent = title;
  append(clear($('detailContent')), body);
  append(clear($('detailFooter')), footer);
  $('detailContent').scrollTop = 0;
  panel.classList.add('open');
  panel.removeAttribute('inert');
  panel.setAttribute('aria-hidden', 'false');
  $('detailTitle').focus({ preventScroll: true });
}

export function isDetailOpen() {
  return $('detail').classList.contains('open');
}

/** Paneli kapatır. ``silent``: sayfa değişiminde çağrılır; onClose ve odak iadesi yapılmaz. */
export function closeDetail({ silent = false } = {}) {
  const panel = $('detail');
  if (!panel.classList.contains('open')) return;
  panel.classList.remove('open');
  panel.setAttribute('inert', '');
  panel.setAttribute('aria-hidden', 'true');
  const state = detailState;
  detailState = null;
  if (silent || !state) return;
  state.onClose?.();
  if (state.opener && document.contains(state.opener)) state.opener.focus({ preventScroll: true });
}

let toastTimer = null;
export function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 4200);
}

function openOverlay(dialog, onClose) {
  closeOverlay();
  const opener = document.activeElement;
  const el = h('div', { class: 'overlay', onclick: (ev) => { if (ev.target === el) closeOverlay(); } }, dialog);
  document.body.append(el);
  overlay = { el, close: () => { el.remove(); onClose?.(); if (opener && document.contains(opener)) opener.focus(); } };
  return overlay;
}

export function closeOverlay() {
  if (!overlay) return false;
  const o = overlay;
  overlay = null;
  o.close();
  return true;
}

export function isOverlayOpen() {
  return overlay !== null;
}

/** İletişim kutusu. ``footer``: eylem düğmeleri; ``focus``: açılınca odaklanacak öğe (yoksa Kapat);
 * ``onClose``: nasıl kapanırsa kapansın (Esc, ✕, dış tıklama, düğme) bir kez çağrılır. */
export function dialog({ title, body, footer = [], focus = null, onClose = null }) {
  const close = h('button', { class: 'btn ghost', type: 'button', 'aria-label': 'Kapat', onclick: closeOverlay }, '✕');
  const box = h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'dialogTitle' },
    h('header', {}, h('h2', { id: 'dialogTitle' }, title), close),
    h('div', { class: 'body' }, body),
    footer.length ? h('footer', {}, footer) : null);
  openOverlay(box, onClose);
  (focus || close).focus();
}

/**
 * Arama paleti (Ctrl+K). ``items``: [{group, label, sub, kind, href, matched?}] — yalnızca arama anında üretilir.
 */
export function palette(buildItems) {
  const input = h('input', {
    class: 'palette-input', type: 'search', id: 'paletteInput', placeholder: 'CI adı, IP, kaynak veya sayfa…',
    'aria-label': 'Ara', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'paletteList', autocomplete: 'off',
  });
  const list = h('ul', { class: 'pal-list', id: 'paletteList', role: 'listbox', 'aria-label': 'Sonuçlar' });
  const box = h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Hızlı arama' }, input, list);
  let results = [];
  let active = 0;

  function go(item) {
    closeOverlay();
    location.hash = item.href;
  }
  function paint() {
    clear(list);
    let group = null;
    results.forEach((it, i) => {
      if (it.group !== group) {
        group = it.group;
        list.append(h('li', { class: 'pal-group', role: 'presentation' }, group));
      }
      list.append(h('li', {
        role: 'option', id: `pal-${i}`, 'aria-selected': i === active ? 'true' : 'false',
        onclick: () => go(it), onmousemove: () => { if (active !== i) { active = i; mark(); } },
      }, h('span', {}, it.label, it.sub ? h('span', { class: 'cell-sub' }, it.sub) : null), h('span', { class: 'kind' }, it.kind)));
    });
    if (!results.length) list.append(h('li', { class: 'pal-group', role: 'presentation' }, 'Sonuç yok'));
    mark();
  }
  function mark() {
    list.querySelectorAll('[role=option]').forEach((li) => li.setAttribute('aria-selected', li.id === `pal-${active}` ? 'true' : 'false'));
    input.setAttribute('aria-activedescendant', results.length ? `pal-${active}` : '');
    list.querySelector(`#pal-${active}`)?.scrollIntoView({ block: 'nearest' });
  }
  let seq = 0;
  async function search() {
    const raw = input.value.trim();
    const q = fold(raw);
    const my = ++seq;
    const all = await buildItems(raw);
    if (my !== seq) return; // eski yanıt yeni aramayı ezmesin
    // Arama motorunun bulduğu öğeler (``matched``) yeniden süzülmez: yazım hatası ve önek eşleşmeleri korunur.
    results = (q ? all.filter((it) => it.matched || fold(`${it.label} ${it.sub || ''} ${it.keywords || ''}`).includes(q))
      : all.filter((it) => it.group === 'Sayfalar'))
      .slice(0, 40);
    active = 0;
    paint();
  }
  input.addEventListener('input', search);
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown') { ev.preventDefault(); active = Math.min(results.length - 1, active + 1); mark(); }
    if (ev.key === 'ArrowUp') { ev.preventDefault(); active = Math.max(0, active - 1); mark(); }
    if (ev.key === 'Enter' && results[active]) { ev.preventDefault(); go(results[active]); }
  });
  openOverlay(box);
  input.focus();
  search();
}
