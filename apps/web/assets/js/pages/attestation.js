// Sahiplik Onayı (U6.4, F1-17, ADR-0027): CI sorumlusunun dönemsel beyanı; sahipsiz, reddedilen ve onay bekleyen CI'lar.
// Onay ve ret istek olarak kaydedilir; uzlaştırıcı kişiyi ve kapsamı yeniden denetleyip uygular (kural 1).
import { h, append, clear, formatDate, formatDateTime, metric, percent, pill, section, tabs } from '../lib/dom.js';
import { closeOverlay, dialog, setHeader, toast } from '../lib/shell.js';
import { attest, attestationItems, attestationRequests, attestationSummary, backend } from '../lib/data.js';

export const STATE_TONE = { valid: 'good', due_soon: 'warn', expired: 'bad', not_reviewed: 'warn', rejected: 'bad', out_of_scope: 'neutral' };
const VIEWS = [
  ['mine', 'Bende', 'Sorumlusu (sahibi veya yöneteni) olduğunuz CI\'lar; süresi dolan ve hiç onaylanmamış olanlar önce.'],
  ['invalid', 'Onay bekleyen', 'Hiç onaylanmamış veya onayının süresi dolmuş CI\'lar (tüm sorumlular).'],
  ['rejected', 'Reddedilen', 'Sorumlusunun reddettiği CI\'lar; Veri sorumlusu ele alır.'],
  ['orphan', 'Sahipsiz', 'Kapsamda ama sahibi ve yöneteni boş: onay verecek kimse yok, sahip ataması gerekli.'],
];
const wait = (ms) => new Promise((r) => { setTimeout(r, ms); });

/** Ret penceresi (CI ayrıntısı ve liste ortak kullanır). onSend(reason, note) → Promise. */
export function openRejectDialog({ name, reasons, onSend }) {
  const sel = h('select', { id: 'attReason', 'aria-describedby': 'attRejectHelp' },
    reasons.map((r) => h('option', { value: r.value }, r.label)));
  const note = h('input', { type: 'text', id: 'attNote', maxlength: '2000', required: 'required',
    placeholder: 'Ör. sunucu Ekim\'de kapatıldı; sahibi Ağ ekibi…' });
  const send = h('button', { class: 'btn', type: 'button', id: 'attRejectSend', onclick: async () => {
    if (!note.value.trim()) {
      note.setAttribute('aria-invalid', 'true');
      toast('Gerekçe zorunludur.');
      note.focus();
      return;
    }
    send.disabled = true;
    closeOverlay();
    await onSend(sel.value, note.value.trim());
  } }, 'Reddet');
  note.addEventListener('input', () => note.removeAttribute('aria-invalid'));
  note.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); send.click(); } });
  dialog({
    title: 'Sahiplik onayını reddet',
    body: [
      h('p', {}, `"${name}" kaydını doğrulamıyorsunuz. Ret Veri sorumlusuna iletilir; CI silinmez veya emekliye ayrılmaz.`),
      h('label', { class: 'field', for: 'attReason' }, 'Neden'), sel,
      h('label', { class: 'field', for: 'attNote' }, 'Gerekçe'), note,
      h('p', { class: 'muted small', id: 'attRejectHelp' }, 'Karar adınızla denetim izine yazılır. Kayıt düzeltilince yeniden onaylayabilirsiniz.'),
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: sel,
  });
}

/** İstekler uygulanana kadar bekler (en çok ~20 sn); uygulanmayanları döner. */
export async function waitForRequests(ids) {
  let st = { requests: [], done: false };
  for (let i = 0; i < 28 && !st.done; i += 1) {
    await wait(i ? 700 : 300);
    st = await attestationRequests(ids);
  }
  return st;
}

export async function renderAttestation(view) {
  const { mode } = await backend();
  setHeader({
    title: 'Sahiplik Onayı',
    sub: 'Kapsamdaki CI\'ın sorumlusu kaydı dönemsel olarak onaylar: "Bu kayıt doğru, sorumlusu benim." Süre dolunca veya sorumlu değişince onay geçersiz olur.',
    crumbs: [['Sahiplik Onayı']],
  });
  if (mode !== 'api') {
    view.append(h('div', { class: 'notice info', role: 'note' }, h('strong', {}, 'Demo modu. '),
      'Sahiplik onayı canlı API ile çalışır; demo verisinde hiçbir CI onaylanmamıştır. Kapsamdaki CI\'lar GRC kontrolü CMDB-06\'da görünür.'),
    h('p', {}, h('a', { href: '#/kontroller/CMDB-06' }, 'CMDB-06 kontrolünü aç')));
    return {};
  }
  let summary = await attestationSummary();
  let active = summary.user.mapped ? 'mine' : 'invalid';
  let rows = [];
  let q = '';
  const selected = new Set();
  const cards = h('div', { class: 'cards five', id: 'attCards' });
  const tabBox = h('div');
  const listBox = h('div', { id: 'attList' });
  const search = h('input', { type: 'search', class: 'searchbar', id: 'attSearch', placeholder: 'CI adı', 'aria-label': 'CI ara' });
  const bulk = h('button', { class: 'btn', type: 'button', id: 'attBulk', disabled: 'disabled' }, 'Seçilenleri onayla');
  const status = h('p', { class: 'muted small', id: 'attStatus', 'aria-live': 'polite' });

  function renderCards() {
    const c = summary.counts;
    clear(cards).append(
      metric({ label: 'Kapsamda', value: summary.in_scope, foot: `${summary.policies.length} politika` }),
      metric({ label: 'Onaylı', value: summary.valid_ratio === null ? '—' : percent(summary.valid_ratio * 100),
        tone: summary.valid_ratio >= 0.9 ? 'good' : 'warn', foot: `${c.valid + c.due_soon} CI · ${c.due_soon} süresi yaklaşıyor` }),
      metric({ label: 'Onay bekleyen', value: c.not_reviewed + c.expired, tone: c.not_reviewed + c.expired ? 'warn' : 'good',
        foot: `${c.expired} süresi doldu · ${c.not_reviewed} hiç onaylanmadı` }),
      metric({ label: 'Reddedilen', value: c.rejected, tone: c.rejected ? 'bad' : 'good', foot: 'Veri sorumlusu ele alır' }),
      metric({ label: 'Sahipsiz', value: summary.orphan, tone: summary.orphan ? 'bad' : 'good', foot: 'Sahip ataması gerekli' }));
  }

  function renderTabs() {
    const mine = summary.mine ? Object.values(summary.mine).reduce((a, b) => a + b, 0) : 0;
    const counts = { mine, invalid: summary.counts.not_reviewed + summary.counts.expired, rejected: summary.counts.rejected, orphan: summary.orphan };
    clear(tabBox).append(tabs(VIEWS.map(([id, label]) => ({ id, label, count: counts[id] })), active, (id) => {
      active = id;
      selected.clear();
      renderTabs();
      load();
    }, 'Sahiplik onayı görünümleri'));
  }

  async function send(items, decision, reason = null, note = null) {
    bulk.disabled = true;
    try {
      const res = await attest({ items, decision, reason, note });
      toast(res.message);
      status.textContent = 'Uzlaştırıcı uyguluyor…';
      const st = await waitForRequests(res.accepted.map((a) => a.request));
      const failed = st.requests.filter((r) => r.status === 'rejected');
      status.textContent = st.done
        ? `${st.requests.length - failed.length} istek uygulandı${failed.length ? `, ${failed.length} uygulanmadı: ${failed[0].result_note}` : '.'}`
        : 'İstekler arka planda uygulanıyor; liste birazdan güncellenir.';
      selected.clear();
      summary = await attestationSummary();
      renderCards();
      renderTabs();
      await load();
    } catch (err) {
      const why = err.details?.[0]?.reason;
      toast(`Kaydedilemedi: ${err.message}${why ? ` (${why})` : ''}`);
      updateBulk();
    }
  }

  function updateBulk() {
    bulk.disabled = !selected.size;
    bulk.textContent = selected.size ? `Seçilenleri onayla (${selected.size})` : 'Seçilenleri onayla';
  }
  bulk.addEventListener('click', () => send([...selected], 'attested'));

  function rowActions(r) {
    if (!r.mine || !summary.can_attest) return null;
    if (r.pending) return pill('Uygulanıyor', 'neutral');
    return h('div', { class: 'link-actions' },
      h('button', { class: 'btn secondary small', type: 'button', 'data-attest': r.sys_id, onclick: () => send([r.sys_id], 'attested') }, 'Onayla'),
      h('button', { class: 'btn ghost small', type: 'button', 'data-reject': r.sys_id,
        onclick: () => openRejectDialog({ name: r.name, reasons: REASONS, onSend: (reason, note) => send([r.sys_id], 'rejected', reason, note) }) }, 'Reddet…'));
  }

  function table() {
    const selectable = active === 'mine' && summary.can_attest;
    const head = ['CI', 'Sınıf', 'Durum', 'Geçerlilik sonu', 'Son onay', 'Sorumlu'];
    if (selectable) head.unshift('');
    head.push('');
    return h('div', { class: 'tablewrap' }, h('table', { 'aria-label': VIEWS.find((v) => v[0] === active)[1], id: 'attTable' },
      h('thead', {}, h('tr', {}, head.map((t, i) => h('th', { scope: 'col' }, i === 0 && selectable
        ? h('input', { type: 'checkbox', id: 'attAll', 'aria-label': 'Onaylanabilecek tümünü seç', onchange: (ev) => {
          rows.filter((r) => r.mine && !r.pending).forEach((r) => (ev.target.checked ? selected.add(r.sys_id) : selected.delete(r.sys_id)));
          listBox.querySelectorAll('input[data-pick]').forEach((b) => { b.checked = selected.has(b.dataset.pick); });
          updateBulk();
        } }) : t)))),
      h('tbody', {}, rows.map((r) => h('tr', {},
        selectable ? h('td', {}, r.mine && !r.pending ? h('input', { type: 'checkbox', dataset: { pick: r.sys_id },
          'aria-label': `${r.name} seç`, checked: selected.has(r.sys_id) ? 'checked' : null, onchange: (ev) => {
            if (ev.target.checked) selected.add(r.sys_id); else selected.delete(r.sys_id);
            updateBulk();
          } }) : null) : null,
        h('td', {}, h('a', { href: `#/ci/${r.sys_id}`, class: 'cell-primary' }, r.name)),
        h('td', {}, r.class_label, r.used_for_label ? h('div', { class: 'cell-sub' }, r.used_for_label) : null),
        h('td', {}, pill(r.state_label, STATE_TONE[r.state] || 'neutral')),
        h('td', {}, r.valid_until ? formatDate(r.valid_until.slice(0, 10)) : '—'),
        h('td', {}, r.attested_date ? h('span', {}, formatDateTime(r.attested_date), h('div', { class: 'cell-sub' }, r.attested_by || '')) : '—'),
        h('td', {}, r.owned_by || r.managed_by || h('span', { class: 'muted' }, 'yok')),
        h('td', {}, rowActions(r)))))));
  }

  async function load() {
    const seq = (load.seq = (load.seq || 0) + 1);
    clear(listBox).append(h('p', { class: 'muted' }, 'Yükleniyor…'));
    const res = await attestationItems(active, q);
    if (seq !== load.seq) return;
    rows = res.rows;
    clear(listBox);
    const hint = VIEWS.find((v) => v[0] === active)[2];
    listBox.append(h('p', { class: 'muted small' }, hint));
    if (active === 'mine' && !res.user_mapped) {
      listBox.append(h('div', { class: 'notice warn', role: 'note' }, h('strong', {}, 'Kullanıcı kaydınız eşlenmedi. '),
        'Oturumunuz bir sys_user kaydına bağlanamadı; sorumlusu olduğunuz CI\'lar gösterilemiyor ve onay veremezsiniz (Entra ile kullanıcı eşitlemesi U8).'));
      return;
    }
    if (!rows.length) {
      listBox.append(h('div', { class: 'empty' }, h('strong', {}, 'Kayıt yok'), active === 'mine' ? 'Sorumlusu olduğunuz kapsamdaki CI yok.' : 'Bu görünümde CI yok.'));
      return;
    }
    append(listBox, [table(), res.total > rows.length ? h('p', { class: 'muted small' }, `İlk ${rows.length} / ${res.total} CI gösteriliyor; aramayı daraltın.`) : null]);
    updateBulk();
  }

  let timer = null;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { q = search.value.trim(); load(); }, 250);
  });

  renderCards();
  renderTabs();
  const REASONS = [{ value: 'not_owner', label: 'Bu CI\'ın sorumlusu ben değilim' }, { value: 'not_exists', label: 'Bu CI artık yok' },
    { value: 'data_wrong', label: 'Kayıttaki bilgiler yanlış' }];
  append(view, [cards,
    !summary.user.mapped ? h('div', { class: 'notice warn', role: 'note' }, 'Oturumunuz bir kullanıcı kaydına (sys_user) eşlenemedi; raporları görebilirsiniz ama onay veremezsiniz.')
      : !summary.can_attest ? h('p', { class: 'muted small' }, 'Onay vermek için CI sahibi rolü gerekir; raporları görebilirsiniz.') : null,
    tabBox,
    h('div', { class: 'toolbar' }, search, summary.can_attest ? bulk : null),
    status, listBox,
    section('Politikalar', h('ul', { class: 'mini', id: 'attPolicies' }, summary.policies.map((p) => h('li', {},
      h('div', { class: 'pills' }, pill(`${p.period_days} gün`, 'info'), p.where_label ? pill(p.where_label, 'neutral') : null),
      h('div', {}, h('strong', {}, p.label)),
      h('div', { class: 'cell-sub' }, `${p.applies_to.length} sınıf ve alt sınıfları`)))),
    h('p', { class: 'muted small' }, `Onay verebilenler: ${summary.owner_fields.join(', ')} alanındaki kişi (CI sahibi rolüyle). Süre bitmeden ${summary.reminder_days} gün önce "süresi yaklaşıyor" görünür. Kaynak: metadata/attestation.yaml.`))]);
  await load();
  return {};
}
