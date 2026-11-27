// Zamanlanmış yönetişim kampanyaları (U8.5, ADR-0036, F1-25). Konfigürasyon yöneticisi tanımlar: ne istenecek
// (veri güncelliği teyidi, erişim gözden geçirme, kontrol listesi), kimden (kapsam: sınıf + koşul; sorumlu CI'dan
// bulunur), ne sıklıkla ve kaç günde. Görev çalışanı dönem gelince her CI için sorumlusuna görev açar; aynı CI için
// açık görev varsa yenisi açılmaz. Denetçi salt okur.
import { h, clear, dataTable, formatDateTime, formatNumber, kv, pill, section } from '../lib/dom.js';
import { closeDetail, closeOverlay, dialog, openDetail, toast } from '../lib/shell.js';
import {
  campaignCreate, campaignDetail, campaignPreview, campaignRun, campaignUpdate, campaignsList, taskChannels,
} from '../lib/data.js';

const MIN_TEXT = 10;
const STATE_TONE = { draft: 'neutral', active: 'good', paused: 'warn', retired: 'neutral' };
const RUN_TR = { requested: 'İstendi', running: 'Çalışıyor', done: 'Tamamlandı', failed: 'Başarısız' };
const RUN_TONE = { requested: 'warn', running: 'warn', done: 'good', failed: 'bad' };
const TRIGGER_TR = { schedule: 'Zamanlama', manual: 'Elle' };
const BASIS_TR = { owned_by: 'Sahibi', managed_by: 'Yöneteni', support_group: 'Destek grubu', unassigned: 'Atanmamış' };
const KIND_HELP = {
  attestation: 'Sorumlu, CI\'ın sahip, destek grubu, ortam gibi bilgilerinin doğru olduğunu teyit eder. CI\'ın sahiplik onayı verilince veya reddedilince görev kendiliğinden kapanır.',
  access_review: 'Uygulama veya sistem sahibi kullanıcı erişimlerini gözden geçirir; sonucu (doğrulandı, düzeltildi, sorun var) görevde kaydeder.',
  checklist: 'Sorumlu yönergedeki kontrolleri yapar ve sonucu görevde kaydeder.',
};
const TRANSITION = {
  active: { label: 'Etkinleştir', verb: 'etkinleştir', help: 'Kampanya zamanlamasına göre görev açmaya başlar.' },
  paused: { label: 'Duraklat', verb: 'duraklat', help: 'Yeni görev açılmaz; açık görevler sürer. Duraklatılmış kampanya düzenlenebilir.' },
  retired: { label: 'Emekliye ayır', verb: 'emekliye ayır', help: 'Kampanya bir daha çalışmaz; açık görevler sürer. Geri alınamaz.' },
};

function stateCell(c) {
  return pill(c.state_tr, STATE_TONE[c.state] || 'neutral');
}

function problem(box, message) {
  clear(box).append(h('div', { class: 'notice bad', role: 'alert' }, message));
}

function scopeText(c) {
  return h('div', {}, c.scope_class_tr, h('div', { class: 'cell-sub mono' }, c.scope_query || 'koşul yok: sınıftaki bütün CI\'lar'));
}

function reasonDialog({ title, intro, label, button, placeholder, run }) {
  const why = h('input', { type: 'text', id: 'campWhy', maxlength: '500', placeholder });
  const error = h('div', { id: 'campWhyError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'campWhySend' }, button);
  send.addEventListener('click', async () => {
    if (why.value.trim().length < MIN_TEXT) {
      why.setAttribute('aria-invalid', 'true');
      problem(error, `Gerekçe en az ${MIN_TEXT} karakter olmalı.`);
      why.focus();
      return;
    }
    send.disabled = true;
    try {
      await run(why.value.trim());
      closeOverlay();
    } catch (err) {
      send.disabled = false;
      problem(error, err.message);
    }
  });
  why.addEventListener('input', () => { why.removeAttribute('aria-invalid'); clear(error); });
  why.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); send.click(); } });
  dialog({ title, body: [h('p', {}, intro), h('label', { class: 'field', for: 'campWhy' }, label), why, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send], focus: why });
}

function previewBox(p) {
  const b = p.by_basis;
  const parts = [
    h('p', { class: 'notice', role: 'status' }, h('strong', {}, `${formatNumber(p.in_scope)} CI kapsamda. `),
      p.in_scope ? `Görevler ${formatNumber(p.people)} kişiye ve ${formatNumber(p.groups)} gruba gider.` : 'Koşulu genişletin veya başka bir sınıf seçin.'),
    p.in_scope ? kv([['Sahibine', formatNumber(b.owned_by)], ['Yönetenine', formatNumber(b.managed_by)],
      ['Destek grubuna', formatNumber(b.support_group)], ['Atanmamış (sorumlusu yok)', formatNumber(b.unassigned)],
      p.already_open ? ['Açık görevi olan (yenisi açılmaz)', formatNumber(p.already_open)] : null]) : null,
    b.unassigned ? h('p', { class: 'muted small' }, 'Sorumlusu olmayan CI\'ların görevi "Atanmamış" görünümüne düşer; önce sahiplik alanlarını tamamlamak iyi olur.') : null,
  ];
  if (p.sample.length) {
    parts.push(h('ul', { class: 'mini', id: 'campSample', 'aria-label': 'Örnek CI\'lar' }, p.sample.map((s) => h('li', {},
      h('strong', {}, s.name), h('div', { class: 'cell-sub' }, `${BASIS_TR[s.basis]}: ${s.user || s.group || '—'}`)))));
  }
  return parts;
}

/** Yeni kampanya veya taslak/duraklatılmış kampanyayı düzenleme. Kaydetmeden önce kapsam önizlenir. */
export async function openCampaignDialog(meta, onDone, existing = null) {
  const edit = Boolean(existing);
  const c = existing || {};
  const { channels } = edit ? { channels: [] } : await taskChannels();
  const kind = h('select', { id: 'campKind', disabled: edit, 'aria-describedby': 'campKindHelp' },
    meta.kinds.map((k) => h('option', { value: k.id, selected: k.id === (c.kind || 'attestation') }, k.label)));
  const kindHelp = h('p', { class: 'muted small', id: 'campKindHelp' });
  const name = h('input', { type: 'text', id: 'campName', maxlength: '120', value: c.name || '', placeholder: 'Ör. Uygulama verisi çeyrek teyidi' });
  const title = h('input', { type: 'text', id: 'campTitle', maxlength: '120', value: c.title || '', placeholder: 'Ör. Uygulamanızın kayıtlı bilgilerini teyit edin' });
  const instructions = h('textarea', { id: 'campInstr', rows: '3', maxlength: '4000', placeholder: 'Sorumlunun ne yapacağı, nereye bakacağı' });
  instructions.value = c.instructions || '';
  const cls = h('select', { id: 'campClass', 'aria-describedby': 'campClassHelp' }, meta.classes.map((x) => h('option',
    { value: x.name, selected: x.name === (c.scope_class || 'cmdb_ci_appl') }, `${'\u00a0\u00a0'.repeat(x.depth)}${x.depth ? '└ ' : ''}${x.label}`)));
  const query = h('input', { type: 'text', id: 'campQuery', maxlength: '2000', value: c.scope_query || '', class: 'mono',
    placeholder: 'Ör. used_for=Production^operational_status=1', 'aria-describedby': 'campQueryHelp' });
  const freq = h('select', { id: 'campFreq' }, meta.frequencies.map((f) => h('option', { value: f.id, selected: f.id === (c.frequency || 'quarterly') }, f.label)),
    h('option', { value: 'custom', selected: edit && !c.frequency }, 'Özel zamanlama (cron)'));
  const cron = h('input', { type: 'text', id: 'campCron', maxlength: '100', class: 'mono', value: edit && !c.frequency ? c.schedule : '',
    placeholder: 'dakika saat gün ay haftanın-günü · Ör. 0 9 * * 1', hidden: !(edit && !c.frequency), 'aria-label': 'Özel zamanlama' });
  const due = h('input', { type: 'number', id: 'campDue', min: '1', max: '180', value: String(c.due_days || 30) });
  const max = h('input', { type: 'number', id: 'campMax', min: '1', max: '20000', value: String(c.max_tasks || 2000), 'aria-describedby': 'campMaxHelp' });
  const channel = edit ? null : h('select', { id: 'campChannel' }, h('option', { value: '' }, 'Varsayılan kanal (görev açılırken)'),
    channels.filter((x) => x.lifecycle === 'active').map((x) => h('option', { value: x.name }, x.label_tr)));
  const preview = h('div', { id: 'campPreview', 'aria-live': 'polite' });
  const error = h('div', { id: 'campError' });
  const previewBtn = h('button', { class: 'btn secondary', type: 'button', id: 'campPreviewBtn' }, 'Kapsamı önizle');
  const save = h('button', { class: edit ? 'btn' : 'btn secondary', type: 'button', id: 'campSave' }, edit ? 'Kaydet' : 'Taslak kaydet');
  const activate = edit ? null : h('button', { class: 'btn', type: 'button', id: 'campActivate' }, 'Kaydet ve etkinleştir');
  let previewed = false;
  const showKind = () => { kindHelp.textContent = KIND_HELP[kind.value] || ''; };
  showKind();
  kind.addEventListener('change', showKind);
  freq.addEventListener('change', () => { cron.hidden = freq.value !== 'custom'; if (!cron.hidden) cron.focus(); });
  for (const el of [cls, query]) el.addEventListener('input', () => { previewed = false; clear(preview); el.removeAttribute('aria-invalid'); });
  for (const el of [name, title, cron, query, due, max]) el.addEventListener('input', () => { el.removeAttribute('aria-invalid'); clear(error); });

  async function runPreview() {
    clear(error);
    previewBtn.disabled = true;
    previewBtn.setAttribute('aria-busy', 'true');
    try {
      const p = await campaignPreview({ scope_class: cls.value, scope_query: query.value.trim(), campaign: c.sys_id || null });
      if (!p.valid) {
        clear(preview);
        query.setAttribute('aria-invalid', 'true');
        problem(error, `Koşul geçersiz: ${p.problem}`);
        query.focus();
        return null;
      }
      clear(preview).append(...previewBox(p));
      previewed = true;
      return p;
    } catch (err) {
      query.setAttribute('aria-invalid', 'true');
      problem(error, err.message);
      return null;
    } finally {
      previewBtn.disabled = false;
      previewBtn.removeAttribute('aria-busy');
    }
  }
  previewBtn.addEventListener('click', runPreview);
  query.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); runPreview(); } });

  function invalid(el, message) {
    el.setAttribute('aria-invalid', 'true');
    problem(error, message);
    el.focus();
    return false;
  }
  function check() {
    if (name.value.trim().length < 3) return invalid(name, 'Kampanyaya bir ad verin (en az 3 karakter).');
    if (title.value.trim().length < 3) return invalid(title, 'Görev başlığını yazın (en az 3 karakter).');
    if (freq.value === 'custom' && !cron.value.trim()) return invalid(cron, 'Özel zamanlama ifadesini yazın veya bir sıklık seçin.');
    const d = Number(due.value);
    if (!Number.isInteger(d) || d < 1 || d > 180) return invalid(due, 'Süre 1 ile 180 gün arasında olmalı.');
    const m = Number(max.value);
    if (!Number.isInteger(m) || m < 1 || m > 20000) return invalid(max, 'Kapsam sınırı 1 ile 20 000 arasında olmalı.');
    return true;
  }
  function payload() {
    const custom = freq.value === 'custom';
    return { name: name.value.trim(), title: title.value.trim(), instructions: instructions.value.trim(),
      scope_class: cls.value, scope_query: query.value.trim(), frequency: custom ? null : freq.value,
      schedule: custom ? cron.value.trim() : null, due_days: Number(due.value), max_tasks: Number(max.value) };
  }
  async function submit(button, active) {
    if (!check()) return;
    if (!previewed && !(await runPreview())) return;
    button.disabled = true;
    try {
      const res = edit ? await campaignUpdate(c.sys_id, payload())
        : await campaignCreate({ ...payload(), kind: kind.value, channel: channel.value || null, activate: active });
      closeOverlay();
      toast(res.message);
      onDone(res.sys_id || c.sys_id);
    } catch (err) {
      button.disabled = false;
      problem(error, err.message);
    }
  }
  save.addEventListener('click', () => submit(save, false));
  activate?.addEventListener('click', () => submit(activate, true));
  dialog({
    title: edit ? `Kampanyayı düzenle: ${c.name}` : 'Yeni kampanya',
    body: [
      edit ? null : h('p', { class: 'muted small' }, 'Düzenli görev: kapsamdaki her CI için sorumlusuna (sahip → yöneten → destek grubu) bir görev açılır. Aynı CI için açık görev varsa yenisi açılmaz.'),
      h('label', { class: 'field', for: 'campKind' }, 'Ne istenecek?'), kind, kindHelp,
      h('label', { class: 'field', for: 'campName' }, 'Kampanya adı'), name,
      h('label', { class: 'field', for: 'campTitle' }, 'Görev başlığı'), title,
      h('p', { class: 'muted small' }, 'Her görevin başlığı bu metin ve CI adıdır. Ör. "Uygulamanızı teyit edin — Bordro".'),
      h('label', { class: 'field', for: 'campInstr' }, 'Yönerge'), instructions,
      h('label', { class: 'field', for: 'campClass' }, 'Kapsam: sınıf'), cls,
      h('p', { class: 'muted small', id: 'campClassHelp' }, 'Üst sınıf alt sınıflarını da kapsar (Sunucu → Linux, Windows…). Emekli CI\'lar kapsama girmez.'),
      h('label', { class: 'field', for: 'campQuery' }, 'Kapsam: koşul (isteğe bağlı)'), query,
      h('p', { class: 'muted small', id: 'campQueryHelp' }, 'ServiceNow koşul sözdizimi: alan=değer, ^ ile "ve", ^OR ile "veya". Boş bırakılırsa sınıftaki bütün CI\'lar. ',
        h('a', { href: '#/kilavuz/gorevler' }, 'Koşul örnekleri')),
      h('div', { class: 'task-actions' }, previewBtn), preview,
      h('label', { class: 'field', for: 'campFreq' }, 'Sıklık'), freq, cron,
      h('p', { class: 'muted small' }, 'Hazır sıklıklar dönemin ilk günü 09:00\'da (İstanbul) çalışır.'),
      h('label', { class: 'field', for: 'campDue' }, 'Süre (gün)'), due,
      channel ? h('label', { class: 'field', for: 'campChannel' }, 'Kanal') : null, channel,
      h('label', { class: 'field', for: 'campMax' }, 'Kapsam sınırı'), max,
      h('p', { class: 'muted small', id: 'campMaxHelp' }, 'Kapsam bu sayıyı aşarsa çalışma görev açmadan durur (yanlış koşulla binlerce görev açılmaz).'),
      error,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save, activate].filter(Boolean),
    focus: edit ? name : kind,
  });
}

/**
 * Kampanyalar sekmesi. ``body``: sekme içeriği; ``onRoute``: adres çubuğunu günceller.
 * Döner: { open(id), create() } — doğrudan bağlantılar için.
 */
export async function campaignsTab(body, { onRoute, onChange }) {
  const meta = await campaignsList();
  let selected = null;
  const list = h('div', { id: 'campaignList' });
  const add = meta.can_admin ? h('button', { class: 'btn', type: 'button', id: 'campNew', onclick: () => create() }, 'Yeni kampanya') : null;
  clear(body).append(
    h('div', { class: 'toolbar' }, h('p', { class: 'muted small' }, 'Düzenli yönetişim görevleri: veri güncelliği teyidi, erişim gözden geçirme, kontrol listeleri. Görev çalışanı dönem gelince kapsamdaki her CI için sorumlusuna görev açar.'), add),
    list);

  async function load() {
    const res = await campaignsList();
    Object.assign(meta, res);
    clear(list).append(dataTable({
      label: 'Kampanyalar', idOf: (c) => c.sys_id, selectedId: () => selected,
      empty: meta.can_admin ? 'Henüz kampanya yok. "Yeni kampanya" ile ilk düzenli görevi tanımlayın.' : 'Henüz kampanya yok.',
      columns: [
        { key: 'name', label: 'Kampanya', render: (c) => h('div', {}, h('strong', {}, c.name), h('div', { class: 'cell-sub' }, c.kind_tr)) },
        { key: 'scope', label: 'Kapsam', sortable: false, render: scopeText },
        { key: 'frequency_tr', label: 'Sıklık', render: (c) => h('div', {}, c.frequency_tr, h('div', { class: 'cell-sub' }, `${c.due_days} gün süre`)) },
        { key: 'next_run_at', label: 'Sonraki çalışma', render: (c) => (c.next_run_at ? formatDateTime(c.next_run_at) : '—') },
        { key: 'open', label: 'Açık görev', render: (c) => h('div', {}, formatNumber(c.open),
          c.overdue ? h('div', { class: 'cell-sub' }, pill(`${formatNumber(c.overdue)} süresi geçti`, 'bad')) : null) },
        { key: 'state', label: 'Durum', render: stateCell },
      ],
      rows: meta.items, onOpen: (c, tr) => open(c.sys_id, tr),
    }));
  }

  function changeState(c, target) {
    const t = TRANSITION[target];
    reasonDialog({
      title: `${t.label}: ${c.name}`, intro: t.help, label: 'Gerekçe', button: t.label,
      placeholder: 'Ör. çeyrek dönemi teyidi başlıyor, TALEP-12',
      run: async (reason) => {
        const res = await campaignUpdate(c.sys_id, { state: target, reason });
        toast(res.message);
        await refresh(c.sys_id);
      },
    });
  }

  function runNow(c) {
    reasonDialog({
      title: `Şimdi çalıştır: ${c.name}`,
      intro: 'Zamanlamayı beklemeden kapsamdaki CI\'lar için görev açılır. Açık görevi olan CI\'a yenisi açılmaz; sonraki zamanlanmış çalışma değişmez.',
      label: 'Gerekçe', button: 'Çalıştır', placeholder: 'Ör. denetim öncesi erken başlatma',
      run: async (reason) => {
        const res = await campaignRun(c.sys_id, reason);
        toast(res.message);
        await refresh(c.sys_id);
      },
    });
  }

  async function refresh(id) {
    await load();
    onChange?.();
    await open(id);
  }

  async function open(id, opener) {
    selected = id;
    onRoute(`kampanyalar/${id}`);
    let c;
    try { c = await campaignDetail(id); } catch (err) { toast(err.message); return; }
    const footer = [];
    if (c.can_admin) {
      if (c.state === 'active') footer.push(h('button', { class: 'btn', type: 'button', id: 'campRunNow', onclick: () => runNow(c) }, 'Şimdi çalıştır'));
      for (const target of c.transitions) {
        footer.push(h('button', { class: target === 'active' ? 'btn' : 'btn secondary', type: 'button', dataset: { transition: target },
          onclick: () => changeState(c, target) }, TRANSITION[target].label));
      }
      if (c.state === 'draft' || c.state === 'paused') {
        footer.push(h('button', { class: 'btn secondary', type: 'button', id: 'campEdit', onclick: () => openCampaignDialog(meta, (x) => refresh(x), c) }, 'Düzenle'));
      }
    }
    const lastFailed = c.runs.find((r) => r.status === 'failed');
    openDetail({
      kicker: `Kampanya · ${c.kind_tr}`, title: c.name, opener,
      onClose: () => { selected = null; onRoute('kampanyalar'); },
      body: [
        h('div', { class: 'pills' }, stateCell(c), pill(c.frequency_tr, 'neutral')),
        c.state === 'draft' ? h('p', { class: 'notice' }, 'Taslak: görev açmaz. Kapsamı kontrol edip etkinleştirin.') : null,
        lastFailed && lastFailed === c.runs[0] ? h('p', { class: 'notice bad' }, `Son çalışma başarısız: ${lastFailed.note || 'bilinmeyen hata'}`) : null,
        section('Görev', kv([['Başlık', c.title], ['Yönerge', c.instructions || '—'], ['Süre', `${c.due_days} gün`],
          ['Kanal', c.channel || 'varsayılan kanal']])),
        section('Kapsam ve zamanlama', kv([
          ['Sınıf', c.scope_class_tr], ['Koşul', c.scope_query ? h('span', { class: 'mono' }, c.scope_query) : 'yok (sınıftaki bütün CI\'lar)'],
          ['Sıklık', c.frequency_tr], ['Sonraki çalışma', c.next_run_at ? formatDateTime(c.next_run_at) : '—'],
          ['Son çalışma', c.last_run_at ? formatDateTime(c.last_run_at) : 'henüz çalışmadı'],
          ['Kapsam sınırı', formatNumber(c.max_tasks)],
        ])),
        section('Görevler', kv([['Açık', formatNumber(c.open)], ['Süresi geçen', formatNumber(c.overdue)], ['Kapanan', formatNumber(c.closed)]]),
          h('p', { class: 'small' }, h('a', { href: `#/gorevler/kampanya/${c.sys_id}`, id: 'campTasks' }, 'Bu kampanyanın görevleri'))),
        section('Çalışmalar', c.runs.length ? h('ul', { class: 'mini', id: 'campRuns' }, c.runs.map((r) => h('li', {},
          h('strong', {}, `${TRIGGER_TR[r.trigger]} · `, pill(RUN_TR[r.status], RUN_TONE[r.status])),
          h('div', { class: 'cell-sub' }, [formatDateTime(r.started_at), r.requested_by,
            r.status === 'done' ? `${formatNumber(r.in_scope)} kapsamda, ${formatNumber(r.created)} görev açıldı, ${formatNumber(r.already_open)} zaten açık, ${formatNumber(r.unassigned)} atanmamış` : null,
            r.note].filter(Boolean).join(' · ')))))
          : h('p', { class: 'muted small' }, 'Henüz çalışmadı.')),
        section('Kayıt', kv([['Oluşturan', `${c.created_by} · ${formatDateTime(c.created_at)}`],
          c.updated_by ? ['Son değişiklik', `${c.updated_by} · ${formatDateTime(c.updated_at)}`] : null])),
      ],
      footer,
    });
  }

  function create() {
    if (!meta.can_admin) return;
    openCampaignDialog(meta, (id) => refresh(id));
  }

  await load();
  return { open, create, close: () => closeDetail({ silent: true }) };
}
