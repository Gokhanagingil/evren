// GRC Kontrolleri: GRC Platformu'na (tedarik) giden CMDB tabanlı kontrol sonuçlarının önizlemesi.
import { h, dataTable, kv, metric, pill, section } from '../lib/dom.js';
import { closeDetail, openDetail, setHeader } from '../lib/shell.js';
import { CONTROL, demo } from '../lib/data.js';

export async function renderControls(view, param) {
  const d = await demo();
  let selected = null;
  let table = null;

  setHeader({
    title: 'GRC Kontrolleri',
    sub: 'GRC Platformu\'nun CMDB modülü ve otomatik kontrolleri Evren\'den beslenecek. Her sonuç kaynak bilgisi (provenance) taşır; kısmi toplanan veri "kesin değil" üretir.',
    crumbs: [['GRC Kontrolleri']],
  });
  const n = (st) => d.controls.filter((c) => c.status === st).length;

  function open(id, opener) {
    const c = d.controls.find((x) => x.id === id);
    if (!c) return;
    selected = id;
    if (location.hash !== `#/kontroller/${id}`) history.replaceState(null, '', `#/kontroller/${id}`);
    const [label, tone] = CONTROL[c.status];
    openDetail({
      kicker: `GRC kontrolü · ${c.id}`, title: c.title, opener,
      onClose: () => { selected = null; history.replaceState(null, '', '#/kontroller'); table?.refreshSelection(); },
      body: [
        h('div', { class: 'pills' }, pill(label, tone)),
        h('p', { class: `notice ${c.status === 'inconclusive' ? 'warn' : c.status === 'fail' ? 'bad' : 'success'}` }, c.why),
        section('Kaynak bilgisi (provenance)', kv([
          ['source_system', h('span', { class: 'mono' }, c.provenance.source_system)],
          ['query_reference', h('span', { class: 'mono' }, c.provenance.query_reference)],
          ['collection_status', h('span', { class: 'mono' }, c.provenance.collection_status)],
          ['Bağımlı kaynak', c.provenance.depends_on || '—'],
        ])),
        section(`Kanıt (${c.evidence.length})`, c.evidence.length
          ? h('ul', { class: 'mini' }, c.evidence.map((e) => h('li', {}, h('a', { href: `#/ci/${e.sys_id}` }, e.name), h('span', { class: 'cell-sub mono' }, e.sys_id))))
          : h('p', { class: 'muted' }, 'Kanıt yok.')),
      ],
    });
    table?.refreshSelection();
  }

  table = dataTable({
    label: 'GRC kontrolleri', rows: d.controls, idOf: (c) => c.id, selectedId: () => selected, onOpen: (c, tr) => open(c.id, tr),
    columns: [
      { key: 'id', label: 'Kontrol', render: (c) => h('span', { class: 'cell-primary' }, c.id) },
      { key: 'title', label: 'Başlık' },
      { key: 'status', label: 'Sonuç', render: (c) => pill(...CONTROL[c.status]) },
      { key: 'evidence', label: 'Kanıt', num: true, sortValue: (c) => String(c.evidence.length).padStart(4, '0'), render: (c) => String(c.evidence.length) },
      { key: 'collection', label: 'Toplama', sortable: false, render: (c) => (c.provenance.collection_status === 'complete' ? 'Tam' : pill('Kısmi', 'warn')) },
      { key: 'depends', label: 'Bağımlı kaynak', sortable: false, render: (c) => c.provenance.depends_on || '—' },
    ],
  });

  view.append(
    h('div', { class: 'cards' },
      metric({ label: 'Kontrol', value: d.controls.length, foot: 'Bu önizlemede tanımlı' }),
      metric({ label: 'Başarısız', value: n('fail'), tone: n('fail') ? 'bad' : 'good', foot: 'Kanıt listesiyle gönderilir' }),
      metric({ label: 'Kesin değil', value: n('inconclusive'), tone: n('inconclusive') ? 'warn' : 'good', foot: 'Kaynak verisi kısmi toplandı' }),
      metric({ label: 'Başarılı', value: n('pass'), tone: 'good', foot: 'Bulgu yok' })),
    table,
    h('p', { class: 'muted small' }, 'Sözleşme GRC Platformu ile ortaktır: idempotency_key, provenance (source_system, query_reference, collection_status) ve kısmi toplamada inconclusive (docs/08).'),
  );
  if (param) open(param);
  return { update: (p) => (p ? open(p) : closeDetail({ silent: true })) };
}
