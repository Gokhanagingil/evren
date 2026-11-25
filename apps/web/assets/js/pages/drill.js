// Ürün değişimi tatbikatı paneli (F1-13, ADR-0009): BMC → Keşif-B (sentetik), kurallar değişmeden.
// Veri: /content/drill.json (tools/build_drill.py; çekirdeğin gerçek hattından, deterministik, CI'da denetlenir).
import { h, pill } from '../lib/dom.js';
import { LIFECYCLE, drillView, srcChip } from '../lib/data.js';

const fmtVal = (v) => (v === null || v === undefined ? '—' : typeof v === 'boolean' ? (v ? 'Evet' : 'Hayır') : String(v));

function phaseCard(ph, i, labels) {
  const lines = [];
  if (ph.id !== 'baseline') {
    lines.push(['Golden değer değişimi', ph.golden_changes], ['Yalnız kaynak değişimi', ph.provenance_changes]);
  }
  if (ph.new_cis.length) lines.push(['Yeni CI', ph.new_cis.length]);
  if (ph.unmatched_shadow.length) lines.push(['Eşleşmeyen (CI açılmadı)', ph.unmatched_shadow.length]);
  if (ph.orphaned_count) lines.push(['Kaynağını yitiren öznitelik', ph.orphaned_count]);
  lines.push(['Keşif kapsamı', `${ph.discovery_coverage} / ${ph.ci_count} CI`]);
  const top = ph.changes_by_attribute.slice(0, 3).map((x) => `${labels[x.attribute] || x.attribute} (${x.count})`).join(', ');
  return h('li', { class: 'drill-phase' },
    h('div', { class: 'drill-step' }, h('b', { class: 'n' }, String(i)), h('strong', {}, ph.title)),
    h('div', { class: 'pills' }, ph.lifecycle.map((l) => {
      const [label, tone] = LIFECYCLE[l.lifecycle] || [l.lifecycle_label, 'neutral'];
      return h('span', { class: 'pill-pair' }, srcChip(l.source === 'KesifB' ? 'Keşif-B' : l.source, 'Discovery', l.label), pill(`${label} · sıra ${l.role_rank}`, tone));
    })),
    h('dl', { class: 'drill-kv' }, lines.map(([k, v]) => [h('dt', {}, k), h('dd', {}, String(v))])),
    top ? h('p', { class: 'small muted' }, `Değişen: ${top}`) : null);
}

export async function drillPanel() {
  let d;
  try { d = await drillView(); } catch { return null; }
  const labels = d.labels || {};
  const [shadow, promote, retire] = [d.phases[1], d.phases[2], d.phases[3]];
  const passed = d.checks.filter((c) => c.met).length;
  return h('section', { class: 'panel drill', id: 'drillPanel', tabindex: '-1', 'aria-labelledby': 'drillTitle' },
    h('div', { class: 'panel-title' },
      h('h2', { id: 'drillTitle' }, 'Ürün değişimi tatbikatı'),
      h('span', { class: 'pills' }, pill('Sentetik', 'purple'), pill(`${passed}/${d.checks.length} denetim geçti`, d.passed ? 'good' : 'bad'))),
    h('p', { class: 'intro' }, `${d.old.label} → ${d.new.label}, ${d.role_label} rolünde. Yeni ürün yalnız kendi eşleme dosyasıyla (`,
      h('span', { class: 'mono' }, d.new.mapping), ') devreye girdi; uzlaştırma ve tanımlama kuralları hiçbir aşamada değişmedi.'),
    h('ol', { class: 'drill-phases', 'aria-label': 'Tatbikat aşamaları' }, d.phases.map((ph, i) => phaseCard(ph, i, labels))),
    h('div', { class: 'grid' },
      h('div', {},
        h('h3', {}, 'Denetimler'),
        h('ul', { class: 'checks', id: 'drillChecks' }, d.checks.map((c) => h('li', { class: c.met ? 'ok' : 'fail' },
          h('span', { class: 'mark', 'aria-hidden': 'true' }, c.met ? '✓' : '✗'), h('span', { class: 'sr-only' }, c.met ? 'Geçti: ' : 'Başarısız: '), c.text))),
        h('h3', {}, 'Kurallar dosyası özeti (tüm aşamalarda aynı)'),
        h('ul', { class: 'mini' }, Object.entries(d.rules_sha256).map(([f, sha]) => h('li', {},
          h('span', { class: 'mono small' }, f), h('span', { class: 'cell-sub mono', title: sha }, `SHA-256 ${sha.slice(0, 20)}…`))))),
      h('div', {},
        h('h3', {}, 'Gölge modda uyum (yetkili değer ile yeni ürünün değeri)'),
        h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Gölge mod uyumu' },
          h('thead', {}, h('tr', {}, ['Öznitelik', 'Karşılaştırılan', 'Uyum'].map((t) => h('th', { scope: 'col' }, t)))),
          h('tbody', {}, shadow.agreement.map((a) => h('tr', {}, h('td', {}, labels[a.attribute] || a.attribute), h('td', {}, String(a.compared)),
            h('td', {}, pill(`%${a.pct}`, a.pct >= 99 ? 'good' : a.pct >= 80 ? 'warn' : 'bad'))))))),
        h('p', { class: 'small muted' }, `Gölge modda eşleşmeyen ${shadow.unmatched_shadow.length} kayıt (${shadow.unmatched_shadow.join(', ')}) CI açmadı; etkin moda geçince açtı.`))),
    h('h3', {}, 'Etkin + devir: değişen değer örnekleri'),
    h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Değişen değer örnekleri' },
      h('thead', {}, h('tr', {}, ['CI', 'Öznitelik', 'Önce', 'Sonra'].map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', {}, promote.change_samples.slice(0, 6).map((c) => h('tr', {}, h('td', {}, c.name), h('td', {}, labels[c.attribute] || c.attribute),
        h('td', {}, fmtVal(c.before), ' ', c.before_source ? srcChip(c.before_source) : null),
        h('td', {}, fmtVal(c.after), ' ', srcChip(c.after_source === 'KesifB' ? 'Keşif-B' : c.after_source, 'Discovery'))))))),
    h('div', { class: 'notice warn', id: 'drillFinding' },
      h('strong', {}, 'Tatbikatın bulgusu: '),
      `Keşif-B eşlemesi şu alanları üretmiyor: ${d.mapping_gap.map((a) => labels[a] || a).join(', ')}. `,
      `Emeklilik önizlemesi ${promote.retire_preview.reduce((n, x) => n + x.count, 0)} özniteliğin sahipsiz kalacağını önceden gösterdi `,
      `(gerçekleşen: ${retire.orphaned_count}); ${promote.retire_risk.length} CI'ı (${promote.retire_risk.map((x) => x.name).join(', ')}) yalnız eski ürün görüyordu. `,
      'Gerçek bir değişimde emeklilikten önce eşleme genişletilir veya alanların bırakılması onaylanır.',
      shadow.agreement.some((a) => a.pct < 80)
        ? ` Gölge modda düşük uyum: ${shadow.agreement.filter((a) => a.pct < 80).map((a) => `${labels[a.attribute] || a.attribute} %${a.pct}`).join(', ')} — ürünler aynı değeri farklı adlandırıyor; etkin moda geçmeden önce normalleştirme kararı verilmeli.`
        : null),
    h('p', { class: 'small muted' }, d.note));
}
