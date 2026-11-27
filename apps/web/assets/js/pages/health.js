// CMDB sağlığı, kalite ve uyum kuralları, bulgular (U8.6, ADR-0037; F1-09, F1-18, F1-26).
// Genel bakış: genel puan ve dört boyut (tamlık, doğruluk, uyum, kapsam), her boyutun ölçümleri, 30 günlük eğilim.
// Kurallar: Konfigürasyon yöneticisi panelden kural tanımlar (şablondan başlayarak), önizler, etkinleştirir; görev
// çalışanı değerlendirir, bulgu açar ve sorumluya görev verir. Bulgular: neden, sorumlu ve görev; istisna.
import { h, s, append, clear, dataTable, formatDateTime, formatNumber, kv, metric, pill, section, tabs } from '../lib/dom.js';
import { closeDetail, closeOverlay, dialog, openDetail, setHeader, toast } from '../lib/shell.js';
import {
  backend, findingDetail, findingException, findingReopen, findingsList, healthView, qualityFields, qualityRule,
  qualityRules, ruleCreate, rulePreview, ruleRun, ruleUpdate, taskChannels,
} from '../lib/data.js';

const SYSID = /^[0-9a-f]{32}$/;
const MIN_TEXT = 10;
const GRADE = { good: ['İyi', 'good'], warn: ['Dikkat', 'warn'], bad: ['Zayıf', 'bad'], unknown: ['Ölçülemedi', 'neutral'] };
const STATE_TONE = { draft: 'neutral', active: 'good', paused: 'warn', retired: 'neutral' };
const RUN_TONE = { requested: 'warn', running: 'warn', done: 'good', failed: 'bad', inconclusive: 'warn' };
const SEV_TONE = { low: 'neutral', medium: 'warn', high: 'bad', critical: 'bad' };
const FINDING_TONE = { open: 'warn', excepted: 'neutral', resolved: 'good' };
const BASIS_TR = { owned_by: 'Sahibi', managed_by: 'Yöneteni', support_group: 'Destek grubu', unassigned: 'Atanmamış' };
const DIM_HELP = {
  completeness: 'Zorunlu ve önerilen alanlar dolu mu, beklenen ilişkiler var mı?',
  correctness: 'Kayıtlar gerçeği yansıtıyor mu: eskimemiş, ilişkileri izinli kurallara uygun mu?',
  compliance: 'Yönetişim beklentileri karşılanıyor mu: sahip, destek grubu, kurum kuralları?',
  coverage: 'Sunucular keşif, izleme, uç nokta koruması ve mevcut CMDB tarafından görülüyor mu?',
};
const TRANSITION = {
  active: { label: 'Etkinleştir', help: 'Kural seçilen sıklıkla değerlendirilir; ilk değerlendirme birkaç saniye içinde.' },
  paused: { label: 'Duraklat', help: 'Değerlendirme durur; bulgular ve açık görevler kalır. Duraklatılmış kural düzenlenebilir.' },
  retired: { label: 'Emekliye ayır', help: 'Kural bir daha değerlendirilmez. Geri alınamaz.' },
};
const fmt = (v) => (v === null || v === undefined ? '—' : String(v).replace('.', ','));

function problem(box, message) {
  clear(box).append(h('div', { class: 'notice bad', role: 'alert' }, message));
}

function gradePill(g) {
  const [label, tone] = GRADE[g] || GRADE.unknown;
  return pill(label, tone);
}

function reasonDialog({ title, intro, button, placeholder, extra = [], run }) {
  const why = h('input', { type: 'text', id: 'qWhy', maxlength: '500', placeholder });
  const error = h('div', { id: 'qWhyError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'qWhySend' }, button);
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
  dialog({ title, body: [h('p', {}, intro), ...extra, h('label', { class: 'field', for: 'qWhy' }, 'Gerekçe'), why, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send], focus: why });
}

// ---------------------------------------------------------------------------------------------- eğilim çizgisi
/** Genel puanın günlük eğilimi: tek seri (açıklama kutusu yok, başlık adlandırır), 0–100 ekseni, imleç ve klavye.
 * Kapsayıcının gerçek genişliğinde çizilir (1 birim = 1 piksel): yazı ve çizgi her ekranda aynı boyutta kalır. */
function trendChart(points) {
  const vals = points.filter((p) => p.overall !== null);
  if (!vals.length) return h('p', { class: 'muted small' }, 'Eğilim için en az bir ölçüm gerekir.');
  const readout = h('p', { class: 'trend-readout small', id: 'trendReadout', 'aria-live': 'polite' });
  const holder = h('div', { class: 'trend-holder' });
  const day = (iso) => formatDateTime(iso).split(' ')[0];
  const last = vals.length - 1;
  let at = last;
  let svg = null;

  function draw() {
    const W = Math.max(260, Math.round(holder.clientWidth || 640)), H = 180, L = 34, R = 12, T = 12, B = 26;
    const x = (i) => (vals.length === 1 ? (L + W - R) / 2 : L + (i * (W - L - R)) / last);
    const y = (v) => T + ((100 - v) * (H - T - B)) / 100;
    const hadFocus = svg && document.activeElement === svg;
    svg = s('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'trend-chart', role: 'img', tabindex: '0', id: 'trendChart',
      'aria-label': `Genel puanın son ${vals.length} gündeki eğilimi. Sol ve sağ ok tuşlarıyla günleri gezin.` });
    for (const g of [0, 50, 100]) {
      svg.append(s('line', { x1: L, x2: W - R, y1: y(g), y2: y(g), class: 'trend-grid' }),
        s('text', { x: L - 6, y: y(g) + 4, class: 'trend-tick', 'text-anchor': 'end' }, String(g)));
    }
    const line = vals.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.overall).toFixed(1)}`).join(' ');
    if (vals.length > 1) {
      svg.append(s('path', { d: `${line} L${x(last)},${y(0)} L${x(0)},${y(0)} Z`, class: 'trend-area' }),
        s('path', { d: line, class: 'trend-line' }));
    }
    svg.append(s('text', { x: L, y: H - 6, class: 'trend-tick' }, day(vals[0].at)));
    if (vals.length > 1) svg.append(s('text', { x: W - R, y: H - 6, class: 'trend-tick', 'text-anchor': 'end' }, day(vals[last].at)));
    svg.append(s('circle', { cx: x(last), cy: y(vals[last].overall), r: 4, class: 'trend-dot' }));
    const cross = s('line', { x1: 0, x2: 0, y1: T, y2: H - B, class: 'trend-cross', visibility: 'hidden' });
    const focus = s('circle', { cx: 0, cy: 0, r: 5, class: 'trend-focus', visibility: 'hidden' });
    const hit = s('rect', { x: L, y: T, width: W - L - R, height: H - T - B, class: 'trend-hit' });
    svg.append(cross, focus, hit);
    const show = (i) => {
      at = Math.max(0, Math.min(last, i));
      const p = vals[at];
      cross.setAttribute('x1', x(at));
      cross.setAttribute('x2', x(at));
      focus.setAttribute('cx', x(at));
      focus.setAttribute('cy', y(p.overall));
      cross.setAttribute('visibility', 'visible');
      focus.setAttribute('visibility', 'visible');
      append(clear(readout), [h('strong', {}, fmt(p.overall)), ` genel puan · ${day(p.at)} · tamlık ${fmt(p.completeness)}, doğruluk ${fmt(p.correctness)}, uyum ${fmt(p.compliance)}, kapsam ${fmt(p.coverage)}`]);
    };
    const hide = () => { cross.setAttribute('visibility', 'hidden'); focus.setAttribute('visibility', 'hidden'); };
    hit.addEventListener('pointermove', (ev) => {
      const box = svg.getBoundingClientRect();
      const px = ((ev.clientX - box.left) / box.width) * W;
      show(vals.length === 1 ? 0 : Math.round(((px - L) / (W - L - R)) * last));
    });
    hit.addEventListener('pointerleave', hide);
    svg.addEventListener('focus', () => show(at));
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowLeft') { ev.preventDefault(); show(at - 1); }
      if (ev.key === 'ArrowRight') { ev.preventDefault(); show(at + 1); }
    });
    clear(holder).append(svg);
    show(at);
    if (hadFocus) svg.focus(); else hide();
  }
  // Önce varsayılan genişlikle, sayfaya yerleşince ve boyut değişince gerçek genişlikle çizilir
  draw();
  let width = 0;
  const ro = new ResizeObserver(() => {
    if (!holder.isConnected) { ro.disconnect(); return; }
    if (Math.abs(holder.clientWidth - width) < 2) return;
    width = holder.clientWidth;
    draw();
  });
  ro.observe(holder);
  const table = h('details', { class: 'trend-table' }, h('summary', {}, 'Tablo olarak göster'),
    h('div', { class: 'tablewrap' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Gün', 'Genel', 'Tamlık', 'Doğruluk', 'Uyum', 'Kapsam'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, vals.slice().reverse().map((p) => h('tr', {}, h('td', {}, day(p.at)),
        ...['overall', 'completeness', 'correctness', 'compliance', 'coverage'].map((k) => h('td', { class: 'num' }, fmt(p[k])))))))));
  return h('div', { class: 'trend' }, readout, holder, table);
}

function meterList(metrics, dim) {
  if (!metrics.length) return h('p', { class: 'muted small' }, 'Bu boyutta ölçülen bir şey yok.');
  return h('ul', { class: 'meter-list', 'aria-label': 'Ölçümler' }, metrics.map((m) => {
    const tone = m.pct === null ? 'neutral' : m.pct >= 85 ? 'good' : m.pct >= 70 ? 'warn' : 'bad';
    const fill = h('span', { class: `meter-fill ${tone}` });
    fill.style.width = `${m.pct ?? 0}%`; // CSSOM: CSP ile uyumlu
    const name = m.rule ? h('a', { href: `#/saglik/kurallar/${m.rule}` }, m.label) : m.label;
    const foot = m.rule ? `${formatNumber(m.open)} açık bulgu${m.excepted ? `, ${formatNumber(m.excepted)} istisna` : ''} · ${formatNumber(m.total)} CI kapsamda`
      : `${formatNumber(m.good)} / ${formatNumber(m.total)}`;
    return h('li', { class: 'meter-row', dataset: { metric: m.id, dim } },
      h('div', { class: 'meter-name' }, name, h('div', { class: 'cell-sub' }, foot)),
      h('span', { class: `meter-track ${tone}`, role: 'meter', 'aria-valuemin': '0', 'aria-valuemax': '100',
        'aria-valuenow': String(m.pct ?? 0), 'aria-label': m.label }, fill),
      h('span', { class: 'meter-value' }, m.pct === null ? '—' : `%${fmt(m.pct)}`));
  }));
}

async function overviewTab(body) {
  const v = await healthView();
  if (!v.measured) {
    clear(body).append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Henüz ölçüm yok. '),
      'Görev çalışanı CMDB sağlığını saatte bir ölçer; bir kural çalışınca da hemen ölçer. ',
      v.can_admin ? h('a', { href: '#/saglik/kurallar/yeni' }, 'İlk kuralı tanımlayın') : null));
    return;
  }
  const f = v.findings;
  const hero = h('section', { class: 'panel health-hero', id: 'healthHero' },
    h('div', {}, h('div', { class: 'metric-label' }, 'Genel CMDB sağlığı'),
      h('div', { class: 'hero-number', id: 'healthOverall' }, fmt(v.overall)),
      h('div', { class: 'pills' }, gradePill(v.grade), pill(`${formatNumber(v.total_ci)} CI ölçüldü`, 'neutral'))),
    h('div', { class: 'health-hero-side' },
      h('p', { class: 'small muted' }, `Son ölçüm ${formatDateTime(v.measured_at)}. Puan dört boyutun ağırlıklı ortalamasıdır (tamlık %${v.weights.completeness ?? 30}, doğruluk %${v.weights.correctness ?? 30}, uyum %${v.weights.compliance ?? 20}, kapsam %${v.weights.coverage ?? 20}).`),
      v.stale ? h('p', { class: 'notice warn' }, 'Ölçüm iki saatten eski: görev çalışanı çalışıyor mu? Sistem durumuna bakın.') : null,
      h('div', { class: 'link-actions' },
        h('a', { class: 'btn secondary small', href: '#/saglik/bulgular' }, `${formatNumber(f.open)} açık bulgu`),
        f.triage ? h('a', { class: 'btn secondary small', href: '#/saglik/bulgular/ayiklama' }, `${formatNumber(f.triage)} sorumlusu reddetti`) : null,
        f.excepted ? h('a', { class: 'btn ghost small', href: '#/saglik/bulgular/istisna' }, `${formatNumber(f.excepted)} istisna`) : null)));
  const cards = h('div', { class: 'cards', id: 'healthDims' }, ['completeness', 'correctness', 'compliance', 'coverage'].map((d) => {
    const dim = v.dimensions[d];
    return metric({ label: dim.label, value: fmt(dim.score), foot: `${GRADE[dim.grade][0]} · ${DIM_HELP[d]}`,
      tone: GRADE[dim.grade][1] === 'neutral' ? '' : GRADE[dim.grade][1], href: `#/saglik/boyut/${d}`, id: `dim-${d}` });
  }));
  const dims = ['completeness', 'correctness', 'compliance', 'coverage'].map((d) => {
    const dim = v.dimensions[d];
    return h('section', { class: 'panel', id: `dimPanel-${d}`, tabindex: '-1' },
      h('h2', {}, dim.label, ' ', gradePill(dim.grade)), h('p', { class: 'small muted' }, DIM_HELP[d]), meterList(dim.metrics, d));
  });
  clear(body).append(hero, cards,
    h('section', { class: 'panel' }, h('h2', {}, 'Genel puanın eğilimi (son 30 gün)'), trendChart(v.trend)),
    h('div', { class: 'mini-grid' }, dims));
}

// ---------------------------------------------------------------------------------------------- kural penceresi
function multiSelect(id, options, selected, label) {
  return h('select', { id, multiple: true, size: '7', 'aria-label': label },
    options.map((o) => h('option', { value: o.name, selected: selected.includes(o.name) }, `${'  '.repeat(o.depth || 0)}${o.label}`)));
}
const picked = (sel) => [...sel.selectedOptions].map((o) => o.value);

export async function openRuleDialog(meta, onDone, existing = null) {
  const edit = Boolean(existing);
  const r = existing || {};
  const { channels } = edit ? { channels: [] } : await taskChannels();
  const template = edit ? null : h('select', { id: 'ruleTemplate', 'aria-describedby': 'ruleTemplateHelp' },
    h('option', { value: '' }, 'Boş kural'), meta.templates.map((t) => h('option', { value: t.id }, t.name)));
  const name = h('input', { type: 'text', id: 'ruleName', maxlength: '120', value: r.name || '', placeholder: 'Ör. Uygulamasız veritabanı' });
  const desc = h('textarea', { id: 'ruleDesc', rows: '2', maxlength: '2000', placeholder: 'Neden önemli? Bulgu ve görevde gösterilir.' });
  desc.value = r.description || '';
  const kind = h('select', { id: 'ruleKind', disabled: edit, 'aria-describedby': 'ruleKindHelp' },
    meta.kinds.map((k) => h('option', { value: k.id, selected: k.id === (r.check_kind || 'no_relation') }, k.label)));
  const kindHelp = h('p', { class: 'muted small', id: 'ruleKindHelp' });
  const cls = h('select', { id: 'ruleClass' }, meta.classes.map((x) => h('option',
    { value: x.name, selected: x.name === (r.scope_class || 'cmdb_ci_db_instance') }, `${'  '.repeat(x.depth)}${x.depth ? '└ ' : ''}${x.label}`)));
  const query = h('input', { type: 'text', id: 'ruleQuery', maxlength: '2000', class: 'mono', value: r.scope_query || '',
    placeholder: 'Ör. used_for=Production', 'aria-describedby': 'ruleQueryHelp' });
  const paramBox = h('div', { id: 'ruleParams' });
  const dimension = h('select', { id: 'ruleDim' }, meta.dimensions.map((d) => h('option', { value: d.id, selected: d.id === (r.dimension || 'completeness') }, d.label)));
  const severity = h('select', { id: 'ruleSev' }, meta.severities.map((d) => h('option', { value: d.id, selected: d.id === (r.severity || 'medium') }, d.label)));
  const interval = h('select', { id: 'ruleInterval' }, meta.intervals.map((d) => h('option', { value: String(d.id), selected: d.id === (r.interval_hours || 24) }, d.label)));
  const openTask = h('input', { type: 'checkbox', id: 'ruleOpenTask', checked: r.open_task ?? true });
  const taskTitle = h('input', { type: 'text', id: 'ruleTaskTitle', maxlength: '120', value: r.task_title || '', placeholder: 'Ör. Veritabanının ilişkilerini tamamlayın' });
  const taskInstr = h('textarea', { id: 'ruleTaskInstr', rows: '2', maxlength: '4000', placeholder: 'Sorumlunun ne yapacağı' });
  taskInstr.value = r.task_instructions || '';
  const due = h('input', { type: 'number', id: 'ruleDue', min: '1', max: '180', value: String(r.due_days || 30) });
  const exDays = h('input', { type: 'number', id: 'ruleExDays', min: '1', max: '730', value: String(r.exception_days || 180), 'aria-describedby': 'ruleExHelp' });
  const max = h('input', { type: 'number', id: 'ruleMax', min: '1', max: '50000', value: String(r.max_findings || 5000) });
  const channel = edit ? null : h('select', { id: 'ruleChannel' }, h('option', { value: '' }, 'Varsayılan kanal (görev açılırken)'),
    channels.filter((x) => x.lifecycle === 'active').map((x) => h('option', { value: x.name }, x.label_tr)));
  const taskBox = h('div', { id: 'ruleTaskBox' });
  const preview = h('div', { id: 'rulePreview', 'aria-live': 'polite' });
  const error = h('div', { id: 'ruleError' });
  const previewBtn = h('button', { class: 'btn secondary', type: 'button', id: 'rulePreviewBtn' }, 'Bulguları önizle');
  const save = h('button', { class: edit ? 'btn' : 'btn secondary', type: 'button', id: 'ruleSave' }, edit ? 'Kaydet' : 'Taslak kaydet');
  const activate = edit ? null : h('button', { class: 'btn', type: 'button', id: 'ruleActivate' }, 'Kaydet ve etkinleştir');
  let params = { ...(r.params || {}) };
  let readParams = () => ({});
  let previewed = false;
  const reset = () => { previewed = false; clear(preview); clear(error); };

  async function renderParams() {
    const k = kind.value;
    kindHelp.textContent = meta.kinds.find((x) => x.id === k)?.help || '';
    clear(paramBox);
    if (k === 'no_relation') {
      const rel = multiSelect('ruleRelated', meta.classes, params.related_classes || [], 'İlişkili olması beklenen sınıflar');
      const exc = multiSelect('ruleExclude', meta.classes, params.exclude_classes || [], 'Sayılmayacak sınıflar');
      paramBox.append(h('label', { class: 'field', for: 'ruleRelated' }, 'Hangi sınıflarla ilişkisi olmalı?'), rel,
        h('p', { class: 'muted small' }, 'Ctrl veya Shift ile birden çok seçin. Hiçbiri seçilmezse herhangi bir CI ile ilişki yeter. Üst sınıf alt sınıflarını kapsar.'),
        h('label', { class: 'field', for: 'ruleExclude' }, 'Sayılmayacak sınıflar (isteğe bağlı)'), exc,
        h('p', { class: 'muted small' }, 'Ör. veritabanının başka bir veritabanıyla ilişkisi "kullanılıyor" demek değildir.'));
      for (const el of [rel, exc]) el.addEventListener('change', reset);
      readParams = () => ({ related_classes: picked(rel), exclude_classes: picked(exc) });
    } else if (k === 'missing_fields') {
      const { items } = await qualityFields(cls.value);
      const fields = h('select', { id: 'ruleFields', multiple: true, size: '7', 'aria-label': 'Boş olmaması gereken alanlar' },
        items.map((f) => h('option', { value: f.name, selected: (params.fields || []).includes(f.name) }, f.label)));
      fields.addEventListener('change', reset);
      paramBox.append(h('label', { class: 'field', for: 'ruleFields' }, 'Boş olmaması gereken alanlar'), fields,
        h('p', { class: 'muted small' }, 'Seçilen alanlardan biri boşsa bulgu olur; bulgu hangi alanların boş olduğunu yazar.'));
      readParams = () => ({ fields: picked(fields) });
    } else if (k === 'not_seen_by_role') {
      const role = h('select', { id: 'ruleRole' }, meta.roles.map((x) => h('option', { value: x.id, selected: x.id === (params.role || 'Monitoring') }, x.label)));
      role.addEventListener('change', reset);
      paramBox.append(h('label', { class: 'field', for: 'ruleRole' }, 'Hangi kaynak rolü görmeli?'), role,
        h('p', { class: 'muted small' }, 'Kural ürüne değil role bağlıdır: izleme ürünü değişse de kural aynı kalır.'));
      readParams = () => ({ role: role.value });
    } else if (k === 'matches_condition') {
      const cond = h('input', { type: 'text', id: 'ruleCondition', class: 'mono', maxlength: '2000', value: params.condition || '',
        placeholder: 'Ör. install_status=7^operational_status=1' });
      cond.addEventListener('input', reset);
      paramBox.append(h('label', { class: 'field', for: 'ruleCondition' }, 'Bulgu koşulu'), cond,
        h('p', { class: 'muted small' }, 'Kapsamdaki CI bu koşula da uyuyorsa bulgu olur (ServiceNow koşul sözdizimi).'));
      readParams = () => ({ condition: cond.value.trim() });
    } else {
      paramBox.append(h('p', { class: 'muted small' }, 'Parametre yok: izinli ilişki kuralları Veri Modeli sayfasındadır.'));
      readParams = () => ({});
    }
  }
  const showTask = () => { taskBox.hidden = !openTask.checked; };
  openTask.addEventListener('change', showTask);
  kind.addEventListener('change', () => { params = {}; reset(); renderParams(); });
  cls.addEventListener('change', () => { reset(); if (kind.value === 'missing_fields') { params = readParams(); renderParams(); } });
  query.addEventListener('input', reset);
  template?.addEventListener('change', async () => {
    const t = meta.templates.find((x) => x.id === template.value);
    if (!t) return;
    name.value = t.name; desc.value = t.description; kind.value = t.check_kind; cls.value = t.scope_class;
    query.value = t.scope_query; dimension.value = t.dimension; severity.value = t.severity;
    openTask.checked = t.open_task ?? true; taskTitle.value = t.task_title; taskInstr.value = t.task_instructions;
    params = { ...t.params };
    showTask();
    reset();
    await renderParams();
  });

  async function runPreview() {
    clear(error);
    previewBtn.disabled = true;
    previewBtn.setAttribute('aria-busy', 'true');
    try {
      const p = await rulePreview({ check_kind: kind.value, params: readParams(), scope_class: cls.value,
        scope_query: query.value.trim(), rule: r.sys_id || null });
      if (!p.valid) {
        clear(preview);
        problem(error, `Kural geçersiz: ${p.problem}`);
        return null;
      }
      const b = p.by_basis;
      append(clear(preview), [
        p.inconclusive ? h('p', { class: 'notice warn', role: 'status' }, p.inconclusive) : null,
        h('p', { class: 'notice', role: 'status' }, h('strong', {}, `${formatNumber(p.matched)} bulgu`),
          ` (kapsamdaki ${formatNumber(p.in_scope)} CI'dan). `, p.matched ? `Görevler ${formatNumber(p.people)} kişiye ve ${formatNumber(p.groups)} gruba gider.` : 'Şu an kural bulgu üretmiyor.',
          p.partial ? ' Önizleme ilk 5000 adayla sınırlıdır.' : ''),
        p.matched ? kv([['Sahibine', formatNumber(b.owned_by)], ['Yönetenine', formatNumber(b.managed_by)],
          ['Destek grubuna', formatNumber(b.support_group)], ['Atanmamış', formatNumber(b.unassigned)],
          p.current_findings ? ['Şu an açık veya istisna', formatNumber(p.current_findings)] : null]) : null,
        p.sample.length ? h('ul', { class: 'mini', id: 'ruleSample', 'aria-label': 'Örnek bulgular' }, p.sample.map((x) => h('li', {},
          h('strong', {}, x.name), h('div', { class: 'cell-sub' }, `${x.why} · ${BASIS_TR[x.basis]}: ${x.user || x.group || '—'}`)))) : null]);
      previewed = true;
      return p;
    } catch (err) {
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
  for (const el of [name, taskTitle, due, exDays, max]) el.addEventListener('input', () => { el.removeAttribute('aria-invalid'); clear(error); });
  function payload() {
    return { name: name.value.trim(), description: desc.value.trim(), params: readParams(), scope_class: cls.value,
      scope_query: query.value.trim(), dimension: dimension.value, severity: severity.value, open_task: openTask.checked,
      task_title: taskTitle.value.trim() || name.value.trim(), task_instructions: taskInstr.value.trim(),
      due_days: Number(due.value), exception_days: Number(exDays.value), max_findings: Number(max.value),
      interval_hours: Number(interval.value) };
  }
  async function submit(button, active) {
    if (name.value.trim().length < 3) return invalid(name, 'Kurala bir ad verin (en az 3 karakter).');
    if (openTask.checked && taskTitle.value.trim().length < 3) return invalid(taskTitle, 'Görev başlığını yazın.');
    for (const [el, lo, hi, label] of [[due, 1, 180, 'Süre'], [exDays, 1, 730, 'İstisna süresi'], [max, 1, 50000, 'Bulgu sınırı']]) {
      const n = Number(el.value);
      if (!Number.isInteger(n) || n < lo || n > hi) return invalid(el, `${label} ${lo} ile ${hi} arasında olmalı.`);
    }
    if (!previewed && !(await runPreview())) return false;
    button.disabled = true;
    try {
      const res = edit ? await ruleUpdate(r.sys_id, payload())
        : await ruleCreate({ ...payload(), check_kind: kind.value, channel: channel.value || null, activate: active });
      closeOverlay();
      toast(res.message);
      onDone(res.sys_id || r.sys_id);
      return true;
    } catch (err) {
      button.disabled = false;
      problem(error, err.message);
      return false;
    }
  }
  save.addEventListener('click', () => submit(save, false));
  activate?.addEventListener('click', () => submit(activate, true));
  taskBox.append(
    h('label', { class: 'field', for: 'ruleTaskTitle' }, 'Görev başlığı'), taskTitle,
    h('p', { class: 'muted small' }, 'Her görevin başlığı bu metin ve CI adıdır; açıklamasında bulgunun nedeni yazar.'),
    h('label', { class: 'field', for: 'ruleTaskInstr' }, 'Yönerge'), taskInstr,
    h('label', { class: 'field', for: 'ruleDue' }, 'Süre (gün)'), due,
    channel ? h('label', { class: 'field', for: 'ruleChannel' }, 'Kanal') : null, channel,
    h('label', { class: 'field', for: 'ruleExDays' }, 'İstisna süresi (gün)'), exDays,
    h('p', { class: 'muted small', id: 'ruleExHelp' }, 'Sorumlu "Geçerli değil" derse bulgu bu süre boyunca istisna olur, sonra yeniden açılır.'));
  dialog({
    title: edit ? `Kuralı düzenle: ${r.name}` : 'Yeni kalite kuralı',
    body: [
      edit ? null : h('p', { class: 'muted small' }, 'Kural, kapsamdaki CI\'lardan hangilerinin bulgu olduğunu söyler. Görev çalışanı kuralı düzenli değerlendirir; bulgunun sorumlusuna (sahip → yöneten → destek grubu) tek görev açar, bulgu giderilince görev kendiliğinden kapanır.'),
      template ? h('label', { class: 'field', for: 'ruleTemplate' }, 'Şablondan başla') : null, template,
      template ? h('p', { class: 'muted small', id: 'ruleTemplateHelp' }, 'Hazır kurallar: ilişkisiz veritabanı, sahipsiz üretim CI\'ı, izlenmeyen sunucu ve diğerleri. Seçince alanlar dolar; değiştirebilirsiniz.') : null,
      h('label', { class: 'field', for: 'ruleName' }, 'Kural adı'), name,
      h('label', { class: 'field', for: 'ruleDesc' }, 'Neden önemli?'), desc,
      h('label', { class: 'field', for: 'ruleKind' }, 'Ne denetlenecek?'), kind, kindHelp,
      h('label', { class: 'field', for: 'ruleClass' }, 'Kapsam: sınıf'), cls,
      h('label', { class: 'field', for: 'ruleQuery' }, 'Kapsam: koşul (isteğe bağlı)'), query,
      h('p', { class: 'muted small', id: 'ruleQueryHelp' }, 'ServiceNow koşul sözdizimi; boşsa sınıftaki bütün CI\'lar (emekliler hariç). ', h('a', { href: '#/kilavuz/saglik', onclick: closeOverlay }, 'Kılavuz')),
      paramBox,
      h('div', { class: 'task-actions' }, previewBtn), preview,
      h('label', { class: 'field', for: 'ruleDim' }, 'Sağlık boyutu'), dimension,
      h('label', { class: 'field', for: 'ruleSev' }, 'Önem'), severity,
      h('label', { class: 'field', for: 'ruleInterval' }, 'Ne sıklıkla değerlendirilsin?'), interval,
      h('label', { class: 'field', for: 'ruleMax' }, 'Bulgu sınırı'), max,
      h('p', { class: 'muted small' }, 'Bulgu sayısı bunu aşarsa değerlendirme hiçbir şeyi değiştirmeden durur.'),
      h('label', { class: 'check-row', for: 'ruleOpenTask' }, openTask, ' Bulgunun sorumlusuna görev aç'),
      taskBox, error,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save, activate].filter(Boolean),
    focus: edit ? name : template,
  });
  showTask();
  await renderParams();
}

// ---------------------------------------------------------------------------------------------- kurallar sekmesi
async function rulesTab(body, { onRoute }) {
  const meta = await qualityRules();
  let selected = null;
  const list = h('div', { id: 'ruleList' });
  const add = meta.can_admin ? h('button', { class: 'btn', type: 'button', id: 'ruleNew', onclick: () => create() }, 'Yeni kural') : null;
  clear(body).append(h('div', { class: 'toolbar' }, h('p', { class: 'muted small' }, 'Kalite ve uyum kuralları: her biri kapsamdaki CI\'lardan hangilerinin bulgu olduğunu söyler. Görev çalışanı kuralı düzenli değerlendirir, bulgunun sorumlusuna görev açar.'), add), list);

  async function load() {
    Object.assign(meta, await qualityRules());
    clear(list).append(dataTable({
      label: 'Kurallar', idOf: (x) => x.sys_id, selectedId: () => selected,
      empty: meta.can_admin ? 'Henüz kural yok. "Yeni kural" ile şablondan başlayın.' : 'Henüz kural yok.',
      columns: [
        { key: 'name', label: 'Kural', render: (x) => h('div', {}, h('strong', {}, x.name), h('div', { class: 'cell-sub' }, x.check_kind_tr)) },
        { key: 'scope', label: 'Kapsam', sortable: false, render: (x) => h('div', {}, x.scope_class_tr, x.scope_query ? h('div', { class: 'cell-sub mono' }, x.scope_query) : null) },
        { key: 'dimension_tr', label: 'Boyut', render: (x) => h('div', {}, x.dimension_tr, h('div', { class: 'cell-sub' }, pill(x.severity_tr, SEV_TONE[x.severity]))) },
        { key: 'open', label: 'Açık bulgu', render: (x) => h('div', {}, formatNumber(x.open),
          x.triage ? h('div', { class: 'cell-sub' }, `${formatNumber(x.triage)} sorumlusu reddetti`) : null) },
        { key: 'last_run_at', label: 'Son değerlendirme', render: (x) => (x.last_run ? h('div', {}, pill(x.last_run.status_tr, RUN_TONE[x.last_run.status]),
          h('div', { class: 'cell-sub' }, formatDateTime(x.last_run.at))) : '—') },
        { key: 'state', label: 'Durum', render: (x) => pill(x.state_tr, STATE_TONE[x.state]) },
      ],
      rows: meta.items, onOpen: (x, tr) => open(x.sys_id, tr),
    }));
  }

  function changeState(x, target) {
    const t = TRANSITION[target];
    reasonDialog({ title: `${t.label}: ${x.name}`, intro: t.help, button: t.label, placeholder: 'Ör. ilk uyum taraması, TALEP-21',
      run: async (reason) => { const res = await ruleUpdate(x.sys_id, { state: target, reason }); toast(res.message); await refresh(x.sys_id); } });
  }
  function runNow(x) {
    reasonDialog({ title: `Şimdi değerlendir: ${x.name}`, intro: 'Sıklık beklenmeden değerlendirilir: yeni bulgular açılır, giderilenler kapanır, açık bulgulara görev açılır.',
      button: 'Değerlendir', placeholder: 'Ör. düzeltmeler sonrası kontrol',
      run: async (reason) => { const res = await ruleRun(x.sys_id, reason); toast(res.message); await refresh(x.sys_id); } });
  }
  async function refresh(id) { await load(); await open(id); }

  async function open(id, opener) {
    selected = id;
    onRoute(`kurallar/${id}`);
    let x;
    try { x = await qualityRule(id); } catch (err) { toast(err.message); return; }
    const footer = [];
    if (x.can_admin) {
      if (x.state === 'active') footer.push(h('button', { class: 'btn', type: 'button', id: 'ruleRunNow', onclick: () => runNow(x) }, 'Şimdi değerlendir'));
      for (const target of x.transitions) {
        footer.push(h('button', { class: target === 'active' ? 'btn' : 'btn secondary', type: 'button', dataset: { transition: target },
          onclick: () => changeState(x, target) }, TRANSITION[target].label));
      }
      if (x.state === 'draft' || x.state === 'paused') footer.push(h('button', { class: 'btn secondary', type: 'button', id: 'ruleEdit', onclick: () => openRuleDialog(meta, refresh, x) }, 'Düzenle'));
    }
    const p = x.params || {};
    const label = (n) => meta.classes.find((c) => c.name === n)?.label || n;
    const paramText = x.check_kind === 'no_relation' ? `İlişkili olmalı: ${(p.related_classes || []).map(label).join(', ') || 'herhangi bir CI'}${p.exclude_classes?.length ? ` · sayılmaz: ${p.exclude_classes.map(label).join(', ')}` : ''}`
      : x.check_kind === 'missing_fields' ? `Boş olmamalı: ${(p.fields || []).join(', ')}`
        : x.check_kind === 'not_seen_by_role' ? `Görmesi gereken rol: ${meta.roles.find((r2) => r2.id === p.role)?.label || p.role}`
          : x.check_kind === 'matches_condition' ? `Koşul: ${p.condition}` : 'İzinli ilişki kuralları';
    const lastBad = x.runs[0] && ['failed', 'inconclusive'].includes(x.runs[0].status) ? x.runs[0] : null;
    openDetail({
      kicker: `Kural · ${x.dimension_tr}`, title: x.name, opener,
      onClose: () => { selected = null; onRoute('kurallar'); },
      body: [
        h('div', { class: 'pills' }, pill(x.state_tr, STATE_TONE[x.state]), pill(x.severity_tr, SEV_TONE[x.severity]), pill(x.interval_tr, 'neutral')),
        x.state === 'draft' ? h('p', { class: 'notice' }, 'Taslak: değerlendirilmez. Bulguları önizleyip etkinleştirin.') : null,
        lastBad ? h('p', { class: `notice ${lastBad.status === 'failed' ? 'bad' : 'warn'}` }, `Son değerlendirme ${lastBad.status_tr.toLowerCase()}: ${lastBad.note || 'neden yok'}`) : null,
        x.description ? section('Neden önemli', h('p', { class: 'small' }, x.description)) : null,
        section('Kural', kv([['Tür', x.check_kind_tr], ['Ölçüt', paramText], ['Sınıf', x.scope_class_tr],
          ['Koşul', x.scope_query ? h('span', { class: 'mono' }, x.scope_query) : 'yok'], ['Sıklık', x.interval_tr],
          ['Sonraki değerlendirme', x.next_run_at ? formatDateTime(x.next_run_at) : '—'], ['Bulgu sınırı', formatNumber(x.max_findings)]])),
        section('Görev', x.open_task ? kv([['Başlık', x.task_title], ['Yönerge', x.task_instructions || '—'], ['Süre', `${x.due_days} gün`],
          ['İstisna süresi', `${x.exception_days} gün`], ['Kanal', x.channel || 'varsayılan kanal']]) : h('p', { class: 'small muted' }, 'Bu kural görev açmaz; bulgular yalnızca panoda ve listede görünür.')),
        section('Bulgular', kv([['Açık', formatNumber(x.open)], ['İstisna', formatNumber(x.excepted)], ['Sorumlusu reddetti', formatNumber(x.triage)]]),
          h('p', { class: 'small' }, h('a', { href: `#/saglik/bulgular/kural/${x.sys_id}`, id: 'ruleFindings' }, 'Bu kuralın bulguları'))),
        section('Değerlendirmeler', x.runs.length ? h('ul', { class: 'mini', id: 'ruleRuns' }, x.runs.map((rn) => h('li', {},
          h('strong', {}, rn.trigger === 'manual' ? 'Elle · ' : 'Zamanlama · ', pill(rn.status_tr, RUN_TONE[rn.status])),
          h('div', { class: 'cell-sub' }, [formatDateTime(rn.started_at), rn.requested_by,
            rn.status === 'done' ? `${formatNumber(rn.in_scope)} kapsamda, ${formatNumber(rn.matched)} bulgu (${formatNumber(rn.opened)} yeni, ${formatNumber(rn.resolved)} giderildi), ${formatNumber(rn.tasks_opened)} görev açıldı` : null,
            rn.note].filter(Boolean).join(' · ')))))
          : h('p', { class: 'muted small' }, 'Henüz değerlendirilmedi.')),
        section('Kayıt', kv([['Oluşturan', `${x.created_by} · ${formatDateTime(x.created_at)}`],
          x.updated_by ? ['Son değişiklik', `${x.updated_by} · ${formatDateTime(x.updated_at)}`] : null])),
      ],
      footer,
    });
  }
  function create() { if (meta.can_admin) openRuleDialog(meta, (id) => refresh(id)); }
  await load();
  return { open, create };
}

// ---------------------------------------------------------------------------------------------- bulgular sekmesi
async function findingsTab(body, { onRoute, filter }) {
  let selected = null;
  let state = filter.state || 'current';
  let q = '';
  let dim = '';
  const list = h('div', { id: 'findingList' });
  const search = h('input', { type: 'search', class: 'searchbar', id: 'findingSearch', placeholder: 'CI adı', 'aria-label': 'Bulgu ara' });
  const stateSel = h('select', { id: 'findingState', 'aria-label': 'Durum' }, [['current', 'Açık ve istisna'], ['open', 'Açık'], ['excepted', 'İstisna'],
    ['resolved', 'Giderilen'], ['all', 'Hepsi']].map(([v, l]) => h('option', { value: v, selected: v === state }, l)));
  const dimSel = h('select', { id: 'findingDim', 'aria-label': 'Boyut' }, h('option', { value: '' }, 'Bütün boyutlar'),
    [['completeness', 'Tamlık'], ['correctness', 'Doğruluk'], ['compliance', 'Uyum'], ['coverage', 'Kapsam']].map(([v, l]) => h('option', { value: v }, l)));
  const scope = filter.rule || filter.triage ? h('p', { class: 'notice', id: 'findingScope' },
    filter.triage ? 'Sorumlusunun "bu benim değil" dediği bulgular. CI\'ın sahibini düzeltince görev yeni sorumluya kendiliğinden açılır; isterseniz bulguyu yeniden açın.'
      : 'Yalnızca bir kuralın bulguları. ', filter.rule ? h('a', { href: `#/saglik/kurallar/${filter.rule}` }, 'Kuralı aç') : null, ' · ',
    h('a', { href: '#/saglik/bulgular' }, 'Bütün bulgular')) : null;
  append(clear(body), [scope, h('div', { class: 'toolbar' }, search, stateSel, dimSel), list]);

  async function load() {
    const res = await findingsList({ state, q, dimension: dim || null, rule: filter.rule || null, triage: filter.triage || false });
    clear(list).append(dataTable({
      label: 'Bulgular', idOf: (f) => f.sys_id, selectedId: () => selected,
      empty: state === 'current' ? 'Açık bulgu yok.' : 'Bu görünümde bulgu yok.',
      columns: [
        { key: 'ci', label: 'CI', render: (f) => h('div', {}, h('strong', {}, f.ci.name), h('div', { class: 'cell-sub' }, f.ci.class_label)), sortValue: (f) => f.ci.name },
        { key: 'rule', label: 'Kural ve neden', sortable: false, render: (f) => h('div', {}, f.rule.name, h('div', { class: 'cell-sub' }, f.text)) },
        { key: 'severity', label: 'Önem', render: (f) => pill(f.rule.severity_tr, SEV_TONE[f.rule.severity]), sortValue: (f) => ['low', 'medium', 'high', 'critical'].indexOf(f.rule.severity) },
        { key: 'task', label: 'Görev', sortable: false, render: (f) => (f.task ? h('div', {}, f.task.number, h('div', { class: 'cell-sub' }, f.task.assignee || 'Atanmamış')) : '—') },
        { key: 'state', label: 'Durum', render: (f) => h('div', { class: 'pills' }, pill(f.state_tr, FINDING_TONE[f.state]), f.triage ? pill('Sorumlusu reddetti', 'bad') : null) },
      ],
      rows: res.items, onOpen: (f, tr) => open(f.sys_id, tr),
    }));
  }
  let timer = null;
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { q = search.value.trim(); load(); }, 250); });
  stateSel.addEventListener('change', () => { state = stateSel.value; load(); });
  dimSel.addEventListener('change', () => { dim = dimSel.value; load(); });

  async function open(id, opener) {
    selected = id;
    onRoute(`bulgular/${id}`);
    let f;
    try { f = await findingDetail(id); } catch (err) { toast(err.message); return; }
    const refresh = async () => { await load(); await open(id); };
    const footer = [];
    if (f.can_except) {
      footer.push(h('button', { class: 'btn secondary', type: 'button', id: 'findingExcept', onclick: () => {
        const days = h('input', { type: 'number', id: 'qDays', min: '1', max: '730', value: '180' });
        reasonDialog({ title: `İstisna: ${f.ci.name}`, intro: 'Bu durum bilinçli olarak kabul edilir: bulgu süre boyunca istisna olur, açık görevi iptal edilir. Süre dolunca bulgu yeniden açılır.',
          button: 'İstisna tanı', placeholder: 'Ör. göç bitene kadar bilinçli, TALEP-33',
          extra: [h('label', { class: 'field', for: 'qDays' }, 'Süre (gün)'), days],
          run: async (reason) => { const res = await findingException(f.sys_id, reason, Number(days.value) || 180); toast(res.message); await refresh(); } });
      } }, 'İstisna tanı'));
    }
    if (f.can_reopen) {
      footer.push(h('button', { class: 'btn secondary', type: 'button', id: 'findingReopen', onclick: () => reasonDialog({
        title: `Yeniden aç: ${f.ci.name}`, intro: 'Bulgu açılır; kuralın bir sonraki değerlendirmesinde sorumluya görev gider.', button: 'Yeniden aç',
        placeholder: 'Ör. sahip düzeltildi, istisna kararı geri alındı',
        run: async (reason) => { const res = await findingReopen(f.sys_id, reason); toast(res.message); await refresh(); } }) }, 'Yeniden aç'));
    }
    openDetail({
      kicker: `Bulgu · ${f.rule.dimension_tr}`, title: f.ci.name, opener,
      onClose: () => { selected = null; onRoute(filter.rule ? `bulgular/kural/${filter.rule}` : filter.triage ? 'bulgular/ayiklama' : 'bulgular'); },
      body: [
        h('div', { class: 'pills' }, pill(f.state_tr, FINDING_TONE[f.state]), pill(f.rule.severity_tr, SEV_TONE[f.rule.severity]),
          f.triage ? pill('Sorumlusu reddetti', 'bad') : null),
        h('p', { class: 'notice', id: 'findingText' }, f.text),
        f.triage ? h('p', { class: 'notice warn' }, 'Sorumlu bu görevin kendisine ait olmadığını söyledi. CI\'ın sahibini düzeltince görev yeni sorumluya kendiliğinden açılır.') : null,
        f.exception ? h('p', { class: 'notice' }, `İstisna ${formatDateTime(f.exception.until)} tarihine kadar · ${f.exception.by}: ${f.exception.note || ''}`) : null,
        section('Bulgu', kv([['CI', h('a', { href: `#/ci/${f.ci.sys_id}` }, f.ci.name)], ['Sınıf', f.ci.class_label],
          ['Kural', h('a', { href: `#/saglik/kurallar/${f.rule.sys_id}` }, f.rule.name)], ['Neden önemli', f.rule.why || '—'],
          ['İlk görülme', formatDateTime(f.first_seen_at)], ['Son görülme', formatDateTime(f.last_seen_at)],
          f.resolved_at ? ['Giderildi', formatDateTime(f.resolved_at)] : null])),
        section('Görevler', f.tasks.length ? h('ul', { class: 'mini', id: 'findingTasks' }, f.tasks.map((t) => h('li', {},
          h('a', { href: `#/gorevler/${t.sys_id}` }, t.number), ' ', pill(t.state === 'open' ? 'Açık' : (t.outcome === 'resolved' ? 'Bulgu giderildi' : 'Kapalı'), t.state === 'open' ? 'warn' : 'neutral'),
          h('div', { class: 'cell-sub' }, [t.assignee || 'Atanmamış', formatDateTime(t.created_at), t.note].filter(Boolean).join(' · ')))))
          : h('p', { class: 'muted small' }, 'Görev yok (kural görev açmıyor, bulgu istisnada veya sorumlu ayıklamada).')),
      ],
      footer,
    });
  }
  await load();
  return { open };
}

// ---------------------------------------------------------------------------------------------- sayfa
const VIEWS = [['overview', 'Genel bakış'], ['rules', 'Kurallar'], ['findings', 'Bulgular']];
const SLUG = { overview: '', rules: 'kurallar', findings: 'bulgular' };

export async function renderHealth(view, param) {
  const { mode } = await backend();
  setHeader({ title: 'CMDB Sağlığı', crumbs: [['CMDB Sağlığı']],
    sub: 'CMDB ne kadar eksiksiz, doğru, kurallara uygun ve kapsamlı? Kurallar bulgu üretir; bulgunun sorumlusuna görev gider, düzeltilince görev kendiliğinden kapanır.' });
  if (mode !== 'api') {
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '), 'Sağlık panosu ve kurallar canlı API ile çalışır.'));
    return {};
  }
  const tabBox = h('div', { id: 'healthTabs' });
  const body = h('div', { id: 'healthBody' });
  view.append(tabBox, body);
  let active = null;
  let ctl = null;
  let filterKey = '';
  const route = (p) => { const want = `#/saglik${p ? `/${p}` : ''}`; if (location.hash !== want) history.replaceState(null, '', want); };

  async function show(id, filter = {}) {
    const key = JSON.stringify(filter);
    if (active === id && key === filterKey && ctl) return;
    active = id;
    filterKey = key;
    clear(tabBox).append(tabs(VIEWS.map(([v, l]) => ({ id: v, label: l })), id, (v) => { closeDetail({ silent: true }); route(SLUG[v]); show(v); }, 'Sağlık görünümleri'));
    if (id === 'overview') { ctl = {}; await overviewTab(body); return; }
    if (id === 'rules') { ctl = await rulesTab(body, { onRoute: route }); return; }
    ctl = await findingsTab(body, { onRoute: route, filter });
  }

  async function update(p) {
    const parts = (p || '').split('/').filter(Boolean);
    if (parts[0] === 'kurallar') {
      await show('rules');
      if (parts[1] === 'yeni') { closeDetail({ silent: true }); ctl.create(); return; }
      if (SYSID.test(parts[1] || '')) { await ctl.open(parts[1]); return; }
      closeDetail({ silent: true });
      return;
    }
    if (parts[0] === 'bulgular') {
      const filter = parts[1] === 'kural' && SYSID.test(parts[2] || '') ? { rule: parts[2] }
        : parts[1] === 'ayiklama' ? { triage: true } : parts[1] === 'istisna' ? { state: 'excepted' } : {};
      await show('findings', filter);
      if (SYSID.test(parts[1] || '')) { await ctl.open(parts[1]); return; }
      closeDetail({ silent: true });
      return;
    }
    closeDetail({ silent: true });
    await show('overview');
    if (parts[0] === 'boyut' && parts[1]) document.getElementById(`dimPanel-${parts[1]}`)?.focus();
  }
  await update(param);
  return { update };
}
