/* Castor Workspace — 3-zone agentic UI (Manus-parity build).
   Left: report list · Center: chat intake + conversation · Right: Castor Computer
   (live step stream → report viewer + replay scrubber). Vanilla JS, no build step. */
"use strict";

const $ = (id) => document.getElementById(id);
const api = async (method, path, body, timeoutMs = 90000) => {
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), timeoutMs);
  const opt = { method, headers: { "Content-Type": "application/json" }, signal: ctrl.signal };
  if (body) opt.body = JSON.stringify(body);
  try {
    const r = await fetch(path, opt);
    if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`);
    return r.json();
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
let compTab = "steps";     // "steps" | "report"

/* ---------------- conversation ---------------- */
function addMsg(role, text) {
  const m = document.createElement("div");
  m.className = `msg ${role}`;
  m.innerHTML = `<div class="av">${role === "bot" ? "C" : "you"}</div>` +
    `<div class="bubble"></div>`;
  m.querySelector(".bubble").textContent = text;
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
  $("fields").innerHTML = Object.entries(FIELD_LABELS).map(([k, lbl]) => {
    const done = extracted[k] && String(extracted[k]).toLowerCase() !== "null";
    return `<span class="chip ${done ? "done" : ""}">${done ? "✓ " : ""}${lbl}</span>`;
  }).join("");
  const ready = REQUIRED.every((k) => extracted[k] && String(extracted[k]).toLowerCase() !== "null");
  $("launchBtn").disabled = !ready;
}

function buildDescription() {
  const e = extracted;
  const parts = [
    e.product, e.target_customer && `For ${e.target_customer}.`,
    e.business_model, e.geography && `Geography: ${e.geography}.`,
    e.pricing && `Pricing: ${e.pricing}.`,
    e.differentiation && `Differentiator: ${e.differentiation}.`,
  ].filter((x) => x && String(x).toLowerCase() !== "null");
  return parts.join(" ");
}

function startIntake() {
  // Render instantly; the LLM session is created lazily on first send so the
  // page never hangs on a slow/rate-limited intake call.
  resetCenter();
  session = null; extracted = {};
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
    addMsg("bot", r.assistant_message); renderFields();
  } catch (e) {
    addMsg("bot", "⚠ " + (e.name === "AbortError" ? "the model is busy (rate limit) — try again in a moment" : e.message));
  }
  $("sendBtn").disabled = false; $("input").focus();
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
    return `<div class="step ${cls}"><div class="mk">${mk}</div>` +
      `<div><div class="lbl">${lbl}</div></div></div>`;
  }).join("");
  $("compBody").innerHTML = rows;
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
  activeJob = jobId; lastSteps = []; activeJobState = "running"; compTab = "steps";
  $("compStatus").innerHTML = `<span class="live-dot">working</span>`;
  $("scrubber").classList.remove("on");
  renderComputer();
  if (pollTimer) clearInterval(pollTimer);
  pollJob();
  pollTimer = setInterval(pollJob, 2500);
  document.querySelectorAll(".task").forEach((t) =>
    t.classList.toggle("active", t.dataset.id === jobId));
}

async function pollJob() {
  if (!activeJob) return;
  let j;
  try { j = await api("GET", `/jobs/${activeJob}`); } catch { return; }
  activeJobState = j.state;
  const r = j.result || {};
  lastSteps = r._steps_completed || [];
  if (j.state === "running") {
    const nextLbl = (STEPS.find(([k]) => !lastSteps.includes(k)) || [, "wrapping up"])[1];
    $("compStatus").innerHTML = `<span class="live-dot">${nextLbl}…</span> · ${lastSteps.length}/${STEPS.length}`;
    if (compTab === "steps") renderTimeline();
  } else {
    clearInterval(pollTimer); pollTimer = null;
    if (j.state === "error" || r.error) {
      $("compStatus").textContent = "⚠ " + (r.error || "run failed");
      renderTimeline();
    } else {
      $("compStatus").textContent = `Done · ${lastSteps.length} steps`;
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
$("launchBtn").onclick = launch;
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
loadTasks();
startIntake();
