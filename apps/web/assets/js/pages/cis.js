// Konfigürasyon öğeleri: arama ve filtreli liste (API'de sunucu tarafı, imleçli); CI 360° paneli
// ("bu değer nereden geldi?"), API modunda zaman çizelgesi, geçmiş bir an ve ilişki haritası bağlantısı (U7.4).
import { h, clear, dataTable, formatDateTime, fromIstanbulInput, kv, pill, section, toIstanbulInput } from '../lib/dom.js';
import { closeDetail, closeOverlay, dialog, openDetail, setHeader, toast } from '../lib/shell.js';
import { OUTCOME, ROLE_TR, attest, backend, ciAsOf, ciDetail, ciFacets, ciImpact, ciSearch, ciTimeline, reviewAnomaly, splitCi, srcChip } from '../lib/data.js';
import { STATE_TONE, openRejectDialog, waitForRequests } from './attestation.js';
import { STAGE_TONE, observationList, openDecisionDialog, sendDecision } from './staleness.js';
import { openEditDialog } from './ci-edit.js';
import { openCreateDialog } from './ci-create.js';
import { openImportDialog } from './ci-import.js';
import { confirmRemoveRelation, openAddRelationDialog } from './ci-relations.js';

// KISITLI güvenlik duruşu (ADR-0023): API yalnızca Güvenlik okuyucu/Denetçi rolüne döndürür; her gösterim denetim izinde.
const POSTURE_TONE = { None: 'good', Informational: 'good', Low: 'good', Medium: 'warn', High: 'bad' };
const POSTURE_TR = { None: 'Yok', Informational: 'Bilgi', Low: 'Düşük', Medium: 'Orta', High: 'Yüksek' };
function postureSection(p) {
  const level = (v) => (v ? pill(POSTURE_TR[v] || v, POSTURE_TONE[v] || '') : '—');
  return section('Güvenlik duruşu (kısıtlı)',
    h('p', { class: 'notice warn small', role: 'note' }, h('strong', {}, 'Kısıtlı bilgi. '),
      'Yetkinize göre gösteriliyor ve görüntüleme denetim izine yazıldı; paylaşmayın veya rapora kopyalamayın.'),
    kv([
      ['Maruziyet düzeyi', level(p.exposure_level)], ['Risk puanı', level(p.risk_score)],
      ['Koruma durumu', p.protection_status || '—'], ['Sağlık', p.health_status || '—'],
      ['Kritik / yüksek zafiyet', p.vulnerabilities_critical == null && p.vulnerabilities_high == null ? 'Bilinmiyor'
        : `${p.vulnerabilities_critical ?? '—'} / ${p.vulnerabilities_high ?? '—'}`],
      ['Kaynakta son görülme', p.last_seen ? formatDateTime(p.last_seen) : '—'],
      ['Kaynak · gözlem', `${p.data_source} · ${formatDateTime(p.observed_at)}`],
    ]));
}

const SCOPES = {
  '': 'Tüm kapsam',
  izlenmeyen: 'İzlenmeyen sunucular',
  korumasiz: 'Uç nokta koruması olmayan',
  'itsm-yok': 'Mevcut CMDB\'de olmayan',
  yetkisiz: 'Yetkisiz kaynak denemesi olan',
  'yetkisiz-degisiklik': 'Onaysız yapılandırma değişikliği olan',
};

// Değişiklik denetimi (ADR-0025): gözlenen değişiklikler onaylı değişiklik penceresine göre değerlendirilir.
const VERDICT_TONE = { pending: 'neutral', authorized: 'good', unauthorized: 'bad', suppressed: 'warn' };
const ANOMALY_TONE = { open: 'bad', acknowledged: 'warn', resolved: 'good', false_positive: 'neutral' };

function changeWindow(c) {
  if (c.work_start) return `Gerçekleşen: ${formatDateTime(c.work_start)} – ${c.work_end ? formatDateTime(c.work_end) : 'sürüyor'}`;
  if (c.start_date && c.end_date) return `Planlı: ${formatDateTime(c.start_date)} – ${formatDateTime(c.end_date)}`;
  return 'Pencere yok';
}

/** Anomali kararı düğmeleri (yalnızca API modu ve anomaly_review izni). */
function anomalyActions(a, review) {
  if (!review) return null;
  const btn = (label, status, tone = 'secondary') => h('button', { class: `btn ${tone} small`, type: 'button',
    'data-review': status, onclick: (ev) => review(a, status, ev.currentTarget) }, label);
  if (a.status === 'open') return h('div', { class: 'link-actions' }, btn('İncelendi', 'acknowledged'), btn('Yanlış alarm…', 'false_positive', 'ghost'));
  if (a.status === 'acknowledged') return h('div', { class: 'link-actions' }, btn('Yanlış alarm…', 'false_positive', 'ghost'), btn('Yeniden aç', 'open', 'ghost'));
  if (a.status === 'false_positive') return h('div', { class: 'link-actions' }, btn('Yeniden aç', 'open', 'ghost'));
  return null;
}

function changeSection(cc, review = null) {
  if (!cc || (!cc.observations.length && !cc.changes.length && !cc.anomalies?.length)) return null;
  const anomalies = cc.anomalies || [];
  const span = (o) => (o.observed_from ? `${formatDateTime(o.observed_from)} – ${formatDateTime(o.observed_at)}` : formatDateTime(o.observed_at));
  return section('Değişiklik denetimi',
    cc.open_unauthorized ? h('div', { class: 'notice warn', role: 'note' }, h('strong', {}, 'Onaysız değişiklik. '),
      `${cc.open_unauthorized} açık kayıt: keşfin gözlediği değişiklik onaylı bir değişiklik penceresine düşmüyor. Kanıt GRC kontrolü CMDB-05'e gider.`) : null,
    anomalies.length ? h('ul', { class: 'mini', id: 'ciAnomalies', tabindex: '-1', 'aria-label': 'Yetkisiz değişiklik kayıtları' }, anomalies.map((a) => h('li', {},
      h('div', { class: 'pills' }, pill(a.status_label, ANOMALY_TONE[a.status] || 'neutral'), a.related_change ? pill(a.related_change, 'info') : null),
      h('div', { class: 'cell-sub' }, `Tespit: ${formatDateTime(a.detected_at)}`
        + (a.reviewed_by ? ` · karar: ${a.reviewed_by}, ${formatDateTime(a.reviewed_at)}` : '')),
      a.review_note ? h('div', { class: 'small muted' }, `Gerekçe: ${a.review_note}`) : null,
      anomalyActions(a, review)))) : null,
    cc.observations.length
      ? h('ul', { class: 'mini', id: 'ciObservations', 'aria-label': 'Gözlenen yapılandırma değişiklikleri' }, cc.observations.map((o) => h('li', {},
        h('div', { class: 'pills' }, pill(o.verdict_label, VERDICT_TONE[o.verdict] || 'neutral'),
          o.change_number ? pill(o.change_number, 'info') : null, o.late ? pill('Kayıt sonradan açıldı', 'warn') : null,
          o.anomaly_status && o.anomaly_status !== 'open' ? pill(`Karar: ${o.anomaly_status_label}`, ANOMALY_TONE[o.anomaly_status] || 'neutral') : null),
        h('div', {}, h('strong', {}, o.label), `: ${o.old_display} → ${o.new_display}`),
        h('div', { class: 'cell-sub' }, `${o.source} · değişiklik aralığı ${span(o)}`),
        o.explanation ? h('div', { class: 'small muted' }, o.explanation) : null)))
      : h('p', { class: 'muted small' }, 'İzlenen özniteliklerde gözlenen değişiklik yok.'),
    cc.changes.length ? h('div', { id: 'ciChanges' }, h('p', { class: 'small' }, h('strong', {}, 'Bağlı değişiklik kayıtları')),
      h('ul', { class: 'mini' }, cc.changes.map((c) => h('li', {},
        h('div', { class: 'pills' }, pill(c.number, 'neutral'), pill(c.state_label, c.state === 'canceled' ? 'bad' : 'info'),
          c.type === 'standard' ? pill('önceden onaylı (standart)', 'good')
            : pill(`onay: ${c.approval_label}`, c.approval === 'approved' ? 'good' : 'warn'),
          c.type === 'standard' ? null : pill(c.type_label, c.type === 'emergency' ? 'warn' : 'neutral')),
        c.short_description ? h('div', {}, c.short_description) : null,
        h('div', { class: 'cell-sub' }, changeWindow(c)))))) : null);
}
const PAGE = 100;
const SPLIT_TONE = { pending: 'warn', applied: 'good', rejected: 'bad' };
const EDIT_RESULT = { applied: 'uygulandı', cleared: 'elle değer kaldırıldı', overridden: 'yedek', unauthorized: 'elle yazılamaz', invalid: 'geçersiz' };
/** Tarih-saat alanları yerel biçimde (API ISO 8601 UTC döner). */
const shown = (a) => (a.type === 'glide_date_time' ? formatDateTime(a.value) : a.display);

export async function renderCis(view, param) {
  const { mode, me } = await backend();
  const facets = await ciFacets();
  const roleOf = Object.fromEntries(facets.sources.map((x) => [x.name, x.role]));
  const state = { q: '', cls: '', src: '', scope: '' };
  let rows = [];
  let nextCursor = null;
  let total = 0;
  let selected = null;
  let seq = 0;

  // Elle CI oluşturma (U6.1): yalnızca API modunda ve CI oluşturma izni olana
  const canCreate = mode === 'api' && me?.can?.create_ci;
  setHeader({
    title: 'Konfigürasyon Öğeleri',
    sub: 'Bir öğeye tıklayın: her değerin kaynağı ve gerekçesi görünür.',
    crumbs: [['Konfigürasyon Öğeleri']],
    actions: canCreate ? [
      h('button', { class: 'btn secondary', type: 'button', id: 'ciImport', onclick: () => openImportDialog(() => load()) }, 'CSV içe aktar…'),
      h('button', { class: 'btn', type: 'button', id: 'ciCreate',
        onclick: () => openCreateDialog(async (ciId) => { await load(); await open(ciId); }) }, 'Yeni CI…')] : [],
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
      h('summary', {}, h('span', { class: 'k' }, a.label), h('span', { class: 'v' }, shown(a)), srcChip(a.source, a.role)),
      h('div', { class: 'why' },
        h('div', {}, h('strong', {}, 'Gerekçe: '), a.reason),
        a.observed_at ? h('div', {}, `Gözlem: ${formatDateTime(a.observed_at)} · Rol: ${ROLE_TR[a.role] || a.role}`) : null,
        a.overridden.length ? h('div', {}, h('strong', {}, 'Ezilen değerler:'),
          h('ul', {}, a.overridden.map((o) => h('li', {}, `${o.source}: ${o.display}`)))) : null,
        a.shadow?.length ? h('div', {}, h('strong', {}, 'Gölge kaynak (karşılaştırma, yazmaz): '),
          a.shadow.map((o) => `${o.source}: ${o.display}`).join(', ')) : null,
        a.unauthorized.length ? h('div', {}, h('strong', {}, 'Yetkisiz kaynak: '), a.unauthorized.join(', '), ' (bu alan bu kaynağın rolüne kapalı)') : null));
  }

  /** Ayırma onayı: hangi değerlerin etkileneceği önceden gösterilir; gerekçe denetim izine yazılır. */
  function confirmSplit(c, l, opener) {
    const own = c.attributes.filter((a) => a.source === l.data_source);
    const note = h('input', { type: 'text', id: 'splitNote', maxlength: '2000', placeholder: 'Ör. seri numarası başka sunucuya ait',
      'aria-describedby': 'splitHelp' });
    const confirm = h('button', { class: 'btn danger', type: 'button', id: 'splitConfirm', onclick: async () => {
      confirm.disabled = true;
      try {
        const res = await splitCi(c.sys_id, l.id, note.value.trim());
        closeOverlay();
        toast(res.message);
        await open(c.sys_id, opener);
      } catch (err) {
        confirm.disabled = false;
        toast(`Ayırma isteği kaydedilemedi: ${err.message}`);
      }
    } }, 'Ayır');
    dialog({
      title: 'Kaynak kaydını ayır',
      body: [
        h('p', {}, h('strong', {}, `${l.data_source} · ${l.source_key}`), ` kaydı "${c.name}" CI'ından ayrılır ve yeni bir CI'a bağlanır. `,
          'Bağ kalıcıdır: sonraki çalışmalarda kayıt bu CI ile otomatik olarak yeniden birleşmez.'),
        own.length ? h('div', { class: 'notice warn' }, h('strong', {}, 'Bu CI\'da etkilenecek değerler'),
          h('ul', {}, own.map((a) => h('li', {}, `${a.label}: ${shown(a)} → `,
            a.overridden.length ? `${a.overridden[0].source} değeri geçer (${a.overridden[0].display})` : 'temizlenir'))))
          : h('p', { class: 'muted' }, 'Bu kaynak bu CI\'da hiçbir özniteliğin kazananı değil; değerler değişmez.'),
        h('p', { class: 'muted small', id: 'splitHelp' }, 'CI silinmez; bağ geçmişi korunur. İstek uzlaştırıcı tarafından uygulanır ve sizin adınızla denetim izine yazılır.'),
        h('label', { class: 'field', for: 'splitNote' }, 'Gerekçe'), note,
      ],
      footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), confirm],
      focus: note,
    });
  }

  /** İlişkiler: kaynağı görünür; API modunda izinli kural varsa elle ilişki eklenir, elle girilmiş olan kaldırılır (U6.2). */
  function relationSection(c, opener) {
    const canAdd = mode === 'api' && c.can_relate;
    const pending = c.pending_relations || [];
    if (!c.relations.length && !pending.length && !canAdd) return null;
    const reopen = () => open(c.sys_id, opener);
    const verb = (r) => (r.direction === 'parent' ? r.type.split('::')[0] : r.type.split('::')[1]);
    return section('İlişkiler',
      c.relations.length ? h('ul', { class: 'mini', id: 'ciRelations' }, c.relations.map((r) => h('li', {},
        h('span', { class: 'muted' }, `${verb(r)} · `), h('a', { href: `#/ci/${r.target}` }, r.target_name), ' ',
        r.source ? srcChip(r.source, roleOf[r.source] || (r.source === 'Manual' ? 'Manual' : null)) : null,
        r.manual && r.source !== 'Manual' ? pill('elle de girildi', 'neutral') : null,
        mode === 'api' && c.can_unrelate && r.manual ? h('button', { class: 'btn ghost small', type: 'button',
          'data-unrelate': r.target, onclick: () => confirmRemoveRelation(c, r, reopen) }, 'Kaldır') : null))) : null,
      pending.length ? h('ul', { class: 'mini', id: 'ciPendingRelations' }, pending.map((p) => h('li', {},
        pill(p.action === 'add' ? 'Ekleme bekliyor' : 'Kaldırma bekliyor', 'warn'), ` ${verb(p)} · ${p.target_name}`,
        h('span', { class: 'cell-sub' }, `${p.requested_by} · ${formatDateTime(p.requested_at)}`)))) : null,
      canAdd ? h('div', { class: 'panel-actions' }, h('button', { class: 'btn secondary small', type: 'button', id: 'relAdd',
        onclick: () => openAddRelationDialog(c, reopen) }, 'İlişki ekle…')) : null);
  }

  /** Yetkisiz değişiklik kararı (ADR-0025): yanlış alarm gerekçe ister; karar sizin adınızla denetim izine yazılır. */
  // Sahiplik onayı (U6.4, ADR-0027): durum, son onay, sorumlular; sorumluya Onayla / Reddet
  function attestationSection(c, opener) {
    const a = c.attestation;
    if (mode !== 'api' || !a?.enabled || a.state === 'out_of_scope') return null;
    const send = async (decision, reason = null, note = null) => {
      try {
        const res = await attest({ items: [c.sys_id], decision, reason, note });
        toast(res.message);
        const st = await waitForRequests(res.accepted.map((x) => x.request));
        const r = st.requests[0];
        if (r?.status === 'rejected') toast(`Uygulanmadı: ${r.result_note}`);
        await open(c.sys_id, opener);
        document.getElementById('ciAttestation')?.focus({ preventScroll: false });
      } catch (err) {
        toast(`Kaydedilemedi: ${err.details?.[0]?.reason || err.message}`);
      }
    };
    const actions = a.can_attest ? h('div', { class: 'link-actions' },
      h('button', { class: 'btn small', type: 'button', id: 'ciAttest', onclick: (ev) => { ev.currentTarget.disabled = true; send('attested'); } }, 'Onayla'),
      h('button', { class: 'btn ghost small', type: 'button', id: 'ciAttestReject',
        onclick: () => openRejectDialog({ name: c.name, reasons: a.reasons, onSend: (reason, note) => send('rejected', reason, note) }) }, 'Reddet…'))
      : a.blocker && a.blocker_code !== 'role' ? h('p', { class: 'muted small' }, a.blocker) : null;
    const last = a.history?.[0];
    return section('Sahiplik onayı', h('div', { id: 'ciAttestation', tabindex: '-1' },
      h('div', { class: 'pills' }, pill(a.state_label, STATE_TONE[a.state] || 'neutral'), a.pending ? pill('İstek uygulanıyor', 'neutral') : null,
        a.orphan ? pill('Sahipsiz', 'bad') : null, a.open_rejection ? pill('Veri sorumlusunda', 'warn') : null),
      a.state === 'rejected' && last?.decision === 'rejected' ? h('div', { class: 'notice bad', role: 'note' }, h('strong', {}, `${last.reason_label}. `), last.note || '') : null,
      kv([['Politika', a.policy ? `${a.policy.label} · ${a.policy.period_days} gün` : '—'],
        ['Son onay', a.attested_date ? `${formatDateTime(a.attested_date)} · ${a.attested_by || '—'}` : 'Hiç onaylanmadı'],
        ['Geçerlilik sonu', a.valid_until ? formatDateTime(a.valid_until) : '—'],
        ['Onay verebilen', a.owners.length ? a.owners.map((o) => o.name).join(', ') : h('span', { class: 'pill warn' }, 'Sahip ve yönetici yok')]]),
      actions));
  }

  // Etki analizi (U7.3, ADR-0030): bu CI düşerse etkilenenler (yukarı) ve bağımlılıkları (aşağı)
  function impactList(nodes, id, label, up) {
    const LIMIT = 15;
    const item = (n) => h('li', { class: `depth-${Math.min(n.depth, 4)}` },
      h('div', { class: 'pills' }, pill(n.class_label, n.service ? 'info' : 'neutral'), n.production ? pill('Üretim', 'good') : null,
        n.retired ? pill('Emekli', 'warn') : null, n.depth > 1 ? pill(`${n.depth}. düzey`, 'neutral') : null),
      h('a', { href: `#/ci/${n.sys_id}` }, n.name),
      h('div', { class: 'cell-sub' }, up ? `${n.phrase} → ${n.via_name}` : `${n.via_name} → ${n.phrase} → ${n.name}`));
    const list = h('ul', { class: 'mini', id, 'aria-label': label }, nodes.slice(0, LIMIT).map(item));
    if (nodes.length <= LIMIT) return list;
    const more = h('button', { class: 'btn ghost small', type: 'button', onclick: () => {
      list.append(...nodes.slice(LIMIT).map(item));
      more.remove();
    } }, `Tümünü göster (${nodes.length})`);
    return h('div', {}, list, more);
  }

  function impactView(doc) {
    const s = doc.summary;
    const up = doc.upstream;
    const down = doc.downstream;
    const head = s.affected_services
      ? h('div', { class: `notice ${s.affected_production_services ? 'warn' : ''}`.trim(), role: 'note' },
        h('strong', {}, `Bu CI düşerse ${s.affected_services} hizmet veya iş uygulaması etkilenir`),
        s.affected_production_services ? ` (${s.affected_production_services} tanesi üretimde).` : '.')
      : h('p', { class: 'muted small' }, s.affected ? 'Bu CI\'a bağımlı CI\'lar var; hizmet katmanına ulaşan bağımlılık yok.' : 'Bu CI\'a bağımlı CI yok.');
    const note = (part) => (part.hidden || part.truncated ? h('p', { class: 'muted small' },
      part.hidden ? `${part.hidden} CI yetkiniz olmadığı için gösterilmiyor. ` : '', part.truncated ? 'Liste kısaltıldı.' : '') : null);
    return [head,
      up.nodes.length ? h('p', { class: 'small' }, h('strong', {}, 'Etkilenenler')) : null,
      up.nodes.length ? impactList(up.nodes, 'ciImpactUp', 'Bu CI düşerse etkilenenler', true) : null, note(up),
      down.nodes.length ? h('p', { class: 'small' }, h('strong', {}, 'Bağımlı olduğu CI\'lar')) : null,
      down.nodes.length ? impactList(down.nodes, 'ciImpactDown', 'Bu CI\'ın bağımlılıkları', false) : null, note(down),
      h('p', { class: 'muted small' }, 'Yön ilişki tipinden gelir (metadata/relationship_types.yaml → impact); fiziksel bağlantı ve sahiplik etki taşımaz.')]
      .filter(Boolean);
  }

  // Zaman çizelgesi (U7.4, F1-19): aynı çalışmanın veya elle isteğin değişiklikleri tek an; "Daha eski" ile sayfalanır
  function momentItem(m) {
    const who = m.reason === 'reconciliation' ? '' : ` · ${m.actor}`;
    return h('li', { class: 'moment', tabindex: '-1' },
      h('div', { class: 'moment-head' }, `${formatDateTime(m.at)} · ${m.source || 'Evren'} · ${m.reason_label}${who}`),
      h('ul', {},
        m.changes.map((x) => h('li', {}, h('strong', {}, x.label), `: ${x.old} → ${x.new}`)),
        m.relations.map((r) => h('li', {}, r.action === 'added' ? 'İlişki açıldı: ' : 'İlişki kalktı: ', `${r.phrase} → `,
          r.other ? h('a', { href: `#/ci/${r.other}` }, r.other_name) : r.other_name,
          r.no_effect ? h('span', { class: 'cell-sub' }, 'Elle kayıt; ilişkiyi başka bir kaynak da bildirdiği için satır değişmedi') : null,
          r.ended === 'not_reported' ? h('span', { class: 'cell-sub' }, 'Kaynak bu ilişkiyi artık bildirmiyor') : null))));
  }

  function loadTimeline(id, box, before = null) {
    return ciTimeline(id, before).then((doc) => {
      if (!box.isConnected) return;
      if (!before) clear(box);
      box.querySelector('#ciHistoryMore')?.remove();
      let list = box.querySelector('ul.timeline');
      if (!list) { list = h('ul', { class: 'mini timeline', 'aria-label': 'Değişiklikler, yeniden eskiye' }); box.append(list); }
      const items = doc.moments.map(momentItem);
      list.append(...items);
      if (!list.children.length) box.append(h('p', { class: 'muted small' }, 'Kayıt yok.'));
      if (doc.next_cursor) {
        box.append(h('button', { class: 'btn ghost small', type: 'button', id: 'ciHistoryMore',
          onclick: () => loadTimeline(id, box, doc.next_cursor) }, 'Daha eski değişiklikler'));
      }
      if (before && items.length) items[0].focus({ preventScroll: false });
    }).catch(() => { if (box.isConnected) box.append(h('p', { class: 'muted small' }, 'Zaman çizelgesi yüklenemedi.')); });
  }

  // Geçmiş bir an (F1-19): öznitelikler ve ilişkiler o ana geri sarılır; bugünle farkı
  const relList = (items, id) => h('ul', { class: 'mini', id }, items.map((x) => h('li', {}, `${x.phrase} `,
    h('a', { href: `#/ci/${x.other}` }, x.other_name), h('span', { class: 'cell-sub' }, x.other_class_label))));

  function asOfView(c, d) {
    if (!d.existed) {
      return [h('p', { class: 'notice', role: 'note', id: 'ciAsOfNone' },
        `Bu CI ${formatDateTime(d.at)} anında henüz yoktu (oluşturulma: ${formatDateTime(d.created_on)}).`)];
    }
    const changed = d.attributes.filter((a) => a.changed);
    const r = d.relations;
    return [
      h('p', { class: 'small', id: 'ciAsOfSummary' }, h('strong', {}, `${formatDateTime(d.at)} anında: `),
        changed.length ? `${changed.length} öznitelik bugünkünden farklıydı` : 'öznitelikler bugünkü gibiydi',
        `; ${r.then.length} ilişkisi vardı.`, d.class !== c.class ? ` Sınıfı ${d.class_label} idi.` : ''),
      changed.length ? h('ul', { class: 'mini', id: 'ciAsOfChanged', 'aria-label': 'O anda farklı olan öznitelikler' },
        changed.map((a) => h('li', {}, h('strong', {}, a.label), ': ', a.then, h('span', { class: 'cell-sub' }, `Bugün: ${a.now}`)))) : null,
      r.removed_since.length ? h('p', { class: 'small' }, h('strong', {}, 'O anda vardı, bugün yok')) : null,
      r.removed_since.length ? relList(r.removed_since, 'ciAsOfRemoved') : null,
      r.added_since.length ? h('p', { class: 'small' }, h('strong', {}, 'Sonradan açılan ilişkiler')) : null,
      r.added_since.length ? relList(r.added_since, 'ciAsOfAdded') : null,
      d.hidden_relations ? h('p', { class: 'muted small' }, `Okuma izniniz olmayan ${d.hidden_relations} ilişki gösterilmiyor.`) : null,
    ].filter(Boolean);
  }

  function asOfSection(c) {
    const box = h('div', { id: 'ciAsOf', 'aria-live': 'polite' });
    const input = h('input', { type: 'datetime-local', id: 'ciAsOfAt', step: '1', 'aria-label': 'Geçmiş an', max: toIstanbulInput(new Date()),
      value: toIstanbulInput(new Date(Date.now() - 7 * 864e5)) });
    const map = h('a', { class: 'btn ghost small', id: 'ciAsOfMap', href: `#/iliski-haritasi/${c.sys_id}` }, 'O andaki ilişki haritası');
    const sync = () => {
      const iso = fromIstanbulInput(input.value);
      map.setAttribute('href', `#/iliski-haritasi/${c.sys_id}${iso ? `@${iso}` : ''}`);
    };
    const show = async () => {
      if (!input.value) {
        input.setAttribute('aria-invalid', 'true');
        toast('Bir an seçin.');
        input.focus();
        return;
      }
      input.removeAttribute('aria-invalid');
      clear(box).append(h('p', { class: 'muted small' }, 'Yükleniyor…'));
      try {
        const d = await ciAsOf(c.sys_id, fromIstanbulInput(input.value));
        if (box.isConnected) clear(box).append(...asOfView(c, d));
      } catch (err) {
        clear(box).append(h('p', { class: 'muted small' }, `Yüklenemedi: ${err.message}`));
      }
    };
    input.addEventListener('input', sync);
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); show(); } });
    sync();
    return section('Geçmiş bir an', h('p', { class: 'muted small' },
      'Seçtiğiniz andaki öznitelikler ve ilişkiler, değişim geçmişi bugünden geriye sarılarak gösterilir.'),
    h('div', { class: 'asof-form' }, input,
      h('button', { class: 'btn secondary small', type: 'button', id: 'ciAsOfShow', onclick: show }, 'O andaki hâli'), map), box);
  }

  // Bağımlı CI kimliği (U7.2, ADR-0029): ana CI'a bağlı CI'lar (ağ adaptörü, uygulama…) ve bağımlının ana CI'ı
  function dependentSection(c) {
    const deps = c.dependents || [];
    const parents = c.depends_on || [];
    if (!deps.length && !parents.length) return null;
    const link = (x) => h('a', { href: `#/ci/${x.sys_id}` }, x.name);
    return section('Bağımlı CI\'lar', h('div', { id: 'ciDependents' },
      parents.length ? h('p', { class: 'small' }, 'Bu CI ', parents.map((p, i) => h('span', {}, i ? ', ' : '', link(p), ` (${p.class_label})`)),
        ' bağlamında tanımlanır: kimliği ana CI ile kendi yerel anahtarıdır.') : null,
      deps.length ? h('ul', { class: 'mini', 'aria-label': 'Bu CI\'a bağlı CI\'lar' }, deps.map((d) => h('li', {},
        h('div', { class: 'pills' }, pill(d.class_label, 'neutral')), link(d),
        d.key ? h('div', { class: 'cell-sub mono' }, d.key) : null))) : null));
  }

  // Kalite bulguları (U8.6, ADR-0037): CI'ın açık ve istisnalı bulguları, nedeni ve görevi
  function findingSection(list) {
    if (!list?.length) return null;
    return section('Kalite bulguları', h('ul', { class: 'mini', id: 'ciFindings' }, list.map((f) => h('li', {},
      h('div', { class: 'pills' }, pill(f.state === 'excepted' ? 'İstisna' : 'Açık', f.state === 'excepted' ? 'neutral' : 'warn'),
        pill(f.rule.severity_tr, f.rule.severity === 'low' ? 'neutral' : f.rule.severity === 'medium' ? 'warn' : 'bad')),
      h('a', { href: `#/saglik/bulgular/${f.sys_id}` }, f.rule.name),
      h('div', { class: 'cell-sub' }, [f.text, f.task ? `görev ${f.task.number}${f.task.assignee ? ` · ${f.task.assignee}` : ''}` : null].filter(Boolean).join(' · '))))));
  }

  // Eskime (U7.1, ADR-0028): açık öneri veya son karar; Veri sorumlusuna Emekliye ayır / Koru
  function stalenessSection(c, opener) {
    const s = c.staleness;
    if (mode !== 'api' || !s?.case) return null;
    const k = s.case;
    const decide = (action) => openDecisionDialog({ name: c.name, action, onSend: async (note) => {
      try {
        await sendDecision(k.id, action, note);
        await open(c.sys_id, opener);
        document.getElementById('ciStaleness')?.focus();
      } catch (err) {
        toast(`Kaydedilemedi: ${err.message}`);
      }
    } });
    return section('Eskime', h('div', { id: 'ciStaleness', tabindex: '-1' },
      h('div', { class: 'pills' }, pill(k.status === 'open' ? k.stage_label : k.status_label, k.status === 'open' ? STAGE_TONE[k.stage] : 'neutral'),
        k.pending ? pill('Karar uygulanıyor', 'neutral') : null, k.reappeared_at ? pill('Emekliye ayrıldıktan sonra yeniden görüldü', 'warn') : null),
      k.status === 'open' ? h('p', { class: 'small' }, `Gözlemci kaynakların hiçbiri ${k.days_since} gündür görmüyor (son görülme ${formatDateTime(k.last_seen)}). Kural: ${k.rule_label}, ${s.rule ? `${s.rule.stale_after_days} gün eskimiş, ${s.rule.candidate_after_days} gün aday` : ''}.`) : null,
      k.decided_by ? h('p', { class: 'small muted' }, `Karar: ${k.decided_by} · ${k.decision_note || ''}`) : null,
      observationList(k.observations),
      s.can_decide ? h('div', { class: 'link-actions' },
        s.can_retire ? h('button', { class: 'btn secondary small', type: 'button', id: 'ciRetire', onclick: () => decide('retire') }, 'Emekliye ayır…') : null,
        h('button', { class: 'btn ghost small', type: 'button', id: 'ciStaleDismiss', onclick: () => decide('dismiss') }, 'Koru…')) : null));
  }

  function reviewFor(c, opener) {
    if (mode !== 'api' || !c.change_control?.can_review) return null;
    const send = async (a, status, note) => {
      try {
        const res = await reviewAnomaly(a.sys_id, status, note);
        toast(`Karar kaydedildi: ${res.status_label}`);
        await open(c.sys_id, opener);
        const list = document.getElementById('ciAnomalies'); // panel yeniden çizildi: sonucu göster, odağı listeye al
        list?.scrollIntoView({ block: 'center' });
        list?.focus({ preventScroll: true });
      } catch (err) {
        toast(`Karar kaydedilemedi: ${err.message}`);
      }
    };
    return async (a, status, button) => {
      if (status !== 'false_positive') {
        // istek sürerken listedeki bütün karar düğmeleri kilitli (ardışık tıklama çakışmasın)
        button.closest('ul')?.querySelectorAll('button[data-review]').forEach((b) => { b.disabled = true; });
        await send(a, status, null);
        return;
      }
      const note = h('input', { type: 'text', id: 'reviewNote', maxlength: '2000', required: 'required',
        placeholder: 'Ör. değişiklik kaydı başka sistemde; CHG numarası…', 'aria-describedby': 'reviewHelp' });
      const confirm = h('button', { class: 'btn', type: 'button', id: 'reviewConfirm', onclick: async () => {
        if (!note.value.trim()) {
          note.setAttribute('aria-invalid', 'true');
          toast('Gerekçe zorunludur.');
          note.focus();
          return;
        }
        confirm.disabled = true;
        closeOverlay();
        await send(a, status, note.value.trim());
      } }, 'Yanlış alarm olarak işaretle');
      note.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); confirm.click(); } });
      dialog({
        title: 'Yanlış alarm',
        body: [
          h('p', {}, `"${c.name}" için açılan yetkisiz değişiklik kaydı yanlış alarm olarak işaretlenir ve GRC kontrolü CMDB-05'in kanıtından çıkar.`),
          h('label', { class: 'field', for: 'reviewNote' }, 'Gerekçe'), note,
          h('p', { class: 'muted small', id: 'reviewHelp' }, 'Kayıt silinmez; karar adınızla denetim izine yazılır ve istenirse yeniden açılabilir.'),
        ],
        footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), confirm],
        focus: note,
      });
    };
  }

  function linkItem(c, l) {
    let action = null;
    if (mode === 'api' && c.can_split && l.split) {
      if (l.split.allowed) {
        const btn = h('button', { class: 'btn secondary small', type: 'button', dataset: { split: l.id },
          'aria-label': `${l.data_source} kaydını bu CI'dan ayır`, onclick: () => confirmSplit(c, l, btn) }, 'Ayır…');
        action = btn;
      } else if (l.split.pending) {
        action = pill('Ayırma bekliyor', 'warn');
      } else if (l.split.reason) {
        action = h('span', { class: 'muted small' }, l.split.reason);
      }
    }
    return h('li', {},
      h('div', { class: 'pills' }, srcChip(l.data_source, l.role), pill(OUTCOME[l.outcome] || l.outcome, l.outcome === 'new' ? 'info' : 'good')),
      h('div', { class: 'small', title: l.summary }, l.summary),
      h('div', { class: 'cell-sub mono' }, `${l.source_key} · ${formatDateTime(l.observed_at)}`),
      action ? h('div', { class: 'link-actions' }, action) : null);
  }

  function splitItem(s) {
    const other = s.side === 'source' ? s.result_ci : s.ci_sys_id;
    return h('li', {},
      h('div', { class: 'pills' }, pill(s.status_label, SPLIT_TONE[s.status] || 'neutral'), srcChip(s.data_source, roleOf[s.data_source])),
      h('div', { class: 'small' }, `${s.source_key} · ${s.requested_by} · ${formatDateTime(s.requested_at)}`),
      s.note ? h('div', { class: 'small muted' }, s.note) : null,
      other ? h('div', { class: 'small' }, h('a', { href: `#/ci/${other}` }, s.side === 'source' ? 'Kaydın taşındığı CI' : 'Kaydın ayrıldığı CI')) : null);
  }

  function editItem(c, e) {
    const labels = Object.fromEntries(c.editable.map((f) => [f.attribute, f.label]));
    return h('li', {},
      h('div', { class: 'pills' }, pill(e.status_label, SPLIT_TONE[e.status] || 'neutral'), srcChip('Manual', 'Manual')),
      h('div', { class: 'small' }, e.attributes.map((a) => {
        const r = e.result?.[a];
        const label = labels[a] || a;
        if (!r) return label;
        return r.status === 'overridden' ? `${label}: yedek (${r.source} öncelikli)` : `${label}: ${EDIT_RESULT[r.status] || r.status}`;
      }).join(' · ')),
      e.status === 'rejected' && e.note ? h('div', { class: 'small muted' }, e.note) : null,
      h('div', { class: 'cell-sub' }, `${e.requested_by} · ${formatDateTime(e.requested_at)}`));
  }

  // Panel isteği sırası: kullanıcı paneli kapattıktan veya başka CI açıldıktan sonra yanıtlanan eski istek paneli
  // yeniden açmaz (bağlantı tıklaması + hash değişimi gibi ardışık açılışlarda yarış).
  let openSeq = 0;
  async function open(id, opener) {
    const seq = ++openSeq;
    const c = await ciDetail(id);
    if (!c || seq !== openSeq) return;
    selected = id;
    if (location.hash !== `#/ci/${id}`) history.replaceState(null, '', `#/ci/${id}`);
    const cov = [['Keşif', c.coverage.discovery], ['İzleme', c.coverage.monitoring], ['Uç nokta güvenliği', c.coverage.security], ['ITSM (ServiceNow)', c.coverage.itsm]];
    const historyBox = h('div', { id: 'ciHistory' }, h('p', { class: 'muted small' }, 'Yükleniyor…'));
    const impactBox = h('div', { id: 'ciImpact' }, h('p', { class: 'muted small' }, 'Yükleniyor…'));
    openDetail({
      kicker: `Konfigürasyon öğesi · ${c.class_label}`, title: c.name, opener,
      onClose: () => { openSeq += 1; selected = null; history.replaceState(null, '', '#/ci'); table?.refreshSelection(); },
      body: [
        mode === 'api' ? h('div', { class: 'panel-actions' },
          c.can_edit ? h('button', { class: 'btn secondary small', type: 'button', id: 'ciEdit',
            onclick: (ev) => openEditDialog(c, ev.currentTarget, () => open(c.sys_id, opener)) }, 'Düzenle…') : null,
          h('a', { class: 'btn ghost small', id: 'ciMap', href: `#/iliski-haritasi/${c.sys_id}` }, 'İlişki haritası')) : null,
        h('div', { class: 'pills' }, pill(c.class_label, 'neutral'), c.used_for ? pill(c.used_for_label || c.used_for, c.used_for === 'Production' ? 'good' : 'neutral') : null,
          c.operational_status ? pill(c.operational_status, 'info') : null),
        h('div', { class: 'stat-strip', 'aria-label': 'Kaynak kapsamı' }, cov.map(([label, ok]) => h('div', { class: `stat ${ok ? 'good' : c.server || label === 'Keşif' ? 'bad' : ''}` },
          h('span', { class: 'stat-value' }, ok ? 'Var' : 'Yok'), h('span', { class: 'stat-label' }, label)))),
        c.rejected.length ? h('div', { class: 'notice warn' }, h('strong', {}, 'Yetkisiz kaynak denemesi. '),
          c.rejected.map((r) => `${r.attempts.map((x) => x.source).join(', ')} "${r.label}" alanına ${r.attempts.map((x) => x.display).join(', ')} yazmaya çalıştı; bu alan bu kaynağın rolüne kapalı, değer alınmadı.`).join(' ')) : null,
        section('Öznitelikler ve kaynakları', h('p', { class: 'muted small' }, 'Bir satırı açın: değeri veren kaynak, uygulanan kural ve ezilen değerler. Turuncu nokta: kaynaklar farklı değer bildiriyor.'),
          h('div', { class: 'attrs' }, c.attributes.map(attrRow))),
        c.security_posture ? postureSection(c.security_posture) : null,
        changeSection(c.change_control, reviewFor(c, opener)),
        findingSection(c.findings),
        mode === 'api' ? section('Etki', impactBox) : null,
        stalenessSection(c, opener),
        attestationSection(c, opener),
        relationSection(c, opener),
        dependentSection(c),
        section('Kaynak kayıtları ve kimlik kararı', h('ul', { class: 'mini', id: 'ciLinks' }, c.links.map((l) => linkItem(c, l))),
          mode === 'api' && c.can_split && c.links.length > 1
            ? h('p', { class: 'muted small' }, 'Yanlış birleştirilmiş bir kaynak kaydını "Ayır" ile bu CI\'dan ayırabilirsiniz.') : null),
        c.edits?.length ? section('Elle düzenlemeler', h('ul', { class: 'mini', id: 'ciEdits' }, c.edits.map((e) => editItem(c, e)))) : null,
        c.splits?.length ? section('Ayırma geçmişi', h('ul', { class: 'mini', id: 'ciSplits' }, c.splits.map(splitItem))) : null,
        mode === 'api' ? asOfSection(c) : null,
        mode === 'api' ? section('Zaman çizelgesi', historyBox) : null,
        section('Kimlik', kv([['sys_id', h('span', { class: 'mono' }, c.sys_id)], ['Sınıf (sys_class_name)', h('span', { class: 'mono' }, c.class)],
          ['Destek grubu', c.support_group], ['Sahip', c.owned_by || h('span', { class: 'pill warn' }, 'Atanmamış')]])),
      ],
    });
    table?.refreshSelection();
    if (mode === 'api') {
      // Etki ve değişim geçmişi arka planda yüklenir: panel hemen kullanılabilir, açılış bunları beklemez
      ciImpact(id).then((doc) => { if (impactBox.isConnected) clear(impactBox).append(...impactView(doc)); })
        .catch(() => clear(impactBox).append(h('p', { class: 'muted small' }, 'Etki analizi yüklenemedi.')));
      loadTimeline(id, historyBox);
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
