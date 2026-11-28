// Şema ve Değişiklikler (F1-11, ADR-0015): metadata değişiklik setleri — taslak → onay → yayın.
// Hazırlayan onaylayamaz (API + veritabanı). Yayın, metadata/changes/ altına PR ile eklenecek dosyayı üretir.
import { h, clear, dataTable, formatDateTime, kv, pill, section } from '../lib/dom.js';
import { closeDetail, closeOverlay, dialog, openDetail, setHeader, toast } from '../lib/shell.js';
import { backend, changeSetAction, changeSetDetail, changeSets, createChangeSet, schemaCatalog } from '../lib/data.js';

const STATE_TONE = { draft: 'neutral', in_review: 'warn', approved: 'info', published: 'good', rejected: 'bad' };
const TYPE_TR = { string: 'Metin', integer: 'Tam sayı', decimal: 'Ondalık', boolean: 'Evet/hayır', reference: 'Referans',
  glide_date: 'Tarih', glide_date_time: 'Tarih-saat' };
const SENS_TR = { public: 'Genel', internal: 'İç', confidential: 'Gizli', restricted: 'Kısıtlı' };

let uid = 0;
function field(label, control, hint) {
  const id = control.id || `sf-${++uid}`;
  control.id = id;
  return h('div', { class: 'edit-row' }, h('label', { class: 'field', for: id }, label), control,
    hint ? h('div', { class: 'muted small' }, hint) : null);
}
const select = (options, value) => {
  const el = h('select', {}, options.map(([v, l]) => h('option', { value: v }, l)));
  if (value !== undefined) el.value = value;
  return el;
};
const input = (attrs = {}) => h('input', { type: 'text', ...attrs });

/** Bir işlemin formu: işlem türüne göre alanlar; ``read()`` API'ye gidecek işlemi döner.
 *  ``preset``: { op, table } — Veri Modeli sayfasından "bu sınıfa alan ekle" ile gelindiğinde. */
function opRow(cat, onRemove, preset = null) {
  const classOpts = cat.classes.map((c) => [c.name, `${c.label_tr} (${c.name})`]);
  const byName = Object.fromEntries(cat.classes.map((c) => [c.name, c]));
  const kind = select(cat.operations.map((o) => [o.op, o.label]), preset?.op || 'add_field');
  kind.setAttribute('aria-label', 'İşlem türü');
  const body = h('div', { class: 'edit-grid' });
  let read = () => null;

  function classAndField(filter, onField) {
    const cls = select(classOpts, 'cmdb_ci');
    const fld = h('select', {});
    const fill = () => {
      clear(fld);
      const own = (byName[cls.value]?.fields || []).filter(filter);
      own.forEach((f) => fld.append(h('option', { value: f.name }, `${f.label_tr} (${f.name})`)));
      if (!own.length) fld.append(h('option', { value: '' }, '— bu sınıfta uygun alan yok —'));
      onField?.(own.find((f) => f.name === fld.value));
    };
    cls.addEventListener('change', fill);
    fld.addEventListener('change', () => onField?.((byName[cls.value]?.fields || []).find((f) => f.name === fld.value)));
    fill();
    return { cls, fld };
  }

  function paint() {
    clear(body);
    const op = kind.value;
    if (op === 'add_field') {
      const cls = select(classOpts, preset?.table && byName[preset.table] ? preset.table : 'cmdb_ci_server');
      const el = input({ placeholder: 'u_yedekleme_politikasi', maxlength: '62' });
      const label = input({ maxlength: '80' });
      const type = select(cat.field_types.map((t) => [t, TYPE_TR[t] || t]), 'string');
      const len = h('input', { type: 'number', min: '1', max: '4000', value: '255' });
      const sens = select(cat.sensitivities.map((s) => [s, SENS_TR[s] || s]), 'internal');
      const ref = select(cat.reference_tables.map((t) => [t.name, t.label_tr]));
      const choice = h('input', { type: 'checkbox' });
      body.append(field('Sınıf', cls, 'Alan alt sınıflara da kalıtılır.'), field('Alan adı', el, 'u_ ile başlar (ServiceNow özel alan kuralı).'),
        field('Türkçe etiket', label), field('Tip', type), field('Uzunluk (metin)', len), field('Hassasiyet', sens),
        field('Referans tablosu', ref), h('label', { class: 'choice' }, choice, h('span', {}, 'Seçim listeli (metin)')));
      read = () => ({ op, table: cls.value, element: el.value.trim(), payload: {
        type: type.value, label_tr: label.value.trim(), sensitivity: sens.value,
        ...(type.value === 'string' ? { max_length: Number(len.value) || 255, ...(choice.checked ? { choice: true } : {}) } : {}),
        ...(type.value === 'reference' ? { reference: ref.value } : {}) } });
    } else if (op === 'modify_field') {
      const label = input({ maxlength: '80' });
      const sens = select(cat.sensitivities.map((s) => [s, SENS_TR[s] || s]));
      const len = h('input', { type: 'number', min: '1', max: '4000' });
      let cur = null;
      const { cls, fld } = classAndField(() => true, (f) => {
        cur = f;
        label.value = f?.label_tr || ''; sens.value = f?.sensitivity || 'internal'; len.value = f?.max_length || '';
      });
      body.append(field('Sınıf', cls), field('Alan (bu sınıfta tanımlı)', fld), field('Türkçe etiket', label),
        field('Hassasiyet', sens), field('Uzunluk (yalnız artırılabilir)', len));
      read = () => {
        const payload = {};
        if (cur && label.value.trim() !== cur.label_tr) payload.label_tr = label.value.trim();
        if (cur && sens.value !== cur.sensitivity) payload.sensitivity = sens.value;
        if (cur && len.value && Number(len.value) !== Number(cur.max_length)) payload.max_length = Number(len.value);
        return { op, table: cls.value, element: fld.value, payload };
      };
    } else if (op === 'deprecate_field') {
      const { cls, fld } = classAndField((f) => f.storage === 'jsonb' && !f.deprecated);
      body.append(field('Sınıf', cls), field('Alan', fld, 'Değerler kalır; alan düzenlemeden gizlenir.'));
      read = () => ({ op, table: cls.value, element: fld.value, payload: {} });
    } else if (op === 'add_choice') {
      const { cls, fld } = classAndField((f) => f.choice);
      const value = input({ maxlength: '40' });
      const label = input({ maxlength: '80' });
      body.append(field('Sınıf', cls), field('Seçim listeli alan', fld), field('Değer', value), field('Türkçe etiket', label));
      read = () => ({ op, table: cls.value, element: fld.value, payload: { value: value.value.trim(), label_tr: label.value.trim() } });
    } else if (op === 'add_class') {
      const name = input({ placeholder: 'u_cmdb_ci_kiosk', maxlength: '62' });
      const parent = select(classOpts, 'cmdb_ci_hardware');
      const label = input({ maxlength: '80' });
      body.append(field('Sınıf adı', name, 'u_ ile başlar.'), field('Üst sınıf', parent), field('Türkçe etiket', label));
      read = () => ({ op, table: name.value.trim(), element: null, payload: { parent: parent.value, label_tr: label.value.trim() } });
    } else if (op === 'add_data_source') {
      const name = input({ placeholder: 'KesifB', maxlength: '64' });
      const label = input({ maxlength: '80' });
      const role = select(cat.roles.map((r) => [r.name, r.label_tr]), 'Discovery');
      const conn = input({ placeholder: 'kesif_b', maxlength: '64' });
      body.append(field('Veri kaynağı adı', name), field('Türkçe etiket', label), field('Rol', role, 'Kurallar role bağlıdır; ürün adı kurala girmez.'),
        field('Bağlayıcı', conn), h('p', { class: 'muted small' }, 'Yeni kaynak her zaman gölge modda başlar: değer yazmaz, CI açmaz.'));
      read = () => ({ op, table: 'ingest.data_source', element: name.value.trim(), payload: { role: role.value, label_tr: label.value.trim(),
        ...(conn.value.trim() ? { connector: conn.value.trim() } : {}) } });
    } else if (op === 'set_source_lifecycle') {
      const src = select(cat.sources.map((s) => [s.name, `${s.label_tr} · ${s.lifecycle_label} · sıra ${s.role_rank}`]));
      const target = h('select', {});
      const rank = h('input', { type: 'number', min: '1', max: '99', placeholder: 'değişmez' });
      const fill = () => {
        clear(target);
        const s = cat.sources.find((x) => x.name === src.value);
        (s?.transitions || []).forEach((t) => target.append(h('option', { value: t.value }, t.label)));
        if (!s?.transitions.length) target.append(h('option', { value: '' }, '— izinli geçiş yok —'));
      };
      src.addEventListener('change', fill);
      fill();
      body.append(field('Veri kaynağı', src), field('Yeni durum', target, 'İzinli geçişler: gölge → etkin/emekli, etkin → devrediliyor, devrediliyor → etkin/emekli.'),
        field('Rol sırası (isteğe bağlı)', rank));
      read = () => ({ op, table: 'ingest.data_source', element: src.value, payload: { lifecycle: target.value,
        ...(rank.value ? { role_rank: Number(rank.value) } : {}) } });
    }
  }
  kind.addEventListener('change', paint);
  paint();
  const row = h('fieldset', { class: 'op-row' }, h('legend', { class: 'sr-only' }, 'İşlem'),
    h('div', { class: 'op-head' }, kind, h('button', { class: 'btn ghost small', type: 'button', onclick: () => onRemove(row) }, 'Kaldır')), body);
  row.read = () => read();
  return row;
}

function openCreate(cat, refresh, opener, preset = null) {
  const name = input({ id: 'csName', maxlength: '120', placeholder: 'Ör. Sunucu yedekleme alanı' });
  const desc = h('textarea', { id: 'csDesc', rows: '2', maxlength: '2000' });
  const ops = h('div', { class: 'op-list' });
  const errors = h('div', { 'aria-live': 'polite' });
  const remove = (row) => { row.remove(); };
  const add = (p = null) => ops.append(opRow(cat, remove, p));
  add(preset);
  const save = h('button', { class: 'btn', type: 'button', id: 'csSave', onclick: async () => {
    clear(errors);
    const changes = [...ops.children].map((r) => r.read());
    if (!changes.length) { toast('En az bir işlem ekleyin.'); return; }
    save.disabled = true;
    try {
      const res = await createChangeSet({ name: name.value.trim(), description: desc.value.trim() || null, changes });
      closeOverlay();
      toast(res.message);
      await refresh(res.id);
    } catch (err) {
      save.disabled = false;
      errors.append(h('div', { class: 'notice bad', role: 'alert' }, h('strong', {}, err.message),
        err.details?.length ? h('ul', {}, err.details.map((d) => h('li', {}, d.hata || JSON.stringify(d)))) : null));
    }
  } }, 'Taslak olarak kaydet');
  dialog({
    title: 'Yeni değişiklik seti',
    body: [
      h('p', {}, 'İşlemler sırayla uygulanır ve kaydetmeden önce tüm metadata kurallarıyla doğrulanır. Taslak, onaya gönderildikten sonra başka bir kişi tarafından onaylanır.'),
      errors, field('Ad', name), field('Açıklama', desc), ops,
      h('button', { class: 'btn secondary small', type: 'button', id: 'csAddOp', onclick: () => add() }, '+ İşlem ekle'),
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save],
    focus: name,
  });
  void opener;
}

function detailBody(d) {
  const v = d.validation;
  return [
    h('div', { class: 'pills' }, pill(d.state_label, STATE_TONE[d.state])),
    d.description ? h('p', {}, d.description) : null,
    section('İşlemler', h('ol', { class: 'mini', id: 'csChanges' }, d.changes.map((c) => h('li', {},
      h('strong', {}, c.label), h('div', { class: 'small' }, c.description))))),
    v ? section('Doğrulama', v.ok ? h('p', { class: 'notice success' }, `Geçerli · ${formatDateTime(v.checked_at)}`)
      : h('div', { class: 'notice bad' }, h('ul', {}, v.errors.map((e) => h('li', {}, e))))) : null,
    d.impact ? section('Etki analizi', h('ul', { class: 'mini', id: 'csImpact' }, d.impact.map((i) => h('li', {},
      h('div', { class: 'small' }, h('strong', {}, i.change)),
      i.ci_count !== undefined ? h('div', { class: 'cell-sub' }, `Etkilenen CI: ${i.ci_count}`) : null,
      i.notes.map((n) => h('div', { class: 'small muted' }, n)))))) : null,
    section('Süreç', kv([
      ['Hazırlayan', `${d.created_by} · ${formatDateTime(d.created_on)}`],
      d.submitted_on ? ['Onaya gönderildi', formatDateTime(d.submitted_on)] : null,
      d.approved_by ? ['Onaylayan', `${d.approved_by} · ${formatDateTime(d.approved_on)}`] : null,
      d.rejected_by ? ['Reddeden', `${d.rejected_by} · ${formatDateTime(d.rejected_on)}`] : null,
      d.decision_note ? ['Karar notu', d.decision_note] : null,
      d.published_by ? ['Yayınlayan', `${d.published_by} · ${formatDateTime(d.published_on)}`] : null,
    ])),
    d.state === 'published' ? section('Dağıtım', h('div', { class: 'notice' },
      h('p', {}, 'Değişiklik dosyasını indirip ', h('span', { class: 'mono' }, `metadata/changes/${d.artifact_name}`), ' olarak PR ile ekleyin. CI ',
        h('span', { class: 'mono' }, 'load_metadata --check'), ' ile doğrular; dağıtımda yükleyici uygular.'),
      d.changes.some((c) => c.op === 'set_source_lifecycle')
        ? h('p', {}, 'Yaşam döngüsü değiştiği için dağıtımdan sonra: ', h('span', { class: 'mono' },
          `python -m evren_reconciler.worker --rereconcile ${d.changes.find((c) => c.op === 'set_source_lifecycle').element}`)) : null,
      h('p', { class: 'cell-sub mono', title: d.artifact_sha256 }, `SHA-256 ${d.artifact_sha256.slice(0, 20)}…`))) : null,
  ];
}

/** Başka sayfadan değişiklik seti başlatır (Veri Modeli → "Bu sınıfa alan ekle"); kayıttan sonra sete gider. */
export async function openChangeSetFor(preset, opener) {
  const cat = await schemaCatalog();
  openCreate(cat, async (id) => { location.hash = `#/sema/${id}`; }, opener, preset);
}

export async function renderSchema(view, param) {
  const { mode } = await backend();
  setHeader({
    title: 'Şema ve Değişiklikler',
    sub: 'Veri modeli değişiklikleri: taslak → onay → yayın.',
    crumbs: [['Şema ve Değişiklikler']],
  });
  const flow = h('ol', { class: 'flow-strip', 'aria-label': 'Akış' },
    [['Taslak', 'Şema tasarımcısı hazırlar; kurallarla doğrulanır'], ['Onay bekliyor', 'Etki analizi: CI sayısı, kaynak, sahipsiz kalabilecek alan'],
      ['Onaylandı', 'Hazırlayan dışında şema onaylayıcı'], ['Yayınlandı', 'metadata/changes/ dosyası + SHA-256; PR ile dağıtılır']]
      .map(([t, x], i) => h('li', {}, h('b', { class: 'n' }, String(i + 1)), h('span', {}, h('strong', {}, t), x))));
  if (mode !== 'api') {
    view.append(flow, h('div', { class: 'notice' }, h('strong', {}, 'Değişiklik setleri veritabanında tutulur. '),
      'Demo modunda (statik sunucu) kullanılamaz; Evren API\'siyle çalışırken setler burada hazırlanır, onaylanır ve yayınlanır.'));
    return {};
  }
  const [cat] = await Promise.all([schemaCatalog()]);
  const box = h('div');
  let selected = null;
  let table = null;

  async function openSet(id, opener) {
    const d = await changeSetDetail(id);
    selected = id;
    if (location.hash !== `#/sema/${id}`) history.replaceState(null, '', `#/sema/${id}`);
    const note = h('input', { type: 'text', id: 'csNote', maxlength: '2000', placeholder: 'Karar notu (isteğe bağlı)', 'aria-label': 'Karar notu' });
    const act = (action, label, cls = 'btn') => h('button', { class: cls, type: 'button', id: `cs-${action}`, onclick: async (ev) => {
      ev.currentTarget.disabled = true;
      try {
        const res = await changeSetAction(id, action, ['approve', 'reject'].includes(action) ? { note: note.value.trim() || null } : null);
        toast(res.message);
        await refresh(id);
      } catch (err) {
        toast(`${label}: ${err.message}`);
        ev.currentTarget.disabled = false;
      }
    } }, label);
    const footer = [
      d.can_reject ? act('reject', 'Reddet', 'btn secondary') : null,
      d.can_submit ? act('submit', 'Onaya gönder') : null,
      d.can_approve ? act('approve', 'Onayla') : null,
      d.can_publish ? act('publish', 'Yayınla') : null,
      d.state === 'published' ? h('a', { class: 'btn secondary', href: `/api/v1/schema/change-sets/${id}/artifact`, download: '', id: 'csDownload' }, 'Değişiklik dosyasını indir') : null,
    ].filter(Boolean);
    const body = detailBody(d);
    if (d.can_approve || d.can_reject) body.push(section('Karar notu', note));
    if (d.state === 'in_review' && !d.can_approve && cat.can_approve) {
      body.push(h('p', { class: 'notice warn' }, 'Bu seti siz hazırladınız; onaylayamaz veya reddedemezsiniz (görevler ayrılığı).'));
    }
    openDetail({ kicker: 'Değişiklik seti', title: d.name, opener, body, footer,
      onClose: () => { selected = null; history.replaceState(null, '', '#/sema'); table?.refreshSelection(); } });
    table?.refreshSelection();
  }

  async function refresh(openId) {
    const list = await changeSets();
    table = dataTable({
      label: 'Değişiklik setleri', rows: list.items, idOf: (r) => r.id, selectedId: () => selected,
      empty: 'Henüz değişiklik seti yok.', onOpen: (r, tr) => openSet(r.id, tr),
      columns: [
        { key: 'name', label: 'Ad', render: (r) => h('span', { class: 'cell-primary' }, r.name) },
        { key: 'state', label: 'Durum', render: (r) => pill(r.state_label, STATE_TONE[r.state]) },
        { key: 'change_count', label: 'İşlem', render: (r) => String(r.change_count) },
        { key: 'created_by', label: 'Hazırlayan', render: (r) => h('span', {}, r.created_by, h('span', { class: 'cell-sub' }, formatDateTime(r.created_on))) },
        { key: 'approved_by', label: 'Onaylayan', render: (r) => r.approved_by || '—' },
        { key: 'published_on', label: 'Yayın', render: (r) => (r.published_on ? formatDateTime(r.published_on) : '—') },
      ],
    });
    clear(box).append(
      h('div', { class: 'toolbar' }, h('span', { class: 'muted small' }, `Uygulanmış değişiklik dosyası: ${cat.applied_change_files.length}`),
        cat.can_draft ? h('button', { class: 'btn', type: 'button', id: 'csNew', onclick: (ev) => openCreate(cat, refresh, ev.currentTarget) }, 'Yeni değişiklik seti') : null),
      table);
    if (openId) await openSet(openId);
  }

  view.append(flow, box);
  await refresh(param && /^[0-9a-f]{32}$/.test(param) ? param : null);
  return { update: (p) => (p && /^[0-9a-f]{32}$/.test(p) ? openSet(p) : closeDetail({ silent: true })) };
}
