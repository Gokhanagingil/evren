// Eskime (U7.1, F1-06, ADR-0028): gözlemci kaynakların görmediği CI'lar için öneriler ve Veri sorumlusunun kararı.
// Öneriyi uzlaştırıcı açar; golden record kendiliğinden değişmez. Karar istek olarak kaydedilir, uzlaştırıcı yeniden
// denetleyip uygular (aday mı, kaynaklar sağlıklı mı).
import { h, append, clear, formatDateTime, metric, pill, section, tabs } from '../lib/dom.js';
import { closeOverlay, dialog, setHeader, toast } from '../lib/shell.js';
import { backend, retirementStatus, stalenessCases, stalenessDecision, stalenessSummary } from '../lib/data.js';

export const STAGE_TONE = { stale: 'warn', candidate: 'bad' };
const VIEWS = [
  ['candidate', 'Emekliye ayırma adayı', 'Bütün gözlemci kaynaklar uzun süredir görmüyor ve son çalışmaları sağlıklı. Emekliye ayırın veya koruyun.'],
  ['stale', 'Eskimiş', 'Gözlemci kaynaklar bir süredir görmüyor; süre dolunca aday olur. Bilerek kapalıysa koruyabilirsiniz.'],
  ['retired', 'Emekliye ayrılan', 'Kararla emekliye ayrılan CI\'lar. Yeniden görülen emekli CI işaretlenir.'],
  ['dismissed', 'Korunan', 'Korunan öneriler; CI yeniden görülüp kaybolmadıkça öneri açılmaz.'],
];
const wait = (ms) => new Promise((r) => { setTimeout(r, ms); });

/** Karar penceresi (sayfa ve CI paneli ortak). onSend(note) → Promise. */
export function openDecisionDialog({ name, action, onSend }) {
  const retire = action === 'retire';
  const note = h('input', { type: 'text', id: 'staleNote', maxlength: '2000', required: 'required', 'aria-describedby': 'staleHelp',
    placeholder: retire ? 'Ör. donanım iade edildi, CHG0012345' : 'Ör. felaket kurtarma yedeği; bilerek kapalı' });
  const send = h('button', { class: retire ? 'btn' : 'btn secondary', type: 'button', id: 'staleSend', onclick: async () => {
    if (!note.value.trim()) {
      note.setAttribute('aria-invalid', 'true');
      toast('Gerekçe zorunludur.');
      note.focus();
      return;
    }
    send.disabled = true;
    closeOverlay();
    await onSend(note.value.trim());
  } }, retire ? 'Emekliye ayır' : 'Koru');
  note.addEventListener('input', () => note.removeAttribute('aria-invalid'));
  note.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); send.click(); } });
  dialog({
    title: retire ? 'Emekliye ayır' : 'Öneriyi koru',
    body: [
      h('p', {}, retire
        ? `"${name}" emekliye ayrılır: yaşam döngüsü End of Life / Retired olur. CI silinmez, kimlik bağları kalır; gerekirse düzenle penceresinden geri alınır.`
        : `"${name}" için öneri kapanır. CI yeniden görülüp sonra yine kaybolmadıkça aynı öneri açılmaz.`),
      h('label', { class: 'field', for: 'staleNote' }, 'Gerekçe'), note,
      h('p', { class: 'muted small', id: 'staleHelp' }, 'Uzlaştırıcı kararı uygularken yeniden denetler: CI yeniden görüldüyse veya bir kaynağın son çalışması sağlıksızsa uygulanmaz.'),
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: note,
  });
}

/** Kararı gönderir, uzlaştırıcının sonucunu bekler (en çok ~20 sn) ve bildirir. */
export async function sendDecision(caseId, action, note) {
  const res = await stalenessDecision(caseId, action, note);
  toast(res.message);
  let st = { status: 'pending' };
  for (let i = 0; i < 28 && st.status === 'pending'; i += 1) {
    await wait(i ? 700 : 300);
    st = await retirementStatus(res.id);
  }
  if (st.status === 'rejected') toast(`Uygulanmadı: ${st.result_note}`);
  return st;
}

export function observationList(observations) {
  return h('ul', { class: 'mini' }, observations.map((o) => h('li', {},
    h('div', { class: 'pills' }, pill(o.label || o.data_source, 'neutral'), pill(o.status === 'missing' ? 'görmüyor' : 'görüyor', o.status === 'missing' ? 'warn' : 'good'),
      o.source_ok ? null : pill('son çalışma sağlıksız', 'bad')),
    h('div', { class: 'cell-sub' }, `Son görülme: ${formatDateTime(o.last_seen)}`))));
}

export async function renderStaleness(view) {
  const { mode } = await backend();
  setHeader({
    title: 'Eskime',
    sub: 'Keşif, sanallaştırma, ağ, izleme ve uç nokta kaynaklarının hiçbiri uzun süredir görmeyen CI\'lar. Öneri yalnızca kaynakların son çalışması sağlıklıysa ilerler; emekliye ayırma Veri sorumlusunun kararıdır.',
    crumbs: [['Eskime']],
  });
  if (mode !== 'api') {
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '),
      'Eskime önerileri canlı API ve uzlaştırıcıyla çalışır. Kurallar Kurallar sayfasında görünür.'));
    return {};
  }
  let summary = await stalenessSummary();
  let active = 'candidate';
  let q = '';
  const cards = h('div', { class: 'cards', id: 'staleCards' });
  const tabBox = h('div');
  const listBox = h('div', { id: 'staleList' });
  const search = h('input', { type: 'search', class: 'searchbar', id: 'staleSearch', placeholder: 'CI adı', 'aria-label': 'CI ara' });
  const status = h('p', { class: 'muted small', id: 'staleStatus', 'aria-live': 'polite' });

  function renderCards() {
    const c = summary.counts;
    const unhealthy = summary.sources.filter((s) => !s.ok);
    clear(cards).append(
      metric({ label: 'Emekliye ayırma adayı', value: c.candidate, tone: c.candidate ? 'bad' : 'good', foot: 'Karar bekliyor' }),
      metric({ label: 'Eskimiş', value: c.stale, tone: c.stale ? 'warn' : 'good', foot: 'Süre dolunca aday olur' }),
      metric({ label: 'Emekliye ayrılan', value: c.retired, foot: c.reappeared ? `${c.reappeared} tanesi yeniden görüldü` : 'Kararla; CI silinmez', tone: c.reappeared ? 'warn' : '' }),
      metric({ label: 'Kararı bastıran kaynak', value: unhealthy.length, tone: unhealthy.length ? 'warn' : 'good',
        foot: unhealthy.length ? unhealthy.map((s) => s.label).join(', ') : 'Bütün gözlemci kaynaklar sağlıklı' }));
  }

  function renderTabs() {
    const c = summary.counts;
    const counts = { candidate: c.candidate, stale: c.stale, retired: c.retired, dismissed: c.dismissed };
    clear(tabBox).append(tabs(VIEWS.map(([id, label]) => ({ id, label, count: counts[id] })), active, (id) => {
      active = id;
      renderTabs();
      load();
    }, 'Eskime görünümleri'));
  }

  async function decide(r, action) {
    openDecisionDialog({ name: r.name, action, onSend: async (note) => {
      try {
        status.textContent = 'Uzlaştırıcı uyguluyor…';
        const st = await sendDecision(r.id, action, note);
        status.textContent = st.status === 'applied' ? `${r.name}: ${st.result_note}` : st.status === 'rejected'
          ? `${r.name}: uygulanmadı — ${st.result_note}` : 'Karar arka planda uygulanıyor.';
        summary = await stalenessSummary();
        renderCards();
        renderTabs();
        await load();
      } catch (err) {
        toast(`Kaydedilemedi: ${err.message}`);
      }
    } });
  }

  function actions(r) {
    if (r.pending) return pill('Karar uygulanıyor', 'neutral');
    if (!r.can_decide) return null;
    return h('div', { class: 'link-actions' },
      r.stage === 'candidate' ? h('button', { class: 'btn secondary small', type: 'button', 'data-retire': r.id, onclick: () => decide(r, 'retire') }, 'Emekliye ayır…') : null,
      h('button', { class: 'btn ghost small', type: 'button', 'data-dismiss': r.id, onclick: () => decide(r, 'dismiss') }, 'Koru…'));
  }

  function table(rows) {
    const open = active === 'candidate' || active === 'stale';
    const head = ['CI', 'Sınıf', 'Aşama', 'Son görülme', 'Kaynaklar', open ? '' : 'Karar'];
    return h('div', { class: 'tablewrap' }, h('table', { 'aria-label': VIEWS.find((v) => v[0] === active)[1], id: 'staleTable' },
      h('thead', {}, h('tr', {}, head.map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, rows.map((r) => h('tr', {},
        h('td', {}, h('a', { href: `#/ci/${r.ci}`, class: 'cell-primary' }, r.name), r.reappeared_at ? h('div', {}, pill('Yeniden görüldü', 'warn')) : null),
        h('td', {}, r.class_label, h('div', { class: 'cell-sub' }, `kural: ${r.rule_label}`)),
        h('td', {}, pill(r.status === 'open' ? r.stage_label : r.status_label, r.status === 'open' ? STAGE_TONE[r.stage] : r.status === 'retired' ? 'neutral' : 'good')),
        h('td', {}, formatDateTime(r.last_seen), h('div', { class: 'cell-sub' }, `${r.days_since} gün önce`)),
        h('td', {}, r.observations.map((o) => o.label || o.data_source).join(', ')),
        h('td', {}, open ? actions(r) : h('span', { class: 'small' }, r.decided_by || '—',
          r.decision_note ? h('div', { class: 'cell-sub' }, r.decision_note) : null)))))));
  }

  async function load() {
    const seq = (load.seq = (load.seq || 0) + 1);
    clear(listBox).append(h('p', { class: 'muted' }, 'Yükleniyor…'));
    const res = await stalenessCases(active, q);
    if (seq !== load.seq) return;
    clear(listBox);
    append(listBox, [h('p', { class: 'muted small' }, VIEWS.find((v) => v[0] === active)[2]),
      res.rows.length ? table(res.rows) : h('div', { class: 'empty' }, h('strong', {}, 'Kayıt yok'), 'Bu görünümde öneri yok.')]);
  }

  let timer = null;
  search.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { q = search.value.trim(); load(); }, 250);
  });

  renderCards();
  renderTabs();
  append(view, [cards,
    summary.can_decide ? null : h('p', { class: 'muted small' }, 'Emekliye ayırma ve koruma kararını Veri sorumlusu verir; önerileri görebilirsiniz.'),
    tabBox, h('div', { class: 'toolbar' }, search), status, listBox,
    section('Kurallar ve gözlemci kaynaklar',
      h('ul', { class: 'mini', id: 'staleRules' }, summary.rules.map((r) => h('li', {},
        h('div', { class: 'pills' }, pill(`${r.stale_after_days} gün → eskimiş`, 'warn'), pill(`${r.candidate_after_days} gün → aday`, 'bad')),
        h('div', {}, h('strong', {}, r.label), ' ve alt sınıfları')))),
      h('div', { class: 'pills', id: 'staleSources', 'aria-label': 'Gözlemci kaynaklar' }, summary.sources.map((s) => pill(`${s.label}: ${s.ok ? 'sağlıklı' : s.state === 'none' ? 'çalışmadı' : 'sağlıksız'}`, s.ok ? 'good' : 'warn'))),
      h('p', { class: 'muted small' }, 'ITSM (mevcut CMDB), elle giriş ve platform kaynakları CI\'ı "canlı" tutmaz. Gözlemci kaydı olmayan CI (ör. yalnızca ServiceNow\'daki iş uygulaması) eskimeye girmez; doğruluğu sahiplik onayıyla izlenir. Kaynak: metadata/reconciliation_rules.yaml → staleness.'))]);
  await load();
  return {};
}
