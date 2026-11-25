// Genel Bakış: karar bekleyen işler önce; kapsam, kaynak sağlığı ve GRC Platformu'na giden kontroller.
import { h, s, formatDateTime, formatNumber, metric, pill, percent } from '../lib/dom.js';
import { setHeader } from '../lib/shell.js';
import { BREAKER, CONTROL, demo, srcChip } from '../lib/data.js';

function gauge(value) {
  const r = 34;
  const c = 2 * Math.PI * r;
  return s('svg', { class: 'gauge', viewBox: '0 0 84 84', role: 'img', 'aria-label': `${percent(value)} otomatik çözüldü` },
    s('circle', { class: 'track', cx: 42, cy: 42, r, fill: 'none', 'stroke-width': 9 }),
    s('circle', { class: 'value', cx: 42, cy: 42, r, fill: 'none', 'stroke-width': 9, 'stroke-linecap': 'round',
      'stroke-dasharray': `${(c * value) / 100} ${c}`, transform: 'rotate(-90 42 42)' }),
    s('text', { x: 42, y: 49, 'text-anchor': 'middle' }, String(Math.round(value))));
}

export async function renderOverview(view) {
  const d = await demo();
  const sm = d.summary;
  const writers = d.sources.filter((x) => x.runs.length);
  const quarantined = writers.filter((x) => x.breaker === 'open');
  const warned = writers.filter((x) => x.breaker === 'warn');
  const records = writers.reduce((n, x) => n + x.stats.records, 0);
  const auto = records ? (100 * (records - sm.queue_review - sm.queue_insufficient)) / records : 100;
  const date = new Intl.DateTimeFormat('tr-TR', { day: 'numeric', month: 'long', year: 'numeric', weekday: 'long', timeZone: 'Europe/Istanbul' })
    .format(new Date(d.generated_at));

  setHeader({
    title: 'Genel Bakış',
    sub: 'Kaynaklardan gelen gözlemler ara katmanda anlamlanır: kimliği çözülür, kurallarla uzlaştırılır. Karar bekleyen işler önce gelir.',
    crumbs: [['Genel Bakış']],
  });

  view.append(
    h('p', { class: 'notice warn' }, d.note),
    h('section', { class: 'hero', 'aria-label': 'Özet' },
      h('div', {},
        h('div', { class: 'hero-kicker' }, date),
        h('h2', {}, 'Konfigürasyon evreni'),
        h('p', {}, `${writers.length} kaynaktan ${formatNumber(records)} gözlem → ${formatNumber(sm.ci_count)} konfigürasyon öğesi. `,
          `${sm.queue_review + sm.queue_insufficient} kayıt insan kararı bekliyor; ${quarantined.length} kaynak çalışması karantinada.`),
        h('div', { class: 'hero-actions' },
          h('a', { class: 'btn', href: '#/kuyruk' }, 'Eşleştirme kuyruğu'),
          h('a', { class: 'btn secondary', href: '#/kaynaklar' }, 'Veri kaynakları'),
          h('a', { class: 'btn secondary', href: '#/buyuk-resim' }, 'Büyük resim'))),
      h('a', { class: 'hero-score', href: '#/kuyruk', id: 'heroScore' }, gauge(auto),
        h('div', {}, h('strong', {}, 'Otomatik kimlik çözümü'),
          h('span', {}, 'Kaynak kayıtlarının kural veya skorla insan müdahalesi olmadan bağlanan oranı')))),

    h('div', { class: 'domain' }, 'Karar bekleyenler'),
    h('div', { class: 'cards', id: 'overviewStats' },
      metric({ label: 'İnceleme bekleyen eşleşme', value: sm.queue_review, tone: sm.queue_review ? 'bad' : 'good',
        foot: 'Kural eşleşti ama çelişki var veya birden çok aday', href: '#/kuyruk' }),
      metric({ label: 'Kimliği yetersiz kayıt', value: sm.queue_insufficient, tone: sm.queue_insufficient ? 'warn' : 'good',
        foot: 'Yalnız IP veya kaynak içi kimlik; CI açılmadı', href: '#/kuyruk/yetersiz' }),
      metric({ label: 'Yetkisiz kaynak denemesi', value: sm.unauthorized_attempts, tone: sm.unauthorized_attempts ? 'warn' : 'good',
        foot: 'Kaynak, rolüne kapalı bir alana yazmaya çalıştı', href: '#/ci/kapsam=yetkisiz' }),
      metric({ label: 'Karantinadaki çalışma', value: quarantined.length, tone: quarantined.length ? 'bad' : 'good',
        foot: quarantined.length ? `${quarantined.map((x) => x.name).join(', ')}: eskime ve silme durduruldu` : 'Tüm kaynaklar sağlıklı',
        href: quarantined.length ? `#/kaynaklar/${quarantined[0].name}` : '#/kaynaklar' })),

    h('div', { class: 'domain' }, 'Kapsam'),
    h('div', { class: 'cards' },
      metric({ label: 'Sunucu', value: sm.server_count, foot: `${sm.vm_count} sanal makine örneği · ${sm.network_count} ağ cihazı`, href: '#/ci' }),
      metric({ label: 'İzlenmeyen sunucu', value: sm.unmonitored, tone: sm.unmonitored ? 'warn' : 'good',
        foot: 'İzleme rolünde kaydı yok', href: '#/ci/kapsam=izlenmeyen' }),
      metric({ label: 'Uç nokta koruması olmayan', value: sm.unprotected, tone: sm.unprotected ? 'bad' : 'good',
        foot: 'Uç nokta güvenliği rolünde kaydı yok', href: '#/ci/kapsam=korumasiz' }),
      metric({ label: 'Mevcut CMDB\'de olmayan', value: sm.not_in_itsm, tone: sm.not_in_itsm ? 'warn' : 'good',
        foot: 'Keşifte bulunan, ServiceNow\'da kaydı olmayan', href: '#/ci/kapsam=itsm-yok' })),

    h('div', { class: 'grid two' },
      h('section', { class: 'panel' },
        h('div', { class: 'panel-title' }, h('h2', {}, 'Kaynak sağlığı'), h('a', { class: 'btn ghost small', href: '#/kaynaklar' }, 'Tümü →')),
        h('ul', { class: 'attention' }, writers.map((x) => {
          const [label, tone] = BREAKER[x.breaker] || ['—', 'neutral'];
          return h('li', {},
            h('span', {}, h('a', { href: `#/kaynaklar/${x.name}` }, x.label_tr),
              h('span', { class: 'cell-sub' }, `${x.role_label} · son çalışma ${formatDateTime(x.runs[0].started_at)}`)),
            h('span', { class: 'pills' }, srcChip(x.name, x.role), pill(label, tone)));
        })),
        warned.length || quarantined.length
          ? h('p', { class: 'muted small' }, 'Devre kesici uyarı veya karantinadayken kayıtlar işlenir ama eskime, emekliye ayırma ve ilişki silme yapılmaz.')
          : null),
      h('section', { class: 'panel' },
        h('div', { class: 'panel-title' }, h('h2', {}, 'GRC Platformu\'na giden kontroller'),
          h('a', { class: 'btn ghost small', href: '#/kontroller' }, 'Tümü →')),
        h('ul', { class: 'attention' }, d.controls.map((c) => {
          const [label, tone] = CONTROL[c.status];
          return h('li', {},
            h('span', {}, h('a', { href: `#/kontroller/${c.id}` }, c.title),
              h('span', { class: 'cell-sub' }, `${c.id} · ${c.evidence.length} kanıt · toplama: ${c.provenance.collection_status === 'complete' ? 'tam' : 'kısmi'}`)),
            pill(label, tone));
        })),
        h('p', { class: 'muted small' }, 'Kaynak verisi kısmi toplandıysa (devre kesici uyarıda) sonuç "kesin değil" gönderilir; başarılı veya başarısız sayılmaz.'))),
  );
}
