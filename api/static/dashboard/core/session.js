// api/static/dashboard/core/session.js
const KEYS = { token: 'sf_token', parentId: 'sf_parent_id', email: 'sf_email' };

export function getToken() { return sessionStorage.getItem(KEYS.token); }
export function getParentId() { return sessionStorage.getItem(KEYS.parentId); }
export function getEmail() { return sessionStorage.getItem(KEYS.email) || ''; }
export function setEmail(email) { sessionStorage.setItem(KEYS.email, email); }

export function setSession({ token, parentId, email }) {
  sessionStorage.setItem(KEYS.token, token);
  sessionStorage.setItem(KEYS.parentId, parentId);
  sessionStorage.setItem(KEYS.email, email);
}

export function clearSession() {
  sessionStorage.removeItem(KEYS.token);
  sessionStorage.removeItem(KEYS.parentId);
  sessionStorage.removeItem(KEYS.email);
}

export function isAuthenticated() { return !!getToken(); }
