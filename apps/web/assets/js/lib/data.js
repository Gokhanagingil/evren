// Ekranların tek veri katmanı.
// API modu: Evren API'si (services/api) varsa veriler oradan gelir; yazma işlemleri API'ye gider.
// Demo modu: API yoksa (tools/dev_server.py) /content/demo.json — sentetik, evren-core hattından üretilir.
// Ekranlar iki modu ayırt etmez; aynı veri biçimini alır.
import { h, loadJSON, fold } from './dom.js';

export const ROLE_TR = {
  Discovery: 'Keşif', Virtualization: 'Sanallaştırma', Network: 'Ağ keşfi', Monitoring: 'İzleme',
  EndpointSecurity: 'Uç nokta güvenliği', ITSM: 'ITSM', ChangeManagement: 'Değişiklik kayıtları',
  Manual: 'Elle giriş', Platform: 'Platform',
};
export const LIFECYCLE = {
  active: ['Etkin', 'good'], shadow: ['Gölge', 'purple'], retiring: ['Devrediliyor', 'warn'], retired: ['Emekli', 'neutral'],
};
export const BREAKER = { ok: ['Sağlıklı', 'good'], warn: ['Uyarı', 'warn'], open: ['Karantina', 'bad'] };
export const CONTROL = { pass: ['Başarılı', 'good'], fail: ['Başarısız', 'bad'], inconclusive: ['Kesin değil', 'warn'] };
export const OUTCOME = {
  linked: 'Bağlı', rule_match: 'Kuralla eşleşti', score_match: 'Skorla eşleşti', new: 'Yeni CI', review: 'İncelemede',
  insufficient: 'Kimlik yetersiz', import: 'İlk yükleme', manual: 'İnsan kararı', rule: 'Kural', score: 'Skor',
  split: 'Ayrıldı (insan kararı)',
};

/** Kaynak rozeti: renk rolü, metin veri kaynağını gösterir (ürün değişse de renk aynı kalır). */
export function srcChip(source, role, title) {
  return h('span', { class: `src r-${role || 'Platform'}`, title: title || `${source} · ${ROLE_TR[role] || role || ''}` }, source);
}

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.error?.message || `İstek başarısız (HTTP ${status})`);
    this.status = status;
    this.code = body?.error?.code;
    this.details = body?.error?.details || [];
  }
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin', cache: 'no-store', ...options,
    headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.method && options.method !== 'GET' ? { 'X-Evren-Request': '1' } : {}), ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body);
  return body;
}

let modePromise = null;
/** 'api' | 'demo'. Oturum gerekiyorsa giriş sayfasına yönlendirir. */
export function backend() {
  if (!modePromise) {
    // Statik sunucu bu dosyayı {"mode": "demo"} olarak, API sunucusu {"mode": "api"} olarak sunar.
    modePromise = loadJSON('/content/backend.json')
      .then(async (b) => {
        if (b.mode !== 'api') return { mode: 'demo', me: null };
        const res = await fetch('/api/v1/me', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } });
        if (res.status === 401) { location.assign('/auth/login'); return new Promise(() => {}); }
        if (!res.ok) throw new ApiError(res.status, await res.json().catch(() => null));
        return { mode: 'api', me: await res.json() };
      });
  }
  return modePromise;
}

export const demo = () => loadJSON('/content/demo.json');
/** Ürün değişimi tatbikatı (tools/build_drill.py; iki modda da statik içerik). */
export const drillView = () => loadJSON('/content/drill.json');
const isApi = async () => (await backend()).mode === 'api';

export async function overview() {
  if (!(await isApi())) {
    const d = await demo();
    return { summary: { ...d.summary, synthetic: true }, sources: d.sources, controls: d.controls, note: d.note,
      generated_at: d.generated_at };
  }
  const [summary, src, controls] = await Promise.all([api('/api/v1/summary'), api('/api/v1/sources'), api('/api/v1/controls')]);
  return { summary, sources: src.sources, controls, generated_at: summary.generated_at,
    note: summary.synthetic ? 'Veritabanında sentetik demo verisi var (tools/load_demo_pg.py ile yüklendi).' : null };
}

export async function sourcesView() {
  if (!(await isApi())) { const d = await demo(); return { roles: d.roles, sources: d.sources }; }
  return api('/api/v1/sources');
}

export async function rulesView() {
  if (!(await isApi())) return (await demo()).rules;
  return api('/api/v1/rules');
}

export async function controlsView() {
  if (!(await isApi())) return (await demo()).controls;
  return api('/api/v1/controls');
}

const demoDecisions = new Map(); // demo modunda kararlar yalnızca oturumluk

export async function queueView(status = 'open') {
  if (!(await isApi())) {
    const d = await demo();
    const items = d.queue.map((q) => ({ ...q, code: q.id, status: demoDecisions.has(q.id) ? 'decided' : 'open',
      decision: demoDecisions.get(q.id) || null }));
    return { items: status === 'decided' ? items.filter((q) => q.decision) : items.filter((q) => !q.decision),
      can_decide: true, persistent: false };
  }
  const res = await api(`/api/v1/match-queue?status=${encodeURIComponent(status)}`);
  return { ...res, persistent: true };
}

export async function queueCounts() {
  if (!(await isApi())) {
    const d = await demo();
    return d.queue.filter((q) => !demoDecisions.has(q.id)).length;
  }
  const s = await api('/api/v1/summary');
  return s.queue_review + s.queue_insufficient + (s.queue_unmatched || 0);
}

export async function decide(item, body) {
  if (!(await isApi())) {
    const labels = { merge: 'Birleştirildi', new_ci: 'Yeni CI olarak açıldı', defer: 'Ertelendi', fix_at_source: 'Kaynakta düzeltilecek' };
    const decision = { decision: body.decision, label: labels[body.decision], ci_sys_id: body.ci_sys_id || null, applied: false };
    demoDecisions.set(item.id, decision);
    return { ...decision, replayed: false, message: 'Önizleme: karar yalnızca bu oturumda tutulur.' };
  }
  const key = `ui-${crypto.randomUUID()}`;
  return api(`/api/v1/match-queue/${encodeURIComponent(item.id)}/decision`, {
    method: 'POST', body: JSON.stringify({ ...body, idempotency_key: key }),
  });
}

export function undoDemoDecision(id) { demoDecisions.delete(id); }

/** Metadata değişiklik setleri (yalnızca API modu). */
export const schemaCatalog = () => api('/api/v1/schema/catalog');
/** Veri Modeli (U4.4). Demo modunda metadata'dan üretilmiş statik dosya; CI sayıları sentetik demo envanterinden. */
export async function dataModel() {
  if (!(await isApi())) {
    const [m, d] = await Promise.all([loadJSON('/content/model.json'), demo()]);
    const counts = {};
    for (const c of d.cis) counts[c.class] = (counts[c.class] || 0) + 1;
    return { ...m, ci_counts: counts, can_draft: false, demo: true };
  }
  return api('/api/v1/schema/model');
}
export const changeSets = () => api('/api/v1/schema/change-sets');
export const changeSetDetail = (id) => api(`/api/v1/schema/change-sets/${encodeURIComponent(id)}`);
export function createChangeSet(body) {
  return api('/api/v1/schema/change-sets', { method: 'POST', body: JSON.stringify({ ...body, idempotency_key: `ui-${crypto.randomUUID()}` }) });
}
/** ``action``: submit | approve | reject | publish. Onay/ret karar notu alır. */
export function changeSetAction(id, action, body = null) {
  return api(`/api/v1/schema/change-sets/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: JSON.stringify(body || {}) });
}

/** Gölge mod raporu (yalnızca API modu; demo modunda null). */
export async function shadowReport() {
  if (!(await isApi())) return null;
  return api('/api/v1/reports/shadow');
}

export async function shadowSnapshots() {
  if (!(await isApi())) return { items: [] };
  return api('/api/v1/reports/shadow/snapshots');
}

/** Raporu değiştirilemez kanıt olarak kaydeder (SHA-256 ile; GRC Platformu bu kaydı alır). */
export async function createShadowSnapshot(note) {
  return api('/api/v1/reports/shadow/snapshots', {
    method: 'POST', body: JSON.stringify({ note: note || null, idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

/** Elle düzenleme (yalnızca API modu): değerler Manual kaynak kaydı olarak uzlaştırmaya girer; yanıt 202. */
export async function editCi(ciId, attributes, note) {
  return api(`/api/v1/ci/${encodeURIComponent(ciId)}`, {
    method: 'PATCH', body: JSON.stringify({ attributes, note: note || null, idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

/** Referans verisi araması (grup, kişi, konum…). */
export async function refSearch(table, q = '', limit = 200) {
  const p = new URLSearchParams({ limit: String(limit) });
  if (q) p.set('q', q);
  return api(`/api/v1/ref/${encodeURIComponent(table)}?${p}`);
}

/** Bağlayıcıyı şimdi çalıştır (ADR-0021, yalnızca API modu): istek yazılır, çalıştırıcı servisi yürütür (202). */
export async function requestRun(source, { instance, mode, since, note }) {
  return api(`/api/v1/sources/${encodeURIComponent(source)}/run-requests`, {
    method: 'POST', body: JSON.stringify({ instance, mode, since: since || null, note: note || null,
      idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

export async function cancelRun(id) {
  return api(`/api/v1/run-requests/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
}

/** Bağlayıcı kimliği ver: yanıttaki belirteç YALNIZCA bu yanıtta vardır; saklanmaz, yeniden istenemez. */
export async function issueCredential(source, { name, expiresDays, perMinute }) {
  return api(`/api/v1/sources/${encodeURIComponent(source)}/credentials`, {
    method: 'POST', body: JSON.stringify({ name, expires_days: expiresDays, per_minute: perMinute,
      idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

export async function revokeCredential(name) {
  return api(`/api/v1/credentials/${encodeURIComponent(name)}/revoke`, { method: 'POST' });
}

/** Birleştirmeyi ayırma isteği (yalnızca API modu): uzlaştırıcı uygular; yanıt 202 + istek kimliği. */
export async function splitCi(ciId, linkId, note) {
  return api(`/api/v1/ci/${encodeURIComponent(ciId)}/split`, {
    method: 'POST', body: JSON.stringify({ link_id: linkId, note: note || null, idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

/** Yetkisiz değişiklik anomalisinin insan kararı (ADR-0025): incelendi, yanlış alarm (gerekçe zorunlu), yeniden aç. */
export async function reviewAnomaly(id, status, note) {
  return api(`/api/v1/anomalies/${encodeURIComponent(id)}/review`, {
    method: 'POST', body: JSON.stringify({ status, note: note || null }),
  });
}

const SCOPE_TEST = {
  izlenmeyen: (c) => c.server && !c.coverage.monitoring,
  korumasiz: (c) => c.server && !c.coverage.security,
  'itsm-yok': (c) => c.server && !c.coverage.itsm,
  yetkisiz: (c) => c.unauthorized_count > 0,
  'yetkisiz-degisiklik': (c) => (c.unauthorized_changes || 0) > 0,
};

/** CI arama: API'de sunucu tarafında sayfalanır (imleç); demoda istemcide süzülür. */
export async function ciSearch({ q = '', cls = '', src = '', scope = '', cursor = null, limit = 100 } = {}) {
  if (!(await isApi())) {
    const d = await demo();
    const n = fold(q);
    const items = d.cis.filter((c) => (!cls || c.class === cls) && (!src || c.sources.includes(src))
      && (!scope || SCOPE_TEST[scope]?.(c))
      && (!n || fold([c.name, c.fqdn, c.ip_address, c.os, c.sys_id, c.class_label].join(' ')).includes(n)));
    return { items, count: items.length, next_cursor: null };
  }
  const p = new URLSearchParams({ limit: String(limit) });
  if (q) p.set('q', q);
  if (cls) p.set('class', cls);
  if (src) p.set('source', src);
  if (scope) p.set('scope', scope);
  if (cursor) p.set('cursor', cursor);
  return api(`/api/v1/ci?${p}`);
}

/**
 * Küresel CI araması (U3): API modunda OpenSearch (yazım hatası toleranslı, önekle, Türkçe harf duyarsız); OpenSearch
 * yoksa API PostgreSQL'e düşer. Demo modunda yerel alt dize araması. Dönüş: { engine, degraded, items }.
 */
export async function globalSearch(q, limit = 20) {
  if (!(await isApi())) {
    const r = await ciSearch({ q, limit });
    return { engine: 'demo', degraded: false, items: r.items.slice(0, limit) };
  }
  return api(`/api/v1/search?${new URLSearchParams({ q, limit: String(limit) })}`);
}

export async function ciDetail(id) {
  if (!(await isApi())) return (await demo()).cis.find((c) => c.sys_id === id) || null;
  try { return await api(`/api/v1/ci/${encodeURIComponent(id)}`); } catch (e) { if (e.status === 404) return null; throw e; }
}

export async function ciHistory(id) {
  if (!(await isApi())) return null;
  return (await api(`/api/v1/ci/${encodeURIComponent(id)}/history?limit=50`)).items;
}

export async function ciFacets() {
  if (!(await isApi())) {
    const d = await demo();
    const seen = new Map();
    d.cis.forEach((c) => seen.set(c.class, c.class_label));
    return { classes: [...seen].map(([name, label]) => ({ name, label_tr: label })), sources: d.sources };
  }
  const [classes, src] = await Promise.all([api('/api/v1/meta/classes'), api('/api/v1/sources')]);
  return { classes, sources: src.sources };
}
