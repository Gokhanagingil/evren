// Sistem Durumu → Uyarılar ve Bildirimler (U8.9, ADR-0040).
// Uyarılar: görev çalışanının kurallarla açtığı platform uyarıları (etkin / son 30 gün), gerekçeli onay, kurallar.
// Bildirimler: e-posta ve Teams kanallarının durumu, rol ve grup yönlendirmeleri, sınama bildirimi, teslim sorunları.
import { h, append, clear, dataTable, formatDateTime, pill, section, tabs } from '../lib/dom.js';
import { closeOverlay, dialog, toast } from '../lib/shell.js';
import { notifyRoutePut, notifyRoutes, notifyTest, opsAlertAck, opsAlerts } from '../lib/data.js';
import { radioGroup, wfield } from '../lib/wizard.js';

const SEV_TONE = { critical: 'bad', warning: 'warn', info: 'neutral' };
const MODE_TR = { smtp: 'Açık (SMTP)', 'açık': 'Açık', 'simülatör': 'Simülatör (geliştirme)', 'kapalı': 'Kapalı', bilinmiyor: 'Bilinmiyor' };
const MIN_NOTE = 10;

function stat(label, value, tone = '') {
  return h('div', { class: `stat ${tone}`.trim() }, h('span', { class: 'stat-value' }, String(value)), h('span', { class: 'stat-label' }, label));
}

function problem(box, message) {
  clear(box).append(h('div', { class: 'notice bad', role: 'alert' }, message));
}

// ---------------------------------------------------------------------------------------------- uyarılar
function ackDialog(alert, onDone) {
  const note = h('textarea', { id: 'ackNote', rows: '3', maxlength: '1000', placeholder: 'Ör. Kafka düğümü yeniden başlatılıyor; Ayşe ilgileniyor, OLAY-221' });
  const error = h('div', { id: 'ackError' });
  const save = h('button', { class: 'btn', type: 'button', id: 'ackSave' }, 'Onayla');
  note.addEventListener('input', () => { note.removeAttribute('aria-invalid'); clear(error); });
  save.addEventListener('click', async () => {
    if (note.value.trim().length < MIN_NOTE) {
      note.setAttribute('aria-invalid', 'true');
      problem(error, `Onay notu en az ${MIN_NOTE} karakter olmalı: ne yapılıyor, kim ilgileniyor.`);
      note.focus();
      return;
    }
    save.disabled = true;
    try {
      const r = await opsAlertAck(alert.sys_id, note.value.trim());
      closeOverlay();
      toast(r.message);
      await onDone();
    } catch (err) {
      save.disabled = false;
      problem(error, err.message);
    }
  });
  dialog({ title: `Uyarıyı onayla: ${alert.title}`,
    body: [h('p', {}, 'Onay uyarıyı kapatmaz: ekibin durumu bildiğini ve ilgilendiğini kaydeder. Koşul kalkınca görev çalışanı uyarıyı kendiliğinden çözer.'),
      h('label', { class: 'field', for: 'ackNote' }, 'Not'), note, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save], focus: note });
}

function alertItem(a, canAck, reload) {
  const when = a.state === 'firing' ? `${formatDateTime(a.fired_at)} tarihinden beri` : `${formatDateTime(a.fired_at)} – ${formatDateTime(a.resolved_at)}`;
  return h('li', { class: `alert-item sev-${a.severity}`, id: `alert-${a.rule}${a.subject ? `-${a.subject}` : ''}`.replace(/[^A-Za-z0-9_-]/g, '_') },
    h('div', {}, h('div', { class: 'alert-title' }, a.title), h('div', { class: 'muted small' }, a.detail)),
    h('div', { class: 'pills' }, pill(a.severity_label, SEV_TONE[a.severity]), pill(a.state_label, a.state === 'firing' ? 'bad' : 'good'),
      a.ack ? pill('Onaylandı', 'neutral') : null),
    h('div', { class: 'alert-meta small' },
      h('span', { class: 'muted' }, when),
      a.advice ? h('div', {}, h('strong', {}, 'Ne yapılmalı: '), a.advice) : null,
      a.ack ? h('div', { class: 'muted' }, `Onay: ${a.ack.by} · ${formatDateTime(a.ack.at)} · ${a.ack.note}`) : null,
      canAck && a.state === 'firing' && !a.ack ? h('button', { class: 'btn secondary small', type: 'button', onclick: () => ackDialog(a, reload) }, 'Onayla') : null));
}

export async function alertsSection(container) {
  let view = 'active';
  const tabBox = h('div', {});
  const body = h('div', { id: 'alertsBody' });
  const rulesBox = h('details', { class: 'small', id: 'alertRules' });
  const wrap = h('section', { class: 'sys-section', id: 'sysAlerts', 'aria-labelledby': 'alertsH', tabindex: '-1' },
    h('div', { class: 'section-head' }, h('h2', { id: 'alertsH', class: 'sys-group-title' }, 'Uyarılar')), tabBox, body, rulesBox);
  clear(container).append(wrap);

  async function load() {
    let res;
    try {
      res = await opsAlerts(view);
    } catch (err) {
      clear(body).append(err.status === 403 ? h('p', { class: 'muted small' }, 'Uyarıları Platform operatörü, Veri sorumlusu ve Denetçi görür.')
        : h('div', { class: 'notice bad' }, `Uyarılar yüklenemedi: ${err.message}`));
      return;
    }
    clear(tabBox).append(tabs([{ id: 'active', label: 'Etkin', count: view === 'active' ? res.items.length : undefined },
      { id: 'history', label: 'Son 30 gün' }], view, (v) => { view = v; load(); }, 'Uyarı görünümleri'));
    const live = res.channels.live ? null : h('p', { class: 'notice warn', id: 'alertsWatcher' },
      h('strong', {}, 'Uyarı izleyicisi çalışmıyor. '), 'Uyarıları görev çalışanı değerlendirir; çalışan yanıt vermediği için yeni uyarı açılmaz. Kurum izleme sistemi /metrics üzerinden evren_service_up göstergesini izlemeli.');
    append(clear(body), [live, res.items.length
      ? h('ul', { class: 'alert-list', id: 'alertList' }, res.items.map((a) => alertItem(a, res.can_ack, load)))
      : h('p', { class: 'notice success', id: 'alertsNone' }, view === 'active' ? 'Etkin uyarı yok: kurallardaki koşulların hiçbiri şu an doğru değil.' : 'Son 30 günde ateşlenen uyarı yok.')]);
    clear(rulesBox).append(h('summary', {}, `Uyarı kuralları (${res.rules.length})`),
      h('p', { class: 'muted' }, 'Kurallar metadata/alerts.yaml dosyasındadır; koşul belirtilen süre boyunca sürerse uyarı ateşlenir ve alıcı rollere bildirim gider.'),
      dataTable({ label: 'Uyarı kuralları', idOf: (r) => r.id, rows: res.rules, columns: [
        { key: 'title', label: 'Kural', render: (r) => h('div', {}, r.title.replace('{subject}', '…').replace('{value}', '…'), h('div', { class: 'cell-sub' }, r.signal)) },
        { key: 'threshold', label: 'Eşik', render: (r) => (r.threshold === null ? '—' : `${r.threshold} ${r.unit}`) },
        { key: 'for_minutes', label: 'Süre', render: (r) => (r.for_minutes ? `${r.for_minutes} dk` : 'hemen') },
        { key: 'severity', label: 'Önem', render: (r) => pill(r.severity_label, SEV_TONE[r.severity]) },
        { key: 'audience', label: 'Alıcı roller', sortable: false, render: (r) => r.audience.join(', ') },
      ] }));
  }
  await load();
  return { reload: load, element: wrap };
}

// ---------------------------------------------------------------------------------------------- bildirimler
function routeDialog(data, onDone) {
  // Varsayılan alıcı Platform operatörü: platform uyarılarının asıl alıcısı
  const preferred = data.roles.some((r) => r.value === 'evren_operator') ? 'evren_operator' : data.roles[0]?.value || '';
  const st = { audience_kind: 'role', audience: preferred, channel: 'email', target: '', min_severity: 'warning' };
  const audSel = h('select', { id: 'routeAudience' });
  const fillAud = () => {
    const opts = st.audience_kind === 'role' ? data.roles : data.groups;
    clear(audSel).append(...opts.map((o) => h('option', { value: o.value, selected: o.value === st.audience }, o.label)));
    st.audience = audSel.value;
  };
  audSel.addEventListener('change', () => { st.audience = audSel.value; });
  const target = h('input', { type: 'email', id: 'routeTarget', maxlength: '254', placeholder: 'platform-ekibi@kurum.local' });
  const teamsSel = h('select', { id: 'routeTeams' }, data.channels.teams_targets.map((t) => h('option', { value: t }, t)));
  const targetBox = h('div', {});
  const fillTarget = () => {
    clear(targetBox).append(st.channel === 'email'
      ? wfield('E-posta adresi', target, 'Ekip veya dağıtım listesi adresi.')
      : (data.channels.teams_targets.length
        ? wfield('Teams hedefi', teamsSel, 'Hedef adları görev çalışanının sır dosyasındadır; adresler burada görünmez.')
        : h('p', { class: 'notice warn' }, 'Görev çalışanında tanımlı Teams hedefi yok. EVREN_TEAMS_WEBHOOKS_FILE dosyasına ad=adres satırı eklenmeli.')));
  };
  const sevSel = h('select', { id: 'routeSeverity' }, [['info', 'Bütün bildirimler'], ['warning', 'Uyarı ve kritik'], ['critical', 'Yalnızca kritik']]
    .map(([v, l]) => h('option', { value: v, selected: v === st.min_severity }, l)));
  const error = h('div', { id: 'routeError' });
  const save = h('button', { class: 'btn', type: 'button', id: 'routeSave' }, 'Kaydet');
  save.addEventListener('click', async () => {
    const tgt = st.channel === 'email' ? target.value.trim() : teamsSel.value;
    if (!tgt) { problem(error, st.channel === 'email' ? 'E-posta adresini yazın.' : 'Teams hedefi seçin.'); (st.channel === 'email' ? target : teamsSel).focus(); return; }
    save.disabled = true;
    try {
      const r = await notifyRoutePut({ audience_kind: st.audience_kind, audience: st.audience, channel: st.channel, target: tgt, min_severity: sevSel.value, active: true });
      closeOverlay();
      toast(r.message);
      await onDone();
    } catch (err) {
      save.disabled = false;
      problem(error, err.message);
    }
  });
  fillAud();
  fillTarget();
  dialog({ title: 'Bildirim yönlendirmesi ekle', wide: true,
    body: [h('p', {}, 'Bir role veya gruba gelen bildirimler uygulama içinde her zaman görünür. Yönlendirme, aynı bildirimin e-posta veya Teams ile de gönderilmesini sağlar. Kişiye gelen bildirimler kişinin kendi adresine gider (görev ServiceNow\'a teslim edildiyse ServiceNow bildirir).'),
      h('p', { class: 'field' }, 'Alıcı türü'),
      radioGroup('routeKind', 'Alıcı türü', [{ value: 'role', label: 'Rol', sub: 'Ör. Platform operatörü: platform uyarıları' }, { value: 'group', label: 'Grup', sub: 'Destek grubuna atanan görevler' }],
        st.audience_kind, (v) => { st.audience_kind = v; fillAud(); }),
      wfield('Alıcı', audSel),
      h('p', { class: 'field' }, 'Kanal'),
      radioGroup('routeChannel', 'Kanal', [{ value: 'email', label: 'E-posta' }, { value: 'teams', label: 'Teams', sub: 'İş akışı gelen web kancası' }],
        st.channel, (v) => { st.channel = v; fillTarget(); }),
      targetBox,
      wfield('Hangi bildirimler', sevSel, 'Bilgi düzeyi (ör. görev atandı) çok sayıda olabilir; ekip kanalları için "Uyarı ve kritik" önerilir.'),
      error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save], focus: audSel });
}

function testDialog(data) {
  let kind = 'role';
  const audSel = h('select', { id: 'testAudience' });
  const fill = () => clear(audSel).append(...(kind === 'role' ? data.roles : data.groups).map((o) => h('option', { value: o.value }, o.label)));
  fill();
  const error = h('div', {});
  const send = h('button', { class: 'btn', type: 'button', id: 'testSend' }, 'Gönder');
  send.addEventListener('click', async () => {
    send.disabled = true;
    try {
      const r = await notifyTest({ audience_kind: kind, audience: audSel.value });
      closeOverlay();
      toast(r.message);
    } catch (err) {
      send.disabled = false;
      problem(error, err.message);
    }
  });
  dialog({ title: 'Sınama bildirimi gönder',
    body: [h('p', {}, 'Seçilen alıcıya "Sınama bildirimi" gider: uygulama içinde ve tanımlı yönlendirmelerle e-posta ve Teams\'te görünmeli.'),
      radioGroup('testKind', 'Alıcı türü', [{ value: 'role', label: 'Rol' }, { value: 'group', label: 'Grup' }], kind, (v) => { kind = v; fill(); }),
      wfield('Alıcı', audSel), error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send], focus: audSel });
}

export async function notifySection(container) {
  const wrap = h('section', { class: 'sys-section', id: 'sysNotify', 'aria-labelledby': 'notifyH', tabindex: '-1' });
  clear(container).append(wrap);

  async function load() {
    let d;
    try {
      d = await notifyRoutes();
    } catch (err) {
      clear(wrap).append(h('h2', { id: 'notifyH', class: 'sys-group-title' }, 'Bildirimler'),
        h('p', { class: 'muted small' }, err.status === 403 ? 'Bildirim yönlendirmesini Platform operatörü, Veri sorumlusu ve Denetçi görür.' : `Yüklenemedi: ${err.message}`));
      return;
    }
    const ch = d.channels;
    const actions = d.can_admin ? h('div', { class: 'actions' },
      h('button', { class: 'btn secondary small', type: 'button', id: 'routeNew', onclick: () => routeDialog(d, load) }, 'Yönlendirme ekle'),
      h('button', { class: 'btn secondary small', type: 'button', id: 'testOpen', onclick: () => testDialog(d) }, 'Sınama bildirimi')) : null;
    const toggle = (r) => h('button', { class: 'btn secondary small', type: 'button', onclick: async () => {
      try {
        const res = await notifyRoutePut({ audience_kind: r.audience_kind, audience: r.audience, channel: r.channel, target: r.target, min_severity: r.min_severity, active: !r.active });
        toast(res.message);
        await load();
      } catch (err) { toast(err.message); }
    } }, r.active ? 'Kapat' : 'Aç');
    append(clear(wrap), [
      h('div', { class: 'section-head' }, h('h2', { id: 'notifyH', class: 'sys-group-title' }, 'Bildirimler'), actions),
      h('p', { class: 'muted small' }, 'Bildirimler uygulama içinde (zil) her zaman görünür; rol ve gruplar için e-posta veya Teams yönlendirmesi eklenebilir.'),
      h('div', { class: 'stat-strip', id: 'notifyChannels' },
        stat('E-posta', MODE_TR[ch.email] || ch.email, ch.email === 'kapalı' ? '' : 'good'),
        stat('Teams', `${MODE_TR[ch.teams] || ch.teams}${ch.teams_targets.length ? ` · ${ch.teams_targets.join(', ')}` : ''}`, ch.teams === 'kapalı' ? '' : 'good'),
        stat('Gönderilecek', ch.pending ?? '—', ch.pending ? 'warn' : ''),
        stat('Gönderilemeyen (24 saat)', ch.failed_24h ?? '—', ch.failed_24h ? 'bad' : '')),
      section('Yönlendirmeler', dataTable({
        label: 'Bildirim yönlendirmeleri', idOf: (r) => `${r.audience_kind}:${r.audience}:${r.channel}:${r.target}`, rows: d.routes,
        empty: 'Yönlendirme yok: bildirimler yalnızca uygulama içinde ve kişilerin kendi e-postasına gider.',
        columns: [
          { key: 'audience_label', label: 'Alıcı', render: (r) => h('div', {}, r.audience_label, h('div', { class: 'cell-sub' }, r.audience_kind === 'role' ? 'Rol' : 'Grup')) },
          { key: 'channel', label: 'Kanal', render: (r) => (r.channel === 'email' ? 'E-posta' : 'Teams') },
          { key: 'target', label: 'Hedef', render: (r) => h('span', { class: 'mono' }, r.target) },
          { key: 'min_severity', label: 'Hangi bildirimler', render: (r) => r.min_severity_label + (r.min_severity === 'info' ? ' ve üstü' : r.min_severity === 'warning' ? ' ve kritik' : '') },
          { key: 'active', label: 'Durum', render: (r) => h('div', { class: 'pills' }, pill(r.active ? 'Açık' : 'Kapalı', r.active ? 'good' : 'neutral'), d.can_admin ? toggle(r) : null) },
        ] })),
      d.problems.length ? section('Teslim sorunları', dataTable({
        label: 'Teslim sorunları', idOf: (p) => `${p.at}${p.target}`, rows: d.problems, columns: [
          { key: 'at', label: 'Zaman', render: (p) => formatDateTime(p.at) },
          { key: 'title', label: 'Bildirim' },
          { key: 'target', label: 'Hedef', render: (p) => `${p.channel === 'email' ? 'E-posta' : 'Teams'} · ${p.target}` },
          { key: 'error', label: 'Sorun', render: (p) => `${p.error || '—'} (${p.attempts} deneme)` },
        ] })) : null]);
  }
  await load();
  return { reload: load, element: wrap };
}

