// Kurallar: uzlaştırma (sınıf × rol öncelik ızgarası), tanımlama (IRE), veri tazeleme ve eskime. Kaynak: metadata/*.yaml.
import { h, append, clear, pill, tabs } from '../lib/dom.js';
import { setHeader } from '../lib/shell.js';
import { rulesView, sourcesView, srcChip } from '../lib/data.js';

const ATTR_TR = {
  serial_number: 'seri', serial_number_type: 'seri tipi', bios_uuid: 'BIOS UUID', fqdn: 'FQDN', name: 'ad', mac_address: 'MAC',
  correlation_id: 'korelasyon kimliği', vcenter_uuid: 'vCenter UUID', object_id: 'nesne kimliği',
};

export async function renderRules(view, param) {
  const [rules, sv] = await Promise.all([rulesView(), sourcesView()]);
  const d = { rules, roles: sv.roles, sources: sv.sources };
  const roleLabel = Object.fromEntries(d.roles.map((r) => [r.name, r.label_tr]));
  const roleOrder = d.roles.filter((r) => d.rules.reconciliation.some((g) => r.name in g.priorities)).map((r) => r.name);
  let tab = ['uzlastirma', 'tanimlama', 'tazeleme'].includes(param) ? param : 'uzlastirma';
  const box = h('div');

  setHeader({
    title: 'Kurallar',
    sub: 'Hangi kaynak hangi alanı yazar; kurallar kaynak rolüne bağlıdır.',
    crumbs: [['Kurallar']],
  });

  function reconciliation() {
    const head = h('tr', {}, h('th', { scope: 'col' }, 'Sınıf ve öznitelikler'),
      roleOrder.map((r) => h('th', { scope: 'col', class: 'role' }, roleLabel[r],
        h('small', {}, d.sources.filter((x) => x.role === r).map((x) => x.name).join(', ') || '—'))));
    const body = d.rules.reconciliation.map((g) => {
      const best = Math.min(...Object.values(g.priorities));
      return h('tr', {},
        h('td', {}, h('span', { class: 'cell-primary' }, g.description), h('span', { class: 'cell-sub' }, `${g.class_label} · ${g.applies_to}`),
          h('span', { class: 'attrs-list' }, g.attributes.map((a) => a.label).join(', '))),
        roleOrder.map((r) => {
          const p = g.priorities[r];
          return h('td', { class: `p${p === undefined ? ' none' : p === best ? ' top' : ''}`,
            title: p === undefined ? `${roleLabel[r]} bu alanlara yazamaz` : `${roleLabel[r]}: öncelik ${p}` }, p === undefined ? '·' : String(p));
        }));
    });
    return [
      h('p', { class: 'notice' }, 'Sütun başlığının altında o rolü bugün dolduran ürün görünür. Ürün değişirse sütun aynı kalır; yalnızca alttaki ad değişir. ',
        'Nokta (·): rol bu alanlara hiç yazamaz; örneğin keşif ve izleme araçları sahiplik ve destek grubunu ezemez.'),
      h('div', { class: 'tablewrap' }, h('table', { class: 'rulegrid', id: 'ruleGrid', 'aria-label': 'Uzlaştırma kuralları: sınıf ve rol öncelikleri' },
        h('thead', {}, head), h('tbody', {}, body))),
    ];
  }

  function identification() {
    return d.rules.identification.map((r) => h('section', { class: 'panel' },
      h('div', { class: 'panel-title' }, h('h2', {}, `${r.class_label} · ${r.applies_to}`),
        pill(r.independent ? 'Bağımsız' : 'Bağımlı (üst CI bağlamında)', r.independent ? 'neutral' : 'purple')),
      h('p', { class: 'intro' }, r.description),
      h('div', { class: 'tablewrap' }, h('table', { 'aria-label': `${r.class_label} tanımlama kuralı` },
        h('thead', {}, h('tr', {}, h('th', { class: 'num' }, 'Öncelik'), h('th', {}, 'Öznitelikler'), h('th', {}, 'Köken'))),
        h('tbody', {}, r.entries.map((e) => h('tr', {}, h('td', { class: 'num' }, String(e.priority)),
          h('td', {}, e.attributes.map((a) => ATTR_TR[a] || a).join(' + ')),
          h('td', {}, e.evren_extension ? pill('Evren uzantısı', 'info') : pill('ServiceNow IRE', 'good')))))))));
  }

  function refresh() {
    return [
      h('section', { class: 'panel' }, h('h2', {}, 'Veri tazeleme'),
        h('p', { class: 'intro' }, 'Yüksek öncelikli rolün verisi bu süreden eskiyse sıradaki yetkili kaynağın daha taze verisi kabul edilir.'),
        h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Veri tazeleme kuralları' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Sınıf'), h('th', {}, 'Rol'), h('th', { class: 'num' }, 'Geçerlilik (gün)'))),
          h('tbody', {}, d.rules.refresh.map((r) => h('tr', {}, h('td', {}, r.class_label), h('td', {}, srcChip(roleLabel[r.role], r.role)),
            h('td', { class: 'num' }, String(r.effective_days)))))))),
      h('section', { class: 'panel' }, h('h2', {}, 'Eskime'),
        h('p', { class: 'intro' }, 'Hiçbir yetkili kaynak CI\'ı bu sürede görmezse durum önerisi üretilir. Devre kesici sağlıklı değilse eskime işletilmez; CI silinmez.'),
        h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Eskime kuralları' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Sınıf'), h('th', { class: 'num' }, 'Eski sayılır (gün)'), h('th', { class: 'num' }, 'Bulunamıyor önerisi (gün)'),
            h('th', {}, 'Tüm kaynaklar görmemeli'))),
          h('tbody', {}, d.rules.staleness.map((r) => h('tr', {}, h('td', {}, r.class_label), h('td', { class: 'num' }, String(r.stale_after_days)),
            h('td', { class: 'num' }, String(r.absent_candidate_after_days)), h('td', {}, r.require_all_sources_absent ? 'Evet' : 'Hayır'))))))),
    ];
  }

  function render() {
    append(clear(box), [
      tabs([{ id: 'uzlastirma', label: 'Uzlaştırma', count: d.rules.reconciliation.length },
        { id: 'tanimlama', label: 'Tanımlama (IRE)', count: d.rules.identification.length },
        { id: 'tazeleme', label: 'Tazeleme ve eskime' }], tab, (t) => { tab = t; history.replaceState(null, '', `#/kurallar/${t}`); render(); }, 'Kural türü'),
      tab === 'uzlastirma' ? reconciliation() : tab === 'tanimlama' ? identification() : refresh(),
      h('p', { class: 'muted small' }, 'Kaynak: metadata/reconciliation_rules.yaml, metadata/identification_rules.yaml. Değişiklik üretimde değişiklik setiyle yayınlanır: taslak → onay → yayın; hazırlayan onaylayamaz.'),
    ]);
  }
  view.append(box);
  render();
  return { update: (p) => { if (p && p !== tab) { tab = p; render(); } } };
}
