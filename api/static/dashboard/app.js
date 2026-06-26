// app.js — Dashboard entry point
// Boots on DOMContentLoaded; owns auth guard, router, nav shell, and view dispatch.
// No exports (entry point only).

import { setUnauthorizedHandler, apiRequest } from './core/api.js';
import { isAuthenticated, clearSession, getEmail } from './core/session.js';
import { parseRoute } from './core/router.js';
import { renderNav } from './components/nav.js';
import { renderDisclosureFooter } from './components/disclosures.js';
import { el } from './core/dom.js';

import { render as renderLogin } from './views/login.js';
import { render as renderOverview } from './views/overview.js';
import { render as renderSafety } from './views/safety.js';
import { render as renderActivity } from './views/activity.js';
import { render as renderChildren } from './views/children.js';
import { render as renderSettings } from './views/settings.js';

const VIEW_MAP = {
  overview: renderOverview,
  safety: renderSafety,
  activity: renderActivity,
  children: renderChildren,
  settings: renderSettings,
};

// ── Auth ──────────────────────────────────────────────────────────────────────

// Best-effort logout: POST to server, then clear local session and show login.
function logout() {
  apiRequest('POST', '/api/auth/logout').catch(() => {});
  clearSession();
  navMounted = false;
  mainEl = null;
  currentNavWrapper = null;
  showLogin();
}

// ── Login ─────────────────────────────────────────────────────────────────────

function showLogin() {
  const appEl = document.getElementById('app');
  if (!appEl) return;
  appEl.textContent = '';
  renderLogin(appEl);
}

// ── Navigation ────────────────────────────────────────────────────────────────

function onNavigate(view) {
  location.hash = '#/' + view;
}

// ── Shell (layout) ────────────────────────────────────────────────────────────

let navMounted = false;
let mainEl = null;
let currentNavWrapper = null;

function appendSidebarFooter(sidebar) {
  const sidebarFooter = el('div', { class: 'sidebar-footer' }, [
    el('div', { class: 'user-info', text: getEmail() }),
    el('button', { class: 'btn-logout', type: 'button', text: 'Sign Out' }),
  ]);
  sidebarFooter.querySelector('.btn-logout').addEventListener('click', logout);
  sidebar.appendChild(sidebarFooter);
}

function mountShell(initialView) {
  const appEl = document.getElementById('app');
  if (!appEl) return;
  appEl.textContent = '';

  const layout = el('div', { class: 'layout' });

  currentNavWrapper = renderNav(initialView, onNavigate);
  layout.appendChild(currentNavWrapper);

  mainEl = el('main', { class: 'main', id: 'main-content' });
  layout.appendChild(mainEl);

  appEl.appendChild(layout);

  // Persistent disclosure footer (AI-generated content + crisis resources).
  // Lives at the page level so route changes (which only touch main) keep it.
  appEl.appendChild(renderDisclosureFooter());

  const sidebar = layout.querySelector('.sidebar');
  if (sidebar) appendSidebarFooter(sidebar);

  navMounted = true;
}

function updateNav(view) {
  if (!currentNavWrapper) return;
  const layout = currentNavWrapper.parentElement;
  if (!layout) return;

  const newNav = renderNav(view, onNavigate);
  layout.replaceChild(newNav, currentNavWrapper);
  currentNavWrapper = newNav;

  const sidebar = layout.querySelector('.sidebar');
  if (sidebar && !sidebar.querySelector('.sidebar-footer')) {
    appendSidebarFooter(sidebar);
  }
}

// ── Route handler ─────────────────────────────────────────────────────────────

async function handleRoute() {
  if (!isAuthenticated()) {
    showLogin();
    return;
  }

  const { view, params } = parseRoute(location.hash);

  if (!navMounted) {
    mountShell(view);
  } else {
    updateNav(view);
  }

  if (!mainEl) return;

  mainEl.textContent = '';
  mainEl.appendChild(
    el('div', { class: 'loading' }, [
      el('span', { class: 'spinner' }),
      document.createTextNode(' Loading...'),
    ])
  );

  const viewFn = VIEW_MAP[view] || renderOverview;

  try {
    await viewFn(mainEl, params);
  } catch (err) {
    mainEl.textContent = '';
    mainEl.appendChild(
      el('div', { class: 'msg-error', text: 'Failed to load view: ' + (err.message || String(err)) })
    );
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  setUnauthorizedHandler(logout);

  // The settings view dispatches 'sf:logout' (bubbles) when the user clicks Sign Out.
  document.addEventListener('sf:logout', (e) => {
    e.stopPropagation();
    logout();
  });

  window.addEventListener('hashchange', handleRoute);

  if (!isAuthenticated()) {
    showLogin();
  } else {
    handleRoute();
  }
});
