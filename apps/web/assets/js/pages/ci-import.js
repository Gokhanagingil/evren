// CSV ile toplu içe aktarma (U6.3, ADR-0026 §6). İki aşama: önizleme (hiçbir şey yazmaz; sütun eşlemesi ve satır
// hataları) ve onay (geçerli satırlar toplu iş olur; uzlaştırıcı her satırı tek oluşturma gibi kimlik
// çözümlemesinden geçirir). Var olan CI CSV ile güncellenmez; mükerrer satır var olan CI'ı gösterir.
import { h, clear, pill } from '../lib/dom.js';
import { closeOverlay, dialog, toast } from '../lib/shell.js';
import { commitImport, creatableClasses, importStatus, previewImport } from '../lib/data.js';

const MAX_BYTES = 2_000_000;
const TONE = { pending: 'neutral', created: 'good', duplicate: 'warn', needs_decision: 'warn', rejected: 'bad' };
const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

export async function openImportDialog(onDone) {
  const { items } = await creatableClasses();
  if (!items.length) {
    toast('CI oluşturma yetkiniz yok.');
    return;
  }
  const cls = h('select', { id: 'importClass', 'aria-describedby': 'importHelp' }, items.map((c) => h('option', { value: c.name }, c.label_tr)));
  cls.value = items.some((c) => c.name === 'cmdb_ci_linux_server') ? 'cmdb_ci_linux_server' : items[0].name;
  const file = h('input', { type: 'file', id: 'importFile', accept: '.csv,text/csv,text/plain', 'aria-describedby': 'importHelp' });
  const note = h('input', { type: 'text', id: 'importNote', maxlength: '2000', placeholder: 'Ör. ilk envanter aktarımı, kaynak: tesis listesi' });
  const out = h('div', { id: 'importResult', 'aria-live': 'polite' });
  const previewBtn = h('button', { class: 'btn secondary', type: 'button', id: 'importPreview' }, 'Önizle');
  const commitBtn = h('button', { class: 'btn', type: 'button', id: 'importCommit', disabled: true }, 'İçe aktar');
  let text = '';
  let fileName = '';

  async function readFile() {
    const f = file.files[0];
    if (!f) throw new Error('Bir CSV dosyası seçin.');
    if (f.size > MAX_BYTES) throw new Error('Dosya en çok 2 MB olabilir.');
    fileName = f.name;
    return f.text();
  }

  function previewView(p) {
    const rows = p.rows.slice(0, 200);
    return h('div', { id: 'importPreviewBox' },
      h('p', { class: 'small', role: 'status' }, h('strong', {}, `${p.total} satır · ${p.valid_count} geçerli · ${p.invalid_count} hatalı`),
        ` · ayraç: ${p.delimiter}`),
      h('div', { class: 'pills', 'aria-label': 'Sütun eşlemesi' }, p.columns.map((c) => {
        if (c.status === 'unknown' || c.status === 'duplicate') return pill(`${c.header}: ${c.message}`, 'warn');
        const target = c.label || c.field;
        return pill(target && target !== c.header ? `${c.header} → ${target}` : c.header, 'good');
      })),
      h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Önizleme satırları' },
        h('thead', {}, h('tr', {}, ['Satır', 'Ad', 'Sınıf', 'Durum'].map((t) => h('th', { scope: 'col' }, t)))),
        h('tbody', {}, rows.map((r) => h('tr', {},
          h('td', {}, String(r.row_no)), h('td', {}, r.name || '—'), h('td', {}, r.class_label || r.class),
          h('td', {}, r.valid ? pill('Geçerli', 'good') : h('span', {}, pill('Hatalı', 'bad'), ' ', r.errors.join(' · ')))))))),
      p.rows.length > rows.length ? h('p', { class: 'muted small' }, `İlk ${rows.length} satır gösteriliyor (hatalılar önce).`) : null);
  }

  previewBtn.addEventListener('click', async () => {
    clear(out);
    commitBtn.disabled = true;
    try {
      text = await readFile();
      const p = await previewImport({ cls: cls.value, csv: text, fileName });
      out.append(previewView(p));
      commitBtn.disabled = p.valid_count === 0;
      commitBtn.textContent = `İçe aktar (${p.valid_count} satır)`;
      if (p.valid_count) commitBtn.focus();
    } catch (err) {
      out.append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
    }
  });

  function progressView(st) {
    const c = st.counts;
    const done = c.created + c.duplicate + c.needs_decision + c.rejected;
    return h('div', { id: 'importProgress' },
      h('p', { role: 'status' }, h('strong', {}, st.done ? 'İçe aktarma tamamlandı. ' : 'İşleniyor… '),
        `${done}/${st.row_count} satır · açılan ${c.created} · zaten var ${c.duplicate} · karar gerekiyor ${c.needs_decision} · reddedilen ${c.rejected}`,
        st.skipped_count ? ` · önizlemede atlanan ${st.skipped_count}` : ''),
      h('progress', { max: String(st.row_count), value: String(done), 'aria-label': 'İlerleme' }));
  }

  function problemsView(rows) {
    if (!rows.length) return h('p', { class: 'muted small' }, 'Bütün satırlar CI olarak açıldı.');
    return h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Açılmayan satırlar', id: 'importProblems' },
      h('thead', {}, h('tr', {}, ['Satır', 'Ad', 'Sonuç', 'Açıklama'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, rows.map((r) => h('tr', {},
        h('td', {}, String(r.row_no)), h('td', {}, r.name || '—'), h('td', {}, pill(r.status_label, TONE[r.status] || 'neutral')),
        h('td', { class: 'small' }, r.result_note || '', r.result_ci ? h('span', {}, ' ', ciLink(r.result_ci, r.result_ci_name)) : null,
          (r.candidates || []).length ? h('span', { class: 'cands' }, ' Adaylar: ',
            r.candidates.map((c, i) => h('span', {}, i ? ', ' : '', ciLink(c.sys_id, c.name)))) : null))))));
  }

  function ciLink(id, name) {
    return h('a', { href: `#/ci/${id}`, onclick: () => closeOverlay() }, name || id);
  }

  commitBtn.addEventListener('click', async () => {
    commitBtn.disabled = true;
    previewBtn.disabled = true;
    clear(out);
    try {
      const res = await commitImport({ cls: cls.value, csv: text, fileName, note: note.value.trim() });
      let st = await importStatus(res.id);
      out.append(progressView(st));
      for (let i = 0; i < 180 && !st.done; i += 1) {
        await wait(700);
        st = await importStatus(res.id);
        clear(out).append(progressView(st));
      }
      const problems = (await importStatus(res.id)).rows.filter((r) => r.status !== 'created');
      out.append(st.done ? problemsView(problems)
        : h('p', { class: 'notice warn' }, 'İçe aktarma arka planda sürüyor; uzlaştırıcı satırları sırayla işliyor. Pencereyi kapatabilirsiniz.'));
      toast(res.message);
      await onDone();
    } catch (err) {
      previewBtn.disabled = false;
      out.append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
    }
  });

  dialog({
    title: 'CSV içe aktar',
    body: [
      h('p', {}, 'Her satır ', h('strong', {}, 'elle giriş (Manual)'), ' kaynağıyla ve tek CI oluşturma gibi kimlik çözümlemesinden geçer: aynı kimlikte CI varsa açılmaz. Var olan CI CSV ile güncellenmez.'),
      h('label', { class: 'field', for: 'importClass' }, 'Varsayılan sınıf'), cls,
      h('label', { class: 'field', for: 'importFile' }, 'CSV dosyası'), file,
      h('p', { class: 'muted small', id: 'importHelp' },
        'Ad sütunu zorunlu. Başlıklar alan adı (serial_number) veya Türkçe etiket (Seri numarası) olabilir; sys_class_name sütunu satırın sınıfını belirler. ',
        'Ayraç virgül, noktalı virgül veya sekme; UTF-8; en çok 5000 satır ve 2 MB. Referans ve seçim değerleri adla yazılabilir (ör. "Sunucu Operasyon", "Üretim"). ',
        '=, +, @ ile başlayan hücreler güvenlik nedeniyle kabul edilmez.'),
      h('label', { class: 'field', for: 'importNote' }, 'Not'), note,
      out,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Kapat'), previewBtn, commitBtn],
    focus: cls,
  });
}
