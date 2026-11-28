// Sistem durumu (U8.1, ADR-0032): veritabanı, göçler, Kafka, OpenSearch, servis nabızları, olay yayını, kuyruklar ve
// kaynak çalışmaları. Yalnızca "platform" okuma izni olan görür (Platform operatörü, Veri sorumlusu, denetçi).
import { h, clear, errorBox, formatDateTimeSec, kv, metric, pill } from '../lib/dom.js';
import { setHeader } from '../lib/shell.js';
import { backend, systemStatus } from '../lib/data.js';
import { alertsSection, notifySection } from './system-ops.js';

const TONE = { ok: 'good', warn: 'warn', bad: 'bad', off: 'neutral' };
const GROUPS = [
  ['Altyapı', ['api', 'database', 'migrations', 'kafka', 'opensearch']],
  ['Servisler', ['svc_requests', 'svc_ingest', 'svc_indexer', 'svc_connector', 'svc_gateway']],
  ['Veri akışı ve kuyruklar', ['outbox', 'queues', 'sources']],
];
const REFRESH_MS = 30000;


function card(c) {
  return h('section', { class: `panel sys-card sys-${c.state}`, id: `sys-${c.id}`, 'aria-labelledby': `sys-h-${c.id}` },
    h('div', { class: 'sys-head' }, h('h3', { id: `sys-h-${c.id}` }, c.label), pill(c.state_label, TONE[c.state])),
    h('p', { class: 'sys-summary' }, c.summary),
    c.items.length ? kv(c.items.map((i) => [i.label, String(i.value)])) : null,
    c.note ? h('p', { class: 'muted small sys-note' }, c.note) : null);
}

export async function renderSystem(view, param) {
  const { mode } = await backend();
  setHeader({
    title: 'Sistem Durumu',
    sub: 'Servisler, veri akışı, uyarılar ve bildirim kanalları.',
    crumbs: [['Sistem Durumu']],
  });
  if (mode !== 'api') {
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '),
      'Sistem durumu canlı API ile çalışır: servis nabızları, veritabanı ve kuyruklar gerçek kurulumdan okunur.'));
    return {};
  }
  const banner = h('div', { id: 'sysOverall', class: 'notice', role: 'status', 'aria-live': 'polite' });
  const counts = h('div', { class: 'cards', id: 'sysCounts' });
  const body = h('div', { id: 'sysGroups' });
  const refresh = h('button', { class: 'btn secondary small', type: 'button', id: 'sysRefresh' }, 'Yenile');
  const alertsBox = h('div', { id: 'sysAlertsBox' });
  const notifyBox = h('div', { id: 'sysNotifyBox' });
  view.append(h('div', { class: 'toolbar' }, banner, refresh), counts, alertsBox, body, notifyBox);
  const [alerts] = await Promise.all([alertsSection(alertsBox), notifySection(notifyBox)]);

  let timer = null;
  async function load() {
    refresh.disabled = true;
    try {
      const d = await systemStatus();
      clear(banner).append(h('strong', {}, `Genel durum: ${d.overall_label}. `),
        `Son denetim ${formatDateTimeSec(d.checked_at)}; sayfa açıkken 30 saniyede bir yenilenir.`);
      banner.className = `notice ${d.overall === 'bad' ? 'bad' : d.overall === 'warn' ? 'warn' : 'success'}`;
      clear(counts).append(
        metric({ label: 'Çalışıyor', value: d.counts.ok, tone: 'good' }),
        metric({ label: 'Dikkat', value: d.counts.warn, tone: d.counts.warn ? 'warn' : '' }),
        metric({ label: 'Sorun var', value: d.counts.bad, tone: d.counts.bad ? 'bad' : '' }),
        metric({ label: 'Bu kurulumda yok', value: d.counts.off }));
      const byId = new Map(d.components.map((c) => [c.id, c]));
      clear(body).append(...GROUPS.map(([title, ids]) => h('section', { class: 'sys-group', 'aria-label': title },
        h('h2', { class: 'sys-group-title' }, title),
        h('div', { class: 'sys-grid' }, ids.filter((id) => byId.has(id)).map((id) => card(byId.get(id)))))));
    } catch (err) {
      clear(body).append(err.status === 403
        ? h('div', { class: 'notice', role: 'note', id: 'sysForbidden' }, h('strong', {}, 'Yetkiniz yok. '),
          'Sistem durumunu Platform operatörü, Veri sorumlusu ve Denetçi rolleri görür.')
        : errorBox(`Sistem durumu yüklenemedi: ${err.message}`));
      clear(banner); clear(counts);
      clearInterval(timer);
      timer = null;
    } finally {
      refresh.disabled = false;
    }
  }
  refresh.addEventListener('click', () => { load(); alerts.reload(); });
  await load();
  if (body.querySelector('.sys-grid')) {
    timer = setInterval(() => { if (view.isConnected) { load(); alerts.reload(); } else clearInterval(timer); }, REFRESH_MS);
  }
  const focusSection = (p) => {
    const el = p === 'uyarilar' ? document.getElementById('sysAlerts') : p === 'bildirimler' ? document.getElementById('sysNotify') : null;
    if (el) { el.scrollIntoView({ block: 'start' }); el.focus({ preventScroll: true }); }
  };
  focusSection(param);
  return { update: (p) => focusSection(p) };
}
