// Konfigürasyon öğeleri: arama ve filtreli liste; CI 360° paneli ("bu değer nereden geldi?").
import { h, clear, dataTable, formatDateTime, kv, pill, section, fold } from '../lib/dom.js';
import { closeDetail, openDetail, setHeader } from '../lib/shell.js';
import { OUTCOME, ROLE_TR, demo, srcChip } from '../lib/data.js';

const SCOPES = {
  '': 'Tüm kapsam',
  izlenmeyen: 'İzlenmeyen sunucular',
  korumasiz: 'Uç nokta koruması olmayan',
  'itsm-yok': 'Mevcut CMDB\'de olmayan',
  yetkisiz: 'Yetkisiz kaynak denemesi olan',
};

function inScope(c, scope) {
  switch (scope) {
    case 'izlenmeyen': return c.server && !c.coverage.monitoring;
    case 'korumasiz': return c.server && !c.coverage.security;
    case 'itsm-yok': return c.server && !c.coverage.itsm;
    case 'yetkisiz': return c.unauthorized_count > 0;
    default: return true;
  }
}

export async function renderCis(view, param) {
  const d = await demo();
  const roleOf = Object.fromEntries(d.sources.map((x) => [x.name, x.role]));
  const byId = Object.fromEntries(d.cis.map((c) => [c.sys_id, c]));
  const state = { q: '', cls: '', src: '', scope: '' };
  let selected = null;

  setHeader({
    title: 'Konfigürasyon Öğeleri',
    sub: 'Golden record. Her öznitelik, rolüne göre yetkili kaynaktan gelir; ezilen değerler ve kural gerekçesi CI panelinde görünür.',
    crumbs: [['Konfigürasyon Öğeleri']],
  });

  const classes = [...new Set(d.cis.map((c) => c.class_label))].sort((a, b) => a.localeCompare(b, 'tr'));
  const search = h('input', { type: 'search', class: 'searchbar', id: 'ciSearch', placeholder: 'Ad, FQDN, IP, işletim sistemi', 'aria-label': 'CI ara' });
  const selCls = h('select', { class: 'compact', id: 'ciClass', 'aria-label': 'Sınıf' },
    h('option', { value: '' }, 'Sınıf: tümü'), classes.map((c) => h('option', { value: c }, c)));
  const selSrc = h('select', { class: 'compact', id: 'ciSource', 'aria-label': 'Kaynak' },
    h('option', { value: '' }, 'Kaynak: tümü'), d.sources.filter((x) => x.stats.records).map((x) => h('option', { value: x.name }, x.label_tr)));
  const selScope = h('select', { class: 'compact', id: 'ciScope', 'aria-label': 'Kapsam' },
    Object.entries(SCOPES).map(([v, l]) => h('option', { value: v }, l)));
  const count = h('span', { class: 'count', id: 'ciCount', 'aria-live': 'polite' });
  const listBox = h('div');
  let table = null;

  function rows() {
    const q = fold(state.q);
    return d.cis.filter((c) => (!state.cls || c.class_label === state.cls) && (!state.src || c.sources.includes(state.src))
      && inScope(c, state.scope)
      && (!q || fold([c.name, c.fqdn, c.ip_address, c.os, c.sys_id, c.class_label].join(' ')).includes(q)));
  }

  function renderList() {
    const r = rows();
    count.textContent = `${r.length} kayıt`;
    table = dataTable({
      label: 'Konfigürasyon öğeleri', rows: r, idOf: (c) => c.sys_id, selectedId: () => selected,
      onOpen: (c, tr) => open(c.sys_id, tr), sort: { key: 'name', dir: 1 },
      columns: [
        { key: 'name', label: 'Ad', render: (c) => h('span', {}, h('span', { class: 'cell-primary' }, c.name), c.fqdn ? h('span', { class: 'cell-sub' }, c.fqdn) : null) },
        { key: 'class_label', label: 'Sınıf', render: (c) => pill(c.class_label, 'neutral') },
        { key: 'ip_address', label: 'IP adresi', render: (c) => c.ip_address || '—' },
        { key: 'os', label: 'İşletim sistemi', render: (c) => c.os || '—' },
        { key: 'used_for_label', label: 'Kullanım', render: (c) => (c.used_for ? c.used_for_label : '—') },
        { key: 'sources', label: 'Kaynaklar', sortable: false,
          render: (c) => h('span', { class: 'pills' }, c.sources.map((x) => srcChip(x, roleOf[x] || (x === 'Manual' ? 'Manual' : null)))) },
      ],
    });
    clear(listBox).append(table);
  }

  function attrRow(a) {
    const contested = a.overridden.length > 0;
    return h('details', { class: `attr${contested ? ' contested' : ''}` },
      h('summary', {}, h('span', { class: 'k' }, a.label), h('span', { class: 'v' }, a.display), srcChip(a.source, a.role)),
      h('div', { class: 'why' },
        h('div', {}, h('strong', {}, 'Gerekçe: '), a.reason),
        h('div', {}, `Gözlem: ${formatDateTime(a.observed_at)} · Rol: ${ROLE_TR[a.role] || a.role}`),
        a.overridden.length ? h('div', {}, h('strong', {}, 'Ezilen değerler:'),
          h('ul', {}, a.overridden.map((o) => h('li', {}, `${o.source}: ${o.display}`)))) : null,
        a.unauthorized.length ? h('div', {}, h('strong', {}, 'Yetkisiz kaynak: '), a.unauthorized.join(', '), ' (bu alan bu kaynağın rolüne kapalı)') : null));
  }

  function open(id, opener) {
    const c = byId[id];
    if (!c) return;
    selected = id;
    if (location.hash !== `#/ci/${id}`) history.replaceState(null, '', `#/ci/${id}`);
    const cov = [['Keşif', c.coverage.discovery], ['İzleme', c.coverage.monitoring], ['Uç nokta güvenliği', c.coverage.security], ['ITSM (ServiceNow)', c.coverage.itsm]];
    openDetail({
      kicker: `Konfigürasyon öğesi · ${c.class_label}`,
      title: c.name,
      opener,
      onClose: () => { selected = null; history.replaceState(null, '', '#/ci'); table?.refreshSelection(); },
      body: [
        h('div', { class: 'pills' }, pill(c.class_label, 'neutral'), c.used_for ? pill(c.used_for_label, c.used_for === 'Production' ? 'good' : 'neutral') : null,
          c.operational_status ? pill(c.operational_status, 'info') : null),
        h('div', { class: 'stat-strip', 'aria-label': 'Kaynak kapsamı' }, cov.map(([label, ok]) => h('div', { class: `stat ${ok ? 'good' : c.server || label === 'Keşif' ? 'bad' : ''}` },
          h('span', { class: 'stat-value' }, ok ? 'Var' : 'Yok'), h('span', { class: 'stat-label' }, label)))),
        c.rejected.length ? h('div', { class: 'notice warn' }, h('strong', {}, 'Yetkisiz kaynak denemesi. '),
          c.rejected.map((r) => `${r.attempts.map((x) => x.source).join(', ')} "${r.label}" alanına ${r.attempts.map((x) => x.display).join(', ')} yazmaya çalıştı; bu alan yalnızca elle giriş ve ITSM rolüne açık, değer alınmadı.`).join(' ')) : null,
        section('Öznitelikler ve kaynakları', h('p', { class: 'muted small' }, 'Bir satırı açın: değeri veren kaynak, uygulanan kural ve ezilen değerler. Turuncu nokta: kaynaklar farklı değer bildiriyor.'),
          h('div', { class: 'attrs' }, c.attributes.map(attrRow))),
        c.relations.length ? section('İlişkiler', h('ul', { class: 'mini' }, c.relations.map((r) => h('li', {},
          h('span', { class: 'muted' }, `${r.direction === 'parent' ? r.type.split('::')[0] : r.type.split('::')[1]} · `),
          h('a', { href: `#/ci/${r.target}` }, r.target_name))))) : null,
        section('Kaynak kayıtları ve kimlik kararı', h('ul', { class: 'mini' }, c.links.map((l) => h('li', {},
          h('div', { class: 'pills' }, srcChip(l.data_source, l.role), pill(OUTCOME[l.outcome] || l.outcome, l.outcome === 'new' ? 'info' : 'good')),
          h('div', { class: 'small', title: l.summary }, l.summary),
          h('div', { class: 'cell-sub mono' }, `${l.source_key} · ${formatDateTime(l.observed_at)}`))))),
        section('Kimlik', kv([['sys_id', h('span', { class: 'mono' }, c.sys_id)], ['Sınıf (sys_class_name)', h('span', { class: 'mono' }, c.class)],
          ['Destek grubu', c.support_group], ['Sahip', c.owned_by || h('span', { class: 'pill warn' }, 'Atanmamış')]])),
      ],
    });
    table?.refreshSelection();
  }

  function applyParam(p) {
    if (p && p.startsWith('kapsam=')) {
      state.scope = p.slice(7) in SCOPES ? p.slice(7) : '';
      selScope.value = state.scope;
      renderList();
      closeDetail({ silent: true });
    } else if (p && byId[p]) {
      open(p);
    } else {
      closeDetail({ silent: true });
    }
  }

  search.addEventListener('input', () => { state.q = search.value.trim(); renderList(); });
  selCls.addEventListener('change', () => { state.cls = selCls.value; renderList(); });
  selSrc.addEventListener('change', () => { state.src = selSrc.value; renderList(); });
  selScope.addEventListener('change', () => { state.scope = selScope.value; renderList(); });

  view.append(
    h('div', { class: 'toolbar' }, h('div', { class: 'filters' }, search, selCls, selSrc, selScope), count),
    listBox,
  );
  renderList();
  applyParam(param);
  return { update: applyParam };
}
