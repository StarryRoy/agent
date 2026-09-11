// Override before loading the page with ?api=http://127.0.0.1:8000.
const configured = new URLSearchParams(location.search).get('api');
export const API_BASE = (configured || 'http://127.0.0.1:8000').replace(/\/$/, '');

export async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}/api/v1${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(typeof detail === 'string' ? detail : `请求失败（HTTP ${response.status}），请检查输入。`);
  }
  return body;
}

export const submit = text => request('/sessions', { method: 'POST', body: JSON.stringify({ text }) });
export const session = id => request(`/sessions/${encodeURIComponent(id)}`);
export const act = (id, action, text) => request(`/sessions/${encodeURIComponent(id)}/${action}`, {
  method: 'POST', ...(text === undefined ? {} : { body: JSON.stringify({ text }) }),
});
export const trace = id => request(`/sessions/${encodeURIComponent(id)}/trace`);

export function observe(id, callbacks) {
  const stream = new EventSource(`${API_BASE}/api/v1/sessions/${encodeURIComponent(id)}/events`);
  for (const kind of ['progress', 'snapshot', 'reset']) {
    stream.addEventListener(kind, event => callbacks[kind]?.(JSON.parse(event.data)));
  }
  stream.addEventListener('done', () => { stream.close(); callbacks.done?.(); });
  stream.onerror = () => callbacks.error?.();
  return () => stream.close();
}
