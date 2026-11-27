// Yönetişim görevleri (U8.4, ADR-0035, F1-24): bana atanan görevler ve kararı; Konfigürasyon yöneticisi için bütün
// görevler, atanmamışlar, dikkat isteyenler, görev açma ve kanal ayarı. Görev ServiceNow'da açıldıysa karar yine burada
// verilir; görev çalışanı ServiceNow kaydını kararla kapatır.
import { h, clear, dataTable, formatDateTime, kv, pill, section, tabs } from '../lib/dom.js';
import { closeDetail, closeOverlay, dialog, openDetail, setHeader, toast } from '../lib/shell.js';
import {
  backend, ciSearch, refSearch, taskCancel, taskChannels, taskChannelUpdate, taskComplete, taskCreate, taskDetail,
  tasksList,
} from '../lib/data.js';

const SYSID = /^[0-9a-f]{32}$/;
const VIEWS = [['mine', 'Bana atanan'], ['all', 'Tümü'], ['unassigned', 'Atanmamış'], ['attention', 'Dikkat'],
  ['channels', 'Kanallar']];
const PARAM = { atanmamis: 'unassigned', dikkat: 'attention', tumu: 'all', kanallar: 'channels' };
const SLUG = Object.fromEntries(Object.entries(PARAM).map(([k, v]) => [v, k]));
const DELIVERY_TONE = { delivered: 'good', closed_sent: 'neutral', not_needed: 'neutral', pending: 'warn',
  failed: 'bad', kept_in_evren: 'warn' };
const MIN_TEXT = 10;
const EVENT_TR = {
  created: 'Açıldı', delivered: 'ServiceNow\'da açıldı', delivery_failed: 'ServiceNow\'a teslim edilemedi',
  kept_in_evren: 'Evren\'de tutuldu (ServiceNow\'da karşılık yok)', synced: 'Eşitlendi',
  external_closed: 'ServiceNow\'da karar verilmeden kapatıldı', completed: 'Karar verildi', cancelled: 'İptal edildi',
  closed_sent: 'ServiceNow kaydı kapatıldı', reassigned: 'Yeniden atandı',
};

function stateCell(t) {
  if (t.state !== 'open') return pill(t.outcome_tr || 'Kapalı', t.state === 'cancelled' ? 'neutral' : 'good');
  if (t.external_closed) return pill('ServiceNow\'da kararsız kapatıldı', 'bad');
  return t.overdue ? pill('Süresi geçti', 'bad') : pill('Açık', 'warn');
}

function assigneeText(a) {
  const who = [a.user_name, a.group_name].filter(Boolean).join(' · ');
  return h('div', {}, who || h('span', { class: 'muted' }, 'Atanmamış'), h('div', { class: 'cell-sub' }, a.basis_tr));
}

function needText(input, box, message) {
  if (input.value.trim().length >= MIN_TEXT) return true;
  input.setAttribute('aria-invalid', 'true');
  clear(box).append(h('div', { class: 'notice bad', role: 'alert' }, message));
  input.focus();
  return false;
}

function wire(inputs, button, box) {
  for (const el of inputs) {
    el.addEventListener('input', () => { el.removeAttribute('aria-invalid'); clear(box); });
    if (el.tagName === 'INPUT') el.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); button.click(); } });
  }
}

function openCompleteDialog(t, onDone) {
  const outcome = h('select', { id: 'taskOutcome', 'aria-describedby': 'taskOutcomeHelp' },
    t.outcomes.map((o) => h('option', { value: o.id }, o.label)));
  const note = h('textarea', { id: 'taskNote', rows: '3', maxlength: '2000', placeholder: 'Ne yaptınız? Ör. sahip ve destek grubu güncellendi.' });
  const error = h('div', { id: 'taskError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'taskSend' }, 'Kararı kaydet');
  send.addEventListener('click', async () => {
    if (outcome.value !== 'confirmed' && !needText(note, error, `Bu karar için en az ${MIN_TEXT} karakterlik açıklama yazın.`)) return;
    send.disabled = true;
    try {
      const res = await taskComplete(t.sys_id, outcome.value, note.value.trim());
      closeOverlay();
      toast(res.message);
      onDone();
    } catch (err) {
      send.disabled = false;
      clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
    }
  });
  wire([note, outcome], send, error);
  dialog({
    title: `Karar: ${t.number}`,
    body: [h('p', {}, t.title),
      h('label', { class: 'field', for: 'taskOutcome' }, 'Karar'), outcome,
      h('p', { class: 'muted small', id: 'taskOutcomeHelp' }, '"Doğrulandı / yapıldı" dışındaki kararlar açıklama ister.'),
      h('label', { class: 'field', for: 'taskNote' }, 'Açıklama'), note,
      t.delivery_status === 'delivered' ? h('p', { class: 'muted small' }, `ServiceNow'daki ${t.external_number} kaydı bu kararla kapatılır.`) : null,
      error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: outcome,
  });
}

function openCancelDialog(t, onDone) {
  const why = h('input', { type: 'text', id: 'cancelWhy', maxlength: '2000', placeholder: 'Ör. yanlışlıkla açıldı, TALEP-3' });
  const error = h('div', { id: 'cancelError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'cancelSend' }, 'Görevi iptal et');
  send.addEventListener('click', async () => {
    if (!needText(why, error, `İptal gerekçesi en az ${MIN_TEXT} karakter olmalı.`)) return;
    send.disabled = true;
    try {
      const res = await taskCancel(t.sys_id, why.value.trim());
      closeOverlay();
      toast(res.message);
      onDone();
    } catch (err) {
      send.disabled = false;
      clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
    }
  });
  wire([why], send, error);
  dialog({ title: `İptal: ${t.number}`, body: [h('label', { class: 'field', for: 'cancelWhy' }, 'Gerekçe'), why, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send], focus: why });
}

/** Tek seferlik görev aç. CI seçilirse sorumlu CI'dan bulunur (sahip → yöneten → destek grubu). */
export async function openNewTaskDialog(onDone) {
  const { channels } = await taskChannels();
  const title = h('input', { type: 'text', id: 'newTaskTitle', maxlength: '160', placeholder: 'Ör. Uygulama verilerinin güncelliğini teyit edin' });
  const desc = h('textarea', { id: 'newTaskDesc', rows: '3', maxlength: '4000', placeholder: 'Sorumlunun ne yapacağı, nereye bakacağı' });
  const ciInput = h('input', { type: 'search', id: 'newTaskCi', class: 'searchbar', placeholder: 'CI adı, IP veya FQDN', autocomplete: 'off',
    'aria-controls': 'newTaskCiResults', 'aria-describedby': 'newTaskCiChosen' });
  const ciList = h('ul', { class: 'pick-list', id: 'newTaskCiResults', 'aria-label': 'CI sonuçları', 'aria-live': 'polite' });
  const ciChosen = h('p', { class: 'muted small', id: 'newTaskCiChosen' }, 'CI seçilmedi.');
  const who = h('select', { id: 'newTaskWho' }, h('option', { value: 'auto' }, 'CI\'ın sorumlusu (sahip → yöneten → destek grubu)'),
    h('option', { value: 'user' }, 'Bir kişi seçeceğim'), h('option', { value: 'group' }, 'Bir grup seçeceğim'));
  const target = h('select', { id: 'newTaskTarget', hidden: true, 'aria-label': 'Kişi veya grup' });
  const due = h('input', { type: 'number', id: 'newTaskDue', min: '1', max: '365', value: '14' });
  const channel = h('select', { id: 'newTaskChannel' }, channels.filter((c) => c.lifecycle === 'active')
    .map((c) => h('option', { value: c.name, selected: c.is_default }, c.label_tr + (c.is_default ? ' (varsayılan)' : ''))));
  const error = h('div', { id: 'newTaskError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'newTaskSend' }, 'Görevi aç');
  let ci = null;
  let timer = null;
  ciInput.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = ciInput.value.trim();
      if (q.length < 2) { clear(ciList); return; }
      const res = await ciSearch({ q, limit: 6 });
      clear(ciList).append(...res.items.map((c) => h('li', {}, h('button', { class: 'pick-item', type: 'button', dataset: { id: c.sys_id },
        onclick: () => { ci = c; ciChosen.textContent = `Seçilen CI: ${c.name}`; clear(ciList); who.focus(); } },
      h('strong', {}, c.name), h('span', { class: 'cell-sub' }, c.class_label)))));
      if (!res.items.length) ciList.append(h('li', { class: 'muted small' }, 'Eşleşen CI yok.'));
    }, 250);
  });
  who.addEventListener('change', async () => {
    target.hidden = who.value === 'auto';
    if (who.value === 'auto') return;
    const res = await refSearch(who.value === 'user' ? 'sys_user' : 'sys_user_group', '', 200);
    clear(target).append(...res.items.map((x) => h('option', { value: x.sys_id }, x.name)));
  });
  send.addEventListener('click', async () => {
    clear(error);
    if (title.value.trim().length < 3) { title.setAttribute('aria-invalid', 'true'); title.focus(); clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, 'Başlık yazın.')); return; }
    if (who.value === 'auto' && !ci) { ciInput.focus(); clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, 'Sorumluyu CI\'dan bulmak için bir CI seçin veya kişi/grup seçin.')); return; }
    send.disabled = true;
    try {
      const res = await taskCreate({ title: title.value.trim(), description: desc.value.trim(), ci_sys_id: ci?.sys_id || null,
        assignee: who.value === 'auto' ? { mode: 'auto' } : { mode: who.value, sys_id: target.value || null },
        due_days: Number(due.value) || 14, channel: channel.value });
      closeOverlay();
      toast(res.message);
      onDone(res.sys_id);
    } catch (err) {
      send.disabled = false;
      clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, err.code === 'gecersiz_istek' ? 'Alanları kontrol edin (süre 1–365 gün).' : err.message));
    }
  });
  wire([title, desc], send, error);
  dialog({
    title: 'Tek seferlik görev aç',
    body: [
      h('p', { class: 'muted small' }, 'Zamanlanmış görevler (her çeyrek veri teyidi gibi) ve kurala bağlı görevler sonraki adımda gelecek. Kararı sorumlu Evren\'de verir.'),
      h('label', { class: 'field', for: 'newTaskTitle' }, 'Başlık'), title,
      h('label', { class: 'field', for: 'newTaskDesc' }, 'Açıklama'), desc,
      h('label', { class: 'field', for: 'newTaskCi' }, 'CI'), ciInput, ciList, ciChosen,
      h('label', { class: 'field', for: 'newTaskWho' }, 'Sorumlu'), who, target,
      h('label', { class: 'field', for: 'newTaskDue' }, 'Süre (gün)'), due,
      h('label', { class: 'field', for: 'newTaskChannel' }, 'Kanal'), channel,
      error,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: title,
  });
}

function openChannelDialog(c, onDone) {
  const s = c.settings || {};
  const table = h('input', { type: 'text', id: 'chTable', value: s.table || 'sc_task', maxlength: '80' });
  const close = h('input', { type: 'text', id: 'chClose', value: s.close_state || '3', maxlength: '4' });
  const cancel = h('input', { type: 'text', id: 'chCancel', value: s.cancel_state || '4', maxlength: '4' });
  const group = h('input', { type: 'text', id: 'chGroup', value: s.default_group || '', maxlength: '32', placeholder: 'ServiceNow grup sys_id (isteğe bağlı)' });
  const why = h('input', { type: 'text', id: 'chWhy', maxlength: '500', placeholder: 'Ör. süreç sahibi kararı, TALEP-8' });
  const error = h('div', { id: 'chError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'chSend' }, 'Kaydet');
  send.addEventListener('click', async () => {
    if (!needText(why, error, `Gerekçe en az ${MIN_TEXT} karakter olmalı.`)) return;
    send.disabled = true;
    try {
      const res = await taskChannelUpdate(c.name, { reason: why.value.trim(), settings: { table: table.value.trim(),
        close_state: close.value.trim(), cancel_state: cancel.value.trim(), closed_states: s.closed_states,
        default_group: group.value.trim() || null } });
      closeOverlay();
      toast(res.message);
      onDone();
    } catch (err) {
      send.disabled = false;
      clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
    }
  });
  wire([table, close, cancel, group, why], send, error);
  dialog({
    title: `Kanal ayarı: ${c.label_tr}`,
    body: [h('p', { class: 'muted small' }, 'Görev ServiceNow\'da bu tabloya, yalnızca standart görev alanlarıyla açılır; ServiceNow\'da kural veya alan değişikliği gerekmez. Bağlantı adresi ve kimliği sunucu ayarındadır, burada değişmez.'),
      h('label', { class: 'field', for: 'chTable' }, 'Hedef tablo'), table,
      h('label', { class: 'field', for: 'chClose' }, 'Kapanış durumu (tamamlandı)'), close,
      h('label', { class: 'field', for: 'chCancel' }, 'Kapanış durumu (iptal)'), cancel,
      h('label', { class: 'field', for: 'chGroup' }, 'Varsayılan atama grubu'), group,
      h('p', { class: 'muted small' }, 'Sorumlunun ServiceNow\'da karşılığı yoksa görev bu gruba açılır; boşsa görev Evren\'de kalır.'),
      h('label', { class: 'field', for: 'chWhy' }, 'Gerekçe'), why, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: table,
  });
}

export async function renderTasks(view, param) {
  const { mode } = await backend();
  const head = { title: 'Görevler', crumbs: [['Görevler']],
    sub: 'Size ve grubunuza atanan yönetişim görevleri. Kararı burada verirsiniz; görev ServiceNow\'da açıldıysa Evren o kaydı kararla kapatır.' };
  if (mode !== 'api') {
    setHeader(head);
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '), 'Görevler canlı API ile çalışır.'));
    return {};
  }
  let first = await tasksList({ view: 'mine' });
  const admin = first.can_admin;
  const readAll = first.can_read_all;
  const openBtn = admin ? h('button', { class: 'btn', type: 'button', id: 'taskNew', onclick: () => openNewTaskDialog((id) => { show('all').then(() => openTask(id)); }) }, 'Görev aç') : null;
  setHeader({ ...head, actions: openBtn ? [openBtn] : [] });
  const tabBox = h('div', { id: 'taskTabs' });
  const body = h('div', { id: 'taskBody' });
  if (!first.mapped) view.append(h('p', { class: 'notice warn', role: 'note' }, 'Oturumunuz bir kişi kaydına eşlenmedi: size atanan görevler görünmez. Yetki yöneticinize başvurun.'));
  view.append(tabBox, body);
  let active = 'mine';
  let state = 'open';
  let q = '';
  let selected = null;
  let reload = async () => {};

  function renderTabs(counts) {
    const items = VIEWS.filter(([id]) => id === 'mine' || readAll).map(([id, label]) => ({ id, label, count: id === 'channels' ? undefined : counts[id] }));
    clear(tabBox).append(tabs(items, active, (id) => {
      history.replaceState(null, '', id === 'mine' ? '#/gorevler' : `#/gorevler/${SLUG[id]}`);
      show(id);
    }, 'Görev görünümleri'));
  }

  async function listTab() {
    const search = h('input', { type: 'search', class: 'searchbar', id: 'taskSearch', placeholder: 'Numara, başlık veya CI', value: q, 'aria-label': 'Görev ara' });
    const stateSel = h('select', { id: 'taskState', 'aria-label': 'Durum' },
      [['open', 'Açık görevler'], ['closed', 'Kapananlar'], ['all', 'Hepsi']].map(([v, l]) => h('option', { value: v, selected: v === state }, l)));
    const list = h('div', { id: 'taskList' });
    clear(body).append(h('div', { class: 'toolbar' }, search, stateSel), list);
    async function load() {
      const res = first || await tasksList({ view: active, state, q });
      first = null;
      renderTabs(res.counts);
      clear(list).append(dataTable({
        label: 'Görevler', idOf: (t) => t.sys_id, selectedId: () => selected,
        empty: active === 'mine' ? 'Size atanmış açık görev yok.' : 'Bu görünümde görev yok.',
        columns: [
          { key: 'number', label: 'Görev', render: (t) => h('div', {}, h('strong', {}, t.title), h('div', { class: 'cell-sub' }, `${t.number} · ${t.kind_tr}`)) },
          { key: 'ci', label: 'CI', render: (t) => (t.ci ? t.ci.name : '—'), sortValue: (t) => t.ci?.name || '' },
          { key: 'assignee', label: 'Sorumlu', sortable: false, render: (t) => assigneeText(t.assignee) },
          { key: 'due_at', label: 'Son tarih', render: (t) => formatDateTime(t.due_at) },
          { key: 'channel', label: 'Kanal', render: (t) => h('div', {}, pill(t.delivery_tr, DELIVERY_TONE[t.delivery_status] || 'neutral'),
            t.external_number ? h('div', { class: 'cell-sub' }, t.external_number) : null) },
          { key: 'state', label: 'Durum', render: stateCell },
        ],
        rows: res.items, onOpen: (t, tr) => openTask(t.sys_id, tr),
      }));
    }
    reload = load;
    let timer = null;
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { q = search.value.trim(); load(); }, 250); });
    stateSel.addEventListener('change', () => { state = stateSel.value; load(); });
    if (first && active !== 'mine') first = null;
    await load();
  }

  async function openTask(id, opener) {
    selected = id;
    if (location.hash !== `#/gorevler/${id}`) history.replaceState(null, '', `#/gorevler/${id}`);
    let t;
    try { t = await taskDetail(id); } catch (err) { toast(err.message); return; }
    const refresh = async () => { await reload(); await openTask(id); };
    const footer = [];
    if (t.can_complete) footer.push(h('button', { class: 'btn', type: 'button', id: 'taskDecide', onclick: () => openCompleteDialog(t, refresh) }, 'Kararı ver'));
    if (t.can_cancel) footer.push(h('button', { class: 'btn secondary', type: 'button', id: 'taskCancel', onclick: () => openCancelDialog(t, refresh) }, 'İptal et'));
    openDetail({
      kicker: `Görev · ${t.number}`, title: t.title, opener,
      onClose: () => { selected = null; history.replaceState(null, '', active === 'mine' ? '#/gorevler' : `#/gorevler/${SLUG[active]}`); },
      body: [
        h('div', { class: 'pills' }, stateCell(t), pill(t.kind_tr, 'neutral')),
        t.external_closed && t.state === 'open' ? h('p', { class: 'notice warn' }, `ServiceNow'daki ${t.external_number} kaydı karar verilmeden kapatıldı. Kararı burada verin; görev Evren'de açık.`) : null,
        t.delivery_status === 'failed' ? h('p', { class: 'notice bad' }, `ServiceNow'a teslim edilemedi: ${t.delivery_note || 'bilinmeyen hata'}. Görev çalışanı yeniden deneyecek.`) : null,
        t.delivery_status === 'kept_in_evren' ? h('p', { class: 'notice' }, t.delivery_note) : null,
        t.description ? section('Ne yapılacak', h('p', { class: 'small' }, t.description)) : null,
        section('Görev', kv([
          ['CI', t.ci ? h('a', { href: `#/ci/${t.ci.sys_id}` }, t.ci.name) : '—'],
          ['Sorumlu', assigneeText(t.assignee)],
          ['Son tarih', formatDateTime(t.due_at)],
          ['Kanal', `${t.channel_tr} · ${t.delivery_tr}${t.external_number ? ` · ${t.external_number}` : ''}`],
          ['Açan', t.created_by], ['Açılış', formatDateTime(t.created_at)],
        ])),
        t.state !== 'open' ? section('Karar', kv([['Karar', t.outcome_tr], ['Açıklama', t.outcome_note || '—'], ['Veren', t.closed_by], ['Zaman', formatDateTime(t.closed_at)]])) : null,
        section('Geçmiş', h('ul', { class: 'mini', id: 'taskEvents' }, t.events.map((e) => h('li', {},
          h('strong', {}, EVENT_TR[e.action] || e.action), h('div', { class: 'cell-sub' }, `${formatDateTime(e.at)} · ${e.actor}`))))),
      ],
      footer,
    });
  }

  async function channelsTab() {
    const res = await taskChannels();
    const w = res.worker;
    const cards = h('div', { class: 'mini-grid', id: 'channelList' });
    for (const c of res.channels) {
      const s = c.settings || {};
      const actions = [];
      if (res.can_admin && !c.is_default && c.lifecycle === 'active') {
        actions.push(h('button', { class: 'btn small secondary', type: 'button', dataset: { channel: c.name }, onclick: async () => {
          try {
            const r = await taskChannelUpdate(c.name, { is_default: true, reason: 'Varsayılan görev kanalı değişikliği (yönetim ekranı)' });
            toast(r.message);
            channelsTab();
          } catch (err) { toast(err.message); }
        } }, 'Varsayılan yap'));
      }
      if (res.can_admin && c.kind === 'servicenow') actions.push(h('button', { class: 'btn small secondary', type: 'button', id: 'chEdit', onclick: () => openChannelDialog(c, channelsTab) }, 'Ayarları düzenle'));
      cards.append(h('section', { class: 'panel channel-card', dataset: { name: c.name } },
        h('h3', {}, c.label_tr, ' ', c.is_default ? pill('Varsayılan', 'good') : null),
        kv([['Açık görev', String(c.open)], ['Teslim edilemeyen', String(c.failed)],
          c.kind === 'servicenow' ? ['Hedef tablo', h('span', { class: 'mono' }, s.table)] : null,
          c.kind === 'servicenow' ? ['Kapanış durumları', `tamamlandı ${s.close_state}, iptal ${s.cancel_state}`] : null,
          c.kind === 'servicenow' ? ['Varsayılan grup', s.default_group ? h('span', { class: 'mono' }, s.default_group) : 'yok (karşılığı olmayan görev Evren\'de kalır)'] : null,
          c.updated_by ? ['Son değişiklik', `${c.updated_by} · ${formatDateTime(c.updated_at)}`] : null]),
        actions.length ? h('div', { class: 'task-actions' }, actions) : null));
    }
    const status = !w ? h('p', { class: 'notice warn' }, 'Görev çalışanı hiç çalışmadı: ServiceNow kanalındaki görevler teslim bekliyor.')
      : w.alive ? h('p', { class: 'notice success' }, `Görev çalışanı çalışıyor (kanal: ${w.channel}).`)
        : h('p', { class: 'notice warn' }, `Görev çalışanı ${formatDateTime(w.seen_at)} tarihinden beri görülmüyor.`);
    clear(body).append(status, h('p', { class: 'muted small' }, 'Görev Evren\'de tutulur; kanal teslim ve iş takibidir. ServiceNow kanalı ileride Evren kanalıyla değiştirilebilir: varsayılanı değiştirmek yeni görevleri etkiler, açık görevler kanallarında kalır.'), cards);
    renderTabs({});
    reload = channelsTab;
  }

  async function show(id) {
    active = id;
    if (id === 'channels') { await channelsTab(); return; }
    await listTab();
  }

  async function update(p) {
    if (p && SYSID.test(p)) {
      if (!document.getElementById('taskList') && !document.getElementById('channelList')) await show(active);
      await openTask(p);
      return;
    }
    closeDetail({ silent: true });
    if (p === 'yeni') {
      await show(readAll ? 'all' : 'mine');
      if (admin) openNewTaskDialog((id) => { show('all').then(() => openTask(id)); });
      return;
    }
    await show(PARAM[p] && (readAll || PARAM[p] === 'mine') ? PARAM[p] : 'mine');
  }
  await update(param);
  return { update };
}

