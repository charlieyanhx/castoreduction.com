import { get } from '../lib/api.js';

export function init() {
  const el = document.getElementById('usage-display');
  async function refresh() {
    try {
      const u = await get('/usage');
      el.textContent = `${u.calls} calls · ${u.input_tokens + u.output_tokens} tokens · $${u.usd.toFixed(4)}`;
    } catch {
      el.textContent = '—';
    }
  }
  refresh();
  setInterval(refresh, 10000);
}
