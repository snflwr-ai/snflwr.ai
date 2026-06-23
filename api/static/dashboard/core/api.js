export function getCsrfToken() {
  const m = document.cookie.match(/csrf_token=([^;]+)/);
  return m ? m[1] : '';
}

export function apiRequest(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  if (method !== 'GET') opts.headers['X-CSRF-Token'] = getCsrfToken();
  return fetch(path, opts).then((resp) => {
    if (!resp.ok) {
      return resp.json().then(
        (d) => { throw { status: resp.status, detail: d && d.detail }; },
        () => { throw { status: resp.status, detail: null }; }
      );
    }
    return resp.status === 204 ? null : resp.json();
  });
}
