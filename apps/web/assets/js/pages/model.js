// Veri Modeli (U4.4, ADR-0019): ServiceNow uyumlu sınıf ağacı, alanlar, kimlik kuralları, değer listeleri,
// izinli ilişki kuralları ve ServiceNow'dan doğrulanacak öğeler. Tek kaynak metadata/*.yaml; değişiklik yalnızca
// değişiklik setiyle (taslak → onay → yayın). Kalıtılan alanlar ve ilişki önerileri ata zincirinden hesaplanır.
import { h, append, clear, dataTable, fold, formatNumber, kv, metric, pill, section, tabs } from '../lib/dom.js';
import { closeDetail, openDetail, setHeader } from '../lib/shell.js';
import { dataModel } from '../lib/data.js';
import { openFieldWizard } from './schema-wizard.js';

const TYPE_TR = { string: 'Metin', integer: 'Tam sayı', decimal: 'Ondalık', boolean: 'Evet/hayır', reference: 'Referans',
  glide_date: 'Tarih', glide_date_time: 'Tarih-saat' };
const SENS_TR = { public: 'Genel', internal: 'İç', confidential: 'Gizli', restricted: 'Kısıtlı' };
const ENF_TR = { off: 'Kapalı', warn: 'Uyar', enforce: 'Zorla' };
const ENF_TEXT = {
  off: 'Kurallar denetlenmez.',
  warn: 'İzinsiz ilişki yazılır ama kalite bulgusu olarak işaretlenir. Golden record\'a yazımda uygulanması U7\'de.',
  enforce: 'İzinsiz ilişki reddedilir; kaynak kaydı eşleştirme kuyruğuna gider.',
};
const BACKLOG_TR = [['classes', 'Sınıflar'], ['fields', 'Alanlar'], ['choices', 'Değer listeleri'],
  ['relationship_types', 'İlişki tipleri'], ['relationship_rules', 'İlişki kuralları'], ['identification', 'Kimlik kuralları']];
const TABS = ['siniflar', 'degerler', 'iliskiler', 'dogrulama'];
/** Kimlik kurallarında geçen ama her sınıfta alan olmayan öznitelikler (IRE arama tabloları, sanal makine anahtarları). */
const ATTR_TR = { serial_number_type: 'seri tipi', bios_uuid: 'BIOS UUID', vcenter_uuid: 'vCenter UUID', object_id: 'nesne kimliği',
  correlation_id: 'korelasyon kimliği', mac_address: 'MAC adresi', external_id: 'harici kimlik' };

export async function renderModel(view, param) {
  const m = await dataModel();
  const byName = Object.fromEntries(m.classes.map((c) => [c.name, c]));
  const relTypes = Object.fromEntries(m.relationship_types.map((t) => [t.name, t]));
  const counts = m.ci_counts || {};
  const label = (n) => byName[n]?.label_tr || n;

  const ancestors = (name) => {
    const out = [];
    for (let c = byName[name]; c; c = byName[c.parent]) out.push(c.name);
    return out;
  };
  const allFields = (name) => {
    const out = new Map();
    for (const a of ancestors(name).reverse()) for (const f of byName[a].fields) out.set(f.name, { ...f, from: a });
    return out;
  };
  const subtreeCount = (name) => (counts[name] || 0) + byName[name].children.reduce((n, k) => n + subtreeCount(k), 0);
  const relationsFor = (name) => {
    const chain = ancestors(name);
    const out = [];
    for (const r of m.relationship_rules.rules) {
      if (chain.includes(r.parent)) out.push({ dir: 'parent', other: r.child, rule: r, own: r.parent === name });
      if (chain.includes(r.child)) out.push({ dir: 'child', other: r.parent, rule: r, own: r.child === name });
    }
    return out;
  };
  const descriptor = (type, dir) => {
    const [p, c] = (relTypes[type]?.label_tr || type).split('::');
    return dir === 'parent' ? p : (c || p);
  };
  const classLink = (n) => h('a', { href: `#/veri-modeli/${n}`, class: 'chip link', title: n }, label(n));
  const verifyPill = (on) => (on ? pill('Doğrulanacak', 'warn') : null);

  function identText(c) {
    const id = c.identification;
    if (!id) return h('span', { class: 'muted' }, 'Kural yok: olasılıksal skor');
    const fields = allFields(c.name);
    const attr = (a) => fields.get(a)?.label_tr || ATTR_TR[a] || a;
    const first = id.entries.slice(0, 2).map((e) => e.attributes.map(attr).join(' + ')).join(' → ');
    return h('span', {}, id.independent ? first + (id.entries.length > 2 ? ' → …' : '') : `Bağımlı: ${first}`,
      id.inherited ? h('span', { class: 'cell-sub' }, `kalıtılan: ${label(id.applies_to)}`) : null);
  }

  // ---------------------------------------------------------------- sınıf ayrıntısı
  let selected = null;
  let table = null;

  function fieldTable(rows, caption) {
    return h('div', { class: 'tablewrap' }, h('table', { 'aria-label': caption },
      h('thead', {}, h('tr', {}, h('th', {}, 'Alan'), h('th', {}, 'Tip'), h('th', {}, 'Depolama'), h('th', {}, 'Hassasiyet'))),
      h('tbody', {}, rows.map((f) => h('tr', {},
        h('td', {}, h('span', { class: 'cell-primary' }, f.label_tr), h('span', { class: 'cell-sub mono' }, f.name),
          f.verify ? verifyPill(true) : null, f.deprecated ? pill('Kullanımdan kalktı', 'bad') : null),
        h('td', {}, TYPE_TR[f.type] || f.type,
          f.reference ? h('span', { class: 'cell-sub' }, `→ ${byName[f.reference]?.label_tr || refLabel(f.reference)}`) : null,
          f.choice ? h('span', { class: 'cell-sub' }, 'değer listeli') : null),
        h('td', {}, f.storage === 'column' ? 'Kolon' : 'JSONB', f.indexed ? h('span', { class: 'cell-sub' }, 'indeksli') : null),
        h('td', {}, SENS_TR[f.sensitivity] || f.sensitivity))))));
  }
  const refLabel = (n) => m.reference_tables.find((t) => t.name === n)?.label_tr || n;

  function identSection(c) {
    const id = c.identification;
    if (!id) {
      return section('Kimlik kuralı', h('p', { class: 'muted' },
        'Bu sınıf ve atalarında tanımlama kuralı yok. Kayıtlar olasılıksal skorla eşleştirilir; çelişkide kuyruğa düşer.'));
    }
    const fields = allFields(c.name);
    const ctxText = !id.context ? null
      : id.context.kind === 'relation' ? `Üst bağlam: ${label(id.context.parent_class)} (${relTypes[id.context.type]?.label_tr || id.context.type})`
        : `Üst bağlam: ${fields.get(id.context.field)?.label_tr || id.context.field} alanındaki CI`;
    return section('Kimlik kuralı',
      kv([['Kural', id.inherited ? h('span', {}, classLink(id.applies_to), ' sınıfından kalıtılan') : 'Bu sınıfın kendi kuralı'],
        ['Tür', id.independent ? 'Bağımsız: kurum genelinde aranır' : 'Bağımlı: yalnızca üst CI bağlamında tanımlanır'],
        ctxText ? ['Bağlam', ctxText] : null,
        id.description ? ['Açıklama', id.description] : null]),
      h('ol', { class: 'rule-steps' }, id.entries.map((e) => h('li', {},
        h('span', { class: 'mono' }, String(e.priority)), ' ',
        e.attributes.map((a) => fields.get(a)?.label_tr || ATTR_TR[a] || a).join(' + '),
        e.search_table ? h('span', { class: 'cell-sub' }, `${e.search_table} tablosunda`) : null,
        e.evren_extension ? pill('Evren uzantısı', 'info') : null))),
      !id.independent ? h('p', { class: 'muted small' }, 'Üst bağlam çözümlemesi U7\'de; o zamana kadar bu sınıfın kayıtları güvenli varsayılanla "kimlik yetersiz" olarak kuyruğa düşer.') : null,
      verifyPill(id.verify));
  }

  function relationSection(c) {
    const rels = relationsFor(c.name);
    if (!rels.length) return section('İzinli ilişkiler', h('p', { class: 'muted' }, 'Kural yok.'));
    return section('İzinli ilişkiler',
      h('p', { class: 'muted small' }, `Zorlama kipi: ${ENF_TR[m.relationship_rules.enforcement]}. Kurallar alt sınıflara kalıtılır.`),
      h('ul', { class: 'rel-list' }, rels.map((r) => h('li', {},
        r.dir === 'parent'
          ? h('span', {}, h('strong', {}, 'Bu sınıf'), ` ${descriptor(r.rule.type, 'parent')} → `, classLink(r.other))
          : h('span', {}, classLink(r.other), ` ${descriptor(r.rule.type, 'parent')} → `, h('strong', {}, 'bu sınıf')),
        !r.own ? h('span', { class: 'cell-sub' }, `kural: ${label(r.rule.parent)} → ${label(r.rule.child)}`) : null,
        r.rule.verify ? verifyPill(true) : null))));
  }

  function openClass(name, opener) {
    const c = byName[name];
    if (!c) return;
    selected = name;
    if (location.hash !== `#/veri-modeli/${name}`) history.replaceState(null, '', `#/veri-modeli/${name}`);
    const fields = [...allFields(name).values()];
    const own = fields.filter((f) => f.from === name);
    const inherited = fields.filter((f) => f.from !== name);
    const chain = ancestors(name).slice(1).reverse();
    const footer = [];
    if (m.can_draft) {
      footer.push(h('button', { class: 'btn', type: 'button', id: 'modelAddField',
        onclick: () => openFieldWizard({ table: name }) }, 'Bu sınıfa alan ekle'));
    }
    openDetail({
      kicker: 'Sınıf', title: c.label_tr, opener, footer,
      body: [
        kv([['Teknik ad', h('span', { class: 'mono' }, c.name)], ['ServiceNow etiketi', c.label],
          ['Üst sınıflar', chain.length ? h('span', { class: 'chips' }, chain.map(classLink)) : 'Kök sınıf'],
          ['Alt sınıflar', c.children.length ? h('span', { class: 'chips' }, c.children.map(classLink)) : 'Yok'],
          ['Kimlik ailesi', c.family ? label(c.family) : '—'],
          ['SQL görünümü', h('span', { class: 'mono' }, c.sn_view)],
          ['CI sayısı', `${formatNumber(counts[name] || 0)} bu sınıfta · alt sınıflar dahil ${formatNumber(subtreeCount(name))}`],
          ['Doğrulama', c.verify ? pill('ServiceNow örneğinden doğrulanacak', 'warn') : 'ServiceNow temel sınıfı']]),
        section(`Alanlar (${own.length} kendi, ${inherited.length} kalıtılan)`,
          own.length ? fieldTable(own, 'Bu sınıfın alanları') : h('p', { class: 'muted' }, 'Bu sınıf yeni alan tanımlamıyor; alanlarını atalarından alır.'),
          inherited.length ? h('details', { class: 'inherited' }, h('summary', {}, `Kalıtılan alanlar (${inherited.length})`),
            fieldTable(inherited, 'Kalıtılan alanlar')) : null),
        identSection(c),
        relationSection(c),
        !m.can_draft ? h('p', { class: 'muted small' }, m.demo
          ? 'Alan ve sınıf değişiklikleri Evren API\'siyle çalışırken değişiklik setiyle yapılır (taslak → onay → yayın).'
          : 'Değişiklik seti hazırlama yetkiniz yok (Şema tasarımcısı rolü gerekir).') : null,
      ],
      onClose: () => { selected = null; history.replaceState(null, '', '#/veri-modeli'); table?.refreshSelection(); },
    });
    table?.refreshSelection();
  }

  // ---------------------------------------------------------------- sekmeler
  let tab = TABS.includes(param) ? param : 'siniflar';
  let query = '';
  let verifyOnly = false;
  const box = h('div');
  const listBox = h('div');

  function visibleClasses() {
    const q = fold(query.trim());
    if (!q && !verifyOnly) return m.classes.map((c) => ({ c, context: false }));
    const hit = new Set(m.classes.filter((c) => (!q || fold(`${c.label_tr} ${c.name} ${c.label}`).includes(q))
      && (!verifyOnly || c.verify)).map((c) => c.name));
    const keep = new Set();
    for (const n of hit) for (const a of ancestors(n)) keep.add(a);
    return m.classes.filter((c) => keep.has(c.name)).map((c) => ({ c, context: !hit.has(c.name) }));
  }

  function renderList() {
    const rows = visibleClasses();
    const hits = rows.filter((r) => !r.context).length;
    table = dataTable({
      label: 'Sınıf ağacı', rows, idOf: (r) => r.c.name, selectedId: () => selected,
      empty: 'Aramaya uyan sınıf yok.', onOpen: (r, tr) => openClass(r.c.name, tr),
      columns: [
        { key: 'name', label: 'Sınıf', sortable: false, render: ({ c, context }) => h('span', { class: `tree-cell lvl-${Math.min(c.depth, 7)}${context ? ' ctx' : ''}` },
          h('span', { class: 'cell-primary' }, c.label_tr, c.verify ? ' ' : null, verifyPill(c.verify)),
          h('span', { class: 'cell-sub mono' }, c.name)) },
        { key: 'ident', label: 'Kimlik kuralı', sortable: false, render: ({ c }) => identText(c) },
        { key: 'fields', label: 'Alan (kendi/toplam)', sortable: false, num: true, render: ({ c }) => `${c.fields.length} / ${c.field_count}` },
        { key: 'cis', label: 'CI (alt sınıflarla)', sortable: false, num: true, render: ({ c }) => {
          const all = subtreeCount(c.name);
          const own = counts[c.name] || 0;
          return h('span', {}, formatNumber(all), all !== own ? h('span', { class: 'cell-sub' }, `kendisi ${formatNumber(own)}`) : null);
        } },
      ],
    });
    clear(listBox).append(
      h('p', { class: 'muted small', 'aria-live': 'polite', id: 'modelCount' },
        query || verifyOnly ? `${hits} sınıf eşleşti (bağlam için üst sınıflar da gösteriliyor).` : `${m.classes.length} sınıf, kalıtım sırasıyla.`),
      table);
  }

  function classesTab() {
    const search = h('input', { type: 'search', class: 'searchbar', id: 'modelSearch', value: query,
      placeholder: 'Sınıf adı veya ServiceNow tablo adı', 'aria-label': 'Sınıf ara' });
    search.addEventListener('input', () => { query = search.value; renderList(); });
    const only = h('input', { type: 'checkbox', id: 'modelVerifyOnly' });
    only.checked = verifyOnly;
    only.addEventListener('change', () => { verifyOnly = only.checked; renderList(); });
    renderList();
    return [h('div', { class: 'toolbar' }, search,
      h('label', { class: 'check', for: 'modelVerifyOnly' }, only, h('span', {}, 'Yalnızca doğrulanacaklar'))), listBox];
  }

  function valuesTab() {
    const lifecycleFirst = [...m.choices].sort((a, b) => Number(!a.element.startsWith('life_cycle')) - Number(!b.element.startsWith('life_cycle')));
    return lifecycleFirst.map((ch) => {
      const parent = ch.depends_on ? m.choices.find((x) => x.table === ch.table && x.element === ch.depends_on) : null;
      const pLabel = (v) => parent?.values.find((x) => x.value === v)?.label_tr || v;
      const legacyLabel = (el, v) => m.choices.find((x) => x.table === ch.table && x.element === el)?.values.find((x) => x.value === v)?.label_tr || v;
      const hasLegacy = ch.values.some((v) => v.legacy);
      return section(`${ch.label_tr}`,
        h('p', { class: 'muted small' }, h('span', { class: 'mono' }, `${ch.table}.${ch.element}`),
          parent ? ` · ${parent.label_tr} değerine bağımlı` : '', ch.verify ? ' ' : '', verifyPill(ch.verify)),
        h('div', { class: 'tablewrap' }, h('table', { 'aria-label': ch.label_tr },
          h('thead', {}, h('tr', {}, h('th', {}, 'Değer'), h('th', {}, 'Etiket'), parent ? h('th', {}, parent.label_tr) : null,
            hasLegacy ? h('th', {}, 'Eski durum alanları') : null)),
          h('tbody', {}, ch.values.map((v) => h('tr', {},
            h('td', { class: 'mono' }, v.value), h('td', {}, v.label_tr, v.label !== v.label_tr ? h('span', { class: 'cell-sub' }, v.label) : null),
            parent ? h('td', {}, pLabel(v.dependent_value)) : null,
            hasLegacy ? h('td', {}, Object.entries(v.legacy || {}).map(([el, val]) => h('span', { class: 'cell-sub' },
              `${el === 'operational_status' ? 'Operasyonel' : el === 'install_status' ? 'Kurulum' : el}: ${legacyLabel(el, val)}`))) : null))))));
    });
  }

  function relationsTab() {
    const rr = m.relationship_rules;
    return [
      h('div', { class: 'notice' }, h('strong', {}, `Zorlama kipi: ${ENF_TR[rr.enforcement]}. `), ENF_TEXT[rr.enforcement],
        ' ServiceNow bu listeyi yalnızca önerir; Evren denetler ve ters yönü ayrıca tanır.'),
      h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'İzinli ilişki kuralları' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Ebeveyn'), h('th', {}, 'İlişki'), h('th', {}, 'Çocuk'), h('th', {}, 'Açıklama'))),
        h('tbody', {}, rr.rules.map((r) => h('tr', {},
          h('td', {}, classLink(r.parent)), h('td', {}, descriptor(r.type, 'parent'), h('span', { class: 'cell-sub mono' }, r.type)),
          h('td', {}, classLink(r.child)), h('td', {}, r.description || '—', r.verify ? ' ' : null, verifyPill(r.verify))))))),
      section(`İlişki tipleri (${m.relationship_types.length})`,
        h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'İlişki tipleri' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Tip'), h('th', {}, 'Türkçe'))),
          h('tbody', {}, m.relationship_types.map((t) => h('tr', {}, h('td', { class: 'mono' }, t.name),
            h('td', {}, t.label_tr || '—', t.verify ? ' ' : null, verifyPill(t.verify)))))))),
    ];
  }

  const VERDICT_TR = { confirmed: ['Belgeyle doğrulandı', 'good'], corrected: ['Belgeye göre düzeltildi', 'info'],
    partial: ['Kısmen doğrulandı', 'warn'], not_found: ['Belgede bulunamadı', 'bad'], evren: ['Evren tanımı', 'neutral'] };
  const KIND_TR = Object.fromEntries(BACKLOG_TR);

  function evidenceTable(items) {
    const src = m.verification.sources || {};
    return h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Doğrulama kanıtları' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Öğe'), h('th', {}, 'Sonuç'), h('th', {}, 'Belgedeki değer ve not'), h('th', {}, 'Kaynak'))),
      h('tbody', {}, items.map((it) => h('tr', {},
        h('td', {}, it.kind === 'classes' || it.kind === 'identification' ? classLink(it.key) : h('span', { class: 'mono' }, it.key),
          h('span', { class: 'cell-sub' }, KIND_TR[it.kind] || it.kind)),
        h('td', {}, pill(...(VERDICT_TR[it.verdict] || [it.verdict, 'neutral']))),
        h('td', {}, it.documented ? h('span', { class: 'mono' }, it.documented) : null, it.note ? h('span', { class: 'cell-sub' }, it.note) : null),
        h('td', {}, (it.sources || []).map((ref) => (src[ref]
          ? h('a', { href: src[ref].url, target: '_blank', rel: 'noopener noreferrer', class: 'src-link' }, src[ref].title)
          : h('span', { class: 'mono' }, ref)))))))));
  }

  function verifyTab() {
    const ledger = m.verification?.items || [];
    const settled = ledger.filter((it) => ['confirmed', 'corrected', 'evren'].includes(it.verdict));
    const open = ledger.filter((it) => !['confirmed', 'corrected', 'evren'].includes(it.verdict));
    return [
      m.stats.to_verify
        ? h('div', { class: 'notice warn' }, h('strong', {}, `${m.stats.to_verify} öğe hâlâ doğrulanacak. `),
          'Temel ServiceNow adları herkese açık belgelerden doğrulandı; belgede bulunamayanlar ve kurum örneğine özgü özelleştirmeler canlı kabulde (U10) salt okunur dışa aktarımla (tools/sn_metadata_export.py) denetlenir.')
        : h('div', { class: 'notice success' }, h('strong', {}, 'Doğrulanacak öğe kalmadı. '), 'Kurum örneğine özgü özelleştirmeler canlı kabulde denetlenir.'),
      ledger.length ? section(`Belgeyle sonuçlananlar (${settled.length})${m.verification.checked_on ? ` · ${m.verification.checked_on}` : ''}`,
        evidenceTable(settled)) : null,
      open.length ? section(`Belgede tam karşılığı bulunamayanlar (${open.length})`, evidenceTable(open)) : null,
      ...BACKLOG_TR.filter(([k]) => m.backlog[k]?.length).map(([k, title]) => section(`İşaretli: ${title} (${m.backlog[k].length})`,
        h('ul', { class: 'chips wrap' }, m.backlog[k].map((item) => h('li', {}, k === 'classes' || k === 'identification'
          ? classLink(item) : h('span', { class: 'chip plain mono' }, item)))))),
    ];
  }

  function render() {
    const items = [{ id: 'siniflar', label: 'Sınıflar', count: m.stats.classes },
      { id: 'degerler', label: 'Değer listeleri', count: m.stats.choice_lists },
      { id: 'iliskiler', label: 'İlişki kuralları', count: m.stats.relationship_rules },
      { id: 'dogrulama', label: 'Doğrulanacaklar', count: m.stats.to_verify }];
    append(clear(box), [
      tabs(items, tab, (t) => { tab = t; closeDetail({ silent: true }); history.replaceState(null, '', `#/veri-modeli/${t}`); render(); }, 'Veri modeli bölümü'),
      ...(tab === 'siniflar' ? classesTab() : tab === 'degerler' ? valuesTab() : tab === 'iliskiler' ? relationsTab() : verifyTab()),
    ]);
  }

  setHeader({
    title: 'Veri Modeli',
    sub: 'ServiceNow uyumlu sınıf ağacı, alanlar, kimlik kuralları, değer listeleri ve izinli ilişkiler. Tek kaynak metadata/*.yaml; değişiklik yalnızca değişiklik setiyle yapılır.',
    crumbs: [['Veri Modeli']],
  });
  view.append(
    m.demo ? h('p', { class: 'notice' }, h('strong', {}, 'Demo modu. '), 'Model metadata\'dan üretilmiş statik dosyadan okunuyor; CI sayıları sentetik demo envanterinden.') : null,
    h('div', { class: 'cards', id: 'modelStats' },
      metric({ label: 'Sınıf', value: m.stats.classes, foot: `${m.stats.identification_rules} kimlik kuralı` }),
      metric({ label: 'Alan', value: m.stats.fields, foot: `${m.stats.core_columns} kök kolon · gerisi JSONB` }),
      metric({ label: 'İlişki kuralı', value: m.stats.relationship_rules, foot: `${m.stats.relationship_types} tip · kip: ${ENF_TR[m.relationship_rules.enforcement]}` }),
      metric({ label: 'Doğrulanacak', value: m.stats.to_verify, tone: m.stats.to_verify ? 'warn' : 'good', foot: 'ServiceNow örneğinden', href: '#/veri-modeli/dogrulama' })),
    box);
  render();
  if (param && byName[param]) openClass(param);

  return {
    update: (p) => {
      if (p && byName[p]) {
        if (tab !== 'siniflar') { tab = 'siniflar'; render(); }
        openClass(p);
      } else if (p && TABS.includes(p) && p !== tab) {
        tab = p; closeDetail({ silent: true }); render();
      } else if (!p) closeDetail({ silent: true });
    },
  };
}
