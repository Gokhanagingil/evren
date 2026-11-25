// Eşleştirme kuyruğu: otomatik birleştirilmeyen kaynak kayıtları; yan yana karşılaştırma, kanıtlar, karar.
import { h, clear, dataTable, formatDateTime, kv, pill, section, tabs, fold } from '../lib/dom.js';
import { closeDetail, openDetail, setHeader, toast } from '../lib/shell.js';
import { demo, srcChip } from '../lib/data.js';

const COMPARE = [['name', 'Ad'], ['fqdn', 'FQDN'], ['serial_number', 'Seri numarası'], ['bios_uuid', 'BIOS UUID'],
  ['ip_address', 'IP adresi'], ['os', 'İşletim sistemi'], ['used_for', 'Kullanım amacı']];
const decisions = new Map(); // oturum içi önizleme; kalıcı kayıt API ile (F0-09/F0-10)

function same(a, b) {
  if (!a || !b || a === '—' || b === '—') return null;
  return fold(a).split('.')[0] === fold(b).split('.')[0];
}

export async function renderQueue(view, param) {
  const d = await demo();
  let tab = param === 'yetersiz' ? 'insufficient' : 'review';
  let selected = null;
  const box = h('div');

  setHeader({
    title: 'Eşleştirme Kuyruğu',
    sub: 'Otomatik birleştirilmeyen kayıtlar: kural eşleşti ama güçlü çelişki var, birden çok aday var veya kimlik yetersiz. Her kanıt açıklanır; karar geri alınabilir.',
    crumbs: [['Eşleştirme Kuyruğu']],
  });

  function rows() {
    if (tab === 'decided') return d.queue.filter((q) => decisions.has(q.id));
    return d.queue.filter((q) => q.kind === tab && !decisions.has(q.id));
  }

  function render() {
    clear(box);
    const counts = {
      review: d.queue.filter((q) => q.kind === 'review' && !decisions.has(q.id)).length,
      insufficient: d.queue.filter((q) => q.kind === 'insufficient' && !decisions.has(q.id)).length,
      decided: decisions.size,
    };
    box.append(
      tabs([{ id: 'review', label: 'İnceleme', count: counts.review }, { id: 'insufficient', label: 'Kimliği yetersiz', count: counts.insufficient },
        { id: 'decided', label: 'Bu oturumda karar verilen', count: counts.decided }], tab, (t) => { tab = t; render(); }, 'Kuyruk görünümü'),
      dataTable({
        label: 'Eşleştirme kuyruğu',
        rows: rows(),
        idOf: (q) => q.id,
        selectedId: () => selected,
        onOpen: (q, tr) => open(q.id, tr),
        empty: tab === 'decided' ? 'Bu oturumda henüz karar verilmedi.' : 'Bu görünümde bekleyen kayıt yok.',
        columns: [
          { key: 'id', label: 'Kayıt', render: (q) => h('span', { class: 'cell-primary' }, q.id) },
          { key: 'data_source', label: 'Kaynak', render: (q) => srcChip(q.data_source, q.role) },
          { key: 'name', label: 'Kaynak kaydı', sortValue: (q) => q.record.name,
            render: (q) => h('span', {}, q.record.name || q.source_key, h('span', { class: 'cell-sub' }, q.class_label)) },
          { key: 'summary', label: 'Neden', sortable: false, render: (q) => h('span', { class: 'small' }, q.reasons[0] || q.summary) },
          { key: 'best', label: 'En iyi aday', sortValue: (q) => q.candidates[0]?.name,
            render: (q) => (q.candidates.length ? h('span', {}, q.candidates[0].name,
              h('span', { class: 'cell-sub' }, q.candidates.length > 1 ? `+${q.candidates.length - 1} aday` : q.candidates[0].class_label)) : '—') },
          { key: 'decision', label: 'Durum', sortable: false,
            render: (q) => (decisions.has(q.id) ? pill(decisions.get(q.id).label, 'good') : pill(q.kind === 'review' ? 'İnceleme' : 'Park edildi', q.kind === 'review' ? 'bad' : 'warn')) },
        ],
      }),
      h('p', { class: 'muted small' }, 'Kararlar bu önizlemede yalnızca oturum boyunca tutulur. Kalıcı kayıt, karar verenin kimliği ve gerekçesiyle API üzerinden yapılacak (F0-09, F0-10).'),
    );
  }

  function decide(q, label, detail) {
    decisions.set(q.id, { label, detail, at: new Date().toISOString() });
    toast(`${q.id}: ${label}. Önizleme — kalıcı değil.`);
    closeDetail();
    render();
  }

  function open(id, opener) {
    const q = d.queue.find((x) => x.id === id);
    if (!q) return;
    selected = id;
    if (location.hash !== `#/kuyruk/${id}`) history.replaceState(null, '', `#/kuyruk/${id}`);
    const cands = q.candidates;
    let choice = cands[0]?.sys_id || null;
    const table = h('div', { class: 'tablewrap' }, h('table', { class: 'compare', 'aria-label': 'Kaynak kaydı ve adaylar' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Öznitelik'), h('th', {}, `Kaynak kaydı (${q.data_source})`),
        cands.map((c, i) => h('th', {}, `Aday ${i + 1}`)))),
      h('tbody', {}, COMPARE.filter(([k]) => q.record[k] || cands.some((c) => c.attributes[k])).map(([k, label]) => h('tr', {},
        h('th', { scope: 'row' }, label),
        h('td', {}, q.record[k] || '—'),
        cands.map((c) => {
          const v = c.attributes[k] || '—';
          const eq = same(q.record[k], v);
          return h('td', { class: eq === null ? '' : eq ? 'same' : 'diff' }, v);
        }))))));
    const evidence = q.features.length
      ? h('ul', { class: 'evidence' }, q.features.map((f) => h('li', {},
          h('span', { class: `w ${f.weight >= 0 ? 'plus' : 'minus'}` }, `${f.weight > 0 ? '+' : ''}${String(f.weight).replace('.', ',')}`),
          h('span', {}, f.detail))))
      : h('p', { class: 'muted' }, 'Ek kanıt yok.');
    const picker = cands.length > 1 ? h('fieldset', { class: 'section' }, h('legend', { class: 'sr-only' }, 'Birleştirilecek aday'),
      h('h3', {}, 'Birleştirilecek aday'),
      cands.map((c, i) => h('label', { class: 'choice' },
        h('input', { type: 'radio', name: 'cand', value: c.sys_id, checked: i === 0, onchange: () => { choice = c.sys_id; } }),
        h('span', {}, `Aday ${i + 1}: ${c.name} · ${c.class_label} · %${Math.round(c.probability * 1000) / 10}`,
          h('span', { class: 'cell-sub mono' }, c.sys_id))))) : null;

    openDetail({
      kicker: `Eşleştirme · ${q.id}`,
      title: q.record.name || q.source_key,
      opener,
      onClose: () => { selected = null; history.replaceState(null, '', '#/kuyruk'); box.querySelector('.tablewrap')?.refreshSelection?.(); },
      body: [
        h('div', { class: `notice ${q.kind === 'review' ? 'bad' : 'warn'}` }, h('strong', {}, q.summary.split(';')[0]), q.reasons.length ? h('ul', {}, q.reasons.map((r) => h('li', {}, r))) : null),
        section('Kaynak kaydı', kv([
          ['Kaynak', srcChip(q.data_source, q.role)], ['Kaynak anahtarı', h('span', { class: 'mono' }, q.source_key)],
          ['Sınıf ipucu', q.class_label], ['Gözlem', formatDateTime(q.observed_at)], q.rule ? ['Eşleşen kural', q.rule] : null,
          q.lookup.ip.length ? ['IP adresleri', q.lookup.ip.join(', ')] : null, q.lookup.mac.length ? ['MAC adresleri', q.lookup.mac.join(', ')] : null,
        ])),
        cands.length ? section('Yan yana karşılaştırma', table, h('p', { class: 'muted small' }, 'Yeşil: aynı · Kırmızı: farklı (çelişki). Ad karşılaştırması alan adı eki olmadan yapılır.')) : null,
        section('Kanıtlar (log-odds ağırlığı)', evidence),
        cands.length ? section('Adayların kaynakları', h('ul', { class: 'mini' }, cands.map((c, i) => h('li', {},
          h('a', { href: `#/ci/${c.sys_id}` }, `Aday ${i + 1}: ${c.name}`), ' ', h('span', { class: 'pills' }, c.sources.map((src) => srcChip(src, d.sources.find((x) => x.name === src)?.role)))))))
          : null,
        picker,
        decisions.has(q.id) ? h('p', { class: 'notice success' }, `Karar: ${decisions.get(q.id).label}`) : null,
      ],
      footer: decisions.has(q.id)
        ? [h('button', { class: 'btn secondary', type: 'button', onclick: () => { decisions.delete(q.id); toast(`${q.id}: karar geri alındı.`); closeDetail(); render(); } }, 'Kararı geri al')]
        : q.kind === 'review'
          ? [h('button', { class: 'btn secondary', type: 'button', onclick: () => decide(q, 'Ertelendi', null) }, 'Ertele'),
            h('button', { class: 'btn secondary', type: 'button', onclick: () => decide(q, 'Yeni CI olarak açıldı', null) }, 'Ret: yeni CI aç'),
            cands.length ? h('button', { class: 'btn', type: 'button', id: 'mergeBtn',
              onclick: () => decide(q, `Birleştirildi → ${cands.find((c) => c.sys_id === choice)?.name}`, choice) }, 'Adayla birleştir') : null]
          : [h('button', { class: 'btn secondary', type: 'button', onclick: () => decide(q, 'Kaynakta düzeltilecek', null) }, 'Kaynakta düzeltilsin'),
            cands.length ? h('button', { class: 'btn', type: 'button', onclick: () => decide(q, `Elle bağlandı → ${cands[0].name}`, cands[0].sys_id) }, `Elle bağla: ${cands[0].name}`) : null],
    });
    box.querySelector('.tablewrap')?.refreshSelection?.();
  }

  view.append(box);
  render();
  if (param && param !== 'yetersiz') {
    const q = d.queue.find((x) => x.id === param);
    if (q) { tab = decisions.has(q.id) ? 'decided' : q.kind; render(); open(q.id); }
  }
  return {
    update(next) {
      if (next && next !== 'yetersiz') {
        const q = d.queue.find((x) => x.id === next);
        if (q) { tab = decisions.has(q.id) ? 'decided' : q.kind; render(); open(q.id); }
      } else {
        if (next === 'yetersiz') { tab = 'insufficient'; render(); }
        closeDetail({ silent: true });
      }
    },
  };
}
