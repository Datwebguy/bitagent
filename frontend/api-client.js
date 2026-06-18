/* HTTP helpers used by BitAgent frontend modules. */

async function fetchJson(url, options = {}, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers = new Headers(options.headers || {});
    if (_operatorToken) headers.set('X-Admin-Token', _operatorToken);
    const res = await fetch(url, {...options, headers, signal: ctrl.signal});
    let data = null;
    try { data = await res.json(); } catch(e) {}
    if (!res.ok) {
      throw new Error(data?.error || data?.detail || `Request failed (${res.status})`);
    }
    return data ?? {};
  } finally {
    clearTimeout(timer);
  }
}
