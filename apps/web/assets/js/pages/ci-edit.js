// CI elle düzenleme penceresi. Değerler Manual kaynak kaydı olarak uzlaştırmaya girer (CLAUDE.md kural 1):
// yönetişim alanlarında elle değer geçerlidir; kaynak sahipli alanlarda yalnızca yedektir. Yalnızca değişen alanlar
// gönderilir; boş bırakılan alan elle girilmiş değeri kaldırır.
import { h, clear, fromIstanbulInput, toIstanbulInput } from '../lib/dom.js';
import { closeOverlay, dialog, toast } from '../lib/shell.js';
import { ROLE_TR, editCi, refSearch } from '../lib/data.js';

const NONE = '';
/** Yönetişimde en sık düzenlenen alanlar önce; kalanlar ada göre. */
const FIRST = ['support_group', 'owned_by', 'managed_by', 'assignment_group', 'cost_center', 'used_for',
  'operational_status', 'install_status', 'life_cycle_stage', 'life_cycle_stage_status', 'location', 'short_description'];
const order = (f) => (FIRST.includes(f.attribute) ? FIRST.indexOf(f.attribute) : FIRST.length);

function hintText(f) {
  const has = f.value !== null && f.value !== undefined;
  const parts = [];
  if (has) parts.push(`Şu an: ${f.display ?? f.value}${f.source ? ` (${f.source})` : ''}.`);
  if (!f.governed) parts.push(authorityText(f));
  if (!f.governed && f.manual_value !== null && f.manual_value !== undefined && f.source !== 'Manual') parts.push('Elle girilmiş yedek değer var.');
  return parts.join(' ');
}

function authorityText(f) {
  if (!f.authority.length) return 'Kural yok: en güncel gözlem geçerli olur.';
  return `Öncelik: ${f.authority.map((r) => ROLE_TR[r] || r).join(' → ')}`;
}

export function control(f, initial, onRefSearch) {
  const id = `edit-${f.attribute}`;
  const common = { id, name: f.attribute, 'aria-describedby': hintText(f) ? `${id}-hint` : null };
  if (f.choices || f.type === 'boolean') {
    const opts = f.choices || [{ value: 'true', label: 'Evet' }, { value: 'false', label: 'Hayır' }];
    const sel = h('select', common, h('option', { value: NONE }, '— elle değer yok —'),
      opts.map((o) => h('option', { value: String(o.value) }, o.label)));
    sel.value = initial === null || initial === undefined ? NONE : String(initial);
    if (sel.selectedIndex < 0) sel.value = NONE; // listede olmayan değer (ör. kaynakta serbest metin)
    return sel;
  }
  if (f.type === 'reference') {
    const sel = h('select', { ...common, dataset: { ref: f.reference } }, h('option', { value: NONE }, '— elle değer yok —'));
    onRefSearch(sel, f, initial);
    return sel;
  }
  if (f.type === 'integer' || f.type === 'decimal') {
    return h('input', { ...common, type: 'number', step: f.type === 'integer' ? '1' : 'any', value: initial ?? '' });
  }
  if (f.type === 'glide_date') return h('input', { ...common, type: 'date', value: initial ? String(initial).slice(0, 10) : '' });
  if (f.type === 'glide_date_time') return h('input', { ...common, type: 'datetime-local', value: toIstanbulInput(initial, { seconds: false }) });
  if (f.max_length > 255) return h('textarea', { ...common, rows: '3', maxlength: String(f.max_length) }, initial ?? '');
  return h('input', { ...common, type: 'text', maxlength: String(f.max_length), value: initial ?? '' });
}

/** Denetim değerini API'nin beklediği biçime çevirir; boş → null (elle değeri kaldır). */
export function readValue(f, el) {
  const v = el.value;
  if (v === NONE) return null;
  if (f.type === 'boolean') return v === 'true';
  if (f.type === 'integer' && !f.choices) return Number.parseInt(v, 10);
  if (f.type === 'integer' && f.choices) return v;
  if (f.type === 'decimal') return Number(v);
  if (f.type === 'glide_date_time') return fromIstanbulInput(v);
  return f.type === 'string' ? v.trim() : v;
}

/**
 * @param c CI ayrıntısı (API yanıtı)
 * @param onSaved kaydedildikten sonra paneli yenileyen geri çağrı
 */
export function openEditDialog(c, opener, onSaved) {
  const refCache = new Map();
  const errors = h('div', { 'aria-live': 'polite' });
  const rows = new Map(); // attribute → { f, el, initial }

  async function fillRef(sel, f, initial, q = '') {
    const key = `${f.reference}|${q}`;
    if (!refCache.has(key)) refCache.set(key, refSearch(f.reference, q));
    let res;
    try { res = await refCache.get(key); } catch { return; }
    const keep = sel.value || (initial ?? NONE);
    while (sel.options.length > 1) sel.remove(1);
    const items = [...res.items];
    if (initial && !items.some((x) => x.sys_id === initial)) {
      const cur = c.editable.find((x) => x.attribute === f.attribute);
      items.unshift({ sys_id: initial, name: cur?.display && cur.value === initial ? cur.display : initial });
    }
    items.forEach((x) => sel.append(h('option', { value: x.sys_id, selected: x.sys_id === keep }, x.name)));
    if (res.more && !sel.previousElementSibling?.classList.contains('ref-search')) {
      let t = null;
      const search = h('input', { type: 'search', class: 'ref-search', placeholder: `${f.label} ara…`, 'aria-label': `${f.label} ara`,
        oninput: () => { clearTimeout(t); t = setTimeout(() => fillRef(sel, f, initial, search.value.trim()), 250); } });
      sel.before(search);
    }
  }

  function row(f) {
    // Başlangıç: elle girilmiş değer varsa o, yoksa yürürlükteki değer (değiştirilmezse gönderilmez).
    const initial = f.manual_value ?? f.value ?? null;
    const el = control(f, initial, fillRef);
    // Referans seçenekleri sonradan yüklenir; başlangıç değeri o yüzden doğrudan alınır.
    rows.set(f.attribute, { f, el, initial: f.type === 'reference' ? (initial ?? NONE) : el.value });
    const hint = hintText(f);
    return h('div', { class: `edit-row${f.max_length > 255 && f.type === 'string' && !f.choices ? ' wide' : ''}` },
      h('label', { class: 'field', for: `edit-${f.attribute}` }, f.label),
      el, hint ? h('div', { class: 'muted small', id: `edit-${f.attribute}-hint` }, hint) : null);
  }

  const sorted = [...c.editable].sort((a, b) => order(a) - order(b) || a.label.localeCompare(b.label, 'tr'));
  const governed = sorted.filter((f) => f.governed);
  const others = sorted.filter((f) => !f.governed);
  const note = h('input', { type: 'text', id: 'editNote', maxlength: '2000', placeholder: 'Ör. sahiplik devri, değişiklik numarası' });
  const save = h('button', { class: 'btn', type: 'button', id: 'editSave', onclick: submit }, 'Kaydet');

  async function submit() {
    const attributes = {};
    for (const [name, { f, el, initial }] of rows) {
      if (el.value !== initial) attributes[name] = readValue(f, el);
    }
    clear(errors);
    if (!Object.keys(attributes).length) { toast('Değişiklik yok.'); return; }
    save.disabled = true;
    try {
      const res = await editCi(c.sys_id, attributes, note.value.trim());
      closeOverlay();
      toast(res.message);
      await onSaved();
    } catch (err) {
      save.disabled = false;
      const labels = Object.fromEntries(c.editable.map((f) => [f.attribute, f.label]));
      errors.append(h('div', { class: 'notice bad', role: 'alert' }, h('strong', {}, err.message),
        err.details?.length ? h('ul', {}, err.details.map((d) => h('li', {}, `${labels[d.alan] || d.alan}: ${d.hata}`))) : null));
    }
  }

  dialog({
    title: `Düzenle · ${c.name}`,
    body: [
      h('p', {}, 'Değerler ', h('strong', {}, 'elle giriş (Manual)'), ' kaynağı olarak kaydedilir ve uzlaştırmadan geçer. ',
        'Yönetişim alanlarında elle değer geçerlidir; kaynak sahipli alanlarda daha öncelikli bir kaynak bildirdiği sürece yalnızca yedek olarak saklanır. ',
        'Boş bırakılan alan elle girilmiş değeri kaldırır.'),
      errors,
      h('form', { class: 'edit-form', onsubmit: (ev) => { ev.preventDefault(); submit(); } },
        governed.length ? h('fieldset', {}, h('legend', {}, 'Yönetişim alanları'), h('div', { class: 'edit-grid' }, governed.map(row))) : null,
        others.length ? h('details', { class: 'edit-more' }, h('summary', {}, `Kaynak sahipli ve kuralsız alanlar (${others.length})`),
          h('p', { class: 'muted small' }, 'Bu alanlarda daha öncelikli kaynak (keşif, sanallaştırma…) bildirdiği sürece elle değer geçmez; yedek olarak saklanır.'),
          h('fieldset', {}, h('legend', { class: 'sr-only' }, 'Kaynak sahipli ve kuralsız alanlar'), h('div', { class: 'edit-grid' }, others.map(row)))) : null,
        h('label', { class: 'field', for: 'editNote' }, 'Gerekçe'), note),
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save],
    focus: rows.values().next().value?.el || note,
  });
}
