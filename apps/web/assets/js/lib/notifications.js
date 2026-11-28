// Bildirim merkezi (U8.9, ADR-0040): üst çubukta zil ve okunmamış sayısı, sağdan açılan panelde bildirimler.
// Bildirim kişiye, gruba veya role gelir; okundu bilgisi kişi başınadır. Bir bildirime tıklamak onu okundu sayar ve
// ilgili ekrana götürür. Sayı dakikada bir ve panel kapanınca yenilenir.
import { h, append, clear, formatDateTime, pill } from './dom.js';
import { icon } from './icons.js';
import { closeDetail, openDetail, toast } from './shell.js';
import { notificationCount, notificationsList, notificationsRead, notificationsReadAll } from './data.js';

const POLL_MS = 60000;
const SEV_TONE = { critical: 'bad', warning: 'warn', info: 'neutral' };
const VIA = { user: 'Size', group: 'Grubunuza', role: 'Rolünüze' };

let button = null;
let badge = null;

function showCount(n, critical) {
  if (!badge) return;
  badge.textContent = n > 99 ? '99+' : String(n);
  badge.hidden = n === 0;
  badge.classList.toggle('critical', critical > 0);
  button.setAttribute('aria-label', n ? `Bildirimler: ${n} okunmamış` : 'Bildirimler: okunmamış yok');
  button.title = button.getAttribute('aria-label');
}

async function refreshCount() {
  try {
    const c = await notificationCount();
    showCount(c.unread, c.critical_unread);
  } catch { /* oturum düşmüş olabilir; sonraki yoklamada yeniden denenir */ }
}

function item(n, onOpen) {
  const row = h('li', { class: `notif${n.read ? '' : ' unread'}`, dataset: { id: n.sys_id } },
    h('button', { type: 'button', class: 'notif-open', onclick: () => onOpen(n) },
      h('span', { class: 'notif-dot', 'aria-hidden': 'true' }),
      h('span', { class: 'notif-text' },
        h('strong', {}, n.title),
        n.body ? h('span', { class: 'notif-body' }, n.body) : null,
        h('span', { class: 'cell-sub' }, `${formatDateTime(n.created_at)} · ${VIA[n.via] || ''} · ${n.kind_label}`,
          n.read ? '' : ' · okunmadı')),
      n.severity !== 'info' ? pill(n.severity_label, SEV_TONE[n.severity]) : null));
  return row;
}

export async function openNotifications(opener = button) {
  const list = h('ul', { class: 'notif-list', id: 'notifList', 'aria-label': 'Bildirimler' });
  const status = h('p', { class: 'muted small', id: 'notifStatus', 'aria-live': 'polite' });
  const more = h('button', { class: 'btn secondary small', type: 'button', id: 'notifMore', hidden: true }, 'Daha eski bildirimler');
  const onlyUnread = h('input', { type: 'checkbox', id: 'notifUnread' });
  const readAll = h('button', { class: 'btn secondary', type: 'button', id: 'notifReadAll' }, 'Tümünü okundu say');
  let next = null;

  async function open(n) {
    if (!n.read) {
      try { await notificationsRead([n.sys_id]); } catch { /* okundu işareti başarısızsa yine de git */ }
    }
    closeDetail({ silent: true });
    refreshCount();
    if (n.link) location.hash = n.link.slice(1);
  }

  async function load(append_ = false) {
    let res;
    try {
      res = await notificationsList({ unreadOnly: onlyUnread.checked, before: append_ ? next : null });
    } catch (err) {
      status.textContent = `Bildirimler yüklenemedi: ${err.message}`;
      return;
    }
    if (!append_) clear(list);
    append(list, res.items.map((n) => item(n, open)));
    next = res.next;
    more.hidden = !next;
    status.textContent = res.unread ? `${res.unread} okunmamış bildirim.` : 'Okunmamış bildirim yok.';
    if (!list.children.length) list.append(h('li', { class: 'empty' }, onlyUnread.checked ? 'Okunmamış bildirim yok.' : 'Henüz bildirim yok.'));
    showCount(res.unread, res.critical_unread);
  }
  onlyUnread.addEventListener('change', () => load());
  more.addEventListener('click', () => load(true));
  readAll.addEventListener('click', async () => {
    readAll.disabled = true;
    try {
      const r = await notificationsReadAll();
      toast(r.marked ? `${r.marked} bildirim okundu sayıldı.` : 'Okunmamış bildirim yoktu.');
      await load();
    } catch (err) {
      toast(err.message);
    } finally {
      readAll.disabled = false;
    }
  });
  openDetail({
    kicker: 'Bildirim merkezi', title: 'Bildirimler', opener,
    onClose: () => refreshCount(),
    body: [
      h('p', { class: 'muted small' }, 'Size, grubunuza ve rollerinize gelen bildirimler (son 90 gün). Bir bildirime tıklayın: okundu sayılır ve ilgili ekran açılır.'),
      h('label', { class: 'check', for: 'notifUnread' }, onlyUnread, ' Yalnızca okunmamışlar'),
      status, list, more,
    ],
    footer: [readAll],
  });
  await load();
}

/** API modunda zili gösterir ve okunmamış sayısını yoklar. */
export function setupNotifications() {
  button = document.getElementById('notifyOpen');
  if (!button) return;
  badge = h('span', { class: 'notif-badge', hidden: true, 'aria-hidden': 'true' });
  append(button, [icon('bell'), badge]);
  button.hidden = false;
  button.addEventListener('click', () => openNotifications(button));
  refreshCount();
  setInterval(() => { if (!document.hidden) refreshCount(); }, POLL_MS);
}
