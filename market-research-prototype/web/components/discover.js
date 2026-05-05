import { post } from '../lib/api.js';
import { pollJob } from '../lib/poller.js';
import { esc, renderSignals, renderScoreBar, renderTags } from '../lib/render.js';

export function init() {
  const form = document.getElementById('discover-form');
  const status = document.getElementById('discover-status');
  const results = document.getElementById('discover-results');
  const btn = form.querySelector('button[type="submit"]');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btn.disabled = true;
    results.innerHTML = '';
    status.className = 'status-badge running';
    status.textContent = 'submitting...';

    try {
      const fd = new FormData(form);
      const { job_id } = await post('/discover', {
        category: fd.get('category'),
        geo: fd.get('geo') || 'US',
      });

      await pollJob(job_id, {
        onTick: ({ state, elapsed_s }) => {
          status.textContent = `${state} (${elapsed_s}s)`;
        },
        onDone: (result) => {
          status.className = 'status-badge complete';
          status.textContent = 'complete';
          renderDiscoverResult(result, results);
          // Dispatch event so other panels can use the data
          window.dispatchEvent(new CustomEvent('discover-done', { detail: result }));
        },
        onError: (err) => {
          status.className = 'status-badge error';
          status.textContent = `error: ${err}`;
        },
      });
    } catch (e) {
      status.className = 'status-badge error';
      status.textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  });
}

function renderDiscoverResult(result, container) {
  const synth = result.synthesis || {};
  const opps = synth.ranked_opportunities || [];

  let html = '';

  // Category read
  if (synth.category_read) {
    html += `<div class="card mb-4"><p class="text-zinc-300">${esc(synth.category_read)}</p></div>`;
  }

  // Meta stats
  html += `
    <div class="flex gap-4 mb-4 text-sm text-zinc-400">
      <span>Density: <strong class="text-zinc-200">${result.competitor_density ?? '?'}</strong></span>
      <span>Avg score: <strong class="text-zinc-200">${result.avg_opportunity_score ?? '?'}</strong></span>
      <span>Method: <strong class="text-zinc-200">${result.brand_extraction_method ?? '?'}</strong></span>
    </div>
  `;

  // Opportunities
  if (!opps.length) {
    html += '<p class="text-zinc-500">No opportunities found.</p>';
  }
  for (const o of opps) {
    const score = o.opportunity_score ?? o.signals?.trend_slope ?? '?';
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
          <div class="signals-grid mt-2"></div>
        </details>
      </div>
    `;
  }

  container.innerHTML = html;

  // Render signals into each card's details
  const cards = container.querySelectorAll('.opp-card');
  cards.forEach((card, i) => {
    const sigGrid = card.querySelector('.signals-grid');
    if (sigGrid && opps[i]?.signals) {
      renderSignals(opps[i].signals, sigGrid);
    }
    // Click card → populate taste form
    card.addEventListener('click', (e) => {
      if (e.target.closest('a, details, summary')) return;
      const brand = card.dataset.brand;
      const domain = card.dataset.domain;
      if (brand && domain) {
        const bf = document.querySelector('#taste-form input[name="brand"]');
        const df = document.querySelector('#taste-form input[name="domain"]');
        if (bf) bf.value = brand;
        if (df) df.value = domain;
        document.getElementById('taste-panel')?.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });
}
