// ServiceNow Replikası (U9.1, ADR-0042): Evren → ServiceNow CMDB. İlk bakışta mod, sayılar ve karar bekleyenler;
// kapsam ve çalışma geçmişi katlanır alanda (aşamalı açıklama, ADR-0041).
import { h, append, clear, dataTable, formatDateTime, pill } from '../lib/dom.js';
import { closeOverlay, dialog, setHeader, toast } from '../lib/shell.js';
import { backend, replicaApprove, replicaItems, replicaResolve, replicaScope, replicaSetting, replicaView } from '../lib/data.js';
import { radioGroup, wfield } from '../lib/wizard.js';

const MIN_NOTE = 10;
const MODE_TONE = { off: 'neutral', shadow: 'warn', active: 'good' };
const MODE_SUB = {
  off: 'ServiceNow\'a hiçbir şey gönderilmez.',
  shadow: 'ServiceNow\'a yazılmaz; ServiceNow\'un kayıtları nasıl tanıyacağı denenir.',
  active: 'Kapsamdaki CI\'lar ServiceNow\'a yazılır. Önce denenir, beklenen kayıtla eşleşmeyen yazılmaz.',
};
const RUN_TONE = { done: 'good', failed: 'bad', held: 'warn', approved: 'warn', running: 'neutral' };

function stat(label, value, tone = '') {
  return h('div', { class: `stat ${tone}`.trim() }, h('span', { class: 'stat-value' }, String(value)), h('span', { class: 'stat-label' }, label));
}

function problem(box, message) {
  clear(box).append(h('div', { class: 'notice bad', role: 'alert' }, message));
}

/** Gerekçe isteyen ortak pencere: ``run(note)`` başarılıysa kapanır, ileti gösterilir, sayfa yenilenir. */
function noteDialog({ title, intro, placeholder, action, run, reload, extra = [], focus = null }) {
  const note = h('textarea', { id: 'replicaNote', rows: '3', maxlength: '1000', placeholder });
  const error = h('div', { id: 'replicaError' });
  const save = h('button', { class: 'btn', type: 'button', id: 'replicaSave' }, action);
  note.addEventListener('input', () => { note.removeAttribute('aria-invalid'); clear(error); });
  save.addEventListener('click', async () => {
    if (note.value.trim().length < MIN_NOTE) {
      note.setAttribute('aria-invalid', 'true');
      problem(error, `Gerekçe en az ${MIN_NOTE} karakter olmalı.`);
      note.focus();
      return;
    }
    save.disabled = true;
    try {
      const r = await run(note.value.trim());
      closeOverlay();
      toast(r.message);
      await reload();
    } catch (err) {
      save.disabled = false;
      problem(error, err.message);
    }
  });
  dialog({ title, body: [h('p', {}, intro), ...extra, h('label', { class: 'field', for: 'replicaNote' }, 'Gerekçe'), note, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save], focus: focus || note });
}

function modeDialog(d, reload) {
  let mode = d.setting.mode;
  const limit = h('input', { type: 'number', id: 'replicaLimit', min: '1', max: '100000', value: String(d.setting.max_changes), class: 'compact' });
  const options = Object.entries(d.labels.modes).map(([value, label]) => ({ value, label, sub: MODE_SUB[value],
    disabled: value === 'active' && !d.shadow_done && d.setting.mode !== 'active' }));
  const radios = radioGroup('replicaMode', 'Mod', options, mode, (v) => { mode = v; });
  const hint = d.shadow_done ? null
    : h('p', { class: 'muted small', id: 'replicaShadowHint' }, 'Etkin mod, gölge modda en az bir tur tamamlanınca açılır.');
  noteDialog({
    title: 'Replika modunu değiştir', action: 'Kaydet', reload,
    intro: 'Yeni ürün gibi replika da önce gölgede çalışır: ServiceNow\'un kayıtları nasıl tanıyacağını görün, çelişkileri çözün, sonra etkin moda geçin.',
    placeholder: 'Ör. Gölge turunda çelişki kalmadı; değişiklik kurulu onayı DK-114',
    extra: [radios, hint, wfield('Tur başına en çok değişiklik', limit, 'Bu sayıyı aşan toplu değişiklik gönderilmez, onayınızı bekler.')],
    run: (note) => replicaSetting({ mode, max_changes: Number(limit.value), note }),
    focus: radios.querySelector('input:checked'),
  });
}

function resolveDialog(item, action, reload) {
  const titles = { accept: 'ServiceNow kaydını kabul et', exclude: 'Replikanın dışında tut', retry: 'Yeniden dene' };
  const intros = {
    accept: `ServiceNow bu CI'ı başka bir kayıtla eşleştirdi (${item.identified_sn_sys_id}). Kabul ederseniz Evren CI'ı o kayda bağlanır ve sonraki turda değerleri ServiceNow'a yazılır. Kayıtların aynı CI olduğundan emin olun.`,
    exclude: 'Bu CI ServiceNow\'a gönderilmez. Kararı istediğiniz zaman "Yeniden dene" ile geri alabilirsiniz.',
    retry: 'Kayıt sonraki turda yeniden denenir. Önce denenir; beklenen kayıtla eşleşmezse yine yazılmaz.',
  };
  noteDialog({ title: `${titles[action]}: ${item.name}`, intro: intros[action], action: titles[action], reload,
    placeholder: 'Ör. Seri numarası ve konum aynı; ServiceNow kaydı doğru', run: (note) => replicaResolve(item.sys_id, action, note) });
}

function attention(d, items, reload) {
  const rows = items.map((i) => ({ ...i, id: i.sys_id }));
  const actions = (i) => {
    if (!d.can_admin) return null;
    const btn = (label, action) => h('button', { class: 'btn secondary small', type: 'button', dataset: { action },
      onclick: (ev) => { ev.stopPropagation(); resolveDialog(i, action, reload); } }, label);
    return h('div', { class: 'row-actions' },
      i.state === 'conflict' && i.identified_sn_sys_id ? btn('Kabul et…', 'accept') : null,
      i.state !== 'excluded' ? btn('Dışarıda tut…', 'exclude') : null,
      ['error', 'conflict', 'excluded'].includes(i.state) ? btn('Yeniden dene…', 'retry') : null);
  };
  const statePill = (i) => (i.state === 'pending_push' && i.shadow_verdict
    ? pill(`Gölgede ${i.shadow_label.toLocaleLowerCase('tr')}`, i.shadow_verdict === 'error' ? 'bad' : 'warn')
    : pill(i.state_label, i.state === 'error' ? 'bad' : 'warn'));
  return dataTable({
    label: 'Karar bekleyen replika kayıtları', rows, empty: 'Karar bekleyen kayıt yok.',
    columns: [
      { key: 'name', label: 'CI', render: (i) => h('div', {}, h('a', { href: `#/ci/${i.sys_id}` }, i.name), h('div', { class: 'cell-sub' }, i.class_label)) },
      { key: 'state', label: 'Durum', render: statePill },
      { key: 'message', label: 'Neden', sortable: false, render: (i) => h('span', { class: 'small' }, i.message || '—') },
      { key: 'updated_at', label: 'Zaman', render: (i) => formatDateTime(i.updated_at) },
      { key: 'actions', label: '', sortable: false, render: actions },
    ],
  });
}

function scopeList(d, reload) {
  return h('ul', { class: 'mini', id: 'replicaScope' }, d.scope.map((s) => h('li', {},
    h('div', { class: 'pills' }, pill(s.enabled ? 'Replikada' : 'Gönderilmiyor', s.enabled ? 'good' : 'neutral'),
      h('span', { class: 'muted small' }, `${s.ci_count} CI${s.include_descendants ? ' · alt sınıflarıyla' : ''}`)),
    h('div', {}, h('strong', {}, s.label), s.notes ? h('span', { class: 'muted small' }, ` — ${s.notes}`) : null),
    d.can_admin ? h('button', { class: 'btn ghost small', type: 'button', dataset: { scope: s.class },
      onclick: () => noteDialog({ title: `${s.label}: ${s.enabled ? 'replikadan çıkar' : 'replikaya al'}`, reload,
        action: s.enabled ? 'Çıkar' : 'Al', placeholder: 'Ör. Olay süreci bu sınıfı kullanmıyor',
        intro: s.enabled ? 'Bu sınıfın CI\'ları ServiceNow\'a gönderilmez; ServiceNow\'daki mevcut kayıtlar silinmez.'
          : 'Bu sınıfın CI\'ları sonraki turdan itibaren ServiceNow\'a gönderilir (toplu değişiklik sınırı geçerlidir).',
        run: (note) => replicaScope(s.class, { enabled: !s.enabled, note }) }) }, s.enabled ? 'Çıkar…' : 'Al…') : null)));
}

function runsTable(d) {
  return dataTable({
    label: 'Son replika çalışmaları', rows: d.runs, empty: 'Henüz çalışma yok.',
    columns: [
      { key: 'started_at', label: 'Başlangıç', render: (r) => formatDateTime(r.started_at) },
      { key: 'mode', label: 'Mod', render: (r) => r.mode_label },
      { key: 'status', label: 'Durum', render: (r) => pill(r.status_label, RUN_TONE[r.status]) },
      { key: 'sent', label: 'Gönderilen', num: true },
      { key: 'inserted', label: 'Yeni', num: true },
      { key: 'updated', label: 'Güncelleme', num: true },
      { key: 'conflicts', label: 'Çelişki', num: true },
      { key: 'errors', label: 'Hata', num: true },
      { key: 'relations', label: 'İlişki', num: true },
    ],
  });
}

export async function renderReplica(view) {
  const { mode } = await backend();
  setHeader({ title: 'ServiceNow Replikası', sub: 'Evren\'den ServiceNow CMDB\'ye giden kayıtlar.', crumbs: [['ServiceNow Replikası']] });
  if (mode !== 'api') {
    view.append(h('p', { class: 'notice' }, 'ServiceNow replikası canlı API ile çalışır: Evren ana kaynaktır, ServiceNow CMDB replikadır (ADR-0042).'));
    return {};
  }
  const body = h('div', { id: 'replicaBody' });
  view.append(body);

  async function load() {
    let d;
    let items;
    try {
      [d, items] = await Promise.all([replicaView(), replicaItems('attention')]);
    } catch (err) {
      clear(body).append(err.status === 403
        ? h('p', { class: 'notice' }, 'Replikayı Platform operatörü, Veri sorumlusu, Konfigürasyon yöneticisi ve Platform yöneticisi görür.')
        : h('div', { class: 'notice bad' }, `Replika durumu yüklenemedi: ${err.message}`));
      return;
    }
    const s = d.setting;
    const pending = d.counts.conflict + d.counts.error + d.shadow.conflict + d.shadow.error;
    setHeader({ title: 'ServiceNow Replikası', sub: 'Evren\'den ServiceNow CMDB\'ye giden kayıtlar.', crumbs: [['ServiceNow Replikası']],
      actions: d.can_admin ? [h('button', { class: 'btn secondary', type: 'button', id: 'replicaModeBtn', onclick: () => modeDialog(d, load) }, 'Modu değiştir…')] : [] });
    const worker = !d.worker.live
      ? h('p', { class: 'notice warn', id: 'replicaWorker' }, 'sn-sync çalışmıyor: ayar ve kararlar kaydedilir, gönderim çalışan başlayınca yapılır.')
      : d.worker.connection === 'yok' && s.mode !== 'off'
        ? h('p', { class: 'notice warn', id: 'replicaWorker' }, 'ServiceNow bağlantısı ayarlanmamış: hiçbir şey gönderilmiyor.')
        : null;
    const run = d.open_run;
    const held = run && run.status === 'held'
      ? h('div', { class: 'notice warn', id: 'replicaHeld', role: 'status' },
        h('strong', {}, 'Toplu itme onay bekliyor. '), run.held_reason || '',
        d.can_admin ? h('div', { class: 'notice-actions' }, h('button', { class: 'btn small', type: 'button', id: 'replicaApprove',
          onclick: () => noteDialog({ title: `${run.candidates} CI değişikliğini onayla`, action: 'Onayla', reload: load,
            intro: 'Onaylarsanız bekleyen değişiklikler sırayla ServiceNow\'a gönderilir. Eşlemesi doğrulanmamış CI\'lar yine önce denenir.',
            placeholder: 'Ör. İlk yükleme; gölge turunda çelişki yok', run: (note) => replicaApprove(run.id, note) }) }, 'Onayla…')) : null)
      : null;
    append(clear(body), [
      h('div', { class: 'stat-strip', id: 'replicaStats' },
        h('div', { class: 'stat' }, h('span', { class: 'stat-value' }, pill(s.mode_label, MODE_TONE[s.mode])), h('span', { class: 'stat-label' }, 'Mod')),
        stat('Eşit', d.counts.in_sync, d.counts.in_sync ? 'good' : ''),
        stat('Bekleyen', d.counts.pending_push),
        stat('Karar bekleyen', pending, pending ? 'warn' : ''),
        stat('İlişki', d.relations.in_sync)),
      h('p', { class: 'muted small', id: 'replicaModeText' }, MODE_SUB[s.mode],
        s.updated_at ? ` Son değişiklik ${formatDateTime(s.updated_at)} · ${s.updated_by}.` : ''),
      worker, held,
      h('section', { class: 'section', 'aria-labelledby': 'replicaAttH' }, h('h2', { id: 'replicaAttH', class: 'sys-group-title' }, 'Karar bekleyenler'),
        attention(d, items.items, load)),
      h('details', { class: 'inherited', id: 'replicaScopeBox' }, h('summary', {}, `Kapsam (${d.scope.filter((x) => x.enabled).length} sınıf)`), scopeList(d, load)),
      h('details', { class: 'inherited', id: 'replicaRuns' }, h('summary', {}, 'Son çalışmalar'), runsTable(d)),
    ]);
  }
  await load();
  return {};
}
