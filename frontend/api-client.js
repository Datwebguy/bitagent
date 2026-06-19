/* HTTP helpers used by BitAgent frontend modules. */

async function fetchJson(url, options = {}, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const headers = new Headers(options.headers || {});
    if (_operatorMode && _operatorToken) headers.set('X-Admin-Token', _operatorToken);
    const res = await fetch(url, {...options, headers, signal: ctrl.signal});
    let data = null;
    try { data = await res.json(); } catch(e) {}
    if (!res.ok) {
      const detail = data?.detail;
      const detailMsg = Array.isArray(detail)
        ? detail.map(d => d?.msg || JSON.stringify(d)).join('; ')
        : (typeof detail === 'string' ? detail : '');
      throw new Error(data?.error || detailMsg || `Request failed (${res.status})`);
    }
    return data ?? {};
  } finally {
    clearTimeout(timer);
  }
}
