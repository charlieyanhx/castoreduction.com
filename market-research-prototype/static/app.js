// Minimal SPA — posts form data to the API, polls job state, renders results.

const api = {
  post: async (path, body) => {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  },
  get: async (path) => {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  },
};

async function pollJob(jobId, statusEl, onDone) {
  statusEl.className = "status running";
  statusEl.textContent = `job ${jobId.slice(0, 8)}… running`;
  const start = Date.now();
  while (true) {
    const j = await api.get(`/jobs/${jobId}`);
    const elapsed = Math.floor((Date.now() - start) / 1000);
    if (j.state === "complete") {
      statusEl.className = "status complete";
      statusEl.textContent = `complete in ${elapsed}s`;
      onDone(j.result);
      refreshUsage();
      return;
    }
    if (j.state === "error") {
      statusEl.className = "status error";
      statusEl.textContent = `error: ${j.error}`;
      return;
    }
    statusEl.textContent = `${j.state} (${elapsed}s elapsed)`;
    await new Promise((r) => setTimeout(r, 1200));
  }
}

function renderOpportunities(result, container) {
  const opps = (result?.synthesis?.ranked_opportunities) || [];
  if (!opps.length) {
    container.innerHTML = `<pre>${JSON.stringify(result, null, 2)}</pre>`;
    return;
  }
  const html = opps.map((o) => `
    <div class="opportunity">
      <span class="score">${o.opportunity_score ?? "—"}</span>
      <span class="brand">${escapeHtml(o.brand || "")}</span>
      <span class="domain">${escapeHtml(o.domain || "")}</span>
      <div class="thesis">${escapeHtml(o.thesis || "")}</div>
    </div>
  `).join("");
  const density = result.competitor_density ?? "?";
  const avg = result.avg_opportunity_score ?? "?";
  container.innerHTML = `
    <p style="color:#9ca3af;font-size:0.85rem">
      ${opps.length} opportunities · density ${density} · avg score ${avg}
    </p>
    ${html}
    <details style="margin-top:1rem">
      <summary style="cursor:pointer;color:#9ca3af">raw JSON</summary>
      <pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>
    </details>
  `;
}

function renderTaste(result, container) {
  if (result.error) {
    container.innerHTML = `<pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>`;
    return;
  }
  const hooks = (result.hook_angles_that_would_work || []).map((h) => `<li>${escapeHtml(h)}</li>`).join("");
  container.innerHTML = `
    <p><strong>Motivation:</strong> ${escapeHtml(result.purchase_motivation || "—")}</p>
    <p><strong>Confidence:</strong> ${result.confidence ?? "—"} — ${escapeHtml(result.confidence_reasoning || "")}</p>
    <p><strong>Recommended hook angles:</strong></p>
    <ul>${hooks}</ul>
    <details>
      <summary style="cursor:pointer;color:#9ca3af">full profile JSON</summary>
      <pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>
    </details>
  `;
}

function renderMatch(result, container) {
  container.innerHTML = `
    <p><strong>Match score:</strong> <span class="score">${result.match_score ?? "—"}</span> / 100</p>
    <p><strong>Positioning:</strong> ${escapeHtml(result.recommended_positioning || "—")}</p>
    <details open>
      <summary style="cursor:pointer;color:#9ca3af">full match JSON</summary>
      <pre>${escapeHtml(JSON.stringify(result, null, 2))}</pre>
    </details>
  `;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function refreshUsage() {
  try {
    const u = await api.get("/usage");
    document.getElementById("usage-display").textContent = JSON.stringify(u, null, 2);
  } catch (e) {
    document.getElementById("usage-display").textContent = "usage unavailable";
  }
}

// Wire forms ----------------------------------------------------------------
function wire(formId, endpoint, buildPayload, statusId, resultsId, renderer) {
  const form = document.getElementById(formId);
  const statusEl = document.getElementById(statusId);
  const resultsEl = document.getElementById(resultsId);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button");
    btn.disabled = true;
    resultsEl.innerHTML = "";
    try {
      const payload = buildPayload(new FormData(form));
      const { job_id } = await api.post(endpoint, payload);
      await pollJob(job_id, statusEl, (result) => renderer(result, resultsEl));
    } catch (e) {
      statusEl.className = "status error";
      statusEl.textContent = `failed: ${e.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

wire(
  "discover-form", "/discover",
  (fd) => ({ category: fd.get("category"), geo: fd.get("geo") || "US" }),
  "discover-status", "discover-results", renderOpportunities,
);

wire(
  "taste-form", "/taste",
  (fd) => ({ brand: fd.get("brand"), domain: fd.get("domain") }),
  "taste-status", "taste-results", renderTaste,
);

wire(
  "match-form", "/match",
  (fd) => {
    let tp;
    try { tp = JSON.parse(fd.get("taste_profile")); }
    catch { throw new Error("taste_profile must be valid JSON"); }
    return { idea: fd.get("idea"), taste_profile: tp };
  },
  "match-status", "match-results", renderMatch,
);

refreshUsage();
setInterval(refreshUsage, 15000);
