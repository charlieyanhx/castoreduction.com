import { post } from '../lib/api.js';
import { pollJob } from '../lib/poller.js';
import { esc, renderScoreBar } from '../lib/render.js';
import { tasteStore } from './taste.js';

export function init() {
  const form = document.getElementById('match-form');
  const status = document.getElementById('match-status');
  const results = document.getElementById('match-results');
  const btn = form.querySelector('button[type="submit"]');
  const profileSelect = document.getElementById('taste-profile-select');
  const profileJson = document.getElementById('taste-profile-json');

  // Populate dropdown when taste profiles complete
  function refreshDropdown() {
    profileSelect.innerHTML = '<option value="">-- paste JSON or select --</option>';
    for (const [brand, profile] of tasteStore) {
      const opt = document.createElement('option');
      opt.value = brand;
      opt.textContent = `${brand} (confidence ${profile.confidence ?? '?'})`;
      profileSelect.appendChild(opt);
    }
  }
  window.addEventListener('taste-done', refreshDropdown);
  refreshDropdown();

  profileSelect.addEventListener('change', () => {
    const brand = profileSelect.value;
    if (brand && tasteStore.has(brand)) {
      profileJson.value = JSON.stringify(tasteStore.get(brand), null, 2);
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    btn.disabled = true;
    results.innerHTML = '';
    status.className = 'status-badge running';

    try {
      const fd = new FormData(form);
      let tp;
      try {
        tp = JSON.parse(fd.get('taste_profile'));
      } catch {
        throw new Error('Taste profile must be valid JSON');
      }
      const { job_id } = await post('/match', {
        idea: fd.get('idea'),
        taste_profile: tp,
      });

      await pollJob(job_id, {
        onTick: ({ state, elapsed_s }) => {
          status.textContent = `${state} (${elapsed_s}s)`;
        },
        onDone: (result) => {
          status.className = 'status-badge complete';
          status.textContent = 'complete';
          renderMatchResult(result, results);
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

function renderMatchResult(r, container) {
  const bd = r.score_breakdown || {};
  const breakdownBars = Object.entries(bd).map(([k, v]) => `
    <div class="mb-1">
      <div class="flex justify-between text-xs text-zinc-400 mb-0.5">
        <span>${esc(k.replace(/_/g, ' '))}</span><span>${v}/100</span>
      </div>
      ${renderScoreBar(v)}
    </div>
  `).join('');

  container.innerHTML = `
    <div class="card space-y-5">
      <!-- Big score -->
      <div class="text-center">
        <div class="text-5xl font-bold ${r.match_score >= 70 ? 'text-emerald-400' : r.match_score >= 40 ? 'text-amber-400' : 'text-red-400'}">
          ${r.match_score ?? '?'}
        </div>
        <div class="text-sm text-zinc-500 mt-1">match score</div>
      </div>

      <!-- Breakdown -->
      <div>
        <div class="section-label">Score breakdown</div>
        ${breakdownBars}
      </div>

      <!-- Positioning -->
      ${r.recommended_positioning ? `
        <div>
          <div class="section-label">Recommended positioning</div>
          <blockquote class="border-l-2 border-blue-500 pl-3 text-zinc-200 italic">${esc(r.recommended_positioning)}</blockquote>
        </div>
      ` : ''}

      <!-- Hook angles -->
      <div>
        <div class="section-label">Hook angles</div>
        <div class="space-y-2">
          ${(r.recommended_hook_angles || []).map((h, i) => `
            <div class="hook-card">
              <span class="hook-num">${i + 1}</span>
              <div>
                <div class="text-zinc-200 font-medium">${esc(typeof h === 'string' ? h : h.hook || '')}</div>
                ${h.reasoning ? `<div class="text-xs text-zinc-500 mt-0.5">${esc(h.reasoning)}</div>` : ''}
              </div>
            </div>
          `).join('')}
        </div>
      </div>

      <!-- Why / why not -->
      <div class="grid grid-cols-2 gap-4">
        <div>
          <div class="section-label text-emerald-400">Why it matches</div>
          <ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">
            ${(r.why_it_matches || []).map(t => `<li>${esc(t)}</li>`).join('')}
          </ul>
        </div>
        <div>
          <div class="section-label text-red-400">Why it might not</div>
          <ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">
            ${(r.why_it_might_not || []).map(t => `<li>${esc(t)}</li>`).join('')}
          </ul>
        </div>
      </div>

      <!-- Proof points -->
      ${(r.required_proof_points?.length) ? `
        <div>
          <div class="section-label">Required proof points</div>
          <ul class="list-disc list-inside text-sm text-zinc-300 space-y-1">
            ${r.required_proof_points.map(t => `<li>${esc(t)}</li>`).join('')}
          </ul>
        </div>
      ` : ''}

      <!-- Offer shape -->
      ${r.offer_shape_suggestion ? `
        <div>
          <div class="section-label">Offer shape</div>
          <p class="text-zinc-300">${esc(r.offer_shape_suggestion)}</p>
        </div>
      ` : ''}
    </div>
  `;
}
