// Veri kaynakları: rol → ürün kaydı, yaşam döngüsü, devre kesici ve çalışma geçmişi; ürün değişimi yordamı.
import { h, s, dataTable, formatDateTime, formatNumber, kv, metric, pill, section } from '../lib/dom.js';
import { closeDetail, openDetail, setHeader } from '../lib/shell.js';
import { BREAKER, LIFECYCLE, sourcesView, srcChip } from '../lib/data.js';
import { drillPanel } from './drill.js';

const MODE = { snapshot: 'Tam liste', delta: 'Değişenler' };
const CRED_STATE = { active: ['Etkin', 'good'], expired: ['Süresi doldu', 'warn'], revoked: ['İptal edildi', 'bad'] };

// Bağlayıcının bildirdiği tamamlanma (olay omurgası): eksik çekme veya hata, devre kesiciden ayrı gösterilir.
function runStatus(r) {
  if (r.closing) return h('span', { class: 'cell-sub' }, 'Kapanış bekleniyor');
  if (r.via_gateway && r.reported_completed === false) return h('span', { class: 'cell-sub' }, 'Bağlayıcı: tamamlanmadı');
  return null;
}

function runsChart(runs) {
  const list = [...runs].reverse();
  const W = 560;
  const H = 150;
  const pad = { l: 34, r: 8, t: 10, b: 24 };
  const max = Math.max(1, ...list.map((r) => r.records));
  const top = Math.ceil(max / 10) * 10;
  const bw = (W - pad.l - pad.r) / list.length;
  const y = (v) => pad.t + (H - pad.t - pad.b) * (1 - v / top);
  return s('svg', { class: 'runs', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': 'Son çalışmaların kayıt sayısı ve devre kesici durumu' },
    [0, top / 2, top].map((v) => [s('line', { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v), stroke: '#e4e6ea' }),
      s('text', { x: pad.l - 6, y: y(v) + 3, 'text-anchor': 'end' }, formatNumber(v))]),
    list.map((r, i) => s('g', {},
      s('rect', { class: r.state, x: pad.l + i * bw + 3, y: y(r.records), width: Math.max(2, bw - 6), height: Math.max(1, H - pad.b - y(r.records)), rx: 2 },
        s('title', {}, `${formatDateTime(r.started_at)} · ${r.records} kayıt · ${BREAKER[r.state][0]}`)),
      i % 2 === 0 || i === list.length - 1 ? s('text', { x: pad.l + i * bw + bw / 2, y: H - 8, 'text-anchor': 'middle' },
        formatDateTime(r.started_at).slice(0, 5)) : null)));
}

export async function renderSources(view, param) {
  const d = await sourcesView();
  let selected = null;
  let table = null;

  setHeader({
    title: 'Veri Kaynakları',
    sub: 'Her veri kaynağı bir rolü doldurur. Uzlaştırma kuralları ürün adını değil rolü bilir: keşif ürünü değişirse yalnızca bağlayıcı, eşleme ve bu kayıt değişir (ADR-0009).',
    crumbs: [['Veri Kaynakları']],
  });

  function open(name, opener) {
    const x = d.sources.find((v) => v.name === name);
    if (!x) return;
    selected = name;
    if (location.hash !== `#/kaynaklar/${name}`) history.replaceState(null, '', `#/kaynaklar/${name}`);
    const last = x.runs[0];
    const [bl, bt] = BREAKER[x.breaker] || ['Çalışma yok', 'neutral'];
    const [ll, lt] = LIFECYCLE[x.lifecycle];
    openDetail({
      kicker: `Veri kaynağı · ${x.name}`, title: x.label_tr, opener,
      onClose: () => { selected = null; history.replaceState(null, '', '#/kaynaklar'); table?.refreshSelection(); },
      body: [
        h('div', { class: 'pills' }, srcChip(x.role_label, x.role), pill(ll, lt), x.runs.length ? pill(`Devre kesici: ${bl}`, bt) : null),
        last && last.state !== 'ok' ? h('div', { class: `notice ${last.state === 'open' ? 'bad' : 'warn'}` },
          h('strong', {}, last.state === 'open' ? 'Son çalışma karantinada. ' : 'Son çalışmada uyarı. '),
          'Kayıtlar analiz için saklanır; eskime, emekliye ayırma ve ilişki silme yapılmaz.', h('ul', {}, last.reasons.map((r) => h('li', {}, r)))) : null,
        x.open_question ? h('p', { class: 'notice warn' }, h('strong', {}, 'Açık soru: '), x.open_question) : null,
        section('Rol ve ürün', kv([
          ['Kaynak rolü', `${x.role_label} (${x.role})`], ['Rol içi sıra', String(x.role_rank)],
          ['Ürün', [x.product.vendor, x.product.name].filter(Boolean).join(' · ') || '—'], ['Arayüz', x.product.interface],
          ['Bağlayıcı', x.connector ? h('span', { class: 'mono' }, x.connector) : '—'],
          ['Eşleme dosyaları', x.mappings.length ? h('div', {}, x.mappings.map((m) => h('div', { class: 'mono' }, m))) : '—'],
          ['Zamanlama (cron)', x.schedule ? h('span', { class: 'mono' }, x.schedule) : '—'],
          ['Yetkili olduğu alanlar', x.authoritative_for_tr],
          ['Öznitelik yazar', x.writes_attributes ? 'Evet' : 'Hayır'], ['Yeni CI açar', x.may_create_ci ? 'Evet' : 'Hayır'],
        ])),
        x.runs.length ? section('Son çalışmalar', runsChart(x.runs),
          h('div', { class: 'legend' }, h('span', {}, h('i', { class: 'ok' }), 'Sağlıklı'), h('span', {}, h('i', { class: 'warn' }), 'Uyarı'),
            h('span', {}, h('i', { class: 'open' }), 'Karantina')),
          h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Çalışma geçmişi' },
            h('thead', {}, h('tr', {}, h('th', {}, 'Başlangıç'), h('th', {}, 'Mod'), h('th', { class: 'num' }, 'Kayıt'),
              h('th', { class: 'num' }, 'Değişen'), h('th', { class: 'num' }, 'Kabul / bildirilen'), h('th', {}, 'Durum'),
              h('th', {}, 'Gerekçe'))),
            h('tbody', {}, x.runs.slice(0, 6).map((r) => h('tr', {}, h('td', {}, formatDateTime(r.started_at),
              r.opened_by ? h('span', { class: 'cell-sub mono' }, r.opened_by) : null),
              h('td', {}, MODE[r.mode] || '—'), h('td', { class: 'num' }, formatNumber(r.records)),
              h('td', { class: 'num' }, formatNumber(r.changed)),
              h('td', { class: 'num' }, r.via_gateway ? `${formatNumber(r.accepted)} / ${r.reported == null ? '—' : formatNumber(r.reported)}` : '—'),
              h('td', {}, pill(...BREAKER[r.state]), runStatus(r)),
              h('td', { class: 'small' }, [...r.reasons, ...(r.notes ? [r.notes] : [])].join(' · ')
                || (r.baseline ? `Taban çizgisi ${formatNumber(r.baseline)}` : '—')))))))) : null,
        x.credentials ? section('Bağlayıcı kimlikleri', x.credentials.length ? [h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Bağlayıcı kimlikleri' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Ad'), h('th', {}, 'Durum'), h('th', {}, 'Son kullanım'), h('th', {}, 'Geçerlilik sonu'),
            h('th', {}, 'Veren'))),
          h('tbody', {}, x.credentials.map((c) => h('tr', {}, h('td', {}, h('span', { class: 'mono' }, c.name),
            h('span', { class: 'cell-sub' }, `Belirteç …${c.token_hint} · dakikada ${formatNumber(c.per_minute)} kayıt`)),
            h('td', {}, pill(...CRED_STATE[c.state])), h('td', {}, c.last_used_at ? formatDateTime(c.last_used_at) : 'Kullanılmadı'),
            h('td', {}, formatDateTime(c.expires_at)), h('td', {}, c.revoked_by ? `${c.created_by} · iptal: ${c.revoked_by}` : c.created_by)))))),
          h('p', { class: 'small muted' }, 'Belirteç yalnızca verildiği anda bir kez gösterilir; burada son dört karakteri görünür. Kimlik verme ve iptal şimdilik komut satırından (evren connector issue | revoke).')]
          : h('p', { class: 'small muted' }, 'Bu kaynağa verilmiş bağlayıcı kimliği yok. Komut satırından verilir: evren connector issue --data-source ' + x.name + '.')) : null,
        x.stats.records ? section('Kimlik sonuçları', kv([
          ['Kaynak kaydı', formatNumber(x.stats.records)], ['Mevcut CI\'a bağlandı', formatNumber(x.stats.linked)],
          ['Yeni CI açtı', formatNumber(x.stats.new)], ['İncelemede', formatNumber(x.stats.review)], ['Kimliği yetersiz', formatNumber(x.stats.insufficient)],
        ])) : null,
      ],
    });
    table?.refreshSelection();
  }

  const active = d.sources.filter((x) => x.lifecycle === 'active');
  const withRuns = d.sources.filter((x) => x.runs.length);
  table = dataTable({
    label: 'Veri kaynakları', rows: d.sources, idOf: (x) => x.name, selectedId: () => selected, onOpen: (x, tr) => open(x.name, tr),
    columns: [
      { key: 'label_tr', label: 'Kaynak', render: (x) => h('span', {}, h('span', { class: 'cell-primary' }, x.label_tr), h('span', { class: 'cell-sub mono' }, x.name)) },
      { key: 'role_label', label: 'Rol', render: (x) => srcChip(x.role_label, x.role) },
      { key: 'product', label: 'Ürün', sortValue: (x) => x.product.name, render: (x) => x.product.name || '—' },
      { key: 'lifecycle', label: 'Yaşam döngüsü', render: (x) => pill(...LIFECYCLE[x.lifecycle]) },
      { key: 'last', label: 'Son çalışma', sortValue: (x) => x.runs[0]?.started_at,
        render: (x) => (x.runs.length ? h('span', {}, formatDateTime(x.runs[0].started_at), h('span', { class: 'cell-sub' }, `${formatNumber(x.runs[0].records)} kayıt`)) : '—') },
      { key: 'breaker', label: 'Devre kesici', render: (x) => (x.breaker ? pill(...BREAKER[x.breaker]) : '—') },
    ],
  });

  const roles = d.roles.map((r) => ({ ...r, sources: d.sources.filter((x) => x.role === r.name) }));
  view.append(
    h('div', { class: 'cards' },
      metric({ label: 'Etkin veri kaynağı', value: active.length, foot: `${d.roles.length} kaynak rolünü dolduruyor` }),
      metric({ label: 'Sağlıklı son çalışma', value: withRuns.filter((x) => x.breaker === 'ok').length, tone: 'good', foot: `${withRuns.length} zamanlanmış kaynaktan` }),
      metric({ label: 'Uyarı', value: withRuns.filter((x) => x.breaker === 'warn').length, tone: withRuns.some((x) => x.breaker === 'warn') ? 'warn' : 'good', foot: 'Eskime bekletiliyor' }),
      metric({ label: 'Karantina', value: withRuns.filter((x) => x.breaker === 'open').length, tone: withRuns.some((x) => x.breaker === 'open') ? 'bad' : 'good', foot: 'Yıkıcı işlem yok' })),
    table,
    h('div', { class: 'grid wide-left' },
      h('section', { class: 'panel' }, h('div', { class: 'panel-title' }, h('h2', {}, 'Kaynak rolleri'), h('span', { class: 'sub' }, 'Kurallar bu adlarla yazılır')),
        h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Kaynak rolleri' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Rol'), h('th', {}, 'Bugünkü ürün'), h('th', {}, 'Açıklama'))),
          h('tbody', {}, roles.map((r) => h('tr', {},
            h('td', {}, h('span', { class: 'cell-primary' }, r.label_tr), h('span', { class: 'cell-sub mono' }, r.name)),
            h('td', {}, h('span', { class: 'pills' }, r.sources.map((x) => srcChip(x.name, r.name)))),
            h('td', { class: 'small' }, r.description_tr, r.ci_attribute_source ? null : h('span', { class: 'cell-sub' }, 'CI özniteliği yazmaz')))))))),
      h('section', { class: 'panel' }, h('h2', {}, 'Ürün değişimi nasıl yapılır'),
        h('p', { class: 'intro' }, 'Örnek: BMC Helix Discovery yerine başka bir keşif ürünü. Kurallar, CI\'lar ve sys_id\'ler değişmez.'),
        h('ol', { class: 'steps' },
          [['Bağlayıcı ve eşleme', 'connectors/<yeni>/ altında; sentetik örnek ve test.'],
            ['Gölge mod', 'Aynı rolde yeni kaynak, lifecycle: shadow. Kimlik çözülür, kapsam ve değer farkı raporlanır; golden record değişmez.'],
            ['Devir', 'Yeni kaynak active ve sıra 1; eskisi retiring ve sıra 2. Mevcut CI\'lar seri, UUID ve FQDN ile yeni kayıtlara bağlanır.'],
            ['Emeklilik', 'Eski kaynak retired. Kayıtları ve kimlik bağları denetim izi olarak kalır.']]
            .map(([t, x], i) => h('li', {}, h('b', { class: 'n' }, String(i + 1)), h('span', {}, h('strong', {}, t), x)))),
        h('button', { class: 'btn ghost small', type: 'button', id: 'drillJump', onclick: () => {
          const panel = document.getElementById('drillPanel');
          panel?.scrollIntoView({ behavior: 'smooth', block: 'start' });
          panel?.focus({ preventScroll: true });
        } }, 'Tatbikatı görün ↓'))),
    await drillPanel(),
  );
  if (param) open(param);
  return { update: (p) => (p ? open(p) : closeDetail({ silent: true })) };
}
