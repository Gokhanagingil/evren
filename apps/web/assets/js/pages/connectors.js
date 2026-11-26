// Veri kaynağı ayrıntısında bağlayıcı yönetimi (U5.3b, ADR-0021): çalıştırıcının bildirdiği örnekler, "şimdi
// çalıştır", çalıştırma istekleri ve iptal, bağlayıcı kimliği verme/iptal. API bağlayıcıyı çalıştırmaz; istek yazar,
// ayrı çalıştırıcı servisi yürütür. Belirteç yalnızca verildiği anda bu pencerede görünür ve hiçbir yerde saklanmaz.
import { h, formatDateTime, formatNumber, pill, section } from '../lib/dom.js';
import { closeOverlay, dialog, toast } from '../lib/shell.js';
import { cancelRun, issueCredential, requestRun, revokeCredential } from '../lib/data.js';

export const MODE = { snapshot: 'Tam liste', delta: 'Değişenler' };
const CRED_STATE = { active: ['Etkin', 'good'], expired: ['Süresi doldu', 'warn'], revoked: ['İptal edildi', 'bad'] };
const REQ_TONE = { pending: 'neutral', running: '', succeeded: 'good', failed: 'bad', cancelled: 'warn', rejected: 'warn' };
const ORIGIN = { manual: 'Elle', schedule: 'Zamanlama' };
const ACTIVE = new Set(['pending', 'running']);
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$/;

/** Birden çok giriş alanlı formda Enter ile gönderim (tarayıcının örtük gönderimi gönder düğmesi ister). */
function formOf(submit, ...children) {
  return h('form', {
    onsubmit: (ev) => { ev.preventDefault(); submit(); },
    onkeydown: (ev) => { if (ev.key === 'Enter' && ev.target.tagName === 'INPUT') { ev.preventDefault(); submit(); } },
  }, ...children);
}

function errorNotice(box, err) {
  box.replaceChildren(h('div', { class: 'notice bad', role: 'alert' }, h('strong', {}, err.message)));
}

function instanceState(i) {
  if (!i.enabled) return pill('Devre dışı', 'neutral');
  return i.available ? pill('Hazır', 'good') : pill('Çalıştırıcı görülmüyor', 'warn');
}

function runDialog(x, inst, onChanged) {
  const errors = h('div', { 'aria-live': 'polite' });
  const mode = h('select', { id: 'runMode' }, h('option', { value: 'snapshot' }, 'Tam liste (eskime ve "kaynakta yok" tespiti)'),
    h('option', { value: 'delta' }, 'Değişenler'));
  const since = h('input', { type: 'datetime-local', id: 'runSince', 'aria-describedby': 'runSinceHint' });
  const sinceRow = h('div', { hidden: true }, h('label', { class: 'field', for: 'runSince' }, 'Şu andan beri değişenler'), since,
    h('div', { class: 'muted small', id: 'runSinceHint' }, 'Boş bırakılırsa son başarılı çalışmanın başlangıcından 10 dakika öncesi kullanılır; hiç başarılı çalışma yoksa tam liste çalışır.'));
  mode.addEventListener('change', () => { sinceRow.hidden = mode.value !== 'delta'; });
  const note = h('input', { type: 'text', id: 'runNote', maxlength: '500', placeholder: 'Ör. eşleme değişikliği sonrası doğrulama' });
  const go = h('button', { class: 'btn', type: 'button', id: 'runGo', onclick: submit }, 'Çalıştır');

  async function submit() {
    errors.replaceChildren();
    go.disabled = true;
    try {
      const res = await requestRun(x.name, { instance: inst.name, mode: mode.value,
        since: mode.value === 'delta' && since.value ? new Date(since.value).toISOString() : null, note: note.value.trim() });
      closeOverlay();
      toast(res.message);
      await onChanged();
    } catch (err) {
      go.disabled = false;
      errorNotice(errors, err);
    }
  }

  dialog({
    title: `Şimdi çalıştır · ${inst.name}`,
    body: [
      h('p', {}, 'İstek kaydedilir; bağlayıcıyı ', h('strong', {}, 'çalıştırıcı servisi'), ' yürütür. Kaynak verisi alım geçidi üzerinden uzlaştırıcıya gider; golden record\'a yalnızca uzlaştırıcı yazar.'),
      errors,
      formOf(submit,
        h('label', { class: 'field', for: 'runMode' }, 'Mod'), mode, sinceRow,
        h('label', { class: 'field', for: 'runNote' }, 'Not'), note),
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), go],
    focus: mode,
  });
}

function instancesSection(x, can, onChanged) {
  const active = new Set((x.requests || []).filter((r) => ACTIVE.has(r.status)).map((r) => r.instance));
  if (!x.connectors.length) {
    return section('Bağlayıcı örnekleri', h('p', { class: 'small muted' },
      'Çalıştırıcı servisine bildirilmiş örnek yok. Örnekler çalıştırıcının yapılandırma dosyasında tanımlanır (EVREN_CONNECTORS_FILE); kaynak adresi ve sır yolları yalnızca orada durur.'));
  }
  const retired = x.lifecycle === 'retired';
  return section('Bağlayıcı örnekleri', h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Bağlayıcı örnekleri' },
    h('thead', {}, h('tr', {}, h('th', {}, 'Örnek'), h('th', {}, 'Durum'), h('th', {}, 'Zamanlama'), can.connector_admin ? h('th', {}, 'Eylem') : null)),
    h('tbody', {}, x.connectors.map((i) => {
      let action = null;
      if (can.connector_admin) {
        const why = retired ? 'Kaynak emekli' : !i.enabled ? 'Devre dışı' : !i.available ? 'Çalıştırıcı yok' : active.has(i.name) ? 'Çalışma sürüyor' : null;
        action = h('td', {}, why ? h('span', { class: 'cell-sub' }, why)
          : h('button', { class: 'btn small', type: 'button', dataset: { run: i.name }, onclick: () => runDialog(x, i, onChanged) }, 'Şimdi çalıştır'));
      }
      return h('tr', {},
        h('td', {}, h('span', { class: 'mono' }, i.name), h('span', { class: 'cell-sub' }, `${i.connector} · süre sınırı ${i.max_runtime_minutes} dk`)),
        h('td', {}, instanceState(i), h('span', { class: 'cell-sub' }, `Son bildirim ${formatDateTime(i.seen_at)}`)),
        h('td', {}, i.schedule ? [h('span', { class: 'mono' }, i.schedule),
          h('span', { class: 'cell-sub' }, `${MODE[i.schedule_mode]} · ${i.schedule_tz}${i.next_run_at ? ` · sonraki ${formatDateTime(i.next_run_at)}` : ''}`)]
          : h('span', { class: 'cell-sub' }, 'Zamanlama yok; yalnızca elle')),
        action);
    })))),
  h('p', { class: 'small muted' }, 'Kaçırılan zamanlama telafi edilmez; çalıştırıcı geri gelince bir sonraki zamanlama çalışır. Bir örnekte aynı anda tek çalışma olur.'));
}

function summaryText(r) {
  const s = r.summary || {};
  const parts = [];
  if (s.sent !== undefined) parts.push(`${formatNumber(s.sent)} gönderildi`);
  if (s.fetched !== undefined && s.fetched !== s.sent) parts.push(`${formatNumber(s.fetched)} çekildi`);
  if (s.errors) parts.push(`${formatNumber(s.errors)} hata`);
  if (s.duration_s !== undefined) parts.push(`${formatNumber(Math.round(s.duration_s))} sn`);
  if (s.effective_mode && s.effective_mode !== r.mode) parts.push(`${MODE[s.effective_mode]} olarak çalıştı`);
  return parts.join(' · ');
}

function requestsSection(x, can, onChanged) {
  if (!x.requests.length) return null;
  async function cancel(r, btn) {
    btn.disabled = true;
    try {
      toast((await cancelRun(r.id)).message);
      await onChanged();
    } catch (err) {
      btn.disabled = false;
      toast(err.message);
    }
  }
  return section('Çalıştırma istekleri', h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Çalıştırma istekleri' },
    h('thead', {}, h('tr', {}, h('th', {}, 'İstendi'), h('th', {}, 'Örnek'), h('th', {}, 'Mod'), h('th', {}, 'Durum'), h('th', {}, 'Sonuç'),
      can.connector_admin ? h('th', {}, 'Eylem') : null)),
    h('tbody', {}, x.requests.slice(0, 10).map((r) => h('tr', {},
      h('td', {}, formatDateTime(r.requested_at), h('span', { class: 'cell-sub' }, `${ORIGIN[r.origin]} · ${r.requested_by}`)),
      h('td', {}, h('span', { class: 'mono' }, r.instance)),
      h('td', {}, MODE[r.mode], r.since ? h('span', { class: 'cell-sub' }, `${formatDateTime(r.since)} sonrası`) : null),
      h('td', {}, pill(r.status_label, REQ_TONE[r.status]), r.cancel_requested && ACTIVE.has(r.status) ? h('span', { class: 'cell-sub' }, 'İptal istendi') : null),
      h('td', { class: 'small' }, summaryText(r) || '—', r.result_note ? h('span', { class: 'cell-sub' }, r.result_note) : null),
      can.connector_admin ? h('td', {}, ACTIVE.has(r.status) && !r.cancel_requested
        ? h('button', { class: 'btn secondary small', type: 'button', dataset: { cancel: r.id }, onclick: (ev) => cancel(r, ev.currentTarget) }, 'İptal et')
        : null) : null))))),
  h('div', { class: 'row-actions' }, h('button', { class: 'btn ghost small', type: 'button', id: 'requestsRefresh', onclick: onChanged }, 'Yenile')));
}

function tokenDialog(res, onChanged) {
  const field = h('input', { type: 'text', id: 'tokenValue', readonly: true, value: res.token, class: 'mono', 'aria-describedby': 'tokenWarn',
    autocomplete: 'off', spellcheck: 'false' });
  const copy = h('button', { class: 'btn secondary', type: 'button', id: 'tokenCopy', onclick: async () => {
    try { await navigator.clipboard.writeText(field.value); toast('Belirteç panoya kopyalandı.'); } catch { field.select(); toast('Kopyalanamadı; seçili metni kopyalayın.'); }
  } }, 'Kopyala');
  const done = h('button', { class: 'btn', type: 'button', id: 'tokenDone', onclick: closeOverlay }, 'Sır kasasına koydum, kapat');
  dialog({
    title: `Bağlayıcı kimliği verildi · ${res.name}`,
    body: [
      h('p', { class: 'notice warn', id: 'tokenWarn' }, h('strong', {}, 'Belirteç bir daha gösterilmez. '),
        'Şimdi kurum sır kasasına koyun ve çalıştırıcıdaki gateway_token_file dosyasına bağlayın. Günlüğe, bilete veya sohbete yazmayın; kaybedilirse kimliği iptal edip yenisini verin.'),
      h('label', { class: 'field', for: 'tokenValue' }, 'Belirteç'), field,
      h('p', { class: 'small muted' }, `${res.data_source} · son dört karakter …${res.token_hint} · geçerlilik sonu ${formatDateTime(res.expires_at)} · dakikada ${formatNumber(res.per_minute)} kayıt`),
    ],
    footer: [copy, done],
    focus: copy,
    // Esc, ✕ veya düğme: belirteç alandan silinir, pencere DOM'dan kalkar, liste yenilenir
    onClose: () => { field.value = ''; onChanged(); },
  });
}

function issueDialog(x, onChanged) {
  const errors = h('div', { 'aria-live': 'polite' });
  const name = h('input', { type: 'text', id: 'credName', maxlength: '64', placeholder: `${x.name.toLocaleLowerCase('tr')}-dc1`,
    'aria-describedby': 'credNameHint', autocomplete: 'off' });
  const days = h('input', { type: 'number', id: 'credDays', min: '1', max: '730', step: '1', value: '365' });
  const rate = h('input', { type: 'number', id: 'credRate', min: '1', max: '10000000', step: '1', value: '60000' });
  const go = h('button', { class: 'btn', type: 'button', id: 'credGo', onclick: submit }, 'Kimlik ver');

  async function submit() {
    errors.replaceChildren();
    if (!NAME_RE.test(name.value.trim())) {
      errors.append(h('div', { class: 'notice bad', role: 'alert' }, 'Ad harf veya rakamla başlamalı; 2–64 karakter: A-Z a-z 0-9 _ . -'));
      name.focus();
      return;
    }
    go.disabled = true;
    try {
      const res = await issueCredential(x.name, { name: name.value.trim(), expiresDays: Number(days.value), perMinute: Number(rate.value) });
      closeOverlay();
      if (res.token) tokenDialog(res, onChanged);
      else { toast(res.message); await onChanged(); }
    } catch (err) {
      go.disabled = false;
      errorNotice(errors, err);
    }
  }

  dialog({
    title: `Bağlayıcı kimliği ver · ${x.label_tr}`,
    body: [
      h('p', {}, 'Kimlik alım geçidine yalnızca ', h('strong', {}, x.name), ' veri kaynağı adına kayıt gönderme yetkisi verir. Belirteç yalnızca bir sonraki pencerede, bir kez gösterilir; Evren yalnızca özetini saklar.'),
      errors,
      formOf(submit,
        h('label', { class: 'field', for: 'credName' }, 'Ad'), name,
        h('div', { class: 'muted small', id: 'credNameHint' }, 'Örnek adıyla aynı olması önerilir. İptal edilen ad yeniden kullanılamaz.'),
        h('label', { class: 'field', for: 'credDays' }, 'Geçerlilik (gün, 1–730)'), days,
        h('label', { class: 'field', for: 'credRate' }, 'Dakikalık kayıt sınırı'), rate),
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), go],
    focus: name,
  });
}

function revokeDialog(c, onChanged) {
  const errors = h('div', { 'aria-live': 'polite' });
  const go = h('button', { class: 'btn danger', type: 'button', id: 'revokeGo', onclick: async () => {
    go.disabled = true;
    try {
      const res = await revokeCredential(c.name);
      closeOverlay();
      toast(res.message);
      await onChanged();
    } catch (err) {
      go.disabled = false;
      errorNotice(errors, err);
    }
  } }, 'İptal et');
  dialog({
    title: `Kimliği iptal et · ${c.name}`,
    body: [h('p', {}, 'Alım geçidi bu belirteci hemen reddeder; bu kimliği kullanan bağlayıcı kayıt gönderemez. İşlem geri alınamaz; gerekirse yeni adla kimlik verin.'), errors],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), go],
    focus: go,
  });
}

function credentialsSection(x, can, onChanged) {
  const issue = can.connector_admin && x.connector && x.lifecycle !== 'retired'
    ? h('button', { class: 'btn small', type: 'button', id: 'credIssue', onclick: () => issueDialog(x, onChanged) }, 'Kimlik ver') : null;
  const list = x.credentials.length ? h('div', { class: 'tablewrap' }, h('table', { 'aria-label': 'Bağlayıcı kimlikleri' },
    h('thead', {}, h('tr', {}, h('th', {}, 'Ad'), h('th', {}, 'Durum'), h('th', {}, 'Son kullanım'), h('th', {}, 'Geçerlilik sonu'),
      h('th', {}, 'Veren'), can.connector_admin ? h('th', {}, 'Eylem') : null)),
    h('tbody', {}, x.credentials.map((c) => h('tr', {}, h('td', {}, h('span', { class: 'mono' }, c.name),
      h('span', { class: 'cell-sub' }, `Belirteç …${c.token_hint} · dakikada ${formatNumber(c.per_minute)} kayıt`)),
      h('td', {}, pill(...CRED_STATE[c.state])), h('td', {}, c.last_used_at ? formatDateTime(c.last_used_at) : 'Kullanılmadı'),
      h('td', {}, formatDateTime(c.expires_at)), h('td', {}, c.revoked_by ? `${c.created_by} · iptal: ${c.revoked_by}` : c.created_by),
      can.connector_admin ? h('td', {}, c.state !== 'revoked'
        ? h('button', { class: 'btn secondary small', type: 'button', dataset: { revoke: c.name }, onclick: () => revokeDialog(c, onChanged) }, 'İptal et')
        : null) : null)))))
    : h('p', { class: 'small muted' }, 'Bu kaynağa verilmiş bağlayıcı kimliği yok.');
  return section('Bağlayıcı kimlikleri', list,
    h('p', { class: 'small muted' }, 'Belirteç yalnızca verildiği anda bir kez gösterilir; burada son dört karakteri görünür.'),
    issue ? h('div', { class: 'row-actions' }, issue) : null);
}

/** Kaynak ayrıntısının bağlayıcı bölümleri; ``connector_read`` yoksa (alanlar null) hiçbiri gösterilmez. */
export function connectorSections(x, can, onChanged) {
  if (!x.credentials && !x.connectors) return [];
  return [
    x.connectors ? instancesSection(x, can, onChanged) : null,
    x.requests ? requestsSection(x, can, onChanged) : null,
    x.credentials ? credentialsSection(x, can, onChanged) : null,
  ];
}
