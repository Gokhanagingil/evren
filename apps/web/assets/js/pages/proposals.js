// Yapay zekâ önerileri (U9.2, ADR-0043): ajan önerir, insan karar verir. Bekleyenler önce; gerekçe, model ve öneren
// görünür. Kabul, kararı verenin yetkisiyle Evren'in her zamanki yolunu çalıştırır (elle düzenleme, kuyruk kararı).
import { h, append, clear, formatDateTime, pill, tabs } from '../lib/dom.js';
import { closeOverlay, dialog, setHeader, toast } from '../lib/shell.js';
import { backend, proposalDecide, proposalsList } from '../lib/data.js';

const MIN_NOTE = 10;
const STATUS_TONE = { pending: 'warn', accepted: 'good', rejected: 'neutral', failed: 'bad' };

function decideDialog(p, decision, reload) {
  const note = h('textarea', { id: 'proposalNote', rows: '3', maxlength: '2000',
    placeholder: decision === 'accept' ? 'Ör. Kanıtlar doğru; CI\'ın görevi bu' : 'Ör. Açıklama zaten doğru' });
  const error = h('div', { id: 'proposalError' });
  const save = h('button', { class: 'btn', type: 'button', id: 'proposalSave' }, decision === 'accept' ? 'Kabul et' : 'Reddet');
  note.addEventListener('input', () => { note.removeAttribute('aria-invalid'); clear(error); });
  save.addEventListener('click', async () => {
    if (note.value.trim().length < MIN_NOTE) {
      note.setAttribute('aria-invalid', 'true');
      clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, `Gerekçe en az ${MIN_NOTE} karakter olmalı.`));
      note.focus();
      return;
    }
    save.disabled = true;
    try {
      const r = await proposalDecide(p.sys_id, decision, note.value.trim());
      closeOverlay();
      toast(r.message);
      await reload();
    } catch (err) {
      save.disabled = false;
      clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, err.message));
    }
  });
  const intro = decision === 'accept'
    ? (p.kind === 'ci_update'
      ? 'Değerler Manual kaynağı olarak uzlaştırmaya girer; CI uzlaştırıcı uygulayınca güncellenir.'
      : 'Eşleştirme kararı sizin adınıza kuyruğa işlenir.')
    : 'Öneri reddedilir; Evren\'de hiçbir şey değişmez.';
  dialog({ title: `${p.number}: ${decision === 'accept' ? 'kabul et' : 'reddet'}`,
    body: [h('p', {}, intro), h('label', { class: 'field', for: 'proposalNote' }, 'Gerekçe'), note, error],
    footer: [h('button', { class: 'btn secondary', type: 'button', onclick: closeOverlay }, 'Vazgeç'), save], focus: note });
}

function card(p, reload) {
  const target = p.kind === 'ci_update' ? h('a', { href: `#/ci/${p.target}` }, p.target_label || p.target)
    : h('a', { href: `#/kuyruk/${p.target}` }, p.target_label || 'kuyruk kaydı');
  return h('li', { class: 'proposal', id: `proposal-${p.number}` },
    h('div', { class: 'proposal-head' },
      h('strong', {}, `${p.number} · ${p.kind_label}`), pill(p.status_label, STATUS_TONE[p.status]),
      p.channel === 'mcp' ? pill('MCP', 'neutral') : pill('REST', 'neutral')),
    h('div', {}, target, ': ', h('span', {}, p.summary || '—')),
    h('details', { class: 'inherited' }, h('summary', {}, 'Gerekçe ve kaynak'),
      h('p', { class: 'small proposal-rationale' }, p.rationale),
      h('p', { class: 'muted small' }, `Model: ${p.model || 'bildirilmedi'} · Öneren: ${p.proposed_by} · ${formatDateTime(p.proposed_at)}`)),
    p.status !== 'pending' ? h('p', { class: 'muted small' }, `${p.status_label}: ${p.decided_by} · ${formatDateTime(p.decided_at)} · ${p.decision_note || ''}`) : null,
    p.can_decide ? h('div', { class: 'row-actions' },
      h('button', { class: 'btn small', type: 'button', dataset: { action: 'accept' }, onclick: () => decideDialog(p, 'accept', reload) }, 'Kabul et…'),
      h('button', { class: 'btn secondary small', type: 'button', dataset: { action: 'reject' }, onclick: () => decideDialog(p, 'reject', reload) }, 'Reddet…'))
      : p.status === 'pending' && p.own ? h('p', { class: 'muted small' }, 'Kendi önerinize karar veremezsiniz.') : null);
}

export async function renderProposals(view) {
  const { mode } = await backend();
  setHeader({ title: 'Yapay Zekâ Önerileri', sub: 'Ajanların önerileri; karar sizin.', crumbs: [['Öneriler']] });
  if (mode !== 'api') {
    view.append(h('p', { class: 'notice' }, 'Öneriler canlı API ile çalışır: MCP ajanları önerir, yetkili kişi karar verir (ADR-0043).'));
    return {};
  }
  let status = 'pending';
  const tabBox = h('div', {});
  const body = h('div', { id: 'proposalBody' });
  append(view, [tabBox, body]);

  async function load() {
    clear(tabBox).append(tabs([{ id: 'pending', label: 'Karar bekleyen' }, { id: 'decided', label: 'Karar verilen' }], status,
      async (id) => { status = id; await load(); }, 'Öneri görünümü'));
    let res;
    try {
      res = await proposalsList(status);
    } catch (err) {
      clear(body).append(h('div', { class: 'notice bad' }, `Öneriler yüklenemedi: ${err.message}`));
      return;
    }
    if (!res.items.length) {
      clear(body).append(h('div', { class: 'empty' }, h('strong', {}, 'Öneri yok'),
        status === 'pending' ? 'Karar bekleyen yapay zekâ önerisi yok.' : 'Henüz karar verilmiş öneri yok.'));
      return;
    }
    clear(body).append(h('ul', { class: 'proposal-list', id: 'proposalList' }, res.items.map((p) => card(p, load))));
  }
  await load();
  return {};
}
