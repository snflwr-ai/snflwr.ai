// components/toast.js — transient, non-blocking notifications.
// Replaces native alert(): alert() blocks the page (and browser automation),
// and looks out of place in a styled SPA. Toasts auto-dismiss and are
// announced to assistive tech via role="alert" (errors) / role="status".

export function showToast(message, type = 'error') {
  const toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.setAttribute('role', type === 'error' ? 'alert' : 'status');
  toast.textContent = String(message);
  document.body.appendChild(toast);

  // Animate in on the next frame so the transition runs.
  requestAnimationFrame(() => toast.classList.add('toast-show'));

  setTimeout(() => {
    toast.classList.remove('toast-show');
    setTimeout(() => toast.remove(), 300);
  }, 4000);

  return toast;
}
