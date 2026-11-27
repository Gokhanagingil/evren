// Sınıf ve alan sihirbazı (U8.7, ADR-0038). Yeni sınıf: üst sınıf → ad → alanlar → gözden geçir. Var olan sınıfa
// alan: sınıf → alanlar → gözden geçir. Sonuç bir değişiklik setidir (ADR-0015): sunucuda kaydetmeden doğrulanır,
// taslak oluşturulur ve istenirse onaya gönderilir; hazırlayan onaylayamaz. Teknik ad (u_…) Türkçe etiketten önerilir.
import { h, clear, fold } from '../lib/dom.js';
import { toast } from '../lib/shell.js';
import { changeSetAction, createChangeSet, dataModel, schemaCatalog, validateChangeSet } from '../lib/data.js';
import { wfield, wizard } from '../lib/wizard.js';

const TYPE_HELP = {
  string: ['Metin', 'Serbest metin veya seçim listesi.'], integer: ['Tam sayı', 'Ör. port, adet.'],
  decimal: ['Ondalık', 'Ör. kapasite (GB).'], boolean: ['Evet/hayır', 'Ör. yedekleniyor mu.'],
  reference: ['Referans', 'Kişi, grup, konum gibi başka bir kayda bağlantı.'],
  glide_date: ['Tarih', 'Ör. garanti bitişi.'], glide_date_time: ['Tarih-saat', 'Ör. son bakım zamanı.'],
};
const SENS_HELP = { public: 'Genel', internal: 'İç (varsayılan)', confidential: 'Gizli: yalnızca gizli okuma izni olan görür',
  restricted: 'Kısıtlı: yalnızca kısıtlı okuma izni olan görür' };
const TR_MAP = { ç: 'c', ğ: 'g', ı: 'i', ö: 'o', ş: 's', ü: 'u', â: 'a', î: 'i', û: 'u' };

/** Türkçe etiketten teknik ad parçası: küçük harf, Türkçe harfler ASCII, diğerleri alt çizgi. */
export function slug(text, max = 50) {
  return fold(text).replace(/[çğıöşüâîû]/g, (ch) => TR_MAP[ch]).replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, max)
    .replace(/_+$/, '');
}

function input(id, attrs = {}) {
  return h('input', { type: 'text', id, autocomplete: 'off', ...attrs });
}

function inherited(model, name) {
  const byName = Object.fromEntries(model.classes.map((c) => [c.name, c]));
  const out = [];
  for (let c = byName[name]; c; c = byName[c.parent]) out.push(...c.fields.map((f) => ({ ...f, from: c.label_tr })));
  return out;
}

function identityText(c) {
  const id = c?.identification;
  if (!id) return 'Bu dalda kimlik kuralı yok: CI\'lar yalnızca elle veya kaynak anahtarıyla eşleşir.';
  const first = id.entries.map((e) => e.attributes.join(' + ')).join(' → ');
  return `${id.inherited ? `${id.applies_to} kuralından devralır` : 'Kendi kuralı'}: ${first}${id.context ? ' (ana CI bağlamında)' : ''}.`;
}

/** Aranabilir sınıf ağacı; seçilen sınıfın devralınan alan sayısı, kimlik kuralı ve alt sınıfları. */
function classPicker(model, selected, onPick, label) {
  const search = input('wizClassSearch', { type: 'search', class: 'searchbar', placeholder: 'Sınıf ara (ör. sunucu, uygulama, ağ)', 'aria-label': label, 'data-no-enter': '1' });
  const list = h('ul', { class: 'class-pick', id: 'wizClassList', 'aria-label': label });
  const info = h('div', { id: 'wizClassInfo', 'aria-live': 'polite' });
  const byName = Object.fromEntries(model.classes.map((c) => [c.name, c]));
  function paintInfo() {
    const c = byName[selected];
    clear(info);
    if (!c) return;
    info.append(h('p', { class: 'notice', role: 'status' }, h('strong', {}, `${c.label_tr} (${c.name}). `),
      `${c.field_count} alan devralınır; ${c.children.length ? `${c.children.length} alt sınıfı var.` : 'alt sınıfı yok.'} `, identityText(c)));
  }
  function paint() {
    const q = fold(search.value.trim());
    clear(list);
    for (const c of model.classes) {
      if (q && !fold(`${c.label_tr} ${c.name}`).includes(q)) continue;
      const b = h('button', { type: 'button', 'aria-pressed': c.name === selected ? 'true' : 'false', dataset: { cls: c.name },
        onclick: () => { selected = c.name; onPick(c.name); paint(); paintInfo(); } },
      `${q ? '' : '  '.repeat(c.depth)}${c.label_tr}`, h('span', { class: 'cell-sub mono' }, c.name));
      list.append(h('li', {}, b));
    }
    if (!list.children.length) list.append(h('li', { class: 'muted small' }, 'Eşleşen sınıf yok.'));
  }
  search.addEventListener('input', paint);
  paint();
  paintInfo();
  return h('div', {}, search, list, info);
}

/** Alan satırı: etiket → önerilen ad, tip, uzunluk/referans/seçim değerleri, hassasiyet. ``read()`` işlemleri döner. */
function fieldRow(cat, index, onRemove) {
  const id = (k) => `wf${index}-${k}`;
  const label = input(id('label'), { maxlength: '80', placeholder: 'Ör. Ekran boyutu' });
  const name = input(id('name'), { maxlength: '62', class: 'mono', placeholder: 'u_ekran_boyutu' });
  let touched = false;
  label.addEventListener('input', () => { if (!touched) name.value = label.value.trim() ? `u_${slug(label.value)}` : ''; });
  name.addEventListener('input', () => { touched = true; });
  const type = h('select', { id: id('type') }, cat.field_types.map((t) => h('option', { value: t }, TYPE_HELP[t]?.[0] || t)));
  const len = h('input', { type: 'number', id: id('len'), min: '1', max: '4000', value: '255' });
  const ref = h('select', { id: id('ref') }, cat.reference_tables.map((t) => h('option', { value: t.name }, t.label_tr)));
  const sens = h('select', { id: id('sens') }, cat.sensitivities.map((v) => h('option', { value: v, selected: v === 'internal' }, SENS_HELP[v] || v)));
  const choice = h('input', { type: 'checkbox', id: id('choice') });
  const values = h('textarea', { id: id('values'), rows: '3', maxlength: '2000', placeholder: 'Her satıra bir değer: Lobi\nOfis\nDepo' });
  const help = h('p', { class: 'muted small', id: id('typeHelp') });
  const lenBox = wfield('Uzunluk', len);
  const refBox = wfield('Hangi kayda bağlanır?', ref);
  const choiceBox = h('label', { class: 'check-row', for: id('choice') }, choice, ' Seçim listeli (açılır liste)');
  const valuesBox = wfield('Seçim değerleri', values, 'Her satır bir değer; teknik değer etiketten üretilir.');
  const sync = () => {
    help.textContent = TYPE_HELP[type.value]?.[1] || '';
    lenBox.hidden = type.value !== 'string';
    refBox.hidden = type.value !== 'reference';
    choiceBox.hidden = type.value !== 'string';
    valuesBox.hidden = !(type.value === 'string' && choice.checked);
  };
  type.addEventListener('change', sync);
  choice.addEventListener('change', sync);
  const remove = h('button', { class: 'btn ghost small', type: 'button', onclick: () => onRemove(row) }, 'Kaldır');
  const row = h('fieldset', { class: 'field-row', dataset: { row: String(index) } }, h('legend', { class: 'sr-only' }, `Alan ${index + 1}`),
    h('div', { class: 'wiz-grid' }, wfield('Türkçe etiket', label), wfield('Teknik ad', name, 'u_ ile başlar; etiketten önerilir.'),
      h('div', {}, wfield('Tip', type), help)),
    h('div', { class: 'wiz-grid' }, lenBox, refBox, wfield('Hassasiyet', sens)), choiceBox, valuesBox, h('div', { class: 'link-actions' }, remove));
  sync();
  row.read = (table) => {
    const el = name.value.trim();
    const ops = [{ op: 'add_field', table, element: el, payload: {
      type: type.value, label_tr: label.value.trim(), sensitivity: sens.value,
      ...(type.value === 'string' ? { max_length: Number(len.value) || 255, ...(choice.checked ? { choice: true } : {}) } : {}),
      ...(type.value === 'reference' ? { reference: ref.value } : {}) } }];
    if (type.value === 'string' && choice.checked) {
      for (const v of values.value.split('\n').map((x) => x.trim()).filter(Boolean)) {
        ops.push({ op: 'add_choice', table, element: el, payload: { value: slug(v, 40) || v, label_tr: v } });
      }
    }
    return ops;
  };
  row.element = () => name.value.trim();
  row.check = () => {
    if (!label.value.trim()) return [label, 'Alanın Türkçe etiketini yazın.'];
    if (!/^u_[a-z][a-z0-9_]{0,60}$/.test(name.value.trim())) return [name, 'Teknik ad u_ ile başlamalı (küçük harf, rakam, alt çizgi).'];
    return null;
  };
  return row;
}

function fieldsStep(cat, model, getTable, required) {
  return {
    id: 'alanlar', title: required ? 'Alanlar' : 'Alanlar (isteğe bağlı)', render: (st) => {
      const rows = h('div', { class: 'field-rows', id: 'wizFieldRows' });
      st.n = st.n || 0;
      const add = () => {
        const r = fieldRow(cat, st.n++, (row) => { row.remove(); st.rows = st.rows.filter((x) => x !== row); });
        st.rows.push(r);
        rows.append(r);
        r.querySelector('input')?.focus();
      };
      st.rows = st.rows || [];
      if (st.rows.length) rows.append(...st.rows); // geri dönülünce girilenler kalır
      else if (required) add();
      const parent = st.parent || st.table;
      const inh = inherited(model, parent);
      return h('div', {},
        h('p', {}, required ? 'Eklenecek alanlar. Alanlar alt sınıflara da kalıtılır; mevcut CI\'lar değişmez, değer kaynak eşlemesi veya elle girişle dolar.'
          : 'Yeni sınıfa özel alanlar. Üst sınıfın alanları zaten devralınır; aynı bilgiyi ikinci kez eklemeyin.'),
        h('details', { class: 'small' }, h('summary', {}, `Devralınan ${inh.length} alanı göster`),
          h('ul', { class: 'mini' }, inh.map((f) => h('li', {}, `${f.label_tr} `, h('span', { class: 'cell-sub mono' }, `${f.name} · ${f.from}`))))),
        rows,
        h('button', { class: 'btn secondary small', type: 'button', id: 'wizAddField', onclick: add }, '+ Alan ekle'));
    },
    next: (st, a) => {
      if (required && !st.rows.length) { a.error('En az bir alan ekleyin.'); return false; }
      const inh = new Set(inherited(model, st.parent || st.table).map((f) => f.name));
      const seen = new Set();
      for (const r of st.rows) {
        const bad = r.check();
        if (bad) { bad[0].setAttribute('aria-invalid', 'true'); bad[0].focus(); a.error(bad[1]); return false; }
        const el = r.element();
        if (inh.has(el) || seen.has(el)) { a.error(`${el}: bu ad devralınan alanlarda veya bu listede zaten var.`); return false; }
        seen.add(el);
      }
      st.changes = [...(st.classOp ? [st.classOp] : []), ...st.rows.flatMap((r) => r.read(getTable(st)))];
      return true;
    },
  };
}

function reviewStep(defaultName) {
  return {
    id: 'kontrol', title: 'Gözden geçir', render: async (st) => {
      const v = await validateChangeSet(st.changes);
      st.valid = v.ok;
      const name = input('wizSetName', { maxlength: '120', value: st.setName || defaultName(st) });
      name.addEventListener('input', () => { st.setName = name.value; });
      st.setName = name.value;
      const desc = h('textarea', { id: 'wizSetDesc', rows: '2', maxlength: '2000', placeholder: 'Neden gerekli? Talep numarası.' });
      desc.value = st.setDesc || '';
      desc.addEventListener('input', () => { st.setDesc = desc.value; });
      const submit = h('input', { type: 'checkbox', id: 'wizSubmit', checked: st.submit !== false });
      submit.addEventListener('change', () => { st.submit = submit.checked; });
      return h('div', {},
        v.ok ? h('p', { class: 'notice success', role: 'status', id: 'wizValid' }, h('strong', {}, 'Doğrulandı. '), 'Bütün metadata kurallarına uyuyor; henüz hiçbir şey kaydedilmedi.')
          : h('div', { class: 'notice bad', role: 'alert', id: 'wizValid' }, h('strong', {}, 'Düzeltilmesi gerekenler: '),
            h('ul', {}, v.errors.map((e) => h('li', {}, e))), 'Geri dönüp düzeltin.'),
        h('h4', {}, 'Yapılacaklar'),
        h('ol', { class: 'small', id: 'wizChanges' }, v.changes.map((c, i) => h('li', {}, c,
          ...(v.impact[i]?.notes || []).map((n) => h('div', { class: 'cell-sub' }, n))))),
        wfield('Değişiklik setinin adı', name), wfield('Açıklama', desc),
        h('label', { class: 'check-row', for: 'wizSubmit' }, submit, ' Hemen onaya gönder'),
        h('p', { class: 'muted small' }, 'Sonra: başka bir kişi (Şema onaylayıcı) inceler ve onaylar; yayında değişiklik dosyası üretilir ve dağıtımla (PR) geçerli olur. Hazırlayan onaylayamaz.'));
    },
    next: async (st, a) => {
      if (!st.valid) { a.error('Önce hataları düzeltin (Geri).'); return false; }
      if ((st.setName || '').trim().length < 3) { a.error('Değişiklik setine bir ad verin.'); document.getElementById('wizSetName')?.focus(); return false; }
      return true;
    },
  };
}

async function finish(st) {
  const res = await createChangeSet({ name: st.setName.trim(), description: (st.setDesc || '').trim() || null, changes: st.changes });
  let msg = res.message;
  if (st.submit !== false) {
    const sent = await changeSetAction(res.id, 'submit');
    msg = sent.message || 'Onaya gönderildi.';
  }
  toast(msg);
  location.hash = `#/sema/${res.id}`;
}

export async function openClassWizard() {
  const [cat, model] = await Promise.all([schemaCatalog(), dataModel()]);
  const st = { parent: 'cmdb_ci_hardware', rows: [], label: '', name: '', classOp: null };
  return wizard({
    title: 'Yeni sınıf ekle', state: st, finishLabel: st.submit === false ? 'Taslağı oluştur' : 'Oluştur',
    onFinish: async (s) => { await finish(s); },
    steps: [
      { id: 'ust', title: 'Nerede duracak?', render: (s) => h('div', {},
        h('p', {}, 'Yeni sınıf hangi sınıfın türü? Seçtiğiniz sınıfın bütün alanlarını ve kimlik kuralını devralır. Ör. yeni bir cihaz tipi için "Donanım", yeni bir uygulama türü için "Uygulama".'),
        classPicker(model, s.parent, (v) => { s.parent = v; }, 'Üst sınıf')) },
      { id: 'ad', title: 'Adı', render: (s) => {
        const label = input('wizClassLabel', { maxlength: '80', value: s.label, placeholder: 'Ör. Kiosk cihazı' });
        const name = input('wizClassName', { maxlength: '62', value: s.name, class: 'mono', placeholder: 'u_cmdb_ci_kiosk_cihazi' });
        let touched = Boolean(s.name);
        label.addEventListener('input', () => { s.label = label.value; if (!touched) { name.value = label.value.trim() ? `u_cmdb_ci_${slug(label.value, 48)}` : ''; s.name = name.value; } });
        name.addEventListener('input', () => { touched = true; s.name = name.value.trim(); });
        return h('div', {}, wfield('Türkçe ad', label, 'Listelerde ve formlarda görünür.'),
          wfield('Teknik ad', name, 'ServiceNow özel tablo kuralı: u_ ile başlar. Etiketten önerilir; sonradan değiştirilemez.'));
      },
      next: (s, a) => {
        if (!s.label.trim()) { a.error('Sınıfın Türkçe adını yazın.'); document.getElementById('wizClassLabel')?.focus(); return false; }
        if (!/^u_[a-z][a-z0-9_]{0,60}$/.test(s.name || '')) { a.error('Teknik ad u_ ile başlamalı (küçük harf, rakam, alt çizgi).'); document.getElementById('wizClassName')?.focus(); return false; }
        if (model.classes.some((c) => c.name === s.name)) { a.error('Bu teknik adla bir sınıf zaten var.'); return false; }
        s.classOp = { op: 'add_class', table: s.name, payload: { parent: s.parent, label_tr: s.label.trim() } };
        s.changes = [s.classOp];
        return true;
      } },
      fieldsStep(cat, model, (s) => s.name, false),
      reviewStep((s) => `Yeni sınıf: ${s.label.trim()}`),
    ],
  });
}

export async function openFieldWizard({ table = null } = {}) {
  const [cat, model] = await Promise.all([schemaCatalog(), dataModel()]);
  const st = { table: table && model.classes.some((c) => c.name === table) ? table : 'cmdb_ci_server', rows: [] };
  return wizard({
    title: 'Sınıfa alan ekle', state: st, finishLabel: 'Oluştur', start: table ? 1 : 0,
    onFinish: async (s) => { await finish(s); },
    steps: [
      { id: 'sinif', title: 'Hangi sınıf?', render: (s) => h('div', {},
        h('p', {}, 'Alan bu sınıfa ve bütün alt sınıflarına eklenir. Genel bir bilgiyse üst sınıfa, yalnızca bir türe özgüyse o türe ekleyin.'),
        classPicker(model, s.table, (v) => { s.table = v; }, 'Sınıf')) },
      fieldsStep(cat, model, (s) => s.table, true),
      reviewStep((s) => `${model.classes.find((c) => c.name === s.table)?.label_tr || s.table}: yeni alan`),
    ],
  });
}
