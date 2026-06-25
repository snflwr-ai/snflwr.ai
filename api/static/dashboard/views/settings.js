// views/settings.js — Account settings view
// Shows account info, billing link, and logout button.
// No dedicated settings API endpoint in legacy dashboard.js — uses session data.

import { getEmail, getParentId } from '../core/session.js';
import { apiRequest } from '../core/api.js';
import { el } from '../core/dom.js';
import { card } from '../components/card.js';

export async function render(container, params) {
  container.textContent = '';

  const header = el('div', { class: 'page-header' }, [
    el('h2', { text: 'Settings' }),
    el('p', { text: 'Manage your account and preferences' }),
  ]);
  container.appendChild(header);

  const email = getEmail();
  const parentId = getParentId();

  // Account info card
  const accountBody = el('div', { class: 'settings-section' });
  accountBody.appendChild(
    el('div', { class: 'settings-row' }, [
      el('span', { class: 'settings-label', text: 'Email' }),
      el('span', { class: 'settings-value', text: email || '—' }),
    ])
  );
  accountBody.appendChild(
    el('div', { class: 'settings-row' }, [
      el('span', { class: 'settings-label', text: 'Account ID' }),
      el('span', { class: 'settings-value', text: parentId || '—' }),
    ])
  );

  container.appendChild(card({ title: 'Account', body: accountBody }));

  // Billing card. The customer-portal URL is configured server-side
  // (LS_CUSTOMER_PORTAL_URL) and fetched on demand from /api/billing/portal-url
  // rather than hardcoded, so it stays correct across environments/providers.
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

  // Sign out card
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

  // The logout handler is wired in app.js via a custom event or direct reference.
  // We dispatch a custom event so app.js can intercept it without coupling.
  logoutBtn.addEventListener('click', () => {
    const event = new CustomEvent('sf:logout', { bubbles: true });
    logoutBtn.dispatchEvent(event);
  });
}
