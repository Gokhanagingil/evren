// Eşleştirme kuyruğu: otomatik birleştirilmeyen kaynak kayıtları; yan yana karşılaştırma, kanıtlar, karar.
// API modunda kararlar kalıcıdır (idempotency_key ile); uzlaştırıcı uygular. Demo modunda oturumluk önizlemedir.
import { h, clear, dataTable, formatDateTime, kv, pill, section, tabs, fold } from '../lib/dom.js';
import { closeDetail, openDetail, setHeader, toast } from '../lib/shell.js';
import { backend, decide, queueView, srcChip, undoDemoDecision } from '../lib/data.js';

const COMPARE = [['name', 'Ad'], ['fqdn', 'FQDN'], ['serial_number', 'Seri numarası'], ['bios_uuid', 'BIOS UUID'],
  ['ip_address', 'IP adresi'], ['os', 'İşletim sistemi'], ['used_for', 'Kullanım amacı']];
const KIND_LABEL = { review: 'İnceleme', insufficient: 'Kimliği yetersiz', unmatched_shadow: 'Gölge kaynak eşleşmedi',
  unmatched_retiring: 'Devredilen kaynak eşleşmedi' };

function same(a, b) {
  if (!a || !b || a === '—' || b === '—') return null;
  return fold(a).split('.')[0] === fold(b).split('.')[0];
}

export async function renderQueue(view, param) {
  const { mode } = await backend();
  let open = await queueView('open');
  let decided = null;
  let tab = param === 'yetersiz' ? 'insufficient' : 'review';
  let selected = null;
  const box = h('div');

  setHeader({
    title: 'Eşleştirme Kuyruğu',
    sub: 'Otomatik birleştirilmeyen kayıtlar: kural eşleşti ama güçlü çelişki var, birden çok aday var veya kimlik yetersiz. Her kanıt açıklanır; karar geri alınabilir.',
    crumbs: [['Eşleştirme Kuyruğu']],
  });

  async function refresh() {
    open = await queueView('open');
    decided = tab === 'decided' ? await queueView('decided') : null;
    render();
  }

  function rows() {
    if (tab === 'decided') return decided?.items || [];
    if (tab === 'other') return open.items.filter((q) => !['review', 'insufficient'].includes(q.kind));
    return open.items.filter((q) => q.kind === tab);
  }

  function render() {
    clear(box);
    const count = (k) => open.items.filter((q) => q.kind === k).length;
    const others = open.items.filter((q) => !['review', 'insufficient'].includes(q.kind)).length;
    const tabItems = [{ id: 'review', label: 'İnceleme', count: count('review') },
      { id: 'insufficient', label: 'Kimliği yetersiz', count: count('insufficient') }];
    if (others) tabItems.push({ id: 'other', label: 'Eşleşmeyen (gölge/devir)', count: others });
    tabItems.push({ id: 'decided', label: mode === 'api' ? 'Karar verilenler' : 'Bu oturumda karar verilen' });
    box.append(
      tabs(tabItems, tab, async (t) => {
        tab = t;
        if (t === 'decided') { decided = null; render(); decided = await queueView('decided'); }
        render();
      }, 'Kuyruk görünümü'),
      dataTable({
        label: 'Eşleştirme kuyruğu', rows: rows(), idOf: (q) => q.id, selectedId: () => selected,
        onOpen: (q, tr) => openItem(q.id, tr),
        empty: tab === 'decided' ? (decided === null ? 'Yükleniyor…' : 'Henüz karar verilmedi.') : 'Bu görünümde bekleyen kayıt yok.',
        columns: [
          { key: 'code', label: 'Kayıt', render: (q) => h('span', { class: 'cell-primary' }, q.code || q.id) },
          { key: 'data_source', label: 'Kaynak', render: (q) => srcChip(q.data_source, q.role) },
          { key: 'name', label: 'Kaynak kaydı', sortValue: (q) => q.record.name,
            render: (q) => h('span', {}, q.record.name || q.source_key, h('span', { class: 'cell-sub' }, q.class_label)) },
          { key: 'summary', label: 'Neden', sortable: false, render: (q) => h('span', { class: 'small' }, q.reasons[0] || q.summary) },
          { key: 'best', label: 'En iyi aday', sortValue: (q) => q.candidates[0]?.name,
            render: (q) => (q.candidates.length ? h('span', {}, q.candidates[0].name,
              h('span', { class: 'cell-sub' }, q.candidates.length > 1 ? `+${q.candidates.length - 1} aday` : q.candidates[0].class_label)) : '—') },
          { key: 'state', label: 'Durum', sortable: false,
            render: (q) => (q.decision ? pill(q.decision.label + (q.decision.applied ? ' · uygulandı' : ''), 'good')
              : pill(KIND_LABEL[q.kind] || q.kind, q.kind === 'review' ? 'bad' : 'warn')) },
        ],
      }),
      h('p', { class: 'muted small' }, mode === 'api'
        ? 'Kararlar kalıcıdır ve karar verenin kimliğiyle kaydedilir. Uzlaştırıcı uyguladığında kimlik bağı kurulur ve CI güncellenir.'
        : 'Demo modu: kararlar yalnızca bu oturumda tutulur. API ile çalışırken kalıcı olarak kaydedilir.'),
    );
  }

  async function submit(q, body, opener) {
    try {
      const res = await decide(q, body);
      toast(`${q.code || q.id}: ${res.label || res.decision}. ${res.message || ''}`.trim());
      closeDetail();
      await refresh();
    } catch (err) {
      toast(`Karar kaydedilemedi: ${err.message}`);
      opener?.focus?.();
    }
  }

  function openItem(id, opener) {
    const q = [...open.items, ...(decided?.items || [])].find((x) => x.id === id);
    if (!q) return;
    selected = id;
    if (location.hash !== `#/kuyruk/${id}`) history.replaceState(null, '', `#/kuyruk/${id}`);
    const cands = q.candidates;
    let choice = cands[0]?.sys_id || null;
    const table = h('div', { class: 'tablewrap' }, h('table', { class: 'compare', 'aria-label': 'Kaynak kaydı ve adaylar' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Öznitelik'), h('th', {}, `Kaynak kaydı (${q.data_source})`),
        cands.map((c, i) => h('th', {}, `Aday ${i + 1}`)))),
      h('tbody', {}, COMPARE.filter(([k]) => q.record[k] || cands.some((c) => c.attributes[k])).map(([k, label]) => h('tr', {},
        h('th', { scope: 'row' }, label), h('td', {}, q.record[k] || '—'),
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
    const note = h('input', { type: 'text', id: 'decisionNote', placeholder: 'Gerekçe (isteğe bağlı; denetim izine yazılır)',
      'aria-label': 'Karar gerekçesi', maxlength: '2000' });
    const canDecide = open.can_decide && !q.decision;
    const act = (decision, extra = {}) => submit(q, { decision, note: note.value.trim() || null, ...extra }, opener);

    let footer = [];
    if (q.decision && mode === 'demo') {
      footer = [h('button', { class: 'btn secondary', type: 'button', onclick: () => { undoDemoDecision(q.id); toast(`${q.code}: karar geri alındı.`); closeDetail(); refresh(); } }, 'Kararı geri al')];
    } else if (canDecide && q.kind === 'review') {
      footer = [h('button', { class: 'btn secondary', type: 'button', onclick: () => act('defer') }, 'Ertele'),
        h('button', { class: 'btn secondary', type: 'button', onclick: () => act('new_ci') }, 'Ret: yeni CI aç'),
        cands.length ? h('button', { class: 'btn', type: 'button', id: 'mergeBtn', onclick: () => act('merge', { ci_sys_id: choice }) }, 'Adayla birleştir') : null];
    } else if (canDecide) {
      footer = [h('button', { class: 'btn secondary', type: 'button', onclick: () => act('fix_at_source') }, 'Kaynakta düzeltilsin'),
        cands.length ? h('button', { class: 'btn', type: 'button', onclick: () => act('merge', { ci_sys_id: cands[0].sys_id }) }, `Elle bağla: ${cands[0].name}`) : null];
    }

    openDetail({
      kicker: `Eşleştirme · ${q.code || q.id}`, title: q.record.name || q.source_key, opener,
      onClose: () => { selected = null; history.replaceState(null, '', '#/kuyruk'); box.querySelector('.tablewrap')?.refreshSelection?.(); },
      body: [
        h('div', { class: `notice ${q.kind === 'review' ? 'bad' : 'warn'}` }, h('strong', {}, q.summary.split(';')[0]),
          q.reasons.length ? h('ul', {}, q.reasons.map((r) => h('li', {}, r))) : null),
        q.decision ? h('p', { class: 'notice success' }, `Karar: ${q.decision.label}`,
          q.decision.by ? ` · ${q.decision.by}` : '', q.decision.at ? ` · ${formatDateTime(q.decision.at)}` : '',
          q.decision.applied ? ' · uzlaştırıcı uyguladı' : mode === 'api' ? ' · uygulanmayı bekliyor' : '') : null,
        section('Kaynak kaydı', kv([
          ['Kaynak', srcChip(q.data_source, q.role)], ['Kaynak anahtarı', h('span', { class: 'mono' }, q.source_key)],
          ['Sınıf ipucu', q.class_label], ['Gözlem', formatDateTime(q.observed_at)], q.rule ? ['Eşleşen kural', q.rule] : null,
          q.lookup.ip.length ? ['IP adresleri', q.lookup.ip.join(', ')] : null, q.lookup.mac.length ? ['MAC adresleri', q.lookup.mac.join(', ')] : null,
        ])),
        cands.length ? section('Yan yana karşılaştırma', table, h('p', { class: 'muted small' }, 'Yeşil: aynı · Kırmızı: farklı (çelişki). Ad karşılaştırması alan adı eki olmadan yapılır.')) : null,
        section('Kanıtlar (log-odds ağırlığı)', evidence),
        cands.length ? section('Adayların kaynakları', h('ul', { class: 'mini' }, cands.map((c, i) => h('li', {},
          h('a', { href: `#/ci/${c.sys_id}` }, `Aday ${i + 1}: ${c.name}`), ' ', h('span', { class: 'pills' }, c.sources.map((src) => srcChip(src)))))))
          : null,
        canDecide ? picker : null,
        canDecide ? section('Gerekçe', note) : null,
        !open.can_decide && !q.decision ? h('p', { class: 'notice' }, 'Karar vermek için Eşleştirme analisti rolü gerekir.') : null,
      ],
      footer,
    });
    box.querySelector('.tablewrap')?.refreshSelection?.();
  }

  view.append(box);
  render();
  const route = (p) => {
    if (p && p !== 'yetersiz') {
      const q = open.items.find((x) => x.id === p);
      if (q) { tab = ['review', 'insufficient'].includes(q.kind) ? q.kind : 'other'; render(); openItem(q.id); }
    } else {
      if (p === 'yetersiz') { tab = 'insufficient'; render(); }
      closeDetail({ silent: true });
    }
  };
  route(param);
  return { update: route };
}
