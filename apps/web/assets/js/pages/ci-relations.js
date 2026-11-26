// Elle ilişki ekleme/kaldırma (U6.2, ADR-0026 §5). İstek uzlaştırıcıda uygulanır; izinli ilişki kuralı zorunludur,
// yalnızca elle girilmiş ilişki kaldırılabilir (keşfin bildirdiği ilişki kaynağından kalkar).
import { h, clear } from '../lib/dom.js';
import { closeOverlay, dialog, toast } from '../lib/shell.js';
import { ciSearch, relationOptions, relationStatus, requestRelation } from '../lib/data.js';

const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/** İsteği gönderir, uzlaştırıcının uygulamasını kısa süre bekler, sonucu bildirir; dönüş: uygulandı mı. */
async function sendAndWait(ciId, payload) {
  let st = await requestRelation(ciId, payload);
  for (let i = 0; i < 25 && st.status === 'pending'; i += 1) {
    await wait(400);
    st = await relationStatus(st.id);
  }
  if (st.status === 'applied') toast(st.result_note || 'İlişki güncellendi.');
  else if (st.status === 'rejected') toast(`Uygulanmadı: ${st.result_note}`);
  else toast('İstek kaydedildi; uzlaştırıcı uyguladığında ilişki güncellenir.');
  return st.status;
}

export async function openAddRelationDialog(c, onDone) {
  const opts = await relationOptions(c.sys_id);
  if (!opts.options.length) {
    toast('Bu sınıf için izinli ilişki kuralı yok.');
    return;
  }
  const kind = h('select', { id: 'relKind', 'aria-describedby': 'relKindHelp' },
    opts.options.map((o, i) => h('option', { value: String(i) }, `${o.label} → ${o.other_class_label}`)));
  const q = h('input', { type: 'search', id: 'relSearch', placeholder: 'Hedef CI ara (ad, FQDN, IP)', 'aria-controls': 'relTarget' });
  const target = h('select', { id: 'relTarget', size: '6', 'aria-label': 'Hedef CI' });
  const note = h('input', { type: 'text', id: 'relNote', maxlength: '2000', placeholder: 'Ör. uygulama bu sunucuda çalışır (sözleşme ekinde)' });
  const errors = h('div', { 'aria-live': 'polite' });
  const save = h('button', { class: 'btn', type: 'button', id: 'relSave' }, 'Ekle');
  let seq = 0;
  let timer = null;

  async function search() {
    const my = ++seq;
    const o = opts.options[Number(kind.value)];
    let res;
    try {
      res = await ciSearch({ q: q.value.trim(), cls: o.other_class, limit: 30 });
    } catch (err) {
      clear(errors).append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
      return;
    }
    if (my !== seq) return;
    clear(target);
    res.items.filter((x) => x.sys_id !== c.sys_id).forEach((x) => target.append(
      h('option', { value: x.sys_id }, `${x.name} · ${x.class_label}`)));
    if (!target.options.length) target.append(h('option', { value: '', disabled: true }, 'Eşleşen CI yok'));
    else target.selectedIndex = 0;
  }

  async function submit() {
    clear(errors);
    const o = opts.options[Number(kind.value)];
    if (!target.value) {
      errors.append(h('div', { class: 'notice bad', role: 'alert' }, 'Hedef CI seçin.'));
      q.focus();
      return;
    }
    save.disabled = true;
    try {
      const status = await sendAndWait(c.sys_id, { action: 'add', type: o.type, direction: o.direction, target: target.value, note: note.value.trim() });
      closeOverlay();
      if (status !== 'rejected') await onDone();
    } catch (err) {
      save.disabled = false;
      errors.append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
    }
  }

  save.addEventListener('click', submit);
  kind.addEventListener('change', search);
  q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(search, 250); });
  q.addEventListener('keydown', (ev) => { if (ev.key === 'ArrowDown') { ev.preventDefault(); target.focus(); } });
  target.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); submit(); } });
  note.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); submit(); } });
  dialog({
    title: `İlişki ekle · ${c.name}`,
    body: [
      h('p', {}, 'İlişki ', h('strong', {}, 'elle giriş (Manual)'), ' kaynağıyla kaydedilir. Yalnızca izinli ilişki kuralları listelenir; uzlaştırıcı yine denetler.'),
      h('label', { class: 'field', for: 'relKind' }, 'İlişki'), kind,
      h('p', { class: 'muted small', id: 'relKindHelp' }, `"${c.name}" ilişkinin solundadır; hedef sağdadır.`),
      h('label', { class: 'field', for: 'relSearch' }, 'Hedef CI'), q, target,
      h('label', { class: 'field', for: 'relNote' }, 'Not'), note,
      errors,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save],
    focus: kind,
  });
  await search();
}

export function confirmRemoveRelation(c, r, onDone) {
  const note = h('input', { type: 'text', id: 'unrelNote', maxlength: '2000', placeholder: 'Ör. uygulama taşındı' });
  const ok = h('button', { class: 'btn danger', type: 'button', id: 'unrelConfirm' }, 'Kaldır');
  ok.addEventListener('click', async () => {
    ok.disabled = true;
    try {
      const status = await sendAndWait(c.sys_id, { action: 'remove', type: r.type, direction: r.direction, target: r.target, note: note.value.trim() });
      closeOverlay();
      if (status !== 'rejected') await onDone();
    } catch (err) {
      ok.disabled = false;
      toast(`İstek kaydedilemedi: ${err.message}`);
    }
  });
  note.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); ok.click(); } });
  const verb = r.direction === 'parent' ? r.type.split('::')[0] : r.type.split('::')[1];
  dialog({
    title: 'Elle girilmiş ilişkiyi kaldır',
    body: [
      h('p', {}, h('strong', {}, `${c.name} · ${verb} · ${r.target_name}`)),
      h('p', { class: 'muted small' }, 'Yalnızca elle giriş kaydı kaldırılır. İlişkiyi bir keşif kaynağı da bildiriyorsa satır kalır. Karar adınızla denetim izine yazılır.'),
      h('label', { class: 'field', for: 'unrelNote' }, 'Gerekçe'), note,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), ok],
    focus: note,
  });
}
