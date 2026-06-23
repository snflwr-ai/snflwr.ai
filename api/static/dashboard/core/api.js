// api/static/dashboard/core/api.js
import { getToken, clearSession } from './session.js';

let _onUnauthorized = null;
export function setUnauthorizedHandler(fn) { _onUnauthorized = fn; }

export function getCsrfToken() {
  const m = document.cookie.match(/csrf_token=([^;]+)/);
  return m ? m[1] : '';
}

const CSRF_METHODS = ['POST', 'PATCH', 'DELETE', 'PUT'];

export function apiRequest(method, path, body) {
  const headers = {
    'Authorization': 'Bearer ' + (getToken() || ''),
    'Content-Type': 'application/json',
  };
  if (CSRF_METHODS.indexOf(method.toUpperCase()) !== -1) {
    headers['X-CSRF-Token'] = getCsrfToken();
  }
  const opts = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);
  return fetch(path, opts).then((resp) => {
    if (resp.status === 401) {
      clearSession();
      if (_onUnauthorized) _onUnauthorized();
      return Promise.reject(new Error('Session expired'));
    }
    if (!resp.ok) {
      return resp.json().then(
        (d) => { throw { status: resp.status, detail: d && d.detail }; },
        () => { throw { status: resp.status, detail: null }; }
      );
    }
    return resp.status === 204 ? null : resp.json();
  });
}
