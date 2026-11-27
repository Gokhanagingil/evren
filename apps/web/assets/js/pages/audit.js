// Denetim izi (U8.8, ADR-0039; F1-16).
// Olaylar: güvenlik ve yönetim olayları, CI değişiklikleri ve görev olayları tek listede; dönem, akış, kategori, sonuç
// ve arama süzgeci; bir olaydan kişiye, nesneye veya aynı isteğe geçiş; CSV/NDJSON dışa aktarma (aktarma da izdedir).
// Bütünlük: akış başına saatlik zincirli mühürler, kendiliğinden ve elle doğrulama. İncelemeler: Denetçinin dönem
// incelemesi (karar ve not) kanıt olarak kaydedilir. Yalnızca Denetçi görür.
import { h, append, clear, dataTable, formatDateTime, formatDateTimeSec, formatNumber, fromIstanbulInput, kv, metric, pill,
  section, tabs, toIstanbulInput } from '../lib/dom.js';
import { closeDetail, closeOverlay, dialog, openDetail, setHeader, toast } from '../lib/shell.js';
import { auditCatalog, auditEvent, auditEvents, auditExportUrl, auditIntegrity, auditReview, auditReviews, auditSeals,
  auditSummary, auditVerify, backend } from '../lib/data.js';
import { radioGroup } from '../lib/wizard.js';

const VIEWS = [['events', 'Olaylar'], ['integrity', 'Bütünlük'], ['reviews', 'İncelemeler']];
const SLUG = { events: '', integrity: 'butunluk', reviews: 'incelemeler' };
const PERIODS = [['1', 'Son 24 saat'], ['7', 'Son 7 gün'], ['30', 'Son 30 gün'], ['90', 'Son 90 gün'], ['custom', 'Özel dönem']];
const OUTCOME_TONE = { allow: 'good', deny: 'bad' };
const RID = /^[0-9a-f]{32}$/;
const MIN_NOTE = 10;

const daysAgo = (n) => new Date(Date.now() - n * 86400000);
const shortHash = (v) => (v ? `${v.slice(0, 12)}…` : '—');

function problem(box, message) {
  clear(box).append(h('div', { class: 'notice bad', role: 'alert' }, message));
}

function periodOf(state) {
  if (state.period === 'custom') return { since: state.since, until: state.until };
  return { since: daysAgo(Number(state.period)).toISOString(), until: null };
}

// ---------------------------------------------------------------------------------------------- olaylar
async function eventsTab(body, { onRoute, pivot }) {
  const catalog = await auditCatalog();
  const state = { period: '7', since: null, until: null, source: '', category: '', outcome: '', q: '', ...pivot };
  let selected = null;
  let cursor = null;
  let items = [];

  const summaryBox = h('div', { class: 'cards five', id: 'auditSummary' });
  const pivotBox = h('div', { class: 'pills audit-pivots', id: 'auditPivots' });
  const list = h('div', { id: 'auditList' });
  const totalLine = h('p', { class: 'muted small', id: 'auditTotal', 'aria-live': 'polite' });
  const more = h('button', { class: 'btn secondary', type: 'button', id: 'auditMore', hidden: true }, 'Daha eski olayları yükle');
  const periodSel = h('select', { id: 'auditPeriod', 'aria-label': 'Dönem' },
    PERIODS.map(([v, l]) => h('option', { value: v, selected: v === state.period }, l)));
  const sinceIn = h('input', { type: 'datetime-local', id: 'auditSince', 'aria-label': 'Başlangıç', step: '1' });
  const untilIn = h('input', { type: 'datetime-local', id: 'auditUntil', 'aria-label': 'Bitiş', step: '1' });
  const custom = h('span', { class: 'audit-custom', hidden: true }, sinceIn, untilIn);
  const sourceSel = h('select', { id: 'auditSource', 'aria-label': 'Akış' }, h('option', { value: '' }, 'Bütün akışlar'),
    catalog.streams.map((s) => h('option', { value: s.value }, s.label)));
  const categorySel = h('select', { id: 'auditCategory', 'aria-label': 'Kategori' }, h('option', { value: '' }, 'Bütün kategoriler'),
    catalog.categories.map((c) => h('option', { value: c.value }, c.label)));
  const outcomeSel = h('select', { id: 'auditOutcome', 'aria-label': 'Sonuç' }, h('option', { value: '' }, 'İzin ve ret'),
    catalog.outcomes.map((o) => h('option', { value: o.value }, o.value === 'deny' ? 'Yalnızca ret' : 'Yalnızca izin')));
  const search = h('input', { type: 'search', class: 'searchbar', id: 'auditSearch', placeholder: 'Kişi, olay, nesne veya ayrıntı',
    'aria-label': 'Denetim izinde ara' });
  const csvLink = h('a', { class: 'btn secondary', id: 'auditCsv', download: '' }, 'CSV indir');
  const ndLink = h('a', { class: 'btn secondary', id: 'auditNdjson', download: '' }, 'NDJSON indir');
  append(clear(body), [
    h('div', { class: 'toolbar audit-toolbar' }, periodSel, custom, sourceSel, categorySel, outcomeSel, search),
    pivotBox, summaryBox,
    h('div', { class: 'audit-listhead' }, totalLine, h('div', { class: 'actions' }, csvLink, ndLink)),
    list, more,
  ]);

  function filters() {
    const { since, until } = periodOf(state);
    return { since, until, source: state.source || null, category: state.category || null, outcome: state.outcome || null,
      q: state.q || null, actor: state.actor || null, object: state.object || null, request_id: state.request_id || null,
      ip: state.ip || null };
  }

  function renderPivots() {
    const chips = [['actor', 'Kişi'], ['object', 'Nesne'], ['request_id', 'İstek'], ['ip', 'Kaynak IP']]
      .filter(([k]) => state[k]).map(([k, label]) => h('span', { class: 'chip plain pivot' }, `${label}: ${k === 'request_id' ? shortHash(state[k]) : state[k]}`,
        h('button', { type: 'button', class: 'pivot-x', 'aria-label': `${label} süzgecini kaldır`, onclick: () => { delete state[k]; reload(); } }, '×')));
    clear(pivotBox).append(...chips);
    pivotBox.hidden = !chips.length;
  }

  let summarySeq = 0;
  async function renderSummary() {
    const { since, until } = periodOf(state);
    const mine = ++summarySeq;
    try {
      const s = await auditSummary({ since, until });
      if (mine !== summarySeq) return;
      const by = Object.fromEntries(s.by_source.map((x) => [x.value, x.count]));
      clear(summaryBox).append(
        metric({ label: 'Olay', value: formatNumber(s.events), foot: 'seçilen dönem, süzgeçsiz' }),
        metric({ label: 'Ret', value: formatNumber(s.denied), tone: s.denied ? 'warn' : '', foot: s.denied_actors.length ? `en çok: ${s.denied_actors[0].actor}` : 'yetki reddi yok' }),
        metric({ label: 'Güvenlik ve yönetim', value: formatNumber(by.security || 0) }),
        metric({ label: 'CI değişiklikleri', value: formatNumber(by.ci || 0) }),
        metric({ label: 'Görev olayları', value: formatNumber(by.task || 0) }));
    } catch (err) {
      problem(summaryBox, err.message);
    }
  }

  function table() {
    return dataTable({
      label: 'Denetim izi olayları', idOf: (e) => e.key, selectedId: () => selected,
      empty: 'Bu süzgeçle olay yok. Dönemi genişletin veya süzgeçleri kaldırın.',
      columns: [
        { key: 'at', label: 'Zaman', render: (e) => formatDateTimeSec(e.at) },
        { key: 'actor', label: 'Kim', render: (e) => h('span', { class: 'mono' }, e.actor || '—') },
        { key: 'action_label', label: 'Olay', render: (e) => h('div', {}, e.action_label, h('div', { class: 'cell-sub' }, `${e.category_label} · ${e.source_label}`)) },
        { key: 'object', label: 'Nesne', render: (e) => h('span', { class: 'mono' }, e.object || '—') },
        { key: 'outcome', label: 'Sonuç', render: (e) => pill(e.outcome_label, OUTCOME_TONE[e.outcome]) },
        { key: 'client_ip', label: 'Kaynak IP', render: (e) => e.client_ip || '—' },
      ],
      rows: items, onOpen: (e, tr) => open(e.source, e.id, tr),
    });
  }

  // Süzgeçler art arda değişince yalnızca son isteğin yanıtı işlenir (eski yanıt yeniyi ezmez).
  let loadSeq = 0;
  async function load(append_ = false) {
    const f = filters();
    const mine = ++loadSeq;
    let res;
    try {
      res = await auditEvents({ ...f, cursor: append_ ? cursor : null });
    } catch (err) {
      if (mine !== loadSeq) return;
      problem(list, err.message);
      more.hidden = true;
      return;
    }
    if (mine !== loadSeq) return;
    items = append_ ? items.concat(res.items) : res.items;
    cursor = res.next;
    if (res.total !== null && res.total !== undefined) {
      totalLine.textContent = `${res.total_capped ? '10.000+' : formatNumber(res.total)} olay · ${formatDateTime(res.since)} – ${formatDateTime(res.until)}`;
    }
    clear(list).append(table());
    more.hidden = !cursor;
    csvLink.href = auditExportUrl(f, 'csv');
    ndLink.href = auditExportUrl(f, 'ndjson');
  }

  function reload() {
    renderPivots();
    load();
    renderSummary();
  }

  periodSel.addEventListener('change', () => {
    state.period = periodSel.value;
    custom.hidden = state.period !== 'custom';
    if (state.period === 'custom') {
      if (!sinceIn.value) sinceIn.value = toIstanbulInput(daysAgo(1));
      if (!untilIn.value) untilIn.value = toIstanbulInput(new Date());
      state.since = fromIstanbulInput(sinceIn.value);
      state.until = fromIstanbulInput(untilIn.value);
      sinceIn.focus();
    }
    reload();
  });
  for (const input of [sinceIn, untilIn]) {
    input.addEventListener('change', () => { state.since = fromIstanbulInput(sinceIn.value); state.until = fromIstanbulInput(untilIn.value); reload(); });
  }
  sourceSel.addEventListener('change', () => { state.source = sourceSel.value; load(); });
  categorySel.addEventListener('change', () => { state.category = categorySel.value; load(); });
  outcomeSel.addEventListener('change', () => { state.outcome = outcomeSel.value; load(); });
  let timer = null;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { state.q = search.value.trim(); load(); }, 300); });
  more.addEventListener('click', () => load(true));

  function pivotTo(key, value) {
    for (const k of ['actor', 'object', 'request_id', 'ip']) delete state[k];
    state[key] = value;
    closeDetail({ silent: true });
    onRoute('');
    reload();
    document.getElementById('auditPivots')?.querySelector('.pivot-x')?.focus();
  }

  async function open(source, id, opener) {
    selected = `${source}:${id}`;
    list.querySelector('.tablewrap')?.refreshSelection?.();
    onRoute(`olay/${source}/${id}`);
    let e;
    try { e = await auditEvent(source, id); } catch (err) { toast(err.message); return; }
    const link = (label, key, value) => h('button', { type: 'button', class: 'linkbtn', id: `pivot-${key}`, onclick: () => pivotTo(key, value) }, label);
    const sealText = e.seal
      ? [`Mühürlü: ${formatDateTime(e.seal.period_start)} – ${formatDateTime(e.seal.period_end)}`,
        e.seal.last_check_ok === true ? ' · son doğrulama başarılı' : e.seal.last_check_ok === false ? ' · son doğrulama SORUNLU' : ' · henüz doğrulanmadı']
      : ['Henüz mühürlenmedi: dönemin bitişinden 15 dakika sonra mühürlenir.'];
    openDetail({
      kicker: `${e.source_label} · ${e.category_label}`, title: e.action_label, opener,
      onClose: () => { selected = null; list.querySelector('.tablewrap')?.refreshSelection?.(); onRoute(''); },
      body: [
        h('div', { class: 'pills' }, pill(e.outcome_label, OUTCOME_TONE[e.outcome]), e.masked ? pill('Değer gizli', 'neutral') : null),
        e.masked ? h('p', { class: 'notice' }, 'Bu alanın hassasiyetini görme yetkiniz yok; eski ve yeni değer gizlendi.') : null,
        section('Olay', kv([
          ['Zaman', `${formatDateTimeSec(e.at)} (İstanbul)`],
          ['Kim', h('span', {}, h('span', { class: 'mono' }, e.actor || '—'), ' ', e.actor ? link('Bu kişinin olayları', 'actor', e.actor) : null)],
          ['Olay türü', h('code', {}, e.action)],
          ['Nesne', e.object ? h('span', {}, e.object_link ? h('a', { href: e.object_link }, e.object) : h('span', { class: 'mono' }, e.object), ' ',
            link('Bu nesnenin olayları', 'object', e.object)) : '—'],
          ['Kaynak IP', e.client_ip ? h('span', {}, e.client_ip, ' ', link('Bu adresten gelenler', 'ip', e.client_ip)) : '—'],
          ['İstek kimliği', e.request_id ? h('span', {}, h('code', {}, shortHash(e.request_id)), ' ', link('Aynı istekteki olaylar', 'request_id', e.request_id)) : '—'],
          ['Bütünlük', h('span', { id: 'eventSeal' }, ...sealText)],
        ])),
        section('Ayrıntı', Object.keys(e.detail || {}).length
          ? h('pre', { class: 'md-code audit-json', id: 'eventDetail', tabindex: '0', 'aria-label': 'Olay ayrıntısı (JSON)' },
            JSON.stringify(e.detail, null, 2))
          : h('p', { class: 'muted small', id: 'eventDetail' }, 'Bu olayın ek ayrıntısı yok.')),
        e.related.length ? section('Aynı istekteki diğer olaylar', h('ul', { class: 'mini', id: 'eventRelated' }, e.related.map((r) => h('li', {},
          h('button', { type: 'button', class: 'linkbtn', onclick: () => open(r.source, r.id) }, r.action_label), ' ',
          pill(r.outcome_label, OUTCOME_TONE[r.outcome]), h('div', { class: 'cell-sub' }, `${formatDateTimeSec(r.at)} · ${r.object || '—'}`))))) : null,
      ],
    });
  }

  renderPivots();
  await Promise.all([load(), renderSummary()]);
  return { open };
}

// ---------------------------------------------------------------------------------------------- bütünlük
async function integrityTab(body) {
  const [st, seals] = await Promise.all([auditIntegrity(), auditSeals({ limit: 24 })]);
  const result = h('div', { id: 'verifyResult', 'aria-live': 'polite' });
  const small = (el) => { el.classList.add('metric-sm'); return el; };
  const streamCards = h('div', { class: 'cards', id: 'sealStreams' }, st.streams.map((s) => small(metric({
    label: s.label, id: `sealStream-${s.stream}`,
    value: s.sealed_until ? formatDateTime(s.sealed_until) : 'Mühür yok',
    tone: s.lagging ? 'warn' : 'good',
    foot: s.sealed_until ? `${formatNumber(s.seals)} mühür · ${s.lagging ? 'mühürleme gecikti' : 'güncel'}` : 'görev çalışanı ilk mührü bekliyor',
  }))));
  const checks = h('div', { class: 'cards', id: 'sealChecks' },
    small(metric({ label: 'Kendiliğinden doğrulama', value: st.last_auto_check ? formatDateTime(st.last_auto_check) : 'Henüz yok', foot: 'görev çalışanı günde bir, son 7 gün' })),
    small(metric({ label: 'Elle doğrulama', value: st.last_manual_check ? formatDateTime(st.last_manual_check) : 'Henüz yok', foot: 'Denetçi, istediği dönem' })),
    metric({ label: 'Sorunlu doğrulama (30 gün)', value: formatNumber(st.failed_30d), tone: st.failed_30d ? 'bad' : 'good',
      foot: st.failed_30d ? 'kayıtlar mühürden sonra değişmiş olabilir' : 'kayıtlar mühürlendiği gibi' }));
  const failed = st.failed.length ? section('Sorunlu doğrulamalar', dataTable({
    label: 'Sorunlu doğrulamalar', idOf: (f) => `${f.at}-${f.stream}`, columns: [
      { key: 'at', label: 'Doğrulama', render: (f) => formatDateTime(f.at) },
      { key: 'stream_label', label: 'Akış' },
      { key: 'period_start', label: 'Dönem', render: (f) => `${formatDateTime(f.period_start)} – ${formatDateTime(f.period_end)}` },
      { key: 'problem', label: 'Sorun', sortable: false },
      { key: 'by', label: 'Doğrulayan', render: (f) => h('span', { class: 'mono' }, f.by) },
    ], rows: st.failed })) : null;

  let verify = null;
  if (st.can_verify) {
    const periodSel = h('select', { id: 'verifyPeriod', 'aria-label': 'Doğrulanacak dönem' },
      [['1', 'Son 24 saat'], ['7', 'Son 7 gün'], ['30', 'Son 30 gün'], ['92', 'Son 92 gün']].map(([v, l]) => h('option', { value: v, selected: v === '7' }, l)));
    const streamSel = h('select', { id: 'verifyStream', 'aria-label': 'Akış' }, h('option', { value: '' }, 'Bütün akışlar'),
      st.streams.map((s) => h('option', { value: s.stream }, s.label)));
    const run = h('button', { class: 'btn', type: 'button', id: 'verifyRun' }, 'Mühürleri doğrula');
    run.addEventListener('click', async () => {
      run.disabled = true;
      clear(result).append(h('p', { class: 'muted small' }, 'Dönemin kayıtları yeniden hesaplanıyor…'));
      try {
        const r = await auditVerify({ since: daysAgo(Number(periodSel.value)).toISOString(), until: new Date().toISOString(), stream: streamSel.value || null });
        clear(result).append(h('div', { class: `notice ${r.ok ? 'success' : r.failed ? 'bad' : ''}`.trim(), role: r.failed ? 'alert' : 'status' }, r.message,
          r.problems.length ? h('ul', {}, r.problems.map((p) => h('li', {}, `${p.stream_label} · ${formatDateTime(p.period_start)} – ${formatDateTime(p.period_end)}: ${p.problem}`))) : null));
      } catch (err) {
        problem(result, err.message);
      } finally {
        run.disabled = false;
      }
    });
    verify = section('Elle doğrula', h('p', { class: 'muted small' }, 'Seçilen dönemin mühürleri bugünkü kayıtlarla yeniden hesaplanır. Sonuç kanıt olarak kaydedilir ve SIEM\'e gider.'),
      h('div', { class: 'toolbar' }, periodSel, streamSel, run), result);
  }
  const sealTable = section('Son mühürler', dataTable({
    label: 'Son mühürler', idOf: (s) => String(s.id), columns: [
      { key: 'stream_label', label: 'Akış' },
      { key: 'period_start', label: 'Dönem', render: (s) => `${formatDateTime(s.period_start)} – ${formatDateTime(s.period_end)}` },
      { key: 'row_count', label: 'Kayıt', num: true, render: (s) => formatNumber(s.row_count) },
      { key: 'chain', label: 'Zincir', sortable: false, render: (s) => h('code', { title: s.chain }, shortHash(s.chain)) },
      { key: 'last_check', label: 'Son doğrulama', sortable: false, render: (s) => (s.last_check
        ? pill(s.last_check.ok ? 'Başarılı' : 'Sorunlu', s.last_check.ok ? 'good' : 'bad') : pill('Doğrulanmadı', 'neutral')) },
    ], rows: seals.items, empty: 'Henüz mühür yok. Görev çalışanı biten her saati 15 dakika sonra mühürler.' }));
  append(clear(body), [
    h('p', { class: 'notice', id: 'integrityHelp' }, 'Her akışın kayıtları saatlik dönemlerde mühürlenir. Mühür dönemin özetini ve bir önceki mührün zincir değerini içerir; bir kaydın sonradan değiştirilmesi, silinmesi veya eklenmesi doğrulamada görünür. Mühürler ve doğrulamalar kurum SIEM\'ine de gider.'),
    streamCards, checks, failed, verify, sealTable,
  ]);
  return {};
}

// ---------------------------------------------------------------------------------------------- incelemeler
function reviewDialog(suggested, onSaved) {
  const startIn = h('input', { type: 'datetime-local', id: 'revStart', step: '1', value: toIstanbulInput(suggested.period_start) });
  const endIn = h('input', { type: 'datetime-local', id: 'revEnd', step: '1', value: toIstanbulInput(suggested.period_end) });
  const summary = h('div', { id: 'revSummary', 'aria-live': 'polite' });
  let outcome = 'no_findings';
  const outcomes = radioGroup('revOutcome', 'İnceleme sonucu', [
    { value: 'no_findings', label: 'Bulgu yok', sub: 'Olağandışı bir olay görülmedi; mühürler doğrulanırsa kaydedilir.' },
    { value: 'findings', label: 'Bulgu var', sub: 'İncelenmesi gereken bir olay veya mühür sorunu var; notta açıklayın.' },
  ], outcome, (v) => { outcome = v; });
  const note = h('textarea', { id: 'revNote', rows: '4', maxlength: '4000', placeholder: 'Neye bakıldı, ne görüldü, gerekiyorsa hangi kayıt açıldı (ör. TALEP-112).' });
  const error = h('div', { id: 'revError' });
  const save = h('button', { class: 'btn', type: 'button', id: 'revSave' }, 'İncelemeyi kaydet');

  async function preview() {
    const since = fromIstanbulInput(startIn.value);
    const until = fromIstanbulInput(endIn.value);
    if (!since || !until) return;
    try {
      const s = await auditSummary({ since, until });
      const cat = Object.fromEntries(s.by_category.map((c) => [c.value, c.count]));
      clear(summary).append(h('div', { class: 'notice' }, h('strong', {}, `${formatNumber(s.events)} olay, ${formatNumber(s.denied)} ret. `),
        'Gözden geçirin: ', h('ul', {},
          h('li', {}, `Yetki reddi: ${formatNumber(s.denied)}`, s.denied_actors.length ? ` (en çok ${s.denied_actors.map((a) => `${a.actor}: ${a.count}`).join(', ')})` : ''),
          h('li', {}, `Kimlik ve yetki değişiklikleri: ${formatNumber(cat.iam || 0)}`),
          h('li', {}, `Veri modeli ve entegrasyon: ${formatNumber((cat.schema || 0) + (cat.integration || 0))}`),
          h('li', {}, `Denetim olayları (dışa aktarma, doğrulama): ${formatNumber(cat.audit || 0)}`)),
        h('p', { class: 'muted small' }, 'Kaydederken dönemin mühürleri doğrulanır; sayılar inceleme kaydına sabitlenir.')));
    } catch (err) {
      problem(summary, err.message);
    }
  }
  startIn.addEventListener('change', preview);
  endIn.addEventListener('change', preview);
  note.addEventListener('input', () => { note.removeAttribute('aria-invalid'); clear(error); });
  save.addEventListener('click', async () => {
    const since = fromIstanbulInput(startIn.value);
    const until = fromIstanbulInput(endIn.value);
    if (!since || !until || since >= until) { problem(error, 'Dönemin başlangıcı bitişinden önce olmalı.'); startIn.focus(); return; }
    if (note.value.trim().length < MIN_NOTE) {
      note.setAttribute('aria-invalid', 'true');
      problem(error, `İnceleme notu en az ${MIN_NOTE} karakter olmalı: neye bakıldı, ne görüldü.`);
      note.focus();
      return;
    }
    save.disabled = true;
    try {
      const res = await auditReview({ period_start: since, period_end: until, outcome, note: note.value.trim() });
      closeOverlay();
      toast(res.message);
      await onSaved();
    } catch (err) {
      save.disabled = false;
      problem(error, err.message);
    }
  });
  dialog({ title: 'Denetim izi incelemesi', wide: true,
    body: [h('p', {}, 'Dönemin olaylarını gözden geçirin ve sonucu kaydedin. İnceleme değiştirilemez; kanıt olarak denetim izinde ve SIEM\'de kalır.'),
      h('div', { class: 'grid two' }, h('div', {}, h('label', { class: 'field', for: 'revStart' }, 'Dönem başlangıcı'), startIn),
        h('div', {}, h('label', { class: 'field', for: 'revEnd' }, 'Dönem bitişi'), endIn)),
      summary, h('p', { class: 'field' }, 'Sonuç'), outcomes, h('label', { class: 'field', for: 'revNote' }, 'Not'), note, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save], focus: startIn });
  preview();
}

async function reviewsTab(body, { create = false } = {}) {
  const res = await auditReviews();
  const reload = async () => { await reviewsTab(body); };
  const start = res.can_review ? h('button', { class: 'btn', type: 'button', id: 'reviewNew', onclick: () => reviewDialog(res.suggested, reload) }, 'İnceleme kaydet') : null;
  append(clear(body), [
    res.due ? h('div', { class: 'notice warn', id: 'reviewDue' }, h('strong', {}, 'İnceleme zamanı. '),
      `Denetim izi ${res.every_days} günde bir gözden geçirilir; son incelemeden bu yana olayları inceleyip kaydedin.`)
      : h('p', { class: 'notice success', id: 'reviewDue' }, 'Son inceleme güncel.'),
    h('div', { class: 'toolbar' }, start),
    dataTable({
      label: 'Denetim izi incelemeleri', idOf: (r) => String(r.id),
      empty: 'Henüz inceleme yok. İlk incelemeyi kaydederek başlayın.',
      columns: [
        { key: 'reviewed_at', label: 'Tarih', render: (r) => formatDateTime(r.reviewed_at) },
        { key: 'reviewer', label: 'İnceleyen', render: (r) => h('span', { class: 'mono' }, r.reviewer) },
        { key: 'period_start', label: 'Dönem', render: (r) => `${formatDateTime(r.period_start)} – ${formatDateTime(r.period_end)}` },
        { key: 'events', label: 'Olay / ret', num: true, render: (r) => `${formatNumber(r.events)} / ${formatNumber(r.denied)}` },
        { key: 'seals_ok', label: 'Mühürler', render: (r) => (r.seals_checked
          ? pill(`${r.seals_checked} · ${r.seals_ok ? 'sağlam' : 'sorunlu'}`, r.seals_ok ? 'good' : 'bad')
          : pill('Mühürlenmemiş dönem', 'neutral')) },
        { key: 'outcome', label: 'Sonuç ve not', sortable: false, render: (r) => h('div', {}, pill(r.outcome_label, r.outcome === 'findings' ? 'warn' : 'good'),
          h('div', { class: 'cell-sub' }, r.note)) },
      ],
      rows: res.items,
    }),
  ]);
  if (create && res.can_review) reviewDialog(res.suggested, reload);
  return {};
}

// ---------------------------------------------------------------------------------------------- sayfa
export async function renderAudit(view, param) {
  const { mode, me } = await backend();
  setHeader({ title: 'Denetim İzi', crumbs: [['Denetim İzi']],
    sub: 'Kim, ne zaman, neyi, nereden ve hangi sonuçla yaptı? Yönetim olayları, CI değişiklikleri ve görev olayları tek yerde; kayıtlar saatlik zincirli mühürle korunur ve kurum SIEM\'ine akar.' });
  if (mode !== 'api') {
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '), 'Denetim izi canlı API ile çalışır.'));
    return {};
  }
  if (!me?.can?.audit_read) {
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Yetkiniz yok. '),
      'Denetim izini Denetçi rolü görür. Görevler ayrılığı gereği Platform yöneticisi kendi denetçisi olamaz.'));
    return {};
  }
  const tabBox = h('div', { id: 'auditTabs' });
  const body = h('div', { id: 'auditBody' });
  view.append(tabBox, body);
  let active = null;
  let ctl = null;
  let pivotKey = '';
  const route = (p) => { const want = `#/denetim${p ? `/${p}` : ''}`; if (location.hash !== want) history.replaceState(null, '', want); };

  async function show(id, opts = {}) {
    const key = JSON.stringify(opts);
    if (active === id && key === pivotKey && ctl && !opts.force) return;
    active = id;
    pivotKey = key;
    clear(tabBox).append(tabs(VIEWS.map(([v, l]) => ({ id: v, label: l })), id,
      (v) => { closeDetail({ silent: true }); route(SLUG[v]); show(v); }, 'Denetim izi görünümleri'));
    try {
      if (id === 'integrity') ctl = await integrityTab(body);
      else if (id === 'reviews') ctl = await reviewsTab(body, opts);
      else ctl = await eventsTab(body, { onRoute: route, pivot: opts.pivot || {} });
    } catch (err) {
      problem(body, err.message);
      ctl = null;
    }
  }

  async function update(p) {
    const parts = (p || '').split('/').filter(Boolean);
    if (parts[0] === 'butunluk') { closeDetail({ silent: true }); await show('integrity'); return; }
    if (parts[0] === 'incelemeler') { closeDetail({ silent: true }); await show('reviews', { create: parts[1] === 'yeni' }); return; }
    if (parts[0] === 'istek' && RID.test(parts[1] || '')) {
      closeDetail({ silent: true });
      await show('events', { pivot: { request_id: parts[1], period: '90' } });
      return;
    }
    await show('events');
    if (parts[0] === 'olay' && ['security', 'ci', 'task'].includes(parts[1]) && /^\d+$/.test(parts[2] || '')) {
      await ctl?.open(parts[1], Number(parts[2]));
      return;
    }
    closeDetail({ silent: true });
  }
  await update(param);
  return { update };
}
