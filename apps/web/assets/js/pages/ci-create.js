// Elle CI oluşturma penceresi (U6.1, ADR-0026). API CI açmaz: istek uzlaştırıcıda kimlik çözümlemesinden geçer.
// Aynı kimlikte CI varsa yenisi açılmaz (var olan gösterilir); benzer CI varsa gerekçeli karar istenir. Değerler
// elle giriş (Manual) kaynağıdır ve rol önceliğine uyar.
import { h, clear } from '../lib/dom.js';
import { closeOverlay, dialog, toast } from '../lib/shell.js';
import { createCi, createForm, createStatus, creatableClasses, refSearch } from '../lib/data.js';
import { control, readValue } from './ci-edit.js';

const FIRST = ['support_group', 'owned_by', 'managed_by', 'used_for', 'operational_status', 'install_status',
  'location', 'short_description'];
const order = (f) => (FIRST.includes(f.attribute) ? FIRST.indexOf(f.attribute) : FIRST.length);
const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
const ciLink = (id, label) => h('a', { href: `#/ci/${id}`, onclick: () => closeOverlay() }, label);

/**
 * Odak pencere kapanınca açan düğmeye döner (iletişim kutusu altyapısı).
 * @param onCreated (ciSysId) => açılan CI'ı listede gösteren geri çağrı
 */
export async function openCreateDialog(onCreated) {
  const { items } = await creatableClasses();
  if (!items.length) {
    toast('CI oluşturma yetkiniz yok.');
    return;
  }
  const cls = h('select', { id: 'createClass', 'aria-describedby': 'createClassHelp' },
    items.map((c) => h('option', { value: c.name }, c.label_tr)));
  cls.value = items.some((c) => c.name === 'cmdb_ci_linux_server') ? 'cmdb_ci_linux_server' : items[0].name;
  const fieldsBox = h('div', { id: 'createFields' });
  const result = h('div', { id: 'createResult', 'aria-live': 'polite' });
  const note = h('input', { type: 'text', id: 'createNote', maxlength: '2000',
    placeholder: 'Ör. keşif ağının dışında; sözleşme numarası' });
  const save = h('button', { class: 'btn', type: 'button', id: 'createSave', onclick: () => submit(false) }, 'Oluştur');
  const refCache = new Map();
  let rows = new Map();

  async function fillRef(sel, f, initial, q = '') {
    const key = `${f.reference}|${q}`;
    if (!refCache.has(key)) refCache.set(key, refSearch(f.reference, q));
    let res;
    try { res = await refCache.get(key); } catch { return; }
    const keep = sel.value;
    while (sel.options.length > 1) sel.remove(1);
    res.items.forEach((x) => sel.append(h('option', { value: x.sys_id, selected: x.sys_id === keep }, x.name)));
    if (res.more && !sel.previousElementSibling?.classList.contains('ref-search')) {
      let t = null;
      const search = h('input', { type: 'search', class: 'ref-search', placeholder: `${f.label} ara…`, 'aria-label': `${f.label} ara`,
        oninput: () => { clearTimeout(t); t = setTimeout(() => fillRef(sel, f, initial, search.value.trim()), 250); } });
      sel.before(search);
    }
  }

  function row(f, required = false) {
    const el = control(f, null, fillRef);
    if (required) {
      el.required = true;
      el.setAttribute('aria-required', 'true');
      el.addEventListener('input', () => el.removeAttribute('aria-invalid'));
    }
    rows.set(f.attribute, { f, el });
    return h('div', { class: `edit-row${f.max_length > 255 && f.type === 'string' && !f.choices ? ' wide' : ''}` },
      h('label', { class: 'field', for: `edit-${f.attribute}` }, required ? `${f.label} (zorunlu)` : f.label), el);
  }

  async function loadForm() {
    clear(result);
    let form;
    try {
      form = await createForm(cls.value);
    } catch (err) {
      clear(fieldsBox).append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
      return;
    }
    rows = new Map();
    const byName = new Map(form.fields.map((f) => [f.attribute, f]));
    const identity = form.identity.filter((a) => a !== 'name').map((a) => byName.get(a)).filter(Boolean);
    const used = new Set(['name', ...identity.map((f) => f.attribute)]);
    const sortLabel = (a, b) => order(a) - order(b) || a.label.localeCompare(b.label, 'tr');
    const governed = form.fields.filter((f) => f.governed && !used.has(f.attribute)).sort(sortLabel);
    const others = form.fields.filter((f) => !f.governed && !used.has(f.attribute)).sort(sortLabel);
    clear(fieldsBox).append(
      h('form', { class: 'edit-form', onsubmit: (ev) => { ev.preventDefault(); submit(false); } },
        byName.has('name') ? h('div', { class: 'edit-grid' }, row(byName.get('name'), true)) : null,
        identity.length ? h('fieldset', {}, h('legend', {}, 'Kimlik'),
          h('p', { class: 'muted small' }, 'Keşfin bu CI\'ı sonradan tanıması için bilinen bir anahtar girin. Aynı kimlikte CI varsa yenisi açılmaz.'),
          h('div', { class: 'edit-grid' }, identity.map((f) => row(f)))) : null,
        governed.length ? h('fieldset', {}, h('legend', {}, 'Yönetişim alanları'), h('div', { class: 'edit-grid' }, governed.map((f) => row(f)))) : null,
        others.length ? h('details', { class: 'edit-more' }, h('summary', {}, `Diğer alanlar (${others.length})`),
          h('p', { class: 'muted small' }, 'Bu alanlarda keşif kaynağı değer bildirdiğinde elle değer yedek olarak kalır.'),
          h('fieldset', {}, h('legend', { class: 'sr-only' }, 'Diğer alanlar'), h('div', { class: 'edit-grid' }, others.map((f) => row(f))))) : null,
        // Birden çok alanlı formda Enter ile gönderim (örtük gönderim) için gizli gönder düğmesi
        h('button', { type: 'submit', class: 'sr-only', tabindex: '-1' }, 'Oluştur')));
  }

  function collect() {
    const attributes = {};
    for (const [name, { f, el }] of rows) {
      const v = readValue(f, el);
      if (v !== null && v !== '' && !(typeof v === 'number' && Number.isNaN(v))) attributes[name] = v;
    }
    return attributes;
  }

  function showResult(st, attributes) {
    clear(result);
    if (st.status === 'created') {
      closeOverlay();
      toast(`CI açıldı: ${st.result_ci_name || st.name}`);
      onCreated(st.result_ci);
      return;
    }
    if (st.status === 'duplicate') {
      result.append(h('div', { class: 'notice warn', role: 'alert' }, h('strong', {}, 'Bu CI zaten var. '), st.result_note, ' ',
        st.result_ci ? ciLink(st.result_ci, `${st.result_ci_name || st.result_ci} CI'ını aç`) : null));
      return;
    }
    if (st.status === 'needs_decision') {
      const reason = h('input', { type: 'text', id: 'createReason', maxlength: '2000', 'aria-describedby': 'createReasonHelp',
        placeholder: 'Ör. aynı adlı ikinci cihaz; etiket numarası farklı' });
      const force = h('button', { class: 'btn secondary', type: 'button', id: 'createForce', onclick: () => {
        if (!reason.value.trim()) {
          reason.setAttribute('aria-invalid', 'true');
          toast('Gerekçe zorunludur.');
          reason.focus();
          return;
        }
        submit(true, reason.value.trim(), attributes);
      } }, 'Yine de yeni CI aç');
      reason.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); force.click(); } });
      reason.addEventListener('input', () => reason.removeAttribute('aria-invalid'));
      result.append(h('div', { class: 'notice warn', role: 'alert' }, h('strong', {}, 'Karar gerekiyor. '), st.result_note,
        h('ul', { id: 'createCandidates' }, st.candidates.map((c) => h('li', {}, ciLink(c.sys_id, c.name || c.sys_id),
          ` · ${c.class_label}${c.probability != null ? ` · olasılık %${Math.round(c.probability * 100)}` : ' · aynı ad'}`))),
        h('label', { class: 'field', for: 'createReason' }, 'Gerekçe'), reason,
        h('p', { class: 'muted small', id: 'createReasonHelp' }, 'Karar ve gerekçe adınızla denetim izine yazılır.'), force));
      reason.focus();
      return;
    }
    if (st.status === 'rejected') {
      result.append(h('div', { class: 'notice bad', role: 'alert' }, h('strong', {}, 'Oluşturulamadı. '), st.result_note || ''));
      return;
    }
    result.append(h('div', { class: 'notice warn', role: 'status' }, 'İstek kaydedildi ama uzlaştırıcı henüz uygulamadı. ',
      'Uzlaştırıcı çalışanının durumunu Genel Bakış\'ta görebilirsiniz; istek kaybolmaz.'));
  }

  async function submit(force, reasonText = '', fixed = null) {
    const attributes = fixed || collect();
    clear(result);
    if (!attributes.name) {
      const nameEl = rows.get('name')?.el;
      nameEl?.setAttribute('aria-invalid', 'true');
      result.append(h('div', { class: 'notice bad', role: 'alert' }, 'Ad zorunludur.'));
      nameEl?.focus();
      return;
    }
    save.disabled = true;
    result.append(h('p', { class: 'muted small', role: 'status' }, 'Kimlik çözümlemesi yapılıyor…'));
    try {
      let st = await createCi({ cls: cls.value, attributes, note: force ? reasonText : note.value.trim(), force });
      for (let i = 0; i < 40 && st.status === 'pending'; i += 1) {
        await wait(400);
        st = await createStatus(st.id);
      }
      showResult(st, attributes);
    } catch (err) {
      clear(result).append(h('div', { class: 'notice bad', role: 'alert' }, h('strong', {}, err.message),
        err.details?.length ? h('ul', {}, err.details.map((d) => h('li', {}, `${rows.get(d.alan)?.f.label || d.alan}: ${d.hata}`))) : null));
    } finally {
      save.disabled = false;
    }
  }

  cls.addEventListener('change', loadForm);
  await loadForm();
  dialog({
    title: 'Yeni CI',
    body: [
      h('p', {}, 'CI ', h('strong', {}, 'elle giriş (Manual)'), ' kaynağıyla açılır ve keşif kaynaklarıyla aynı akıl katmanından geçer: ',
        'aynı kimlikte CI varsa yenisi açılmaz, benzer CI varsa karar istenir.'),
      h('label', { class: 'field', for: 'createClass' }, 'Sınıf'), cls,
      h('p', { class: 'muted small', id: 'createClassHelp' }, 'Alanlar sınıfa göre değişir; zorunlu alan yalnızca ad.'),
      fieldsBox,
      h('label', { class: 'field', for: 'createNote' }, 'Not'), note,
      result,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save],
    focus: cls,
  });
}
