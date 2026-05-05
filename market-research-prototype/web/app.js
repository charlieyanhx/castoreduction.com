// Castor Research — iter 37 chat-based intake.
// Landing page only. Dashboard + progress have their own scripts.

const EXAMPLES = {
  dtc:
    "We're MintBox — a monthly subscription box delivering premium mint candies for adult professionals. Each box has 6-8 limited-edition mint varieties from indie chocolate makers. Target customer is the 25-40 working professional who freshens breath between meetings and wants something more refined than gum. DTC e-commerce, $25/box subscription, US-first launching January.",
  b2b:
    "LightCart is a Shopify analytics dashboard for sub-$5M DTC brands. We surface the metrics merchants actually use to make decisions — channel-CAC, rolling MER, repeat purchase ratio — instead of vanity numbers. B2B SaaS, $49-149/month. Buyer is the founder/Head of Growth at a 1-50 person DTC brand. US + UK launch.",
  pet:
    "PawPalette is a monthly subscription box of 4-6 puzzle and enrichment toys for dogs, with ingredients-style novelty rotation. DTC, $39/month. Target customer is the 30-45 dog parent who treats their pet like family and is willing to spend on enrichment. US launch.",
};

const $ = (sel) => document.querySelector(sel);

let session = null;
let extracted = {};
let weights = {
  wtp_x_market_size: 1.0,
  low_price_elasticity: 1.0,
  low_competition: 1.0,
  ease_of_reach: 1.0,
  growth_potential: 1.0,
};

// ---------- DOM helpers ----------
function appendMsg(role, text) {
  const stream = $("#chat-stream");
  const wrapper = document.createElement("div");
  wrapper.className = `msg ${role}`;
  wrapper.innerHTML = `
    <div class="msg-avatar">${role === "user" ? "You" : "C"}</div>
    <div class="msg-bubble"></div>
  `;
  wrapper.querySelector(".msg-bubble").textContent = text;
  stream.appendChild(wrapper);
  stream.scrollTop = stream.scrollHeight;
}

function setStatus(label, cls) {
  const el = $("#chat-status");
  el.textContent = label;
  el.classList.remove("live", "ready");
  if (cls) el.classList.add(cls);
}

function updateExtractedUI(ext) {
  extracted = ext || {};
  const items = document.querySelectorAll("#needed-list li");
  let filledRequired = 0;
  const required = ["product", "target_customer", "business_model", "geography"];
  items.forEach((li) => {
    const f = li.getAttribute("data-field");
    const v = extracted[f];
    const isFilled = v && (Array.isArray(v) ? v.length > 0 : String(v).length > 0);
    li.classList.toggle("filled", !!isFilled);
    if (isFilled && required.includes(f)) filledRequired += 1;
  });
  $("#extracted-meta").textContent = `${filledRequired} of 4 required fields filled`;
}

function disableComposer(disabled) {
  $("#chat-input").disabled = disabled;
  $("#chat-send").disabled = disabled;
}

// ---------- API ----------
async function apiPost(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ---------- Chat flow ----------
async function startSession(initialMessage) {
  setStatus("Starting…", "live");
  disableComposer(true);
  try {
    const out = await apiPost("/intake/start", { initial_message: initialMessage || null });
    session = out.session_id;
    if (initialMessage) appendMsg("user", initialMessage);
    appendMsg("assistant", out.assistant_message);
    updateExtractedUI(out.extracted);
    if (out.ready) {
      handleReady(out);
    } else {
      setStatus("Ready", null);
      disableComposer(false);
      $("#chat-input").focus();
    }
  } catch (e) {
    appendMsg("assistant", `Sorry — ${e.message}. Try refreshing.`);
    setStatus("Error", null);
  }
}

async function sendMessage() {
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text || !session) return;
  appendMsg("user", text);
  input.value = "";
  setStatus("Thinking…", "live");
  disableComposer(true);
  try {
    const out = await apiPost("/intake/message", {
      session_id: session,
      user_message: text,
    });
    appendMsg("assistant", out.assistant_message);
    updateExtractedUI(out.extracted);
    if (out.ready) {
      handleReady(out);
    } else {
      setStatus("Ready", null);
      disableComposer(false);
      input.focus();
    }
  } catch (e) {
    appendMsg("assistant", `Sorry — ${e.message}. Please try again.`);
    setStatus("Error", null);
    disableComposer(false);
  }
}

function handleReady(out) {
  setStatus("Intake complete", "ready");
  disableComposer(true);
  $("#final-paragraph").textContent = out.final_description || "";
  $("#ready-launch").hidden = false;
  $("#ready-launch").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- Weights ----------
function bindWeights() {
  document.querySelectorAll("[data-w]").forEach((slider) => {
    slider.addEventListener("input", () => {
      const key = slider.getAttribute("data-w");
      const v = parseFloat(slider.value);
      weights[key] = v;
      slider.parentElement.querySelector(".w-val").textContent = v.toFixed(1);
    });
  });
}

// ---------- Launch report ----------
async function launchReport() {
  const desc = $("#final-paragraph").textContent.trim();
  if (!desc) return;
  $("#launch-btn").disabled = true;
  $("#launch-btn").textContent = "Submitting…";
  try {
    const out = await apiPost("/plan", {
      description: desc,
      operator_weights: weights,
    });
    if (out.job_id) {
      window.location.href = `/progress.html?job=${out.job_id}`;
    } else {
      throw new Error("no job_id returned");
    }
  } catch (e) {
    $("#launch-btn").disabled = false;
    $("#launch-btn").textContent = "Generate report →";
    alert(`Failed to start report: ${e.message}`);
  }
}

// ---------- Wire-up ----------
window.addEventListener("DOMContentLoaded", () => {
  bindWeights();

  $("#chat-send").addEventListener("click", sendMessage);
  $("#chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  document.querySelectorAll(".example").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-example");
      const text = EXAMPLES[key];
      if (!text) return;
      // Start a session pre-seeded with the example
      $("#chat-stream").innerHTML = "";
      $("#ready-launch").hidden = true;
      startSession(text);
    });
  });

  $("#launch-btn").addEventListener("click", launchReport);

  // Auto-open the chat with the opening question
  startSession(null);
});
