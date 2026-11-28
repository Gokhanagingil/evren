// Gölge mod raporu (F0-13): mevcut CMDB (ITSM rolü; bugün ServiceNow) Evren'in kaynak gözlemleriyle karşılaştırılır.
// ServiceNow'a hiçbir şey yazılmaz. Rapor değiştirilemez kanıt olarak kaydedilip GRC Platformu'na verilir.
import { h, clear, dataTable, formatDateTime, formatNumber, metric, pill, section, tabs } from '../lib/dom.js';
import { setHeader, toast } from '../lib/shell.js';
import { backend, createShadowSnapshot, shadowReport, shadowSnapshots, srcChip } from '../lib/data.js';

const VERDICT_TONE = { kesin: 'bad', güçlü: 'warn', olası: 'neutral', çelişkili: 'info' };
const STATUS = { conclusive: ['Kesin', 'good'], inconclusive: ['Kesin değil', 'warn'] };

const ciLink = (r) => h('a', { href: `#/ci/${r.sys_id}` }, r.name);

function pct(part, total) {
  return total ? ` (%${Math.round((1000 * part) / total) / 10})` : '';
}

function coverageTable(cov) {
  const cell = (n, total, bad) => h('span', { class: bad && n ? 'gap' : '' }, formatNumber(n), h('span', { class: 'cell-sub' }, pct(n, total).trim()));
  return dataTable({
    label: 'Sınıf ve rol bazında kapsam', rows: cov.matrix, idOf: (r) => r.class,
    columns: [
      { key: 'class_label', label: 'Sınıf', render: (r) => h('span', { class: 'cell-primary' }, r.class_label) },
      { key: 'total', label: 'CI', render: (r) => formatNumber(r.total) },
      { key: 'itsm', label: 'Mevcut CMDB', render: (r) => cell(r.itsm, r.total) },
      { key: 'discovery', label: 'Keşif', render: (r) => cell(r.discovery, r.total) },
      { key: 'monitoring', label: 'İzleme', render: (r) => cell(r.monitoring, r.total) },
      { key: 'security', label: 'Uç nokta güvenliği', render: (r) => cell(r.security, r.total) },
      { key: 'itsm_only', label: 'Keşif görmüyor', render: (r) => cell(r.itsm_only, r.itsm, true) },
      { key: 'not_in_itsm', label: 'CMDB\'de yok', render: (r) => cell(r.not_in_itsm, r.discovery, true) },
    ],
  });
}

function duplicateList(dup) {
  if (!dup.groups.length) return h('p', { class: 'muted' }, 'Mevcut CMDB içinde ortak kimlik anahtarı paylaşan kayıt bulunmadı.');
  return h('ul', { class: 'mini dup-list', id: 'shadowDuplicates' }, dup.groups.map((g) => h('li', {},
    h('div', { class: 'pills' }, pill(g.verdict_label, VERDICT_TONE[g.verdict] || 'neutral'),
      g.hardware_conflict && g.verdict !== 'çelişkili' ? pill('Seri farklı', 'warn') : null),
    h('div', { class: 'small' }, 'Ortak: ', g.evidence.map((e) => `${e.key} ${e.value}`).join(' · ')),
    h('div', { class: 'tablewrap' }, h('table', { class: 'compact-table', 'aria-label': 'Gruptaki kayıtlar' },
      h('thead', {}, h('tr', {}, ['Kayıt', 'Seri numarası', 'FQDN', 'IP adresi', 'Keşif'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, g.cis.map((c) => h('tr', {},
        h('td', {}, ciLink(c), h('span', { class: 'cell-sub mono' }, c.sys_id)),
        h('td', { class: 'mono' }, c.serial_number || '—'), h('td', {}, c.fqdn || '—'), h('td', {}, c.ip_address || '—'),
        h('td', {}, c.seen_by_discovery ? pill('Görüyor', 'good') : pill('Görmüyor', 'bad'))))))),
    h('p', { class: 'small advice' }, h('strong', {}, 'Öneri: '), g.advice))));
}

function ciTable(rows, label, empty) {
  return dataTable({
    label, rows, idOf: (r) => r.sys_id, empty, onOpen: (r) => { location.hash = `#/ci/${r.sys_id}`; },
    columns: [
      { key: 'name', label: 'CI', render: (r) => h('span', { class: 'cell-primary' }, r.name) },
      { key: 'class_label', label: 'Sınıf', render: (r) => pill(r.class_label, 'neutral') },
      { key: 'ip_address', label: 'IP adresi', render: (r) => r.ip_address || '—' },
      { key: 'roles', label: 'Gören roller', sortable: false, render: (r) => r.roles.join(', ') },
    ],
  });
}

function driftSection(drift) {
  const box = h('div');
  let showCosmetic = false;
  const toggle = h('label', { class: 'choice' },
    h('input', { type: 'checkbox', id: 'driftCosmetic', onchange: (ev) => { showCosmetic = ev.target.checked; paint(); } }),
    h('span', {}, 'Yalnız yazım farklarını da göster (büyük/küçük harf, alan adı eki)'));
  function paint() {
    const rows = drift.samples.filter((r) => showCosmetic || !r.cosmetic);
    clear(box).append(dataTable({
      label: 'Değer farkı örnekleri', rows, idOf: (r) => `${r.sys_id}:${r.attribute}`,
      empty: 'Gerçek değer farkı yok.', onOpen: (r) => { location.hash = `#/ci/${r.sys_id}`; },
      columns: [
        { key: 'name', label: 'CI', render: (r) => h('span', { class: 'cell-primary' }, r.name, h('span', { class: 'cell-sub' }, r.class_label)) },
        { key: 'label', label: 'Öznitelik', render: (r) => h('span', {}, r.label, r.cosmetic ? h('span', { class: 'cell-sub' }, 'yalnız yazım') : null) },
        { key: 'itsm_value', label: 'Mevcut CMDB değeri', render: (r) => h('span', { class: 'small' }, r.itsm_display ?? r.itsm_value ?? '—') },
        { key: 'golden_value', label: 'Evren değeri', render: (r) => h('span', {}, h('span', { class: 'small' }, r.golden_display ?? r.golden_value ?? '—'), ' ', srcChip(r.golden_source)) },
      ],
    }));
  }
  paint();
  return [
    dataTable({
      label: 'Özniteliğe göre değer farkı', rows: drift.by_attribute, idOf: (r) => r.attribute, empty: 'Değer farkı yok.',
      columns: [
        { key: 'label', label: 'Öznitelik', render: (r) => h('span', { class: 'cell-primary' }, r.label) },
        { key: 'real', label: 'Gerçek fark', render: (r) => (r.real ? pill(String(r.real), 'warn') : '0') },
        { key: 'cosmetic', label: 'Yalnız yazım', render: (r) => String(r.cosmetic) },
        { key: 'count', label: 'Toplam', render: (r) => String(r.count) },
      ],
    }),
    h('div', { class: 'toolbar' }, toggle), box,
  ];
}

function evidenceSection(report, snaps, refresh) {
  const list = snaps.items.length
    ? h('ul', { class: 'mini', id: 'shadowSnapshots' }, snaps.items.map((x) => h('li', {},
        h('div', { class: 'pills' }, pill(...(STATUS[x.status] || ['—', 'neutral'])), pill(x.collection_status === 'complete' ? 'Tam toplama' : 'Kısmi toplama', 'neutral')),
        h('div', { class: 'small' }, `${formatDateTime(x.generated_at)} · ${x.generated_by}`, x.note ? ` · ${x.note}` : ''),
        h('div', { class: 'cell-sub mono', title: x.sha256 }, `SHA-256 ${x.sha256.slice(0, 16)}…`),
        h('a', { class: 'btn secondary small', href: `/api/v1/reports/shadow/snapshots/${x.id}`, download: '' }, 'JSON indir'))))
    : h('p', { class: 'muted small' }, 'Henüz kanıt kaydedilmedi.');
  let form = null;
  if (report.can_snapshot) {
    const note = h('input', { type: 'text', id: 'snapshotNote', maxlength: '2000', placeholder: 'Ör. 2026-Q4 CMDB kapsam denetimi',
      'aria-label': 'Kanıt notu' });
    const btn = h('button', { class: 'btn', type: 'button', id: 'snapshotCreate', onclick: async () => {
      btn.disabled = true;
      try {
        const res = await createShadowSnapshot(note.value.trim());
        toast(`${res.message} SHA-256 ${res.sha256.slice(0, 12)}…`);
        await refresh();
      } catch (err) {
        btn.disabled = false;
        toast(`Kanıt kaydedilemedi: ${err.message}`);
      }
    } }, 'Kanıt olarak kaydet');
    form = h('div', { class: 'toolbar evidence-form' }, note, btn);
  }
  return [
    h('p', { class: 'muted small' }, 'Kanıt, raporun o anki hâlidir: değiştirilemez, SHA-256 özetiyle doğrulanır ve GRC Platformu bu kaydı ',
      h('span', { class: 'mono' }, 'provenance'), ' ve "kesin değil" bilgisiyle alır.'),
    form || h('p', { class: 'muted small' }, 'Kanıt kaydetmek için Veri sorumlusu veya Denetçi rolü gerekir.'),
    list,
  ];
}

export async function renderShadow(view) {
  const { mode } = await backend();
  setHeader({
    title: 'Gölge Mod Raporu',
    sub: 'Mevcut CMDB ile Evren\'in gözlemlerinin karşılaştırması.',
    crumbs: [['Gölge Mod Raporu']],
  });
  if (mode !== 'api') {
    view.append(h('div', { class: 'notice' }, h('strong', {}, 'Bu rapor veritabanından üretilir. '),
      'Demo modunda (statik sunucu) kullanılamaz; Evren API\'siyle çalışırken mevcut CMDB\'deki mükerrer adayları, kapsam boşlukları ve değer farkları burada listelenir ve kanıt olarak kaydedilir.'));
    return {};
  }
  const box = h('div');
  view.append(box);
  let listTab = 'itsm_only';

  async function render() {
    const [r, snaps] = await Promise.all([shadowReport(), shadowSnapshots()]);
    const cov = r.coverage;
    const t = cov.totals;
    const [statusLabel, statusTone] = STATUS[r.status];
    const gaps = h('div');
    const paintGaps = () => clear(gaps).append(
      tabs([{ id: 'itsm_only', label: 'Keşfin görmediği CMDB kaydı', count: cov.itsm_only.length },
        { id: 'not_in_itsm', label: 'CMDB\'de olmayan keşif kaydı', count: cov.not_in_itsm.length }], listTab,
      (id) => { listTab = id; paintGaps(); gaps.querySelector(`#tab-${id}`)?.focus(); }, 'Kapsam boşluğu görünümü'),
      listTab === 'itsm_only'
        ? ciTable(cov.itsm_only, 'Mevcut CMDB\'de olup keşfin görmediği CI\'lar', 'Keşfin görmediği CMDB kaydı yok.')
        : ciTable(cov.not_in_itsm, 'Keşifte olup mevcut CMDB\'de olmayan CI\'lar', 'Tüm keşif kayıtları CMDB\'de var.'));
    paintGaps();
    clear(box).append(
      h('div', { class: `notice ${r.status === 'conclusive' ? 'success' : 'warn'}`, id: 'shadowStatus' },
        h('strong', {}, `${statusLabel}. `),
        r.status === 'conclusive'
          ? 'Bağımlı tüm kaynakların son çalışması sağlıklı.'
          : `${r.provenance.unhealthy_sources.join(', ')} ${r.provenance.unhealthy_sources.length > 1 ? 'kaynaklarının' : 'kaynağının'} son çalışması sağlıklı değil (karantina veya sürüyor); sonuçlar eksik veriyle üretildi ve kanıt "kısmi toplama" olarak işaretlenir.`,
        h('span', { class: 'cell-sub' }, `Üretildi: ${formatDateTime(r.generated_at)} · Bağımlı kaynaklar: ${r.provenance.depends_on.join(', ')}`)),
      h('div', { class: 'cards', id: 'shadowStats' },
        metric({ label: 'Mevcut CMDB\'deki CI', value: formatNumber(t.itsm), foot: `Keşfin gördüğü: %${t.itsm_seen_pct ?? '—'}` }),
        metric({ label: 'Keşfin görmediği CMDB kaydı', value: t.itsm_only, tone: t.itsm_only ? 'bad' : 'good', foot: 'Eski veya hayalet kayıt adayı' }),
        metric({ label: 'CMDB\'de olmayan keşif kaydı', value: t.not_in_itsm, tone: t.not_in_itsm ? 'warn' : 'good', foot: 'Mevcut CMDB\'nin kapsam boşluğu' }),
        metric({ label: 'Mükerrer adayı grubu', value: r.duplicates.group_count, tone: r.duplicates.group_count ? 'warn' : 'good',
          foot: `${r.duplicates.ci_count} kayıt; ${r.duplicates.counts.çelişkili} grup çelişkili` }),
        metric({ label: 'Gerçek değer farkı', value: r.drift.real, tone: r.drift.real ? 'warn' : 'good', foot: `${r.drift.cosmetic} fark yalnız yazım` })),
      section('Kapsam: sınıf × rol', h('p', { class: 'muted small' }, 'Her hücre, o roldeki en az bir kaynağın gördüğü CI sayısıdır. Roller ürüne değil göreve bağlıdır; ürün değişse de tablo aynı kalır.'),
        coverageTable(cov)),
      section('Kapsam boşlukları', gaps),
      section('Mükerrer adayları (mevcut CMDB içinde)', h('p', { class: 'muted small' },
        'Aynı kimlik anahtarını paylaşan CMDB kayıtları. Ad tek başına kimlik değildir: yalnız ad ortaksa "olası", seri farklıysa "çelişkili" sayılır.'),
      duplicateList(r.duplicates)),
      section('Değer farkları (mevcut CMDB ↔ Evren)', ...driftSection(r.drift)),
      section('Gölge kaynak uyumu', r.shadow_sources.length
        ? dataTable({ label: 'Gölge kaynak uyumu', rows: r.shadow_sources, idOf: (x) => x.source, columns: [
            { key: 'source', label: 'Gölge kaynak', render: (x) => srcChip(x.source) },
            { key: 'compared', label: 'Karşılaştırılan değer', render: (x) => String(x.compared) },
            { key: 'agree_pct', label: 'Uyum', render: (x) => `%${x.agree_pct}` }] })
        : h('p', { class: 'muted small' }, 'Gölge modda kaynak yok. Yeni bir ürün gölge moda alındığında değerlerinin yetkili değerlerle uyumu burada ölçülür.')),
      section('Kanıt (GRC Platformu)', ...evidenceSection(r, snaps, render)),
    );
  }
  await render();
  return {};
}
