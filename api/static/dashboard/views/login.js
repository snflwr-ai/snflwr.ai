// views/login.js — Login view
// Does NOT use apiRequest (no token yet); calls fetch('/api/auth/login') directly.

import { setSession } from '../core/session.js';
import { el } from '../core/dom.js';

export function render(container) {
  container.textContent = '';

  const page = el('div', { class: 'login-page' });

  const loginCard = el('div', { class: 'login-card' });

  // Logo section
  const logo = el('div', { class: 'login-logo' });
  const logoIcon = document.createElement('img');
  logoIcon.src = '/dashboard/static/icon.png';
  logoIcon.alt = 'snflwr.ai';
  logoIcon.className = 'logo-icon';
  const logoH1 = el('h1', { text: 'snflwr.ai' });
  const logoP = el('p', { text: 'Parent Dashboard' });
  logo.appendChild(logoIcon);
  logo.appendChild(logoH1);
  logo.appendChild(logoP);

  // Error box
  const errBox = el('div', { id: 'login-error' });

  // Form
  const form = el('form', { id: 'login-form' });

  const emailGroup = el('div', { class: 'form-group' });
  const emailLabel = el('label', { for: 'email', text: 'Email' });
  const emailInput = document.createElement('input');
  emailInput.type = 'email';
  emailInput.id = 'email';
  emailInput.required = true;
  emailInput.autocomplete = 'email';
  emailGroup.appendChild(emailLabel);
  emailGroup.appendChild(emailInput);

  const passGroup = el('div', { class: 'form-group' });
  const passLabel = el('label', { for: 'password', text: 'Password' });
  const passInput = document.createElement('input');
  passInput.type = 'password';
  passInput.id = 'password';
  passInput.required = true;
  passInput.autocomplete = 'current-password';
  passGroup.appendChild(passLabel);
  passGroup.appendChild(passInput);

  const btn = el('button', { type: 'submit', class: 'btn btn-primary btn-full', id: 'login-btn', text: 'Sign In' });

  form.appendChild(emailGroup);
  form.appendChild(passGroup);
  form.appendChild(btn);

  loginCard.appendChild(logo);
  loginCard.appendChild(errBox);
  loginCard.appendChild(form);
  page.appendChild(loginCard);
  container.appendChild(page);

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const email = emailInput.value.trim();
    const password = passInput.value;
    errBox.textContent = '';
    btn.disabled = true;
    btn.textContent = 'Signing in...';

    fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
      .then((resp) => {
        if (!resp.ok) {
          return resp.json().then((d) => {
            throw new Error(d.detail || 'Login failed');
          });
        }
        return resp.json();
      })
      .then((data) => {
        setSession({
          token: data.token,
          parentId: data.session.parent_id,
          email,
        });
        location.hash = '#/overview';
      })
      .catch((err) => {
        const errMsg = el('div', { class: 'msg-error', text: err.message });
        errBox.textContent = '';
        errBox.appendChild(errMsg);
        btn.disabled = false;
        btn.textContent = 'Sign In';
      });
  });
}
