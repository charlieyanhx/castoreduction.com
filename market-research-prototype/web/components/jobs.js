import { get } from '../lib/api.js';
import { esc, timeAgo, renderSignals, renderScoreBar, renderTags } from '../lib/render.js';

let currentJobId = null;

export function init() {
  const list = document.getElementById('jobs-list');

  async function refresh() {
    try {
      const jobs = await get('/jobs?limit=30');
      renderJobList(jobs, list);
    } catch (e) {
      list.innerHTML = `<p class="text-red-400 text-xs">${esc(e.message)}</p>`;
    }
  }

  refresh();
  setInterval(refresh, 5000);
}

function renderJobList(jobs, listEl) {
  if (!jobs.length) {
    listEl.innerHTML = '<p class="text-zinc-600 text-sm">No jobs yet</p>';
    return;
  }

  listEl.innerHTML = jobs.map(j => {
    const stateClass = j.state === 'complete' ? 'text-emerald-400'
      : j.state === 'error' ? 'text-red-400'
      : j.state === 'running' ? 'text-amber-400'
      : 'text-zinc-500';
    const active = j.id === currentJobId ? 'bg-zinc-800' : '';
    return `
      <div class="job-row ${active}" data-job-id="${esc(j.id)}" data-kind="${esc(j.kind)}">
        <span class="font-mono text-xs text-zinc-500">${esc(j.id.slice(0, 8))}</span>
        <span class="text-sm text-zinc-300">${esc(j.kind)}</span>
        <span class="text-xs ${stateClass}">${esc(j.state)}</span>
        <span class="text-xs text-zinc-600">${timeAgo(j.created_at)}</span>
      </div>
    `;
  }).join('');

  listEl.querySelectorAll('.job-row').forEach(row => {
    row.addEventListener('click', async () => {
      const id = row.dataset.jobId;
      const kind = row.dataset.kind;
      currentJobId = id;
      listEl.querySelectorAll('.job-row').forEach(r => r.classList.remove('bg-zinc-800'));
      row.classList.add('bg-zinc-800');

      try {
        const j = await get(`/jobs/${id}`);
        if (j.state === 'complete' && j.result) {
          showResultInMainPanel(kind, j.result);
        } else if (j.state === 'error') {
          showError(kind, j.error);
        }
      } catch (e) {
        console.error('Failed to load job', e);
      }
    });
  });
}

/** Switch to the right tab and render the result in the main panel */
function showResultInMainPanel(kind, result) {
  // Map job kind → tab name + results container
  const mapping = {
    discover: { tab: 'discover', container: 'discover-results', status: 'discover-status' },
    taste:    { tab: 'taste',    container: 'taste-results',    status: 'taste-status' },
    match:    { tab: 'match',    container: 'match-results',    status: 'match-status' },
    full:     { tab: 'discover', container: 'discover-results', status: 'discover-status' },
  };
  const m = mapping[kind];
  if (!m) return;

  // Switch tab
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === m.tab);
  });
  document.querySelectorAll('.tab-panel').forEach(p => {
    p.classList.toggle('hidden', p.id !== `${m.tab}-panel`);
  });

  // Set status
  const statusEl = document.getElementById(m.status);
  if (statusEl) {
    statusEl.className = 'status-badge complete';
    statusEl.textContent = 'complete (loaded from history)';
  }

  // Render into the results container
  const container = document.getElementById(m.container);
  if (!container) return;

  if (kind === 'discover' || kind === 'full') {
    const data = kind === 'full' ? (result.discover || result) : result;
    renderDiscoverInline(data, container);
  } else if (kind === 'taste') {
    renderTasteInline(result, container);
  } else if (kind === 'match') {
    renderMatchInline(result, container);
  }
}

function showError(kind, error) {
  const mapping = { discover: 'discover-results', taste: 'taste-results', match: 'match-results', full: 'discover-results' };
  const container = document.getElementById(mapping[kind]);
  if (container) {
    container.innerHTML = `<div class="card"><p class="text-red-400">${esc(error || 'unknown error')}</p></div>`;
  }
}

// --- Inline renderers (duplicated from discover/taste/match components for sidebar-click use) ---

function renderDiscoverInline(result, container) {
  const synth = result.synthesis || {};
  const opps = synth.ranked_opportunities || [];
  let html = '';

  if (synth.category_read) {
    html += `<div class="card mb-4"><p class="text-zinc-300">${esc(synth.category_read)}</p></div>`;
  }

  html += `
    <div class="flex gap-4 mb-4 text-sm text-zinc-400">
      <span>Density: <strong class="text-zinc-200">${result.competitor_density ?? '?'}</strong></span>
      <span>Avg score: <strong class="text-zinc-200">${result.avg_opportunity_score ?? '?'}</strong></span>
      <span>Method: <strong class="text-zinc-200">${result.brand_extraction_method ?? '?'}</strong></span>
    </div>
  `;

  if (!opps.length) {
    html += '<p class="text-zinc-500">No opportunities found.</p>';
  }
  for (const o of opps) {
    const score = o.opportunity_score ?? 0;
    html += `
      <div class="opp-card" data-brand="${esc(o.brand || '')}" data-domain="${esc(o.domain || '')}">
        <div class="flex items-start justify-between mb-2">
          <div>
            <span class="text-lg font-semibold text-zinc-100">${esc(o.brand || '?')}</span>
            ${o.domain ? `<a href="https://${esc(o.domain)}" target="_blank" class="ml-2 text-sm text-blue-400 hover:underline">${esc(o.domain)}</a>` : ''}
          </div>
          <div class="text-right">${renderScoreBar(typeof score === 'number' ? score : 0)}</div>
        </div>
        <p class="text-sm text-zinc-300 mb-3">${esc(o.thesis || '')}</p>
        ${o.suggested_next_step ? `<span class="tag tag-action">${esc(o.suggested_next_step)}</span>` : ''}
        <details class="mt-3">
          <summary class="text-xs text-zinc-500 cursor-pointer hover:text-zinc-300">signals</summary>
          <div class="signals-grid mt-2" id="sig-${esc(o.brand || '')}"></div>
        </details>
      </div>
    `;
  }

  container.innerHTML = html;

  // Render signals
  opps.forEach(o => {
    const el = document.getElementById(`sig-${o.brand || ''}`);
    if (el && o.signals) renderSignals(o.signals, el);
  });

  // Click to fill taste form
  container.querySelectorAll('.opp-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('a, details, summary')) return;
      const brand = card.dataset.brand;
      const domain = card.dataset.domain;
      if (brand && domain) {
        const bf = document.querySelector('#taste-form input[name="brand"]');
        const df = document.querySelector('#taste-form input[name="domain"]');
        if (bf) bf.value = brand;
        if (df) df.value = domain;
        // Switch to taste tab
        document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'taste'));
        document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('hidden', p.id !== 'taste-panel'));
      }
    });
  });
}

function renderTasteInline(p, container) {
  if (p.error) {
    container.innerHTML = `<div class="card"><p class="text-red-400">${esc(p.error)}</p></div>`;
    return;
  }
  const confidence = p.confidence ?? 0;
  const triggers = p.emotional_triggers || {};
  container.innerHTML = `
    <div class="card space-y-4">
      <div>
        <div class="section-label">Confidence</div>
        ${renderScoreBar(Math.round(confidence * 100))}
        <p class="text-xs text-zinc-500 mt-1">${esc(p.confidence_reasoning || '')}</p>
      </div>
      ${p.purchase_motivation ? `<div><div class="section-label">Purchase motivation</div><p class="text-zinc-200">${esc(p.purchase_motivation)}</p></div>` : ''}
      <div class="grid grid-cols-2 gap-4">
        <div><div class="section-label text-emerald-400">Celebrated</div><ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">${(triggers.celebrated || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>
        <div><div class="section-label text-red-400">Complained</div><ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">${(triggers.complained || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>
      </div>
      <div><div class="section-label">Hook angles</div><div class="space-y-2">${(p.hook_angles_that_would_work || []).map((h, i) => `<div class="hook-card"><span class="hook-num">${i + 1}</span><span class="text-zinc-200">${esc(h)}</span></div>`).join('')}</div></div>
      <div><div class="section-label">Adjacent brands</div>${renderTags(p.adjacent_brands_mentioned, 'tag-brand')}</div>
      <div><div class="section-label">Life context</div>${renderTags(p.life_context)}</div>
    </div>
  `;
}

function renderMatchInline(r, container) {
  const bd = r.score_breakdown || {};
  const bars = Object.entries(bd).map(([k, v]) => `<div class="mb-1"><div class="flex justify-between text-xs text-zinc-400 mb-0.5"><span>${esc(k.replace(/_/g, ' '))}</span><span>${v}/100</span></div>${renderScoreBar(v)}</div>`).join('');
  container.innerHTML = `
    <div class="card space-y-5">
      <div class="text-center">
        <div class="text-5xl font-bold ${r.match_score >= 70 ? 'text-emerald-400' : r.match_score >= 40 ? 'text-amber-400' : 'text-red-400'}">${r.match_score ?? '?'}</div>
        <div class="text-sm text-zinc-500 mt-1">match score</div>
      </div>
      <div><div class="section-label">Score breakdown</div>${bars}</div>
      ${r.recommended_positioning ? `<div><div class="section-label">Positioning</div><blockquote class="border-l-2 border-blue-500 pl-3 text-zinc-200 italic">${esc(r.recommended_positioning)}</blockquote></div>` : ''}
      <div><div class="section-label">Hook angles</div><div class="space-y-2">${(r.recommended_hook_angles || []).map((h, i) => `<div class="hook-card"><span class="hook-num">${i + 1}</span><div><div class="text-zinc-200 font-medium">${esc(typeof h === 'string' ? h : h.hook || '')}</div>${h.reasoning ? `<div class="text-xs text-zinc-500 mt-0.5">${esc(h.reasoning)}</div>` : ''}</div></div>`).join('')}</div></div>
      <div class="grid grid-cols-2 gap-4">
        <div><div class="section-label text-emerald-400">Why it matches</div><ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">${(r.why_it_matches || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>
        <div><div class="section-label text-red-400">Why it might not</div><ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">${(r.why_it_might_not || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>
      </div>
    </div>
  `;
}
