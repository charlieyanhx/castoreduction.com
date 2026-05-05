// Dynamic rendering helpers — handles unknown signal keys gracefully

export function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Render any dict as a definition list, handling nested objects and arrays */
export function renderSignals(obj, container) {
  if (!obj || typeof obj !== 'object') {
    container.textContent = String(obj);
    return;
  }
  const items = Object.entries(obj)
    .filter(([k, v]) => v !== null && v !== undefined && !k.startsWith('_'))
    .map(([k, v]) => {
      const label = k.replace(/_/g, ' ');
      let value;
      if (Array.isArray(v)) {
        value = v.length ? v.map(i => `<span class="tag">${esc(String(i))}</span>`).join(' ') : '<span class="text-zinc-500">none</span>';
      } else if (typeof v === 'object') {
        value = `<pre class="text-xs bg-zinc-900 rounded p-1 mt-1">${esc(JSON.stringify(v, null, 2))}</pre>`;
      } else if (typeof v === 'number') {
        value = `<span class="font-mono text-emerald-400">${v}</span>`;
      } else if (typeof v === 'boolean') {
        value = v ? '<span class="text-emerald-400">yes</span>' : '<span class="text-red-400">no</span>';
      } else {
        value = esc(String(v));
      }
      return `<div class="signal-row"><span class="signal-label">${esc(label)}</span>${value}</div>`;
    });
  container.innerHTML = items.join('');
}

/** Render an array as pill tags */
export function renderTags(arr, className = '') {
  if (!arr || !arr.length) return '<span class="text-zinc-500 text-sm">none</span>';
  return arr.map(t => `<span class="tag ${className}">${esc(String(t))}</span>`).join(' ');
}

/** Render a 0-100 score as a colored bar */
export function renderScoreBar(score, max = 100) {
  const pct = Math.min(100, Math.max(0, (score / max) * 100));
  let color = 'bg-red-500';
  if (pct >= 70) color = 'bg-emerald-500';
  else if (pct >= 40) color = 'bg-amber-500';
  return `
    <div class="flex items-center gap-3">
      <div class="score-bar-track"><div class="score-bar-fill ${color}" style="width:${pct}%"></div></div>
      <span class="font-bold text-lg ${pct >= 70 ? 'text-emerald-400' : pct >= 40 ? 'text-amber-400' : 'text-red-400'}">${score}</span>
    </div>
  `;
}

/** Format a timestamp to relative time */
export function timeAgo(ts) {
  const s = Math.floor((Date.now() / 1000) - ts);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
