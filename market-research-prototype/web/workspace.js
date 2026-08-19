/* Castor Workspace — 3-zone agentic UI (Manus-parity build).
   Left: report list · Center: chat intake + conversation · Right: Castor Computer
   (live step stream → report viewer + replay scrubber). Vanilla JS, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);

// Cloudflare-tunnel blips return 502/503/504 (origin briefly unreachable) — these
// mean the request never completed at the origin, so they're safe to retry. We also
// retry network errors ("Failed to fetch") for everything except POST /plan (the one
// non-idempotent create, where a retry could double-launch a job).
const RETRY_STATUS = new Set([502, 503, 504]);
const _sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const api = async (method, path, body, timeoutMs = 90000, _attempt = 0) => {
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), timeoutMs);
  const opt = { method, headers: { "Content-Type": "application/json" }, signal: ctrl.signal };
  if (body) opt.body = JSON.stringify(body);
  const idempotent = !(method === "POST" && path === "/plan");
  try {
    const r = await fetch(path, opt);
    if (RETRY_STATUS.has(r.status) && _attempt < 3) {
      await _sleep(500 * (_attempt + 1));                       // 0.5s, 1s, 1.5s
      return api(method, path, body, timeoutMs, _attempt + 1);
    }
    if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`);
    return r.json();
  } catch (e) {
    // Network-level failure (TypeError "Failed to fetch") — retry if safe.
    if (e && e.name === "TypeError" && idempotent && _attempt < 3) {
      await _sleep(500 * (_attempt + 1));
      return api(method, path, body, timeoutMs, _attempt + 1);
    }
    throw e;
  } finally { clearTimeout(to); }
};

// Canonical pipeline order → human labels (drives the live timeline + scrubber).
const STEPS = [
  ["profile", "Company profile"],
  ["discover", "Competitor discovery"],
  ["firmographics", "Firmographic enrichment"],
  ["clustering", "Competitive clustering"],
  ["differentiators", "Differentiators & gaps"],
  ["customer_universe", "Customer universe"],
  ["multi_source_signal", "Customer voice"],
  ["competitor_pricing", "Competitor pricing"],
  ["consumer_research", "Consumer research (perspectives)"],
  ["max_diff", "Feature importance (Max-Diff)"],
  ["pricing", "Pricing simulation (Van Westendorp)"],
  ["economics", "Unit economics"],
  ["place", "Channel strategy"],
  ["market_scale", "Market-scale classification"],
  ["market_sizing", "Market sizing — triangulated"],
  ["segment_ranking", "Segment prioritization"],
  ["four_ps", "4Ps marketing plan"],
  ["financials", "3-year projections"],
  ["validation", "Validation gate"],
  ["viability", "Viability score"],
];

let session = null;        // intake session id
let extracted = {};        // intake fields
let activeJob = null;      // current job id
let pollTimer = null;
let lastSteps = [];        // completed step keys for the active job
let lastResult = {};       // partial job result — drives live evidence detail
let compTab = "steps";     // "steps" | "report"
let lastSince = 0;         // cursor into the job's live event stream
let liveEvent = null;      // most recent run event — drives the live activity label

// Make the backend rigor FELT: turn a completed step into a one-line evidence detail
// pulled from the live result (Census count, scraped price, triangulation, gate…).
function _usd(n) {
  if (typeof n !== "number") return "?";
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(0) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
  return "$" + Math.round(n);
}
function stepDetail(key, r) {
  if (!r) return "";
  const ms = r.market_sizing || {}, tam = ms.tam || {};
  if (key === "discover") {
    const c = (r.discover || {}).ranked_opportunities || (r.discover || {}).competitors || [];
    return c.length ? c.length + " competitors found" : "";
  }
  if (key === "competitor_pricing") {
    const n = (r.competitor_pricing || {}).competitors_with_prices;
    return n ? n + " competitor prices scraped" : "";
  }
  if (key === "customer_universe") {
    const n = ((r.customer_universe || {}).segments || []).length;
    return n ? n + " target segments" : "";
  }
  if (key === "consumer_research") {
    const syn = (r.consumer_research || {}).synthesis || {}, wtp = syn.willingness_to_pay || {};
    return syn.n_segments ? syn.n_segments + " segments · WTP $" + (wtp.median || "?") + "/mo" : "";
  }
  if (key === "market_sizing") {
    const tri = tam.triangulation || {};
    if (tam.mid) {
      const ni = tri.n_independent || 1;
      const grounded = (tri.cross_origin || []).some((o) => ["census", "bls"].includes(o.origin));
      return "TAM " + _usd(tam.mid) + " · " + ni + " independent source" + (ni !== 1 ? "s" : "") +
             (grounded ? " · Census-grounded" : "");
    }
    return "";
  }
  if (key === "validation") {
    const v = ms.validation;
    if (!v) return "";
    return v.passed ? "integrity gate: passed" : "gate: " + (v.blocks || []).length + " blocking issue(s)";
  }
  if (key === "viability") {
    const s = (r.viability || {}).viability_score;
    return s ? "viability " + s + "/100" : "";
  }
  return "";
}

/* ---------------- conversation ---------------- */
function addMsg(role, text, why) {
  const m = document.createElement("div");
  m.className = `msg ${role}`;
  m.innerHTML = `<div class="av">${role === "bot" ? "C" : "you"}</div>` +
    `<div class="bubble-wrap"><div class="bubble"></div></div>`;
  m.querySelector(".bubble").textContent = text;
  if (why) {
    // The reason under the question — "confirm your location" is a chore, "this sets the
    // ring we count competitors in" is a reason to answer carefully.
    const w = document.createElement("div");
    w.className = "bubble-why";
    w.textContent = "→ decides " + why;
    m.querySelector(".bubble-wrap").appendChild(w);
  }
  $("convo").appendChild(m);
  $("convo").scrollTop = $("convo").scrollHeight;
}

const FIELD_LABELS = {
  product: "Product", target_customer: "Target customer",
  business_model: "Business model", geography: "Geography",
  pricing: "Pricing", differentiation: "Differentiation", stage: "Stage",
};
const REQUIRED = ["product", "target_customer", "business_model", "geography"];

function renderFields() {
  /* The chips are the venture's OWN question pack (a cafe shows capacity/site/rent, a
     SaaS shows the per-seat question) — the server sends them per turn. Three states:
     open, answered, and "assumed" for a 'not sure', which the report will label. The
     hardcoded list remains only for the pre-first-message render. */
  if (window._treeFields && window._treeFields.length) {
    $("fields").innerHTML = window._treeFields.map((f) => {
      const cls = f.state === "done" ? "done" : (f.state === "assumed" ? "assumed" : "");
      const mark = f.state === "done" ? "✓ " : (f.state === "assumed" ? "≈ " : "");
      return `<span class="chip ${cls}" title="${f.state === "assumed"
        ? "you said you weren't sure — the report will estimate this and label it"
        : ""}">${mark}${f.label}</span>`;
    }).join("");
  } else {
    $("fields").innerHTML = Object.entries(FIELD_LABELS).map(([k, lbl]) => {
      const done = extracted[k] && String(extracted[k]).toLowerCase() !== "null";
      return `<span class="chip ${done ? "done" : ""}">${done ? "✓ " : ""}${lbl}</span>`;
    }).join("");
  }
  /* Server-side ready, not the old local 4-field rule: the tree can still have open
     questions (capacity, competitors, costs) after a verbose first message fills all four
     generic fields — measured live, the card appeared after ONE message while eight pack
     questions were still open, burying the interview it exists to conclude. */
  const ready = !!window._serverReady;
  // Ready is NOT the same as confirmed. The required fields can all be filled and still
  // point the run at the wrong neighbourhood — measured, "San Francisco" satisfies the
  // geography field and geocodes 2.3 km from the Mission, against a 1.5 km trade-area ring.
  // The Generate button waits for the operator to look at the two answers that decide the
  // numbers.
  $("launchBtn").disabled = !(ready && window._confirmed);
  if (ready && !window._rigorShown) {
    window._rigorShown = true;
    addMsg("bot", "Got what I need. When you generate, I'll size the market from real " +
      "data — Census business counts, BLS spending, and live competitor pricing I scrape — " +
      "triangulate each figure across independent sources, validate every number, and " +
      "flag (or withhold) anything I can't stand behind.");
    showConfirmation();
  }
}

/* ---------------------------------------------------------------- confirmation card ---
   The one stop before ~6 minutes of live research. Only the answers whose VALUE changes a
   published number appear here; confirming eight fields would train people to click
   through the two that matter. Each row says what it drives, because "confirm your
   location" is a chore and "this sets the ring we count competitors in" is a reason to
   read it. */
async function showConfirmation() {
  let payload;
  try {
    payload = await api("GET", `/intake/${session}/confirmation`);
  } catch (e) {
    // Never strand the operator behind a card that failed to load.
    window._confirmed = true; renderFields(); return;
  }
  const PROV = {stated: ["said by you", "prov-stated"],
                inferred: ["worked out from your answers", "prov-inferred"],
                assumed: ["will be estimated + labelled", "prov-assumed"]};
  const rows = (payload.items || []).map((it) => {
    const [ptext, pcls] = PROV[it.provenance] || ["", ""];
    return `
    <div class="cf-row ${it.precise ? "" : "warn"}">
      <div class="cf-head">
        <span class="cf-label">${it.label}</span>
        ${ptext ? `<span class="cf-prov ${pcls}">${ptext}</span>` : ""}
        <span class="cf-state">${it.precise ? "✓" : "check this"}</span>
      </div>
      <input class="cf-input" data-field="${it.field}"
             value="${(it.value || "").replace(/"/g, "&quot;")}"
             placeholder="${it.ask}" aria-label="${it.label}">
      <div class="cf-why">Sets ${it.drives}</div>
      ${it.warning ? `<div class="cf-warn">${it.warning}</div>` : ""}
    </div>`; }).join("");
  const card = document.createElement("div");
  card.className = "confirm-card";
  card.id = "confirmCard";
  card.innerHTML = `
    <div class="cf-title">Before I spend six minutes on this</div>
    <div class="cf-sub">These decisions shape every number. Anything marked
    "worked out" is my inference — correct it if I got it wrong.</div>
    ${rows}
    <button class="cf-go" id="confirmBtn">Looks right — continue</button>`;
  $("convo").appendChild(card);
  $("convo").scrollTop = $("convo").scrollHeight;
  $("confirmBtn").addEventListener("click", async () => {
    const btn = $("confirmBtn");
    btn.disabled = true; btn.textContent = "Saving…";
    const corrections = {};
    card.querySelectorAll(".cf-input").forEach((el) => {
      const v = el.value.trim();
      if (v) corrections[el.dataset.field] = v;
    });
    try {
      const res = await api("POST", `/intake/${session}/confirm`, { corrections });
      Object.assign(extracted, corrections);
      // The description the run will actually receive, rebuilt server-side from the
      // corrected answers. Without this the card could not fix the field it exists for.
      if (res && res.final_description) window._serverDescription = res.final_description;
    } catch (e) { /* confirmation is a checkpoint, not a gate that can strand you */ }
    window._confirmed = true;
    card.classList.add("done");
    btn.textContent = "Confirmed ✓";
    addMsg("bot", "Locked in. Hit Generate when you're ready.");
    renderFields();
  });
}

/* The SERVER owns this string now. It used to be assembled here as well, identically and
   identically wrongly: both emitted "Geography: <place>." and plan.extract_location needs a
   prepositional phrase, so it returned None on every description the chat ever produced —
   and without a location size_by_scale returns None (no trade-area sizing at all) while
   geo_competitor_opps returns [] (no local competitors). A neighbourhood cafe silently got
   national sizing.

   Two builders for one fact is how that drifted unnoticed. `_serverDescription` is
   whatever the confirm step rebuilt from the corrected answers; the local assembly stays
   only as a fallback for the path where confirmation never happened. */
function buildDescription() {
  if (window._serverDescription) return window._serverDescription;
  const e = extracted;
  const parts = [
    e.product, e.target_customer && `For ${e.target_customer}.`,
    e.business_model, e.geography && `Located in ${e.geography}.`,
    e.pricing && `Pricing: ${e.pricing}.`,
    e.differentiation && `Differentiator: ${e.differentiation}.`,
  ].filter((x) => x && String(x).toLowerCase() !== "null");
  return parts.join(" ");
}

function startIntake() {
  // Render instantly; the LLM session is created lazily on first send so the
  // page never hangs on a slow/rate-limited intake call.
  resetCenter();
  session = null; extracted = {}; window._rigorShown = false;
  window._serverReady = false; window._treeFields = null; window._confirmed = false;
  addMsg("bot", "Hi — I'll put together a market-research report. In a sentence or two, what does your product do and who is it for?");
  renderFields();
}

async function sendMessage() {
  const text = $("input").value.trim();
  if (!text) return;
  $("input").value = ""; addMsg("user", text);
  $("sendBtn").disabled = true;
  try {
    if (!session) {                                  // lazy session init
      const s = await api("POST", "/intake/start", {});
      session = s.session_id;
    }
    const r = await api("POST", "/intake/message", { session_id: session, user_message: text });
    extracted = r.extracted || extracted;
    window._treeFields = r.tree_fields || window._treeFields;
    window._serverReady = !!r.ready;
    addMsg("bot", r.assistant_message, r.asked_why);
    setNotSureVisible(!!r.asked_field && !r.ready);
    renderFields();
  } catch (e) {
    addMsg("bot", "⚠ " + (e.name === "AbortError" ? "the model is busy (rate limit) — try again in a moment" : e.message));
  }
  $("sendBtn").disabled = false; $("input").focus();
}

/* "Not sure" is a first-class answer: it records an assumption the report must label,
   instead of stalling a founder who genuinely doesn't know their rent yet. One click
   sends the literal phrase the server's not-sure detector reads. */
function setNotSureVisible(on) {
  const b = $("notSureBtn");
  if (b) b.style.display = on ? "" : "none";
}

async function launch() {
  const description = buildDescription();
  if (!description) return;
  $("launchBtn").disabled = true; $("launchBtn").textContent = "Starting…";
  try {
    const r = await api("POST", "/plan", { description, operator_weights: {} });
    addMsg("bot", "Report started — watch it build in the Castor Computer →");
    openJob(r.job_id, true);
    loadTasks();
  } catch (e) {
    addMsg("bot", "⚠ Failed to start: " + e.message);
    $("launchBtn").disabled = false; $("launchBtn").textContent = "Generate report →";
  }
}

/* ---------------- Castor Computer (right panel) ---------------- */
function setTabs(showReport) {
  $("compTabs").innerHTML =
    `<span class="tab ${compTab === "steps" ? "on" : ""}" data-t="steps">Steps</span>` +
    (showReport ? `<span class="tab ${compTab === "report" ? "on" : ""}" data-t="report">Report</span>` : "");
  $("compTabs").querySelectorAll(".tab").forEach((el) =>
    el.onclick = () => { compTab = el.dataset.t; renderComputer(); });
}

function renderTimeline(uptoIdx) {
  // uptoIdx: render done up to this many steps (for replay); default = lastSteps length.
  const doneSet = new Set(lastSteps);
  const cut = uptoIdx == null ? STEPS.length : uptoIdx;
  const running = activeJobState === "running";
  let firstPending = -1;
  const rows = STEPS.map(([key, lbl], i) => {
    const isDone = doneSet.has(key) && i < cut;
    if (!isDone && firstPending < 0) firstPending = i;
    const active = running && i === firstPending && uptoIdx == null;
    const cls = isDone ? "done" : active ? "active" : "pending";
    const mk = isDone ? "✓" : active ? "•" : "";
    const detail = isDone ? stepDetail(key, lastResult) : "";
    return `<div class="step ${cls}"><div class="mk">${mk}</div>` +
      `<div><div class="lbl">${lbl}</div>` +
      (detail ? `<div class="lbl-detail">${detail}</div>` : "") + `</div></div>`;
  }).join("");
  $("compBody").innerHTML =
    `<div class="steps-list">${rows}</div>` +
    `<div class="wire" id="wire"></div>`;
  renderWire();
}

let activeJobState = "idle";
function renderComputer() {
  setTabs(activeJobState === "complete");
  if (compTab === "report" && activeJob) {
    $("compBody").innerHTML = `<iframe class="report-frame" src="/jobs/${activeJob}/report.html"></iframe>`;
    $("compBody").style.padding = "0";
  } else {
    $("compBody").style.padding = "18px 20px";
    renderTimeline();
  }
}

function openJob(jobId, fresh) {
  activeJob = jobId; lastSteps = []; lastResult = {}; activeJobState = "running"; compTab = "steps";
  lastSince = 0; liveEvent = null; eventBuf = [];   // fresh cursor into this job's event stream
  $("compStatus").innerHTML = `<span class="live-dot">working</span>`;
  $("scrubber").classList.remove("on");
  renderComputer();
  if (pollTimer) clearInterval(pollTimer);
  pollJob();
  pollTimer = setInterval(pollJob, 2500);
  document.querySelectorAll(".task").forEach((t) =>
    t.classList.toggle("active", t.dataset.id === jobId));
}

// Wave 3 item 3 (R5): pull the run's newest events and turn the latest one into a
// human label. Cursor-based (`since`) so each poll only ships what's new. Failures are
// silent — this is a nicety on top of the step counter, never a reason to break polling.
let eventBuf = [];                 // the run's whole stream, in arrival order
const EVENT_BUF_MAX = 800;         // a full run is ~530 events; cap defends against runaways

async function pollLiveActivity() {
  try {
    const ev = await api("GET", `/jobs/${activeJob}/events?since=${lastSince}`);
    if (typeof ev.next_since === "number") lastSince = ev.next_since;
    if (ev.events && ev.events.length) {
      liveEvent = ev.events[ev.events.length - 1];
      /* The panel's own tagline promises "every source I open, every figure I compute,
         every check I run — live, in the order it happens." The server flushes exactly
         that stream per event; keeping only the last one reduced it to a ticker. Buffer
         it all — the wire below the step list renders it. */
      eventBuf.push(...ev.events);
      if (eventBuf.length > EVENT_BUF_MAX) eventBuf = eventBuf.slice(-EVENT_BUF_MAX);
    }
  } catch { /* keep the last known label */ }
  return liveActivityLabel(liveEvent);
}

/* One event -> one wire line. Tools are the sources opened, skills the figures computed,
   llm the synthesis, step markers the phase boundaries. Failures render red — a failed
   fetch quietly absorbed upstream is exactly what an operator watching live should see. */
function wireLine(e) {
  const bad = e.ok === false || e.ok === "False" || (e.error && String(e.error).trim());
  const dur = e.duration_s ? ` <span class="w-dur">${(+e.duration_s).toFixed(1)}s</span>` : "";
  if (e.layer === "step") {
    return `<div class="w-line w-step">── ${e.name} ${e.status === "complete" ? "done" : ""} ──</div>`;
  }
  if (e.layer === "tool") {
    const det = e.detail ? ` <span class="w-det">${String(e.detail).slice(0, 46)}</span>` : "";
    return `<div class="w-line w-tool ${bad ? "w-err" : ""}">` +
      `${bad ? "✗" : "⚙"} ${e.name}${det}${dur}` +
      (bad && e.error ? ` <span class="w-det">${String(e.error).slice(0, 60)}</span>` : "") + `</div>`;
  }
  if (e.layer === "skill") {
    return `<div class="w-line w-skill ${bad ? "w-err" : ""}">` +
      `${bad ? "✗" : "ƒ"} ${e.name}${e.produces ? ` → ${e.produces}` : ""}${dur}</div>`;
  }
  if (e.layer === "llm") {
    return `<div class="w-line w-llm">✦ ${e.model || "llm"}` +
      (e.out_tok ? ` <span class="w-det">${e.out_tok} tok</span>` : "") + `</div>`;
  }
  return "";
}

function renderWire() {
  const el = $("wire");
  if (!el) return;
  if (!eventBuf.length) { el.innerHTML = ""; el.classList.remove("on"); return; }
  el.classList.add("on");
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.innerHTML = eventBuf.map(wireLine).join("");
  if (atBottom) el.scrollTop = el.scrollHeight;   // follow live unless the user scrolled up
}

function liveActivityLabel(e) {
  if (!e) return null;
  const where = e.step || null;
  if (e.layer === "tool") return where ? `${where} · ${e.name}` : e.name;
  if (e.layer === "llm") return where ? `${where} · thinking` : "thinking";
  if (e.layer === "step" && e.status !== "complete") return e.name;
  return where;                       // a completed step → keep showing its phase
}

async function pollJob() {
  if (!activeJob) return;
  let j;
  try { j = await api("GET", `/jobs/${activeJob}`); } catch { return; }
  activeJobState = j.state;
  const r = j.result || {};
  lastResult = r;                       // feed live evidence detail
  lastSteps = r._steps_completed || [];
  if (j.state === "running") {
    const nextLbl = (STEPS.find(([k]) => !lastSteps.includes(k)) || [, "wrapping up"])[1];
    // The partial result only advances at CHECKPOINTS, so on its own it can just say
    // which steps finished. The per-event transcript is flushed as things happen, so
    // it can name what the run is doing right now (Wave 3 item 3 / R5).
    const live = await pollLiveActivity();
    $("compStatus").innerHTML =
      `<span class="live-dot">${live || nextLbl}…</span> · ${lastSteps.length}/${STEPS.length}`;
    if (compTab === "steps") { renderTimeline(); renderWire(); }
  } else {
    clearInterval(pollTimer); pollTimer = null;
    if (j.state === "error" || r.error) {
      $("compStatus").textContent = "⚠ " + (r.error || "run failed");
      renderTimeline();
    } else {
      $("compStatus").textContent = `Done · ${lastSteps.length} steps` +
        (eventBuf.length ? ` · ${eventBuf.filter((e) => e.layer === "tool").length} sources/tools` : "");
      compTab = "report"; renderComputer();
      enableScrubber();
      $("launchBtn").textContent = "Generate report →";
      $("centerSub").textContent = "Report ready →";
    }
    loadTasks();
  }
}

/* ---------------- replay scrubber ---------------- */
let playTimer = null;
function enableScrubber() {
  const done = STEPS.filter(([k]) => lastSteps.includes(k)).length;
  const sc = $("scrub");
  sc.max = done; sc.value = done;
  $("scrubLbl").textContent = `step ${done} / ${done}`;
  $("scrubber").classList.add("on");
  sc.oninput = () => {
    compTab = "steps"; setTabs(true); $("compBody").style.padding = "18px 20px";
    renderTimelineReplay(+sc.value);
    $("scrubLbl").textContent = `step ${sc.value} / ${done}`;
  };
  $("playBtn").onclick = () => {
    if (playTimer) { clearInterval(playTimer); playTimer = null; $("playBtn").textContent = "▶"; return; }
    $("playBtn").textContent = "❚❚"; let v = 0; sc.value = 0;
    playTimer = setInterval(() => {
      v++; sc.value = v; sc.oninput();
      if (v >= done) { clearInterval(playTimer); playTimer = null; $("playBtn").textContent = "▶"; }
    }, 420);
  };
}
function renderTimelineReplay(n) {
  // show only the first n completed steps as done (replay the build order)
  const doneKeys = STEPS.filter(([k]) => lastSteps.includes(k)).slice(0, n).map(([k]) => k);
  const set = new Set(doneKeys);
  $("compBody").innerHTML = STEPS.map(([key, lbl]) => {
    const d = set.has(key);
    return `<div class="step ${d ? "done" : "pending"}"><div class="mk">${d ? "✓" : ""}</div>` +
      `<div class="lbl">${lbl}</div></div>`;
  }).join("");
}

/* ---------------- task list ---------------- */
function relTime(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return "just now"; if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`; return `${Math.floor(s / 86400)}d ago`;
}
async function loadTasks() {
  let jobs;
  try { jobs = await api("GET", "/jobs?limit=40"); } catch { return; }
  jobs = (jobs || []).filter((j) => j.kind === "plan");
  $("taskList").innerHTML = jobs.map((j) => {
    const sc = j.state === "complete" ? "s-complete" : j.state === "running" ? "s-running" : "s-error";
    return `<div class="task ${j.id === activeJob ? "active" : ""}" data-id="${j.id}">
      <div class="t-title">${(j.params_title || j.id.slice(0, 8))}</div>
      <div class="t-meta"><span class="t-state ${sc}"></span>${j.state} · ${relTime(j.updated_at)}</div></div>`;
  }).join("") || `<div style="padding:10px;color:oklch(55% 0.01 270);font-size:12px">No reports yet.</div>`;
  $("taskList").querySelectorAll(".task").forEach((t) =>
    t.onclick = () => openCompletedJob(t.dataset.id));
}
function openCompletedJob(jobId) {
  activeJob = jobId; activeJobState = "loading"; compTab = "report";
  // Load the run's whole recorded stream so the Steps tab is the full "in the order it
  // happened" record for a finished report, not an empty checklist.
  eventBuf = []; lastSince = 0;
  api("GET", `/jobs/${jobId}/events`).then((ev) => {
    if (ev && ev.events) { eventBuf = ev.events.slice(-EVENT_BUF_MAX); renderWire(); }
  }).catch(() => {});
  $("compStatus").textContent = "Loading report…";
  if (pollTimer) clearInterval(pollTimer);
  api("GET", `/jobs/${jobId}`).then((j) => {
    activeJobState = j.state; lastSteps = (j.result || {})._steps_completed || [];
    $("compStatus").textContent = j.state === "complete" ? `Done · ${lastSteps.length} steps` : j.state;
    renderComputer();
    if (j.state === "complete") enableScrubber();
  });
  document.querySelectorAll(".task").forEach((t) =>
    t.classList.toggle("active", t.dataset.id === jobId));
}

/* ---------------- center reset ---------------- */
function resetCenter() {
  $("convo").innerHTML = ""; $("centerTitle").textContent = "New report";
  $("centerSub").textContent = "Describe your venture →";
  $("launchBtn").textContent = "Generate report →"; $("launchBtn").disabled = true;
  $("scrubber").classList.remove("on");
}

/* ---------------- views: Library / Capabilities ---------------- */
function setView(view) {
  document.querySelectorAll(".nav-item").forEach((n) =>
    n.classList.toggle("active", n.dataset.view === view));
  const fv = $("fullview");
  if (view === "workspace") { fv.hidden = true; }
  else if (view === "library") { fv.hidden = false; loadLibrary(); }
  else if (view === "plugins") { fv.hidden = false; loadCapabilities(); }
}

async function loadLibrary() {
  $("fvTitle").textContent = "Library";
  $("fvSub").textContent = "Every report you've generated";
  let jobs = await api("GET", "/jobs?limit=60").catch(() => []);
  jobs = (jobs || []).filter((j) => j.kind === "plan" && j.state === "complete");
  $("fvBody").innerHTML = '<div class="grid">' + jobs.map((j) =>
    `<div class="card" data-id="${j.id}">
       <div class="c-title">${j.params_title || j.id.slice(0, 8)}</div>
       <div class="c-meta"><span class="c-badge">report</span> ${relTime(j.updated_at)}</div>
     </div>`).join("") + "</div>";
  $("fvBody").querySelectorAll(".card").forEach((c) =>
    c.onclick = () => { setView("workspace"); openCompletedJob(c.dataset.id); });
}

async function loadCapabilities() {
  $("fvTitle").textContent = "Capabilities";
  $("fvSub").textContent = "The registry powering every report — tools, skills, agents";
  const [tools, skills, agents] = await Promise.all([
    api("GET", "/api/tools").catch(() => ({ tools: [] })),
    api("GET", "/api/skills").catch(() => ({ skills: [] })),
    api("GET", "/api/agents").catch(() => ({ agents: [] })),
  ]);
  const card = (name, desc, badge) =>
    `<div class="card"><div class="c-title">${name}</div>
       <div class="c-meta"><span class="c-badge">${badge || ""}</span></div>
       <div class="c-desc">${(desc || "").slice(0, 160)}</div></div>`;
  const sec = (title, items, render) =>
    `<div class="cat-head">${title} · ${items.length}</div><div class="grid">` +
    items.map(render).join("") + "</div>";
  $("fvBody").innerHTML =
    sec("Tools", tools.tools || [], (t) => card(t.name, t.docstring || t.returns, t.category)) +
    sec("Skills", skills.skills || [], (s) => card(s.name, s.docstring, s.produces)) +
    sec("Agents", agents.agents || [], (a) => card(a.name, a.docstring || a.role, a.produces));
}

document.querySelectorAll(".nav-item").forEach((n) =>
  n.onclick = () => setView(n.dataset.view));

/* ---------------- wire up ---------------- */
$("newBtn").onclick = () => { setView("workspace"); startIntake(); };
$("sendBtn").onclick = sendMessage;
$("notSureBtn").onclick = () => { $("input").value = "not sure"; sendMessage(); };
$("launchBtn").onclick = launch;
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
loadTasks();
startIntake();
