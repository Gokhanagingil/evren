// Konfigürasyon öğeleri: arama ve filtreli liste (API'de sunucu tarafı, imleçli); CI 360° paneli
// ("bu değer nereden geldi?"), API modunda değişim geçmişi.
import { h, clear, dataTable, formatDateTime, kv, pill, section } from '../lib/dom.js';
import { closeDetail, openDetail, setHeader } from '../lib/shell.js';
import { OUTCOME, ROLE_TR, backend, ciDetail, ciFacets, ciHistory, ciSearch, srcChip } from '../lib/data.js';

const SCOPES = {
  '': 'Tüm kapsam',
  izlenmeyen: 'İzlenmeyen sunucular',
  korumasiz: 'Uç nokta koruması olmayan',
  'itsm-yok': 'Mevcut CMDB\'de olmayan',
  yetkisiz: 'Yetkisiz kaynak denemesi olan',
};
const PAGE = 100;

export async function renderCis(view, param) {
  const { mode } = await backend();
  const facets = await ciFacets();
  const roleOf = Object.fromEntries(facets.sources.map((x) => [x.name, x.role]));
  const state = { q: '', cls: '', src: '', scope: '' };
  let rows = [];
  let nextCursor = null;
  let total = 0;
  let selected = null;
  let seq = 0;

  setHeader({
    title: 'Konfigürasyon Öğeleri',
    sub: 'Golden record. Her öznitelik, rolüne göre yetkili kaynaktan gelir; ezilen değerler ve kural gerekçesi CI panelinde görünür.',
    crumbs: [['Konfigürasyon Öğeleri']],
  });

  const search = h('input', { type: 'search', class: 'searchbar', id: 'ciSearch', placeholder: 'Ad, FQDN, IP, işletim sistemi', 'aria-label': 'CI ara' });
  const selCls = h('select', { class: 'compact', id: 'ciClass', 'aria-label': 'Sınıf' },
    h('option', { value: '' }, 'Sınıf: tümü'),
    [...facets.classes].sort((a, b) => a.label_tr.localeCompare(b.label_tr, 'tr')).map((c) => h('option', { value: c.name }, c.label_tr)));
  const selSrc = h('select', { class: 'compact', id: 'ciSource', 'aria-label': 'Kaynak' },
    h('option', { value: '' }, 'Kaynak: tümü'), facets.sources.filter((x) => x.stats.records).map((x) => h('option', { value: x.name }, x.label_tr)));
  const selScope = h('select', { class: 'compact', id: 'ciScope', 'aria-label': 'Kapsam' },
    Object.entries(SCOPES).map(([v, l]) => h('option', { value: v }, l)));
  const count = h('span', { class: 'count', id: 'ciCount', 'aria-live': 'polite' });
  const listBox = h('div');
  const more = h('button', { class: 'btn secondary', type: 'button', id: 'ciMore', onclick: () => load(true) }, 'Daha fazla yükle');
  let table = null;

  async function load(append = false) {
    const my = ++seq;
    const res = await ciSearch({ ...state, cursor: append ? nextCursor : null, limit: PAGE });
    if (my !== seq) return; // eski yanıt yeni aramayı ezmesin
    rows = append ? rows.concat(res.items) : res.items;
    nextCursor = res.next_cursor;
    total = res.count;
    count.textContent = `${total} kayıt${rows.length < Number(total) || String(total).endsWith('+') ? ` · ${rows.length} gösteriliyor` : ''}`;
    renderList();
  }

  function renderList() {
    table = dataTable({
      label: 'Konfigürasyon öğeleri', rows, idOf: (c) => c.sys_id, selectedId: () => selected,
      onOpen: (c, tr) => open(c.sys_id, tr), sort: mode === 'api' ? undefined : { key: 'name', dir: 1 },
      columns: [
        { key: 'name', label: 'Ad', render: (c) => h('span', {}, h('span', { class: 'cell-primary' }, c.name), c.fqdn ? h('span', { class: 'cell-sub' }, c.fqdn) : null) },
        { key: 'class_label', label: 'Sınıf', render: (c) => pill(c.class_label, 'neutral') },
        { key: 'ip_address', label: 'IP adresi', render: (c) => c.ip_address || '—' },
        { key: 'os', label: 'İşletim sistemi', render: (c) => c.os || '—' },
        { key: 'used_for_label', label: 'Kullanım', render: (c) => (c.used_for ? c.used_for_label || c.used_for : '—') },
        { key: 'sources', label: 'Kaynaklar', sortable: false,
          render: (c) => h('span', { class: 'pills' }, c.sources.map((x) => srcChip(x, roleOf[x] || (x === 'Manual' ? 'Manual' : null)))) },
      ],
    });
    clear(listBox).append(table);
    if (nextCursor) listBox.append(h('div', { class: 'toolbar' }, more));
  }

  function attrRow(a) {
    const contested = a.overridden.length > 0;
    return h('details', { class: `attr${contested ? ' contested' : ''}` },
      h('summary', {}, h('span', { class: 'k' }, a.label), h('span', { class: 'v' }, a.display), srcChip(a.source, a.role)),
      h('div', { class: 'why' },
        h('div', {}, h('strong', {}, 'Gerekçe: '), a.reason),
        a.observed_at ? h('div', {}, `Gözlem: ${formatDateTime(a.observed_at)} · Rol: ${ROLE_TR[a.role] || a.role}`) : null,
        a.overridden.length ? h('div', {}, h('strong', {}, 'Ezilen değerler:'),
          h('ul', {}, a.overridden.map((o) => h('li', {}, `${o.source}: ${o.display}`)))) : null,
        a.shadow?.length ? h('div', {}, h('strong', {}, 'Gölge kaynak (karşılaştırma, yazmaz): '),
          a.shadow.map((o) => `${o.source}: ${o.display}`).join(', ')) : null,
        a.unauthorized.length ? h('div', {}, h('strong', {}, 'Yetkisiz kaynak: '), a.unauthorized.join(', '), ' (bu alan bu kaynağın rolüne kapalı)') : null));
  }

  async function open(id, opener) {
    const c = await ciDetail(id);
    if (!c) return;
    selected = id;
    if (location.hash !== `#/ci/${id}`) history.replaceState(null, '', `#/ci/${id}`);
    const cov = [['Keşif', c.coverage.discovery], ['İzleme', c.coverage.monitoring], ['Uç nokta güvenliği', c.coverage.security], ['ITSM (ServiceNow)', c.coverage.itsm]];
    const historyBox = h('div', { id: 'ciHistory' }, h('p', { class: 'muted small' }, 'Yükleniyor…'));
    openDetail({
      kicker: `Konfigürasyon öğesi · ${c.class_label}`, title: c.name, opener,
      onClose: () => { selected = null; history.replaceState(null, '', '#/ci'); table?.refreshSelection(); },
      body: [
        h('div', { class: 'pills' }, pill(c.class_label, 'neutral'), c.used_for ? pill(c.used_for_label || c.used_for, c.used_for === 'Production' ? 'good' : 'neutral') : null,
          c.operational_status ? pill(c.operational_status, 'info') : null),
        h('div', { class: 'stat-strip', 'aria-label': 'Kaynak kapsamı' }, cov.map(([label, ok]) => h('div', { class: `stat ${ok ? 'good' : c.server || label === 'Keşif' ? 'bad' : ''}` },
          h('span', { class: 'stat-value' }, ok ? 'Var' : 'Yok'), h('span', { class: 'stat-label' }, label)))),
        c.rejected.length ? h('div', { class: 'notice warn' }, h('strong', {}, 'Yetkisiz kaynak denemesi. '),
          c.rejected.map((r) => `${r.attempts.map((x) => x.source).join(', ')} "${r.label}" alanına ${r.attempts.map((x) => x.display).join(', ')} yazmaya çalıştı; bu alan bu kaynağın rolüne kapalı, değer alınmadı.`).join(' ')) : null,
        section('Öznitelikler ve kaynakları', h('p', { class: 'muted small' }, 'Bir satırı açın: değeri veren kaynak, uygulanan kural ve ezilen değerler. Turuncu nokta: kaynaklar farklı değer bildiriyor.'),
          h('div', { class: 'attrs' }, c.attributes.map(attrRow))),
        c.relations.length ? section('İlişkiler', h('ul', { class: 'mini' }, c.relations.map((r) => h('li', {},
          h('span', { class: 'muted' }, `${r.direction === 'parent' ? r.type.split('::')[0] : r.type.split('::')[1]} · `),
          h('a', { href: `#/ci/${r.target}` }, r.target_name))))) : null,
        section('Kaynak kayıtları ve kimlik kararı', h('ul', { class: 'mini' }, c.links.map((l) => h('li', {},
          h('div', { class: 'pills' }, srcChip(l.data_source, l.role), pill(OUTCOME[l.outcome] || l.outcome, l.outcome === 'new' ? 'info' : 'good')),
          h('div', { class: 'small', title: l.summary }, l.summary),
          h('div', { class: 'cell-sub mono' }, `${l.source_key} · ${formatDateTime(l.observed_at)}`))))),
        mode === 'api' ? section('Değişim geçmişi', historyBox) : null,
        section('Kimlik', kv([['sys_id', h('span', { class: 'mono' }, c.sys_id)], ['Sınıf (sys_class_name)', h('span', { class: 'mono' }, c.class)],
          ['Destek grubu', c.support_group], ['Sahip', c.owned_by || h('span', { class: 'pill warn' }, 'Atanmamış')]])),
      ],
    });
    table?.refreshSelection();
    if (mode === 'api') {
      const items = await ciHistory(id);
      clear(historyBox).append(items.length
        ? h('ul', { class: 'mini' }, items.slice(0, 20).map((x) => h('li', {},
            h('strong', {}, x.attribute), `: ${x.old ?? '—'} → ${x.new ?? '—'}`,
            h('span', { class: 'cell-sub' }, `${formatDateTime(x.changed_at)} · ${x.source || '—'} · ${x.reason}`))))
        : h('p', { class: 'muted small' }, 'Kayıt yok.'));
    }
  }

  async function applyParam(p) {
    if (p && p.startsWith('kapsam=')) {
      state.scope = p.slice(7) in SCOPES ? p.slice(7) : '';
      selScope.value = state.scope;
      await load();
      closeDetail({ silent: true });
    } else if (p && /^[0-9a-f]{32}$/.test(p)) {
      await open(p);
    } else {
      closeDetail({ silent: true });
    }
  }

  let typing = null;
  search.addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(() => { state.q = search.value.trim(); load(); }, mode === 'api' ? 250 : 0); });
  selCls.addEventListener('change', () => { state.cls = selCls.value; load(); });
  selSrc.addEventListener('change', () => { state.src = selSrc.value; load(); });
  selScope.addEventListener('change', () => { state.scope = selScope.value; load(); });

  view.append(h('div', { class: 'toolbar' }, h('div', { class: 'filters' }, search, selCls, selSrc, selScope), count), listBox);
  if (!(param && param.startsWith('kapsam='))) await load();
  await applyParam(param);
  return { update: applyParam };
}
