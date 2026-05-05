import { post } from '../lib/api.js';
import { pollJob } from '../lib/poller.js';
import { esc, renderTags, renderScoreBar } from '../lib/render.js';

// Store completed taste profiles so match.js can reference them
export const tasteStore = new Map();

export function init() {
  const form = document.getElementById('taste-form');
  const status = document.getElementById('taste-status');
  const results = document.getElementById('taste-results');
  const btn = form.querySelector('button[type="submit"]');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btn.disabled = true;
    results.innerHTML = '';
    status.className = 'status-badge running';
    status.textContent = 'submitting...';

    try {
      const fd = new FormData(form);
      const brand = fd.get('brand');
      const domain = fd.get('domain');
      const { job_id } = await post('/taste', { brand, domain });

      await pollJob(job_id, {
        onTick: ({ state, elapsed_s }) => {
          status.textContent = `${state} (${elapsed_s}s)`;
        },
        onDone: (result) => {
          status.className = 'status-badge complete';
          status.textContent = 'complete';
          tasteStore.set(brand, result);
          renderTasteResult(result, results);
          window.dispatchEvent(new CustomEvent('taste-done', { detail: result }));
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

function renderTasteResult(p, container) {
  if (p.error) {
    container.innerHTML = `<div class="card"><p class="text-red-400">${esc(p.error)}</p></div>`;
    return;
  }

  const confidence = p.confidence ?? 0;
  const triggers = p.emotional_triggers || {};

  container.innerHTML = `
    <div class="card space-y-4">
      <!-- Confidence -->
      <div>
        <div class="section-label">Confidence</div>
        ${renderScoreBar(Math.round(confidence * 100))}
        <p class="text-xs text-zinc-500 mt-1">${esc(p.confidence_reasoning || '')}</p>
      </div>

      <!-- Purchase motivation -->
      ${p.purchase_motivation ? `
        <div>
          <div class="section-label">Purchase motivation</div>
          <p class="text-zinc-200">${esc(p.purchase_motivation)}</p>
        </div>
      ` : ''}

      <!-- Emotional triggers -->
      <div class="grid grid-cols-2 gap-4">
        <div>
          <div class="section-label text-emerald-400">Celebrated</div>
          <ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">
            ${(triggers.celebrated || []).map(t => `<li>${esc(t)}</li>`).join('')}
          </ul>
        </div>
        <div>
          <div class="section-label text-red-400">Complained</div>
          <ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">
            ${(triggers.complained || []).map(t => `<li>${esc(t)}</li>`).join('')}
          </ul>
        </div>
      </div>

      <!-- Tags sections -->
      <div>
        <div class="section-label">Aesthetic</div>
        ${renderTags(p.aesthetic_descriptors)}
      </div>
      <div>
        <div class="section-label">Recurring vocabulary</div>
        ${renderTags(p.vocabulary_repeats)}
      </div>
      <div>
        <div class="section-label">Adjacent brands</div>
        ${renderTags(p.adjacent_brands_mentioned, 'tag-brand')}
      </div>
      <div>
        <div class="section-label">Life context</div>
        ${renderTags(p.life_context)}
      </div>
      <div>
        <div class="section-label">Pre-purchase objections</div>
        ${renderTags(p.pre_purchase_objections, 'tag-warn')}
      </div>

      <!-- Hook angles -->
      <div>
        <div class="section-label">Hook angles that would work</div>
        <div class="space-y-2">
          ${(p.hook_angles_that_would_work || []).map((h, i) => `
            <div class="hook-card">
              <span class="hook-num">${i + 1}</span>
              <span class="text-zinc-200">${esc(h)}</span>
            </div>
          `).join('')}
        </div>
      </div>

      <!-- Evidence -->
      <div class="text-xs text-zinc-600 border-t border-zinc-800 pt-2 mt-2">
        Evidence: ${p._evidence?.trustpilot_review_count ?? 0} Trustpilot reviews,
        ${p._evidence?.reddit_post_count ?? 0} Reddit posts
      </div>
    </div>
  `;
}
