// components/confirm.js — non-blocking confirmation dialog (replaces window.confirm).
// window.confirm blocks the page (and freezes browser automation) and can't be
// styled. This returns a Promise<boolean> resolved when the user chooses, using
// the same modal pattern as the rest of the SPA. Esc/overlay = cancel, Enter =
// confirm; the confirm button is focused for keyboard users.

import { el } from '../core/dom.js';

export function confirmDialog({
  title = 'Are you sure?',
  message = '',
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  danger = false,
} = {}) {
  return new Promise((resolve) => {
    const overlay = el('div', { class: 'modal-overlay' });
    const modal = el('div', {
      class: 'modal modal-sm',
      role: 'alertdialog',
      'aria-modal': 'true',
    });

    const header = el('div', { class: 'modal-header' }, [el('h3', { text: title })]);
    const body = el('div', { class: 'modal-body' }, [el('p', { text: message })]);

    const footer = el('div', { class: 'modal-footer' });
    const cancelBtn = el('button', { class: 'btn btn-outline', type: 'button', text: cancelText });
    const confirmBtn = el('button', {
      class: 'btn ' + (danger ? 'btn-danger' : 'btn-primary'),
      type: 'button',
      text: confirmText,
    });
    footer.appendChild(cancelBtn);
    footer.appendChild(confirmBtn);

    modal.appendChild(header);
    modal.appendChild(body);
    modal.appendChild(footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    function cleanup(result) {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      resolve(result);
    }
    function onKey(e) {
      if (e.key === 'Escape') cleanup(false);
      else if (e.key === 'Enter') cleanup(true);
    }

    cancelBtn.addEventListener('click', () => cleanup(false));
    confirmBtn.addEventListener('click', () => cleanup(true));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(false); });
    document.addEventListener('keydown', onKey);
    confirmBtn.focus();
  });
}
