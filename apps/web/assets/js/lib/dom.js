// Güvenli DOM yardımcıları. HTML dizgesinden DOM üretilmez; metin her zaman textContent ile yazılır.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2), value);
    else if (key === 'href' && typeof value === 'string' && !isSafeHref(value)) continue;
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  append(el, children);
  return el;
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
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

export async function loadJSON(path) {
  const res = await fetch(path, { credentials: 'same-origin', cache: 'no-store' });
  if (!res.ok) throw new Error(`${path} yüklenemedi (HTTP ${res.status})`);
  return res.json();
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

export function formatDate(iso) {
  const [y, m, d] = String(iso).split('-');
  return d && m && y ? `${d}.${m}.${y}` : String(iso);
}
