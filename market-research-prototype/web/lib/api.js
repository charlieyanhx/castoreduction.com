// API client — thin fetch wrapper with error normalization
const BASE = '';

export async function post(path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`POST ${path} → ${r.status}: ${text.slice(0, 200)}`);
  }
  return r.json();
}

export async function get(path) {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) {
    const text = await r.text().catch(() => '');
    throw new Error(`GET ${path} → ${r.status}: ${text.slice(0, 200)}`);
  }
  return r.json();
}
