// Kullanıcılar ve roller (U8.3, ADR-0034, F1-10): kişiler, gruplar, rol–yetki matrisi ve yetki geçmişi tek sayfada.
// Rol verme ve geri alma gerekçelidir; yetkili roller süreli verilir; kimse kendine rol veremez; görevler ayrılığı
// API'de denetlenir, arayüz çatışmayı önceden söyler. Atamalar her istekte okunur: verilen rol hemen geçerli olur.
import { h, append, clear, dataTable, formatDate, formatDateTime, fromIstanbulInput, kv, pill, section, tabs } from '../lib/dom.js';
import { closeDetail, closeOverlay, dialog, openDetail, setHeader, toast } from '../lib/shell.js';
import {
  backend, iamBindGroup, iamCreatePerson, iamEvents, iamGrant, iamGroups, iamPeople, iamPerson, iamRevoke, iamRoles,
  iamUpdatePerson,
} from '../lib/data.js';

const SOURCE_TR = { servicenow: 'ServiceNow', entra: 'Entra', evren: 'Evren' };
const TABS = [['kisiler', 'Kişiler'], ['gruplar', 'Gruplar'], ['roller', 'Roller ve yetkiler'], ['gecmis', 'Yetki geçmişi']];
const SYSID = /^[0-9a-f]{32}$/;

function rolePill(a) {
  const tone = a.expired ? 'neutral' : a.elevated ? 'warn' : 'good';
  const extra = a.via === 'group' ? ` · ${a.group} grubu` : a.expires_at ? ` · ${formatDate(a.expires_at.slice(0, 10))}'e kadar` : '';
  return pill(`${a.label_tr}${extra}`, tone);
}

function until(a) {
  if (!a.expires_at) return 'Süresiz';
  return a.expired ? `Süresi doldu (${formatDateTime(a.expires_at)})` : `${formatDateTime(a.expires_at)} tarihine kadar`;
}

function fieldError(box, message) {
  clear(box).append(h('div', { class: 'notice bad', role: 'alert' }, message));
}

/** Doğrulama hatasını alana göre Türkçe açıklar (sunucu girdi değerini yansıtmaz, yalnızca alan adını verir). */
function explain(err, fields) {
  if (err.code !== 'gecersiz_istek') return err.message;
  const hit = err.details.map((d) => fields[String(d.field || '').split('.').pop()]).find(Boolean);
  return hit || 'Alanları kontrol edin.';
}

function requireText(input, min, box, message) {
  if (input.value.trim().length >= min) return true;
  input.setAttribute('aria-invalid', 'true');
  fieldError(box, message);
  input.focus();
  return false;
}

function enterSubmits(inputs, button, errorBox = null) {
  for (const el of inputs) {
    el.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); button.click(); } });
    el.addEventListener('input', () => { el.removeAttribute('aria-invalid'); if (errorBox) clear(errorBox); });
  }
}

const isoDay = (d) => d.toISOString().slice(0, 10);

/** Rol ver penceresi. target: {type, sys_id, name, roles: mevcut roller}. */
function openGrantDialog(meta, target, onDone) {
  const current = new Set((target.roles || []).filter((a) => !a.expired).map((a) => a.role));
  const options = meta.roles.filter((r) => !r.machine);
  const role = h('select', { id: 'grantRole', 'aria-describedby': 'grantRoleInfo' },
    h('option', { value: '' }, '— rol seçin —'), options.map((r) => h('option', { value: r.name }, r.label_tr + (r.elevated ? ' (yetkili, süreli)' : ''))));
  const info = h('div', { id: 'grantRoleInfo', 'aria-live': 'polite' });
  const expiry = h('input', { type: 'date', id: 'grantExpiry', 'aria-describedby': 'grantExpiryHelp' });
  const expiryHelp = h('p', { class: 'muted small', id: 'grantExpiryHelp' }, 'Boş bırakılırsa süresizdir. Süre dolunca rol kendiliğinden düşer.');
  const why = h('input', { type: 'text', id: 'grantWhy', maxlength: '500', placeholder: 'Ör. TALEP-1234: ekip değişikliği, Veri Yönetimi birimi',
    'aria-describedby': 'grantWhyHelp' });
  const error = h('div', { id: 'grantError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'grantSend' }, 'Rolü ver');
  const today = new Date();
  let limitDays = 730;
  /** Seçilen günün sonu (İstanbul), sınırı aşıyorsa sınırın bir dakika öncesi (ör. break-glass: en çok 24 saat). */
  const expiresAt = () => {
    if (!expiry.value) return null;
    const end = new Date(fromIstanbulInput(`${expiry.value}T23:59`));
    const cap = new Date(Date.now() + limitDays * 86400000 - 60000);
    return (end > cap ? cap : end).toISOString();
  };

  function describe() {
    const r = options.find((x) => x.name === role.value);
    clear(info);
    clear(error);
    send.disabled = false;
    if (!r) { expiry.required = false; return; }
    limitDays = r.elevated ? (meta.policy.break_glass_max_days[r.name] || meta.policy.elevated_max_days) : 730;
    const limit = limitDays;
    expiry.min = isoDay(today);
    expiry.max = isoDay(new Date(today.getTime() + limit * 86400000));
    expiry.required = r.elevated;
    expiryHelp.textContent = r.elevated
      ? `Yetkili rol: bitiş tarihi zorunlu, en çok ${limit} gün.`
      : 'Boş bırakılırsa süresizdir. Süre dolunca rol kendiliğinden düşer.';
    if (r.elevated && !expiry.value) expiry.value = expiry.max;
    const conflicts = r.sod.filter((s) => current.has(s.role));
    const ops = [...new Set(r.permissions.filter((p) => p.effect === 'allow').map((p) => p.operation_tr))];
    append(info, [
      h('p', { class: 'small' }, r.description || ''),
      h('p', { class: 'muted small' }, `Yapabildikleri: ${ops.join(', ') || '—'}`),
      current.has(r.name) ? h('p', { class: 'notice' }, 'Bu rol zaten var: gönderirseniz süresi ve gerekçesi yenilenir.') : null,
      conflicts.length ? h('div', { class: 'notice bad', role: 'alert' }, h('strong', {}, 'Görevler ayrılığı: '),
        conflicts.map((c) => `${c.label_tr} rolüyle birleşemez (${c.description}).`).join(' '),
        ' Önce o rolü geri alın veya başka birine verin.') : null]);
    send.disabled = conflicts.length > 0;  // sunucu da reddeder; boşuna istek gönderilmez
  }
  role.addEventListener('change', describe);
  send.addEventListener('click', async () => {
    clear(error);
    if (!role.value) { fieldError(error, 'Bir rol seçin.'); role.focus(); return; }
    if (expiry.required && !expiry.value) { fieldError(error, 'Yetkili rol için bitiş tarihi seçin.'); expiry.focus(); return; }
    if (!requireText(why, meta.policy.min_text, error, `Gerekçe en az ${meta.policy.min_text} karakter olmalı (talep numarası ve neden).`)) return;
    send.disabled = true;
    try {
      const res = await iamGrant({ principal_type: target.type, target: target.sys_id, role: role.value,
        expires_at: expiresAt(), justification: why.value.trim() });
      closeOverlay();
      toast(res.message);
      onDone();
    } catch (err) {
      send.disabled = false;
      fieldError(error, err.message);
    }
  });
  enterSubmits([why, expiry], send, error);
  dialog({
    title: `Rol ver: ${target.name}`,
    body: [
      h('label', { class: 'field', for: 'grantRole' }, 'Rol'), role, info,
      h('label', { class: 'field', for: 'grantExpiry' }, 'Bitiş tarihi'), expiry, expiryHelp,
      h('label', { class: 'field', for: 'grantWhy' }, 'Gerekçe'), why,
      h('p', { class: 'muted small', id: 'grantWhyHelp' }, 'Gerekçe ve sizin adınız yetki geçmişine yazılır; denetçi görür.'),
      error,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: role,
  });
}

function openRevokeDialog(meta, target, assignment, onDone) {
  const why = h('input', { type: 'text', id: 'revokeWhy', maxlength: '500', placeholder: 'Ör. görev değişti, TALEP-5678' });
  const error = h('div', { id: 'revokeError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'revokeSend' }, 'Geri al');
  send.addEventListener('click', async () => {
    if (!requireText(why, meta.policy.min_text, error, `Gerekçe en az ${meta.policy.min_text} karakter olmalı.`)) return;
    send.disabled = true;
    try {
      const res = await iamRevoke({ principal_type: target.type, target: target.sys_id, role: assignment.role, reason: why.value.trim() });
      closeOverlay();
      toast(res.message);
      onDone();
    } catch (err) {
      send.disabled = false;
      fieldError(error, err.message);
    }
  });
  enterSubmits([why], send, error);
  dialog({
    title: `Rolü geri al: ${assignment.label_tr}`,
    body: [h('p', {}, `${target.name} bu rolü bir sonraki işleminde kaybeder.`),
      h('label', { class: 'field', for: 'revokeWhy' }, 'Gerekçe'), why, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: why,
  });
}

function openAddDialog(meta, onDone) {
  const user = h('input', { type: 'text', id: 'newUser', maxlength: '120', autocomplete: 'off', placeholder: 'ayse.demir@kurum.com.tr',
    'aria-describedby': 'newUserHelp' });
  const name = h('input', { type: 'text', id: 'newName', maxlength: '120', placeholder: 'Ayşe Demir' });
  const email = h('input', { type: 'email', id: 'newEmail', maxlength: '254', placeholder: 'ayse.demir@kurum.com.tr' });
  const oid = h('input', { type: 'text', id: 'newOid', maxlength: '36', autocomplete: 'off', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
    'aria-describedby': 'newOidHelp' });
  const error = h('div', { id: 'newError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'newSend' }, 'Kişiyi ekle');
  send.addEventListener('click', async () => {
    clear(error);
    if (!requireText(user, 2, error, 'Kullanıcı adı zorunludur.') || !requireText(name, 2, error, 'Ad soyad zorunludur.')) return;
    send.disabled = true;
    try {
      const res = await iamCreatePerson({ user_name: user.value.trim(), name: name.value.trim(),
        email: email.value.trim() || null, entra_oid: oid.value.trim() || null });
      closeOverlay();
      toast(res.message);
      onDone(res.sys_id);
    } catch (err) {
      send.disabled = false;
      fieldError(error, explain(err, { entra_oid: 'Entra nesne kimliği GUID biçiminde olmalı (ör. 1b2c3d4e-…).',
        user_name: 'Kullanıcı adı 2–120 karakter olmalı.', name: 'Ad soyad 2–120 karakter olmalı.' }));
    }
  });
  enterSubmits([user, name, email, oid], send, error);
  dialog({
    title: 'Kişi ekle',
    body: [
      h('p', { class: 'muted small' }, 'ServiceNow veya Entra eşitlemesinde henüz olmayan bir kişi için. Kişi önce eklenir, sonra rol verilir.'),
      h('label', { class: 'field', for: 'newUser' }, 'Kullanıcı adı (zorunlu)'), user,
      h('p', { class: 'muted small', id: 'newUserHelp' }, 'Kişinin Entra\'daki oturum adı.'),
      h('label', { class: 'field', for: 'newName' }, 'Ad soyad (zorunlu)'), name,
      h('label', { class: 'field', for: 'newEmail' }, 'E-posta'), email,
      h('label', { class: 'field', for: 'newOid' }, 'Entra nesne kimliği'), oid,
      h('p', { class: 'muted small', id: 'newOidHelp' }, meta.policy.principal_key === 'entra_oid'
        ? 'Kişinin Evren\'e girebilmesi ve rol alabilmesi için gerekir (Entra yönetim merkezinde "Object ID").'
        : 'Geliştirme ortamında rol kullanıcı adına verilir; canlı kurulumda bu kimlik gerekir.'),
      error,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: user,
  });
}

function openEditDialog(meta, person, onDone) {
  const own = person.source === 'evren';
  const name = h('input', { type: 'text', id: 'editName', maxlength: '120', value: person.name || '' });
  const email = h('input', { type: 'email', id: 'editEmail', maxlength: '254', value: person.email || '' });
  const oid = h('input', { type: 'text', id: 'editOid', maxlength: '36', value: person.entra_oid || '', autocomplete: 'off' });
  const active = h('input', { type: 'checkbox', id: 'editActive', checked: person.active });
  const why = h('input', { type: 'text', id: 'editWhy', maxlength: '500', placeholder: 'Ör. İK bildirimi İK-42; Entra eşlemesi TALEP-7',
    'aria-describedby': 'editWhyHelp' });
  const error = h('div', { id: 'editError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'editSend' }, 'Kaydet');
  send.addEventListener('click', async () => {
    clear(error);
    const body = {};
    if (own) {
      if (name.value.trim() !== (person.name || '')) body.name = name.value.trim();
      if (email.value.trim() !== (person.email || '')) body.email = email.value.trim();
      if (active.checked !== person.active) body.active = active.checked;
    }
    const newOid = oid.value.trim();
    if (newOid !== (person.entra_oid || '')) {
      if (newOid) body.entra_oid = newOid; else body.clear_entra_oid = true;
    }
    if (!Object.keys(body).length) { fieldError(error, 'Değişiklik yok.'); return; }
    const needsWhy = 'entra_oid' in body || body.clear_entra_oid || body.active === false;
    if (needsWhy && !requireText(why, meta.policy.min_text, error,
      `Entra kimliği değişikliği ve pasifleştirme için en az ${meta.policy.min_text} karakterlik gerekçe yazın.`)) return;
    if (why.value.trim()) body.reason = why.value.trim();
    send.disabled = true;
    try {
      const res = await iamUpdatePerson(person.sys_id, body);
      closeOverlay();
      toast(res.message);
      onDone();
    } catch (err) {
      send.disabled = false;
      fieldError(error, explain(err, { entra_oid: 'Entra nesne kimliği GUID biçiminde olmalı.', name: 'Ad soyad 2–120 karakter olmalı.' }));
    }
  });
  enterSubmits([name, email, oid, why], send, error);
  dialog({
    title: `Kişiyi düzenle: ${person.name}`,
    body: [
      own ? null : h('p', { class: 'notice' }, `Bu kişi ${SOURCE_TR[person.source] || person.source}'dan geliyor: ad, e-posta ve durum orada değişir. Burada yalnızca Entra nesne kimliği bağlanır.`),
      own ? [h('label', { class: 'field', for: 'editName' }, 'Ad soyad'), name, h('label', { class: 'field', for: 'editEmail' }, 'E-posta'), email] : null,
      h('label', { class: 'field', for: 'editOid' }, 'Entra nesne kimliği'), oid,
      own ? h('label', { class: 'check', for: 'editActive' }, active, ' Etkin (pasifleşen kişinin doğrudan rolleri geri alınır)') : null,
      h('label', { class: 'field', for: 'editWhy' }, 'Gerekçe'), why,
      h('p', { class: 'muted small', id: 'editWhyHelp' }, 'Entra kimliği değişince eski kimliğe verilmiş roller geri alınır; yeniden verin.'),
      error,
    ],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: own ? name : oid,
  });
}

function openBindDialog(meta, group, onDone) {
  const gid = h('input', { type: 'text', id: 'bindGid', maxlength: '36', value: group.entra_group_id || '', autocomplete: 'off',
    placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' });
  const why = h('input', { type: 'text', id: 'bindWhy', maxlength: '500', placeholder: 'Ör. Entra grubu eşlemesi, TALEP-12' });
  const error = h('div', { id: 'bindError' });
  const send = h('button', { class: 'btn', type: 'button', id: 'bindSend' }, 'Kaydet');
  send.addEventListener('click', async () => {
    if (!requireText(why, meta.policy.min_text, error, `Gerekçe en az ${meta.policy.min_text} karakter olmalı.`)) return;
    send.disabled = true;
    try {
      const res = await iamBindGroup(group.sys_id, { entra_group_id: gid.value.trim() || null, reason: why.value.trim() });
      closeOverlay();
      toast(res.message);
      onDone();
    } catch (err) {
      send.disabled = false;
      fieldError(error, explain(err, { entra_group_id: 'Entra grup kimliği GUID biçiminde olmalı.' }));
    }
  });
  enterSubmits([gid, why], send, error);
  dialog({
    title: `Entra grubunu bağla: ${group.name}`,
    body: [h('p', { class: 'muted small' }, 'Grup rolleri Entra grup kimliğine verilir; grubun üyeleri oturum açınca rolü alır.'),
      h('label', { class: 'field', for: 'bindGid' }, 'Entra grup kimliği'), gid,
      h('label', { class: 'field', for: 'bindWhy' }, 'Gerekçe'), why, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), send],
    focus: gid,
  });
}

function eventList(items) {
  if (!items.length) return h('p', { class: 'muted small' }, 'Kayıt yok.');
  return h('ul', { class: 'mini' }, items.map((e) => h('li', {},
    h('div', { class: 'pills' }, pill(e.action_tr, e.action === 'role_delete' ? 'bad' : e.action === 'role_insert' ? 'good' : 'neutral'),
      e.role_tr ? pill(e.role_tr, 'neutral') : null),
    h('div', { class: 'cell-sub' }, `${formatDateTime(e.at)} · ${e.actor}`),
    e.justification ? h('div', { class: 'small' }, `Gerekçe: ${e.justification}`) : null,
    e.reason ? h('div', { class: 'small' }, `Gerekçe: ${e.reason}`) : null)));
}

export async function renderUsers(view, param) {
  const { mode, me } = await backend();
  const head = { title: 'Kullanıcılar ve Roller',
    sub: 'Kim Evren\'de ne yapabilir.',
    crumbs: [['Yönetim', '#/yonetim'], ['Kullanıcılar ve Roller']] };
  if (mode !== 'api') {
    setHeader(head);
    view.append(h('div', { class: 'notice', role: 'note' }, h('strong', {}, 'Demo modu. '),
      'Kullanıcı ve rol yönetimi canlı API ile çalışır. Rollerin ne yaptığı Yönetim Paneli\'ndeki görevlerde ve kılavuzda anlatılır.'));
    return {};
  }
  if (!me.can.iam_read) {
    setHeader(head);
    view.append(h('div', { class: 'notice warn', role: 'note' }, 'Bu sayfayı Yetki yöneticisi ve Denetçi görür. Rol için yetki yöneticinize başvurun.'));
    return {};
  }
  const meta = await iamRoles();
  const admin = meta.can_admin;
  const addBtn = admin ? h('button', { class: 'btn', type: 'button', id: 'iamAdd', onclick: () => openAddDialog(meta, (id) => {
    show('kisiler').then(() => openPerson(id));
  }) }, 'Kişi ekle') : null;
  setHeader({ ...head, actions: addBtn ? [addBtn] : [] });

  const tabBox = h('div', { id: 'iamTabs' });
  const body = h('div', { id: 'iamBody' });
  if (!admin) view.append(h('p', { class: 'notice', role: 'note', id: 'iamReadOnly' }, 'Salt okunur: rol verme ve geri alma Yetki yöneticisinindir.'));
  view.append(tabBox, body);
  let active = 'kisiler';
  let filters = { q: '', role: '', status: 'active', expiring: false };
  let reloadList = () => {};

  // Kişiler ----------------------------------------------------------------------------------------------------
  async function peopleTab() {
    const search = h('input', { type: 'search', class: 'searchbar', id: 'iamSearch', placeholder: 'Ad, kullanıcı adı veya e-posta',
      'aria-label': 'Kişi ara', value: filters.q });
    const roleSel = h('select', { id: 'iamRoleFilter', 'aria-label': 'Role göre süz' }, h('option', { value: '' }, 'Bütün roller'),
      meta.roles.map((r) => h('option', { value: r.name, selected: r.name === filters.role }, r.label_tr)));
    const statusSel = h('select', { id: 'iamStatus', 'aria-label': 'Durum' },
      [['active', 'Etkin kişiler'], ['inactive', 'Pasif kişiler'], ['all', 'Hepsi']].map(([v, l]) => h('option', { value: v, selected: v === filters.status }, l)));
    const count = h('span', { class: 'count', id: 'iamCount', 'aria-live': 'polite' });
    const list = h('div', { id: 'iamList' });
    const note = filters.expiring ? h('p', { class: 'notice', id: 'iamExpiring' }, 'Doğrudan verilmiş rolünün süresi 14 gün içinde dolacak kişiler. ',
      h('button', { class: 'linklike', type: 'button', onclick: () => { filters.expiring = false; show('kisiler'); } }, 'Bütün kişileri göster')) : null;
    clear(body).append(note || '', h('div', { class: 'toolbar' }, search, roleSel, statusSel, count), list);
    let timer = null;
    let table = null;
    async function load() {
      const res = await iamPeople(filters);
      count.textContent = `${res.total} kişi${res.more ? ' (ilk 100 gösteriliyor)' : ''}`;
      table = dataTable({
        label: 'Kişiler', idOf: (r) => r.sys_id, selectedId: () => selected,
        empty: filters.q || filters.role ? 'Süzgece uyan kişi yok.' : 'Kişi yok. "Kişi ekle" ile başlayın.',
        columns: [
          { key: 'name', label: 'Kişi', render: (r) => h('div', {}, h('strong', {}, r.name), h('div', { class: 'cell-sub' }, r.user_name)) },
          { key: 'roles', label: 'Roller', sortable: false, render: (r) => r.roles.length
            ? h('div', { class: 'pills' }, r.roles.map(rolePill)) : h('span', { class: 'muted small' }, 'Rol yok') },
          { key: 'source', label: 'Kaynak', render: (r) => SOURCE_TR[r.source] || r.source },
          { key: 'can_sign_in', label: 'Giriş', render: (r) => (!r.active ? pill('Pasif', 'neutral')
            : r.can_sign_in ? pill('Girebilir', 'good') : pill('Entra kimliği yok', 'warn')) },
        ],
        rows: res.items, onOpen: (r, tr) => openPerson(r.sys_id, tr),
      });
      clear(list).append(table);
    }
    reloadList = load;
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { filters.q = search.value.trim(); load(); }, 250); });
    roleSel.addEventListener('change', () => { filters.role = roleSel.value; load(); });
    statusSel.addEventListener('change', () => { filters.status = statusSel.value; load(); });
    await load();
  }

  let selected = null;
  async function openPerson(id, opener) {
    selected = id;
    if (location.hash !== `#/kullanicilar/${id}`) history.replaceState(null, '', `#/kullanicilar/${id}`);
    let p;
    try { p = await iamPerson(id); } catch (err) { toast(err.message); return; }
    const refresh = async () => { await reloadList(); await openPerson(id); };
    const target = { type: 'user', sys_id: p.sys_id, name: p.name, roles: p.roles };
    const roles = p.roles.length ? h('ul', { class: 'mini', id: 'personRoles' }, p.roles.map((a) => h('li', { dataset: { role: a.role } },
      h('div', { class: 'pills' }, h('strong', {}, a.label_tr), a.elevated ? pill('yetkili', 'warn') : null,
        a.via === 'group' ? pill(`${a.group} grubu yoluyla`, 'neutral') : null, a.expired ? pill('süresi doldu', 'bad') : null),
      h('div', { class: 'cell-sub' }, `${until(a)} · veren: ${a.granted_by} · ${formatDateTime(a.granted_at)}`),
      a.justification ? h('div', { class: 'small' }, `Gerekçe: ${a.justification}`) : null,
      admin && a.via === 'direct' && !p.is_me ? h('button', { class: 'btn ghost small revoke-btn', type: 'button',
        onclick: () => openRevokeDialog(meta, target, a, refresh) }, 'Geri al') : null,
      a.via === 'group' ? h('div', { class: 'muted small' }, 'Grup rolü Gruplar sekmesinden yönetilir.') : null)))
      : h('p', { class: 'muted small', id: 'personRoles' }, 'Rolü yok: Evren\'e girerse hiçbir şey göremez.');
    const footer = [];
    if (admin && !p.is_me) {
      if (p.active && p.can_sign_in) {
        footer.push(h('button', { class: 'btn', type: 'button', id: 'grantOpen', onclick: () => openGrantDialog(meta, target, refresh) }, 'Rol ver'));
      }
      footer.push(h('button', { class: 'btn secondary', type: 'button', id: 'personEdit', onclick: () => openEditDialog(meta, p, refresh) }, 'Düzenle'));
    }
    openDetail({
      kicker: 'Kişi', title: p.name, opener,
      onClose: () => { selected = null; history.replaceState(null, '', '#/kullanicilar'); },
      body: [
        p.is_me ? h('p', { class: 'notice' }, 'Bu sizsiniz: kendi rollerinizi değiştiremezsiniz; başka bir yetki yöneticisi yapmalı.') : null,
        !p.can_sign_in ? h('p', { class: 'notice warn' }, 'Entra nesne kimliği bağlı değil: kişi Evren\'e giremez ve rol alamaz. "Düzenle" ile bağlayın.') : null,
        !p.active ? h('p', { class: 'notice warn' }, 'Pasif kişi: rol verilmez.') : null,
        section('Kişi', kv([['Kullanıcı adı', p.user_name], ['E-posta', p.email || '—'], ['Kaynak', SOURCE_TR[p.source] || p.source],
          ['Entra nesne kimliği', p.entra_oid ? h('span', { class: 'mono' }, p.entra_oid) : '—'], ['Durum', p.active ? 'Etkin' : 'Pasif']])),
        section('Roller', roles),
        section('Gruplar', p.groups.length ? h('ul', { class: 'mini' }, p.groups.map((g) => h('li', {}, g.name,
          g.entra_group_id ? null : h('span', { class: 'cell-sub' }, ' · Entra grubu bağlı değil')))) : h('p', { class: 'muted small' }, 'Grup üyeliği yok.')),
        section('Yetki geçmişi', eventList(p.events)),
      ],
      footer,
    });
  }

  // Gruplar -----------------------------------------------------------------------------------------------------
  async function groupsTab() {
    const res = await iamGroups();
    const table = dataTable({
      label: 'Gruplar', idOf: (g) => g.sys_id, empty: 'Grup yok. Gruplar ServiceNow veya Entra eşitlemesiyle gelir.',
      columns: [
        { key: 'name', label: 'Grup', render: (g) => h('strong', {}, g.name) },
        { key: 'members', label: 'Üye', num: true },
        { key: 'entra_group_id', label: 'Entra grubu', render: (g) => (g.entra_group_id ? pill('Bağlı', 'good') : pill('Bağlı değil', 'warn')) },
        { key: 'roles', label: 'Roller', sortable: false, render: (g) => (g.roles.length ? h('div', { class: 'pills' }, g.roles.map(rolePill)) : '—') },
      ],
      rows: res.items, onOpen: (g, tr) => openGroup(g, tr),
    });
    clear(body).append(h('p', { class: 'muted small' }, 'Grup rolü, Entra grubunun bütün üyelerine geçer. Rol vermeden önce grubun Entra grup kimliği bağlanmalıdır.'), table);
    reloadList = async () => { await groupsTab(); };
  }

  function openGroup(g, opener) {
    const target = { type: 'group', sys_id: g.sys_id, name: g.name, roles: g.roles };
    const refresh = async () => {
      const res = await iamGroups();
      await groupsTab();
      const again = res.items.find((x) => x.sys_id === g.sys_id);
      if (again) openGroup(again);
    };
    const footer = [];
    if (admin) {
      if (g.entra_group_id) footer.push(h('button', { class: 'btn', type: 'button', id: 'grantOpen', onclick: () => openGrantDialog(meta, target, refresh) }, 'Rol ver'));
      footer.push(h('button', { class: 'btn secondary', type: 'button', id: 'groupBind', onclick: () => openBindDialog(meta, g, refresh) },
        g.entra_group_id ? 'Entra grubunu değiştir' : 'Entra grubunu bağla'));
    }
    openDetail({
      kicker: 'Grup', title: g.name, opener,
      body: [
        g.entra_group_id ? null : h('p', { class: 'notice warn' }, 'Entra grup kimliği bağlı değil: bu gruba rol verilemez.'),
        section('Grup', kv([['Üye sayısı', String(g.members)], ['Kaynak', SOURCE_TR[g.source] || g.source],
          ['Entra grup kimliği', g.entra_group_id ? h('span', { class: 'mono' }, g.entra_group_id) : '—']])),
        section('Roller', g.roles.length ? h('ul', { class: 'mini', id: 'groupRoles' }, g.roles.map((a) => h('li', {},
          h('div', { class: 'pills' }, h('strong', {}, a.label_tr), a.elevated ? pill('yetkili', 'warn') : null),
          h('div', { class: 'cell-sub' }, until(a)), a.justification ? h('div', { class: 'small' }, `Gerekçe: ${a.justification}`) : null,
          admin ? h('button', { class: 'btn ghost small revoke-btn', type: 'button', onclick: () => openRevokeDialog(meta, target, a, refresh) }, 'Geri al') : null)))
          : h('p', { class: 'muted small' }, 'Rolü yok.')),
      ],
      footer,
    });
  }

  // Roller ve yetkiler (matris) -----------------------------------------------------------------------------------
  function rolesTab() {
    const human = meta.roles.filter((r) => !r.machine);
    const machine = meta.roles.filter((r) => r.machine);
    const used = meta.operations.filter((o) => meta.roles.some((r) => r.permissions.some((p) => p.operation === o.id && p.effect === 'allow')));
    const cell = (r, o) => {
      const ps = r.permissions.filter((p) => p.operation === o.id && p.effect === 'allow');
      if (!ps.length) return h('td', { class: 'mx-no' }, h('span', { class: 'sr-only' }, 'yok'), '·');
      const what = [...new Set(ps.map((p) => p.resource_tr))].join(', ');
      const inherited = ps.every((p) => p.from !== r.name);
      return h('td', { class: `mx-yes${inherited ? ' mx-inh' : ''}`, title: `${o.label}: ${what}${inherited ? ' (kalıtılan)' : ''}` },
        h('span', { 'aria-hidden': 'true' }, '✓'), h('span', { class: 'sr-only' }, `${what}${inherited ? ', kalıtılan' : ''}`));
    };
    const row = (r) => h('tr', {},
      h('th', { scope: 'row' }, h('button', { class: 'linklike role-link', type: 'button', onclick: (ev) => openRole(r, ev.currentTarget) }, r.label_tr),
        r.elevated ? h('span', { class: 'mx-tag' }, 'yetkili') : null),
      used.map((o) => cell(r, o)),
      h('td', { class: 'num' }, String(r.holders.user + r.holders.group)));
    const matrix = h('div', { class: 'tablewrap matrix-wrap' }, h('table', { class: 'matrix', id: 'roleMatrix', 'aria-label': 'Rol–yetki matrisi' },
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Rol'), used.map((o) => h('th', { scope: 'col' }, h('span', {}, o.label))),
        h('th', { scope: 'col', class: 'num' }, 'Atama'))),
      h('tbody', {}, human.map(row))));
    const pairs = [];
    const seen = new Set();
    for (const r of meta.roles) {
      for (const s of r.sod) {
        const k = [r.name, s.role].sort().join('|');
        if (!seen.has(k)) { seen.add(k); pairs.push([r.label_tr, s.label_tr, s.description]); }
      }
    }
    clear(body).append(
      h('p', { class: 'muted small' }, 'Her satır bir rol, her sütun bir işlem. ✓ izin var (soluk ✓: alt rolden kalıtılan). Hücrenin üstüne gelince hangi kayıtlarda geçerli olduğu görünür. Rol adına tıklayın: ayrıntı ve rolü taşıyanlar.'),
      matrix,
      section('Görevler ayrılığı: birleşemeyen roller', h('ul', { class: 'mini', id: 'sodList' }, pairs.map(([a, b, d]) => h('li', {},
        h('strong', {}, `${a} ↔ ${b}`), h('div', { class: 'cell-sub' }, d))))),
      section('Makine hesabı rolleri', h('p', { class: 'muted small' }, `${machine.map((r) => r.label_tr).join(', ')}: kişiye veya gruba verilmez; bağlayıcı ve eşleme hesapları içindir.`)),
      section('Kurallar', h('ul', { class: 'mini' },
        h('li', {}, `Yetkili roller (${meta.roles.filter((r) => r.elevated).map((r) => r.label_tr).join(', ')}) en çok ${meta.policy.elevated_max_days} gün, break-glass en çok 1 gün süreyle verilir.`),
        h('li', {}, 'Her rol verme ve geri alma gerekçelidir ve yetki geçmişine yazılır.'),
        h('li', {}, 'Kimse kendine veya üyesi olduğu gruba rol veremez, kendi rolünü geri alamaz.'))));
  }

  function openRole(r, opener) {
    const byRes = new Map();
    for (const p of r.permissions.filter((x) => x.effect === 'allow')) {
      if (!byRes.has(p.resource_tr)) byRes.set(p.resource_tr, new Set());
      byRes.get(p.resource_tr).add(p.operation_tr);
    }
    openDetail({
      kicker: 'Rol', title: r.label_tr, opener,
      body: [
        h('p', {}, r.description || ''),
        h('div', { class: 'pills' }, r.elevated ? pill('Yetkili: süreli verilir', 'warn') : pill('Süresiz verilebilir', 'neutral'),
          r.machine ? pill('Makine hesabı', 'neutral') : null),
        section('Yapabildikleri', h('ul', { class: 'mini' }, [...byRes].map(([res, ops]) => h('li', {}, h('strong', {}, res), h('div', { class: 'cell-sub' }, [...ops].join(', ')))))),
        r.contains.length ? section('İçerdiği roller', h('p', { class: 'small' }, r.contains.map((c) => c.label_tr).join(', '))) : null,
        r.sod.length ? section('Birleşemediği roller', h('ul', { class: 'mini' }, r.sod.map((s) => h('li', {}, h('strong', {}, s.label_tr), h('div', { class: 'cell-sub' }, s.description))))) : null,
        section('Atamalar', h('p', { class: 'small' }, `${r.holders.user} kişi, ${r.holders.group} grup.`)),
      ],
      footer: r.machine ? [] : [h('button', { class: 'btn secondary', type: 'button', id: 'roleHolders', onclick: () => {
        closeDetail();
        filters = { q: '', role: r.name, status: 'active', expiring: false };
        show('kisiler');
      } }, 'Bu rolü taşıyan kişiler')],
    });
  }

  // Yetki geçmişi --------------------------------------------------------------------------------------------------
  async function eventsTab() {
    const res = await iamEvents();
    clear(body).append(h('p', { class: 'muted small' }, 'Rol verme, yenileme ve geri alma ile kişi kaydı değişiklikleri. Kayıtlar değiştirilemez (denetim izi).'),
      dataTable({
        label: 'Yetki geçmişi', idOf: (e) => `${e.at}${e.subject}${e.action}`, empty: 'Henüz kayıt yok.',
        columns: [
          { key: 'at', label: 'Zaman', render: (e) => formatDateTime(e.at) },
          { key: 'action_tr', label: 'İşlem', render: (e) => pill(e.action_tr, e.action === 'role_delete' ? 'bad' : e.action === 'role_insert' ? 'good' : 'neutral') },
          { key: 'subject', label: 'Kime' },
          { key: 'role_tr', label: 'Rol', render: (e) => e.role_tr || '—' },
          { key: 'actor', label: 'Yapan' },
          { key: 'why', label: 'Gerekçe', sortable: false, render: (e) => h('div', {}, e.justification || e.reason || '—',
            e.expires_at ? h('div', { class: 'cell-sub' }, `Bitiş: ${formatDateTime(e.expires_at)}`) : null) },
        ],
        rows: res.items,
      }));
    reloadList = eventsTab;
  }

  async function show(tab) {
    active = tab;
    clear(tabBox).append(tabs(TABS.map(([id, label]) => ({ id, label })), active, (id) => {
      if (id === 'kisiler') filters.expiring = false;
      history.replaceState(null, '', id === 'kisiler' ? '#/kullanicilar' : `#/kullanicilar/${id}`);
      show(id);
    }, 'Kullanıcılar ve roller'));
    if (tab === 'gruplar') await groupsTab();
    else if (tab === 'roller') rolesTab();
    else if (tab === 'gecmis') await eventsTab();
    else await peopleTab();
  }

  async function update(p) {
    if (p && SYSID.test(p)) {
      if (active !== 'kisiler' || !document.getElementById('iamList')) await show('kisiler');
      await openPerson(p);
      return;
    }
    closeDetail({ silent: true });
    if (p === 'suresi-yaklasan') { filters = { ...filters, expiring: true }; await show('kisiler'); return; }
    if (p === 'yeni') {
      await show('kisiler');
      if (admin) openAddDialog(meta, (id) => { show('kisiler').then(() => openPerson(id)); });
      return;
    }
    await show(TABS.some(([id]) => id === p) ? p : 'kisiler');
  }
  await update(param);
  return { update };
}
