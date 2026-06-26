// views/settings.js — Account settings view
// Account info, notification-email management, password change, billing, logout.

import { getEmail, getParentId } from '../core/session.js';
import { apiRequest } from '../core/api.js';
import { el } from '../core/dom.js';
import { card } from '../components/card.js';
import { renderDisclosuresBody } from '../components/disclosures.js';

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// A labeled input that returns both the wrapper and the live input element.
function field({ id, label, type = 'text', autocomplete, hint, value }) {
  const group = el('div', { class: 'form-group' });
  group.appendChild(el('label', { for: id, text: label }));
  const input = document.createElement('input');
  input.type = type;
  input.id = id;
  if (autocomplete) input.autocomplete = autocomplete;
  if (value) input.value = value;
  group.appendChild(input);
  if (hint) group.appendChild(el('p', { class: 'settings-hint', text: hint }));
  return { group, input };
}

function statusLine() {
  return el('p', { class: 'settings-hint', 'aria-live': 'polite', text: '' });
}
function setStatus(node, text, kind) {
  node.classList.remove('is-error', 'is-success');
  node.textContent = text;
  if (kind) node.classList.add(kind);
}

export async function render(container, params) {
  container.textContent = '';

  const header = el('div', { class: 'page-header' }, [
    el('h2', { text: 'Settings' }),
    el('p', { text: 'Manage your account and preferences' }),
  ]);
  container.appendChild(header);

  const parentId = getParentId();

  // Pull the account from the server so the notification email reflects the
  // *stored* value (the true alert destination), not the login session. Fall
  // back to the session email if the request fails.
  let account = null;
  try {
    account = await apiRequest('GET', '/api/auth/account');
  } catch (e) {
    account = null;
  }
  const signInEmail = (account && account.sign_in_email) || getEmail();
  let currentNotif = (account && account.notification_email) || getEmail();

  // --- Account info card (read-only sign-in identity) ---
  const accountBody = el('div', { class: 'settings-section' });
  accountBody.appendChild(
    el('div', { class: 'settings-row' }, [
      el('span', { class: 'settings-label', text: 'Sign-in email' }),
      el('span', { class: 'settings-value', text: signInEmail || '—' }),
    ])
  );
  accountBody.appendChild(
    el('div', { class: 'settings-row' }, [
      el('span', { class: 'settings-label', text: 'Account ID' }),
      el('span', { class: 'settings-value', text: parentId || '—' }),
    ])
  );
  container.appendChild(card({ title: 'Account', body: accountBody }));

  // --- Safety & disclosures card (required user-facing notices) ---
  container.appendChild(
    card({ title: 'Safety & Disclosures', body: renderDisclosuresBody() })
  );

  // --- Notification email card ---
  const emailBody = el('div', { class: 'settings-section' });
  emailBody.appendChild(
    el('p', {
      text: 'Safety alerts and account notifications are delivered to this address.',
    })
  );
  const emailForm = el('div', { class: 'settings-form' });
  const emailFld = field({
    id: 'settings-notif-email',
    label: 'Notification email',
    type: 'email',
    autocomplete: 'email',
    value: currentNotif,
  });
  emailForm.appendChild(emailFld.group);
  emailBody.appendChild(emailForm);
  const emailMsg = statusLine();
  const emailBtn = el('button', {
    class: 'btn btn-primary',
    type: 'button',
    text: 'Update email',
  });
  emailBtn.addEventListener('click', () => {
    const next = emailFld.input.value.trim();
    if (!EMAIL_RE.test(next)) {
      setStatus(emailMsg, 'Please enter a valid email address.', 'is-error');
      return;
    }
    if (next === currentNotif) {
      setStatus(emailMsg, 'That is already your notification email.', 'is-error');
      return;
    }
    emailBtn.disabled = true;
    setStatus(emailMsg, 'Saving…');
    apiRequest('POST', '/api/auth/change-email', { new_email: next })
      .then((data) => {
        // Sign-in identity is unchanged; only the alert destination moves.
        currentNotif = (data && data.email) || next;
        setStatus(emailMsg, 'Notification email updated.', 'is-success');
      })
      .catch((err) => {
        setStatus(
          emailMsg,
          (err && err.detail) || 'Could not update email. Please try again.',
          'is-error'
        );
      })
      .finally(() => {
        emailBtn.disabled = false;
      });
  });
  emailBody.appendChild(emailBtn);
  emailBody.appendChild(emailMsg);
  container.appendChild(card({ title: 'Notification Email', body: emailBody }));

  // --- Password card ---
  const passBody = el('div', { class: 'settings-section' });
  passBody.appendChild(
    el('p', { text: 'Change the password you use to sign in.' })
  );
  const passForm = el('div', { class: 'settings-form' });
  const curFld = field({
    id: 'settings-cur-pass',
    label: 'Current password',
    type: 'password',
    autocomplete: 'current-password',
  });
  const newFld = field({
    id: 'settings-new-pass',
    label: 'New password',
    type: 'password',
    autocomplete: 'new-password',
    hint: 'At least 8 characters, with upper- and lower-case letters, a number, and a symbol.',
  });
  const confFld = field({
    id: 'settings-conf-pass',
    label: 'Confirm new password',
    type: 'password',
    autocomplete: 'new-password',
  });
  passForm.appendChild(curFld.group);
  passForm.appendChild(newFld.group);
  passForm.appendChild(confFld.group);
  passBody.appendChild(passForm);
  const passMsg = statusLine();
  const passBtn = el('button', {
    class: 'btn btn-primary',
    type: 'button',
    text: 'Change password',
  });
  passBtn.addEventListener('click', () => {
    const cur = curFld.input.value;
    const nw = newFld.input.value;
    const cf = confFld.input.value;
    if (!cur || !nw || !cf) {
      setStatus(passMsg, 'Please fill in all three fields.', 'is-error');
      return;
    }
    if (nw !== cf) {
      setStatus(passMsg, 'New passwords do not match.', 'is-error');
      return;
    }
    passBtn.disabled = true;
    setStatus(passMsg, 'Saving…');
    apiRequest('POST', '/api/auth/change-password', {
      current_password: cur,
      new_password: nw,
      verify_password: cf,
    })
      .then(() => {
        // The server invalidates every session on a password change, so the
        // only safe next step is to sign back in.
        setStatus(passMsg, 'Password changed. Signing you out…', 'is-success');
        curFld.input.value = newFld.input.value = confFld.input.value = '';
        setTimeout(() => {
          passBtn.dispatchEvent(new CustomEvent('sf:logout', { bubbles: true }));
        }, 1400);
      })
      .catch((err) => {
        setStatus(
          passMsg,
          (err && err.detail) || 'Could not change password. Please try again.',
          'is-error'
        );
        passBtn.disabled = false;
      });
  });
  passBody.appendChild(passBtn);
  passBody.appendChild(passMsg);
  container.appendChild(card({ title: 'Password', body: passBody }));

  // --- Billing card ---
  // The customer-portal URL is configured server-side (LS_CUSTOMER_PORTAL_URL)
  // and fetched on demand from /api/billing/portal-url rather than hardcoded,
  // so it stays correct across environments/providers.
  const billingBody = el('div', { class: 'settings-section' });
  billingBody.appendChild(
    el('p', { text: 'Manage your subscription, payment methods, and billing history.' })
  );
  const billingMsg = el('p', { class: 'settings-hint', 'aria-live': 'polite', text: '' });
  const billingBtn = el('button', {
    class: 'btn btn-outline',
    type: 'button',
    text: 'Manage Billing →',
  });
  billingBtn.addEventListener('click', () => {
    billingBtn.disabled = true;
    billingMsg.textContent = '';
    apiRequest('GET', '/api/billing/portal-url')
      .then((data) => {
        const url = data && data.url;
        if (url) {
          window.open(url, '_blank', 'noopener,noreferrer');
        } else {
          billingMsg.textContent = 'Billing portal is not configured yet.';
        }
      })
      .catch(() => {
        billingMsg.textContent = 'Could not open the billing portal. Please try again later.';
      })
      .finally(() => {
        billingBtn.disabled = false;
      });
  });
  billingBody.appendChild(billingBtn);
  billingBody.appendChild(billingMsg);
  container.appendChild(card({ title: 'Billing', body: billingBody }));

  // --- Sign out card ---
  const signOutBody = el('div', { class: 'settings-section' });
  signOutBody.appendChild(
    el('p', { text: 'Sign out of your account on this device.' })
  );
  const logoutBtn = el('button', {
    id: 'settings-logout-btn',
    class: 'btn btn-danger',
    type: 'button',
    text: 'Sign Out',
  });
  signOutBody.appendChild(logoutBtn);
  container.appendChild(card({ title: 'Sign Out', body: signOutBody }));

  // app.js intercepts this bubbling event to clear the session and route to login.
  logoutBtn.addEventListener('click', () => {
    logoutBtn.dispatchEvent(new CustomEvent('sf:logout', { bubbles: true }));
  });
}
