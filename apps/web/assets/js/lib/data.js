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

/** Elle CI oluşturma (U6.1, ADR-0026; yalnızca API modu): istek uzlaştırıcıda kimlik çözümlemesinden geçer. */
export async function creatableClasses() {
  return api('/api/v1/meta/creatable-classes');
}

export async function createForm(cls) {
  return api(`/api/v1/meta/classes/${encodeURIComponent(cls)}/form`);
}

export async function createCi({ cls, attributes, note, force = false }) {
  return api('/api/v1/ci', {
    method: 'POST',
    body: JSON.stringify({ sys_class_name: cls, attributes, note: note || null, force_new: force,
      idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

export async function createStatus(id) {
  return api(`/api/v1/ci-requests/${encodeURIComponent(id)}`);
}

/** CSV ile toplu içe aktarma (U6.3, ADR-0026 §6; yalnızca API modu): önizleme yazmaz, onay toplu iş açar. */
export async function previewImport({ cls, csv, fileName }) {
  return api('/api/v1/imports/preview', { method: 'POST', body: JSON.stringify({ sys_class_name: cls, csv, file_name: fileName || null }) });
}

export async function commitImport({ cls, csv, fileName, note }) {
  return api('/api/v1/imports', {
    method: 'POST',
    body: JSON.stringify({ sys_class_name: cls, csv, file_name: fileName || null, note: note || null,
      idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

export async function importStatus(id, status = '') {
  const p = new URLSearchParams(status ? { status } : {});
  return api(`/api/v1/imports/${encodeURIComponent(id)}${status ? `?${p}` : ''}`);
}

/** Elle ilişki (U6.2, ADR-0026; yalnızca API modu): izinli ilişkiler, ekleme/kaldırma isteği ve durumu. */
export async function relationOptions(ciId) {
  return api(`/api/v1/ci/${encodeURIComponent(ciId)}/relation-options`);
}

export async function requestRelation(ciId, { action, type, direction, target, note }) {
  return api(`/api/v1/ci/${encodeURIComponent(ciId)}/relations`, {
    method: 'POST',
    body: JSON.stringify({ action, type, direction, target, note: note || null, idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}

export async function relationStatus(id) {
  return api(`/api/v1/relation-requests/${encodeURIComponent(id)}`);
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
// Sahiplik onayı (U6.4, ADR-0027): yalnızca API modunda
export const attestationSummary = () => api('/api/v1/attestation/summary');
export function attestationItems(view, q = '') {
  const p = new URLSearchParams({ view, limit: '500' });
  if (q) p.set('q', q);
  return api(`/api/v1/attestation/items?${p}`);
}
export function attest({ items, decision, reason = null, note = null }) {
  return api('/api/v1/attestations', {
    method: 'POST', body: JSON.stringify({ items, decision, reason, note, idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}
export const attestationRequests = (ids) => api(`/api/v1/attestation-requests?ids=${ids.map(encodeURIComponent).join(',')}`);

// Eskime işletimi (U7.1, ADR-0028): yalnızca API modunda
export const stalenessSummary = () => api('/api/v1/staleness/summary');
export function stalenessCases(view, q = '') {
  const p = new URLSearchParams({ view, limit: '500' });
  if (q) p.set('q', q);
  return api(`/api/v1/staleness/cases?${p}`);
}
export function stalenessDecision(caseId, action, note) {
  return api(`/api/v1/staleness/${encodeURIComponent(caseId)}/decision`, {
    method: 'POST', body: JSON.stringify({ action, note, idempotency_key: `ui-${crypto.randomUUID()}` }),
  });
}
export const retirementStatus = (id) => api(`/api/v1/retirement-requests/${encodeURIComponent(id)}`);

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

export const ciImpact = (id, depth = 6) => api(`/api/v1/ci/${encodeURIComponent(id)}/impact?depth=${depth}`);

export async function ciHistory(id) {
  if (!(await isApi())) return null;
  return (await api(`/api/v1/ci/${encodeURIComponent(id)}/history?limit=50`)).items;
}

/** Yönetim paneli (U8.2, ADR-0033): kişiye düşen bekleyen işler (yalnızca yetkisi olanlar). */
export async function adminAttention() {
  if (!(await isApi())) return null;
  return (await api('/api/v1/admin/attention')).items;
}

/** Kullanım kılavuzu (docs/kilavuz → content/guide.json; tools/build_guide.py). */
export const guide = () => loadJSON('/content/guide.json');

/** Teknik belgeler (oturum ister; demo modunda yok). */
export async function docsList() {
  if (!(await isApi())) return null;
  return api('/api/v1/docs');
}
export async function docGet(name) {
  if (!(await isApi())) return null;
  return api(`/api/v1/docs/${name.split('/').map(encodeURIComponent).join('/')}`);
}

/** Sistem durumu (U8.1, ADR-0032): yalnızca platform okuma izniyle. */
export const systemStatus = () => api('/api/v1/system/status');

/** Değişiklik zaman çizelgesi (U7.4, F1-19): anlar yeniden eskiye; ``before`` sonraki sayfanın imleci. */
export function ciTimeline(id, before = null, limit = 60) {
  const p = new URLSearchParams({ limit: String(limit) });
  if (before) p.set('before', before);
  return api(`/api/v1/ci/${encodeURIComponent(id)}/timeline?${p}`);
}

/** CI'ın geçmiş bir andaki hâli (ISO 8601 an, saat dilimiyle). */
export const ciAsOf = (id, at) => api(`/api/v1/ci/${encodeURIComponent(id)}/as-of?${new URLSearchParams({ at })}`);

/** İlişki haritası (ADR-0031): derinlik 1–3, isteğe bağlı geçmiş an. */
export function ciGraph(id, { depth = 2, at = null } = {}) {
  const p = new URLSearchParams({ depth: String(depth) });
  if (at) p.set('at', at);
  return api(`/api/v1/ci/${encodeURIComponent(id)}/graph?${p}`);
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

// Kullanıcılar ve roller (U8.3, ADR-0034): yalnızca API modu
export const iamRoles = () => api('/api/v1/iam/roles');
export function iamPeople({ q = '', role = '', status = 'active', expiring = false, limit = 100, offset = 0 } = {}) {
  const p = new URLSearchParams({ status, limit: String(limit), offset: String(offset) });
  if (q) p.set('q', q);
  if (role) p.set('role', role);
  if (expiring) p.set('expiring', 'true');
  return api(`/api/v1/iam/people?${p}`);
}
export const iamPerson = (id) => api(`/api/v1/iam/people/${encodeURIComponent(id)}`);
export const iamGroups = (q = '') => api(`/api/v1/iam/groups?${new URLSearchParams(q ? { q } : {})}`);
export const iamEvents = (limit = 200) => api(`/api/v1/iam/events?limit=${limit}`);
export function iamCreatePerson(body) {
  return api('/api/v1/iam/people', { method: 'POST', body: JSON.stringify({ ...body, idempotency_key: `ui-${crypto.randomUUID()}` }) });
}
export function iamUpdatePerson(id, body) {
  return api(`/api/v1/iam/people/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
}
export function iamBindGroup(id, body) {
  return api(`/api/v1/iam/groups/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
}
export function iamGrant(body) {
  return api('/api/v1/iam/grants', { method: 'POST', body: JSON.stringify({ ...body, idempotency_key: `ui-${crypto.randomUUID()}` }) });
}
export function iamRevoke(body) {
  return api('/api/v1/iam/grants/revoke', { method: 'POST', body: JSON.stringify(body) });
}

// Yönetişim görevleri (U8.4, ADR-0035): yalnızca API modu
export function tasksList({ view = 'mine', state = 'open', q = '', campaign = null } = {}) {
  const p = new URLSearchParams({ view, state });
  if (q) p.set('q', q);
  if (campaign) p.set('campaign', campaign);
  return api(`/api/v1/tasks?${p}`);
}
export const taskDetail = (id) => api(`/api/v1/tasks/${encodeURIComponent(id)}`);
export function taskCreate(body) {
  return api('/api/v1/tasks', { method: 'POST', body: JSON.stringify({ ...body, idempotency_key: `ui-${crypto.randomUUID()}` }) });
}
export function taskComplete(id, outcome, note) {
  return api(`/api/v1/tasks/${encodeURIComponent(id)}/complete`, { method: 'POST', body: JSON.stringify({ outcome, note }) });
}
export function taskCancel(id, reason) {
  return api(`/api/v1/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: JSON.stringify({ reason }) });
}
export const taskChannels = () => api('/api/v1/task-channels');
// Sihirbazlar (U8.7, ADR-0038)
export const integrationsCatalog = () => api('/api/v1/integrations');
export function integrationPlan(body) {
  return api('/api/v1/integrations/plan', { method: 'POST', body: JSON.stringify(body) });
}
export const integrationStatus = (name) => api(`/api/v1/integrations/${encodeURIComponent(name)}`);
export function validateChangeSet(changes) {
  return api('/api/v1/schema/change-sets/validate', { method: 'POST', body: JSON.stringify({ changes }) });
}
// CMDB sağlığı, kalite kuralları ve bulgular (U8.6, ADR-0037)
export const healthView = () => api('/api/v1/quality/health');
export const qualityRules = () => api('/api/v1/quality/rules');
export const qualityRule = (id) => api(`/api/v1/quality/rules/${encodeURIComponent(id)}`);
export const qualityFields = (cls) => api(`/api/v1/quality/fields?${new URLSearchParams({ class: cls })}`);
export function rulePreview(body) {
  return api('/api/v1/quality/rules/preview', { method: 'POST', body: JSON.stringify(body) });
}
export function ruleCreate(body) {
  return api('/api/v1/quality/rules', { method: 'POST', body: JSON.stringify({ ...body, idempotency_key: `ui-${crypto.randomUUID()}` }) });
}
export function ruleUpdate(id, body) {
  return api(`/api/v1/quality/rules/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
}
export function ruleRun(id, reason) {
  return api(`/api/v1/quality/rules/${encodeURIComponent(id)}/run`, { method: 'POST', body: JSON.stringify({ reason }) });
}
export function findingsList({ state = 'current', q = '', dimension = null, rule = null, triage = false } = {}) {
  const p = new URLSearchParams({ state });
  if (q) p.set('q', q);
  if (dimension) p.set('dimension', dimension);
  if (rule) p.set('rule', rule);
  if (triage) p.set('triage', 'true');
  return api(`/api/v1/quality/findings?${p}`);
}
export const findingDetail = (id) => api(`/api/v1/quality/findings/${encodeURIComponent(id)}`);
export function findingException(id, reason, days) {
  return api(`/api/v1/quality/findings/${encodeURIComponent(id)}/exception`, { method: 'POST', body: JSON.stringify({ reason, days }) });
}
export function findingReopen(id, reason) {
  return api(`/api/v1/quality/findings/${encodeURIComponent(id)}/reopen`, { method: 'POST', body: JSON.stringify({ reason }) });
}
// Zamanlanmış kampanyalar (U8.5, ADR-0036)
export const campaignsList = () => api('/api/v1/campaigns');
export const campaignDetail = (id) => api(`/api/v1/campaigns/${encodeURIComponent(id)}`);
export function campaignPreview(body) {
  return api('/api/v1/campaigns/preview', { method: 'POST', body: JSON.stringify(body) });
}
export function campaignCreate(body) {
  return api('/api/v1/campaigns', { method: 'POST', body: JSON.stringify({ ...body, idempotency_key: `ui-${crypto.randomUUID()}` }) });
}
export function campaignUpdate(id, body) {
  return api(`/api/v1/campaigns/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) });
}
export function campaignRun(id, reason) {
  return api(`/api/v1/campaigns/${encodeURIComponent(id)}/run`, { method: 'POST', body: JSON.stringify({ reason }) });
}
export function taskChannelUpdate(name, body) {
  return api(`/api/v1/task-channels/${encodeURIComponent(name)}`, { method: 'PATCH', body: JSON.stringify(body) });
}

// ---------------------------------------------------------------------------------------------- denetim izi (U8.8)
function auditQuery(filters) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters || {})) if (v !== null && v !== undefined && v !== '') p.set(k, v);
  return p;
}
export const auditCatalog = () => api('/api/v1/audit/catalog');
export const auditEvents = (filters) => api(`/api/v1/audit/events?${auditQuery(filters)}`);
export const auditEvent = (source, id) => api(`/api/v1/audit/events/${encodeURIComponent(source)}/${encodeURIComponent(id)}`);
export const auditSummary = (filters) => api(`/api/v1/audit/summary?${auditQuery(filters)}`);
/** Dışa aktarma bağlantısı (oturum çereziyle indirilir; aktarmanın kendisi denetim olayıdır). */
export function auditExportUrl(filters, format) {
  const p = auditQuery({ ...filters, cursor: null });
  p.set('format', format);
  return `/api/v1/audit/export?${p}`;
}
export const auditIntegrity = () => api('/api/v1/audit/integrity');
export const auditSeals = (filters) => api(`/api/v1/audit/seals?${auditQuery(filters)}`);
export function auditVerify(body) {
  return api('/api/v1/audit/seals/verify', { method: 'POST', body: JSON.stringify(body) });
}
export const auditReviews = () => api('/api/v1/audit/reviews');
export function auditReview(body) {
  return api('/api/v1/audit/reviews', { method: 'POST', body: JSON.stringify({ ...body, idempotency_key: `ui-${crypto.randomUUID()}` }) });
}

// ---------------------------------------------------------------------------------------------- bildirim ve uyarı (U8.9)
export const notificationCount = () => api('/api/v1/notifications/count');
export function notificationsList({ unreadOnly = false, before = null } = {}) {
  const p = new URLSearchParams({ unread_only: unreadOnly ? 'true' : 'false' });
  if (before) p.set('before', before);
  return api(`/api/v1/notifications?${p}`);
}
export function notificationsRead(ids) {
  return api('/api/v1/notifications/read', { method: 'POST', body: JSON.stringify({ ids }) });
}
export const notificationsReadAll = () => api('/api/v1/notifications/read-all', { method: 'POST' });
export const notifyRoutes = () => api('/api/v1/notifications/routes');
export function notifyRoutePut(body) {
  return api('/api/v1/notifications/routes', { method: 'PUT', body: JSON.stringify(body) });
}
export function notifyTest(body) {
  return api('/api/v1/notifications/test', { method: 'POST', body: JSON.stringify(body) });
}
export const opsAlerts = (view = 'active') => api(`/api/v1/ops/alerts?${new URLSearchParams({ view })}`);
export function opsAlertAck(id, note) {
  return api(`/api/v1/ops/alerts/${encodeURIComponent(id)}/ack`, { method: 'POST', body: JSON.stringify({ note }) });
}

// ---------------------------------------------------------------------------------------------- ServiceNow replikası (U9.1)
export const replicaView = () => api('/api/v1/replica');
export const replicaItems = (view = 'attention') => api(`/api/v1/replica/items?${new URLSearchParams({ view, limit: '100' })}`);
export function replicaSetting(body) {
  return api('/api/v1/replica/settings', { method: 'PUT', body: JSON.stringify(body) });
}
export function replicaScope(cls, body) {
  return api(`/api/v1/replica/scope/${encodeURIComponent(cls)}`, { method: 'PUT', body: JSON.stringify(body) });
}
export function replicaApprove(id, note) {
  return api(`/api/v1/replica/runs/${encodeURIComponent(id)}/approve`, { method: 'POST', body: JSON.stringify({ note }) });
}
export function replicaResolve(id, action, note) {
  return api(`/api/v1/replica/items/${encodeURIComponent(id)}/resolve`, { method: 'POST', body: JSON.stringify({ action, note }) });
}

// ---------------------------------------------------------------------------------------------- yapay zekâ önerileri (U9.2)
export const proposalsList = (status = 'pending') => api(`/api/v1/proposals?${new URLSearchParams({ status })}`);
export function proposalDecide(id, decision, note) {
  return api(`/api/v1/proposals/${encodeURIComponent(id)}/decide`, { method: 'POST', body: JSON.stringify({ decision, note }) });
}
