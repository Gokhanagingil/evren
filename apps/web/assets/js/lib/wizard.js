// Adım adım pencere (U8.7, ADR-0038): sihirbazların ortak bileşeni.
// Üstte adım göstergesi (geçilen, bulunulan, sıradaki), ortada adımın içeriği, altta Vazgeç / Geri / İleri.
// Klavye: metin kutusunda Enter ileri götürür, Esc kapatır; her adım değişince odak adımın ilk alanına gider.
import { h, append, clear } from './dom.js';
import { closeOverlay, dialog } from './shell.js';

/**
 * @param {object} o
 * @param {string} o.title
 * @param {Array<{id: string, title: string, render: Function, next?: Function, onLeave?: Function}>} o.steps
 *   ``render(state, api)`` adımın içeriğini döner (Promise olabilir). ``next(state, api)`` ileri geçmeden önce
 *   çağrılır; ``false`` dönerse geçilmez (hata ``api.error`` ile gösterilir).
 * @param {object} [o.state] adımlar arasında paylaşılan durum
 * @param {number} [o.start] başlangıç adımı
 * @param {string} [o.finishLabel] son adımın düğmesi
 * @param {Function} [o.onFinish] son adımda düğmeye basılınca; ``false`` dönerse pencere açık kalır
 * @param {Function} [o.onClose] pencere nasıl kapanırsa kapansın bir kez
 */
export function wizard({ title, steps, state = {}, start = 0, finishLabel = 'Bitir', onFinish = null, onClose = null }) {
  let at = -1;
  let busy = false;
  const list = h('ol', { class: 'wiz-steps', 'aria-label': 'Adımlar' });
  const body = h('div', { class: 'wiz-body', id: 'wizBody' });
  const error = h('div', { id: 'wizError', 'aria-live': 'polite' });
  const back = h('button', { class: 'btn secondary', type: 'button', id: 'wizBack' }, 'Geri');
  const next = h('button', { class: 'btn', type: 'button', id: 'wizNext' }, 'İleri');
  const cancel = h('button', { class: 'btn ghost', type: 'button', id: 'wizCancel', onclick: closeOverlay }, 'Vazgeç');

  const api = {
    state,
    error(message) {
      clear(error).append(h('div', { class: 'notice bad', role: 'alert' }, message));
    },
    clearError() { clear(error); },
    go(index) { return show(index); },
    get index() { return at; },
    setNextLabel(label) { next.textContent = label; },
    setNextEnabled(on) { next.disabled = !on; },
  };

  function paintSteps() {
    clear(list);
    steps.forEach((s, i) => {
      const status = i < at ? 'done' : i === at ? 'current' : 'todo';
      list.append(h('li', { class: `wiz-step ${status}`, 'aria-current': i === at ? 'step' : null, dataset: { step: s.id } },
        h('span', { class: 'wiz-num', 'aria-hidden': 'true' }, i < at ? '✓' : String(i + 1)),
        h('span', { class: 'wiz-label' }, s.title,
          h('span', { class: 'sr-only' }, status === 'done' ? ' (tamamlandı)' : status === 'current' ? ' (bu adım)' : ''))));
    });
  }

  async function show(index) {
    if (at >= 0 && steps[at].onLeave) steps[at].onLeave(state, api);
    at = Math.max(0, Math.min(steps.length - 1, index));
    clear(error);
    paintSteps();
    back.disabled = at === 0;
    next.disabled = false;
    next.textContent = at === steps.length - 1 ? finishLabel : 'İleri';
    clear(body).append(h('p', { class: 'muted small' }, 'Yükleniyor…'));
    let content;
    try {
      content = await steps[at].render(state, api);
    } catch (err) {
      content = h('div', { class: 'notice bad', role: 'alert' }, `Adım açılamadı: ${err.message}`);
    }
    append(clear(body), [h('h3', { class: 'wiz-title', id: 'wizStepTitle', tabindex: '-1' }, `${at + 1}. ${steps[at].title}`), content]);
    const first = body.querySelector('input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea, [data-autofocus]');
    (first || body.querySelector('#wizStepTitle')).focus();
  }

  async function forward() {
    if (busy) return;
    busy = true;
    next.setAttribute('aria-busy', 'true');
    try {
      clear(error);
      const step = steps[at];
      if (step.next && (await step.next(state, api)) === false) return;
      if (at === steps.length - 1) {
        if (onFinish && (await onFinish(state, api)) === false) return;
        closeOverlay();
        return;
      }
      await show(at + 1);
    } catch (err) {
      api.error(err.message);
    } finally {
      busy = false;
      next.removeAttribute('aria-busy');
    }
  }

  back.addEventListener('click', () => { if (at > 0) show(at - 1); });
  next.addEventListener('click', forward);
  body.addEventListener('keydown', (ev) => {
    const t = ev.target;
    if (ev.key === 'Enter' && t.tagName === 'INPUT' && !['checkbox', 'radio', 'button'].includes(t.type) && !t.dataset.noEnter) {
      ev.preventDefault();
      forward();
    }
  });
  dialog({ title, body: [list, body, error], footer: [cancel, back, next], wide: true, onClose, focus: next });
  show(start);
  return api;
}

/** Sihirbazlarda ortak alan: etiket + denetim + isteğe bağlı açıklama (aria-describedby ile bağlı). */
export function wfield(label, control, hint = null) {
  const id = control.id;
  const hintEl = hint ? h('p', { class: 'muted small', id: `${id}Help` }, hint) : null;
  if (hintEl) control.setAttribute('aria-describedby', `${id}Help`);
  return h('div', { class: 'wiz-field' }, h('label', { class: 'field', for: id }, label), control, hintEl);
}

/** Radyo düğmesi grubu: ``options``: [{value, label, sub?, disabled?}]. ``onChange(value)``. */
export function radioGroup(name, label, options, value, onChange) {
  const group = h('div', { class: 'wiz-radios', role: 'radiogroup', 'aria-label': label });
  for (const o of options) {
    const input = h('input', { type: 'radio', name, value: o.value, id: `${name}-${o.value}`, checked: o.value === value,
      disabled: o.disabled || false });
    input.addEventListener('change', () => { if (input.checked) onChange(o.value); });
    group.append(h('label', { class: `wiz-radio${o.disabled ? ' disabled' : ''}`, for: input.id }, input,
      h('span', {}, h('strong', {}, o.label), o.sub ? h('span', { class: 'cell-sub' }, o.sub) : null)));
  }
  return group;
}
