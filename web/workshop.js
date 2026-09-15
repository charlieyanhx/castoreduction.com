/* The workshop sidebar's behaviour (templates/workshop.html carries the markup and the
   page's constants in window.WORKSHOP). Served no-cache at /workshop.js. */
(function () {
  "use strict";
  // What the page knows and the script does not: written into window.WORKSHOP by the
  // template (templates/workshop.html) before this file loads.
  const CFG = window.WORKSHOP || {};
  const JOB = CFG.job || "";
  const FACTS = CFG.facts || {};
  const CURRENT_STYLE = CFG.style || "";
  const HAS_WRITING = CFG.hasWriting ? 1 : 0;
  const STYLE_NAMES = {memo: "Decision memo", full: "Full report", operating: "Operating plan"};
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
  const html = document.documentElement;

  /* ---- talking to the server ------------------------------------------------------
     Every reply is read as JSON when it can be, and a non-2xx answer becomes an error
     carrying the status and the server's own sentence, so the copy below can say what
     happened in the founder's words rather than "request failed". */
  async function api(method, path, body) {
    let r;
    try {
      r = await fetch(path, {method: method, headers: {"Content-Type": "application/json"},
                             body: body === undefined ? undefined : JSON.stringify(body)});
    } catch (e) {
      const err = new Error("Could not reach the server. Check your connection and try again.");
      err.status = 0; throw err;
    }
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) {
      const err = new Error((data && (data.detail || data.reason)) || ("HTTP " + r.status));
      err.status = r.status; err.data = data; throw err;
    }
    return data;
  }

  /* ---- state ---------------------------------------------------------------------- */
  let st = null;              // GET /jobs/{id}/iteration, with the workshop view on it
  let billing = null;         // GET /billing/status
  let quote = null;           // the selected passage riding the next turn, if any
  let busy = false;           // a turn or a rewrite in flight
  const bal = () => (st && st.workshop && typeof st.workshop.balance === "number") ? st.workshop.balance : 0;
  const cost = (k, d) => (st && st.workshop && st.workshop.costs && typeof st.workshop.costs[k] === "number") ? st.workshop.costs[k] : d;
  const pack = () => (st && st.workshop && st.workshop.pack) || {credits: 30, usd: 5, kind: "workshop"};
  const money = (n) => (typeof n === "number" && isFinite(n)) ? "$" + (Number.isInteger(n) ? n : n.toFixed(2)) : "";

  function say(msg, ok) {
    const box = $("wsErr");
    if (!msg) { box.hidden = true; box.textContent = ""; box.classList.remove("ok"); return; }
    box.hidden = false; box.textContent = msg; box.classList.toggle("ok", !!ok);
  }

  /* Plain words for each way a turn can be refused. The server's sentence is kept where it
     already reads well; the status is what decides which sentence the founder sees. */
  function explain(e, what) {
    if (e.status === 402) return "";                    // the offer card says it
    if (e.status === 409) return e.message || "The workshop needs a finished report.";
    if (e.status === 503) return "The analyst is not switched on for this deployment yet.";
    if (e.status === 502) return "The analyst could not answer just now. Your credit was returned; try again in a moment.";
    if (e.status === 422) return e.message;
    if (e.status === 0) return e.message;
    return (what || "That") + " did not go through: " + (e.message || "unknown error") + ".";
  }

  /* ---- the balance, everywhere it shows --------------------------------------------- */
  function paintBalance() {
    const n = bal();
    $("wsBalN").textContent = String(n);
    $("wsBal").classList.toggle("low", n > 0 && n <= 3);
    $("wsBal").classList.toggle("out", n === 0);
    const tog = document.querySelector(".ws-tog b");
    if (tog) tog.textContent = String(n);
    const turn = cost("turn", 1), exp = cost("explain", 1), rw = cost("rewrite", 10);
    $("wsSend").textContent = (quote ? "Explain" : "Ask") + " · " + (quote ? exp : turn);
    $("wsExplainCost").textContent = "(" + exp + (exp === 1 ? " credit)" : " credits)");
    $("wsRewrite").textContent = "Rewrite with my notes · " + rw;
    const verb = document.querySelector('input[name="wsVerb"]:checked');
    const noting = quote && verb && verb.value === "note";
    if (noting) $("wsSend").textContent = "Save note";
    // The offer appears the moment the pool cannot pay for a turn, and stays until it can.
    const short = n < Math.min(turn, exp);
    $("wsOffer").hidden = !short;
    if (short) {
      const p = pack();
      $("wsOffer").innerHTML = "<b>Out of workshop credits.</b> A pack adds <b>" + esc(p.credits) +
        " credits</b>" + (typeof p.usd === "number" ? " for <b>" + money(p.usd) + "</b>" : "") +
        ": " + Math.floor(p.credits / Math.max(1, turn)) + " answers, or " + Math.floor(p.credits / Math.max(1, rw)) +
        " rewrites." + '<div><button class="ws-btn" type="button" id="wsBuy">Add ' + esc(p.credits) +
        " credits" + (typeof p.usd === "number" ? " · " + money(p.usd) : "") + "</button></div>";
      $("wsBuy").onclick = buy;
    }
    $("wsSend").disabled = busy || (!noting && short);
  }

  /* ---- the conversation -------------------------------------------------------------- */
  const SUGGESTED = ["What should I validate first, and how?",
                     "Which number in this report is the least certain?",
                     "What would change the verdict?"];

  function slug(path) { return "fact-" + path.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""); }

  /* The analyst writes short prose with [path] citations and the odd list or bold. The
     text is escaped first, then the few shapes it uses are drawn; nothing it writes is
     markup, and every link on this panel is a citation into the Cited facts appendix. */
  function renderText(text) {
    const cite = (s) => s.replace(/\[([a-z_][a-z0-9_.\[\]]*(?:\s*,\s*[a-z_][a-z0-9_.\[\]]*)*)\]/gi, (m, inner) =>
      inner.split(",").map((p) => p.trim()).filter(Boolean).map((p) =>
        '<a class="cite" href="#' + slug(p) + '" title="' + esc(p) + '">' + esc(p.split(".").pop()) + "</a>").join(", "));
    const inline = (s) => cite(esc(s)).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    const blocks = String(text || "").trim().split(/\n\s*\n/);
    return blocks.map((b) => {
      const lines = b.split("\n");
      if (lines.every((l) => /^\s*[-*•]\s+/.test(l))) {
        return "<ul>" + lines.map((l) => "<li>" + inline(l.replace(/^\s*[-*•]\s+/, "")) + "</li>").join("") + "</ul>";
      }
      return "<p>" + inline(b).replace(/\n/g, "<br>") + "</p>";
    }).join("");
  }

  function turnHtml(t) {
    if (t.role === "founder") {
      return '<div class="ws-turn ws-founder">' +
        (t.quote ? '<div class="ws-q">“' + esc(t.quote) + '”</div>' : "") +
        '<div class="ws-text">' + esc(t.text) + "</div></div>";
    }
    const cites = (t.citations || []);
    return '<div class="ws-turn ws-analyst' + (t.refused ? " refused" : "") + '"><div class="ws-who">Analyst</div>' +
      '<div class="ws-text">' + renderText(t.text) + "</div>" +
      (cites.length ? '<div class="ws-cites">' + cites.map((p) =>
        '<a class="cite" href="#' + slug(p) + '" title="' + esc(p) + '">' + esc(p) + "</a>").join("") + "</div>" : "") +
      "</div>";
  }

  function paintTurns() {
    const turns = (st && st.chat) || [];
    $("wsIntro").hidden = turns.length > 0;
    if (!turns.length) {
      $("wsChips").innerHTML = SUGGESTED.map((q) => '<button class="ws-chip" type="button">' + esc(q) + "</button>").join("");
      $("wsChips").querySelectorAll(".ws-chip").forEach((b) => { b.onclick = () => { $("wsInput").value = b.textContent; $("wsInput").focus(); autosize(); }; });
    }
    $("wsTurns").innerHTML = turns.map(turnHtml).join("");
    wireCites($("wsTurns"));
    scrollToEnd();
  }

  function scrollToEnd() { const b = $("wsBody"); b.scrollTop = b.scrollHeight; }

  /* A citation click lands on the fact in the appendix when the appendix has it, and
     flashes the row so the eye finds it. A path the appendix does not hold (the chat
     can cite any value, the appendix lists what the report cited) scrolls nowhere and
     the title on the link still names the path. */
  function wireCites(root) {
    root.querySelectorAll("a.cite").forEach((a) => {
      a.onclick = (ev) => {
        const id = decodeURIComponent((a.getAttribute("href") || "").slice(1));
        const target = document.getElementById(id);
        if (!target) { ev.preventDefault(); say("This cites " + a.title + ", a value the appendix does not list.", true); return; }
        ev.preventDefault();
        target.scrollIntoView({behavior: "smooth", block: "center"});
        target.classList.remove("ws-flash"); void target.offsetWidth; target.classList.add("ws-flash");
        history.replaceState(null, "", "#" + id);
      };
    });
  }

  /* ---- one turn ---------------------------------------------------------------------
     The founder's turn is drawn at once, the analyst's as a pending line with a clock,
     and the answer replaces the pending line when it lands. The credit is spent by the
     server before the model is called, so a 402 here means the pool was short: the
     offer card is drawn and nothing was charged. */
  let clock = null;
  function pending(on) {
    const old = $("wsPending"); if (old) old.remove();
    if (clock) { clearInterval(clock); clock = null; }
    if (!on) return;
    const t0 = Date.now();
    const div = document.createElement("div");
    div.id = "wsPending"; div.className = "ws-turn ws-analyst ws-pending";
    div.innerHTML = '<div class="ws-who">Analyst</div><div class="ws-text"><span class="ws-dot"></span>Reading the report <b>0s</b></div>';
    $("wsTurns").appendChild(div); scrollToEnd();
    clock = setInterval(() => { const b = div.querySelector("b"); if (b) b.textContent = Math.round((Date.now() - t0) / 1000) + "s"; }, 1000);
  }

  async function send(message, q) {
    if (busy) return;
    busy = true; say(""); paintBalance();
    $("wsIntro").hidden = true;
    $("wsTurns").insertAdjacentHTML("beforeend", turnHtml({role: "founder", text: message, quote: q}));
    pending(true);
    try {
      const out = await api("POST", "/jobs/" + JOB + "/chat", {message: message, quote: q || ""});
      pending(false);
      st.chat = (st.chat || []).concat([{role: "founder", text: message, quote: q},
        {role: "analyst", text: out.answer, citations: out.citations || [], refused: !!out.refused}]);
      st.workshop.balance = out.balance;
      $("wsTurns").insertAdjacentHTML("beforeend", turnHtml(st.chat[st.chat.length - 1]));
      wireCites($("wsTurns")); scrollToEnd();
      if (out.refused) say("The analyst's answer had a number the evidence does not hold, so it was withheld. The turn was still charged; ask again more narrowly.");
    } catch (e) {
      pending(false);
      if (e.status === 402 && e.data) {
        st.workshop.balance = (typeof e.data.balance === "number") ? e.data.balance : 0;
        if (e.data.pack) st.workshop.pack = e.data.pack;
        // the founder's line stays on screen so they can send it again once topped up
        $("wsInput").value = message; autosize();
      } else {
        say(explain(e, "That question"));
        $("wsInput").value = message; autosize();
      }
      const last = $("wsTurns").lastElementChild;
      if (last && last.classList.contains("ws-founder")) last.remove();
    } finally {
      busy = false; paintBalance(); paintRewrite();
    }
  }

  /* ---- notes --------------------------------------------------------------------- */
  function paintNotes() {
    const notes = (st && st.notes) || [];
    $("wsNotesToggle").textContent = String(notes.length);
    $("wsNotes").innerHTML = notes.length ? notes.map((n) =>
      '<div class="ws-note" data-note="' + esc(n.id) + '">' +
      '<button class="ws-nx" type="button" aria-label="Remove this note">&times;</button>' +
      (n.section ? '<span class="ws-lab">' + esc(n.section) + "</span> " : "") +
      (n.quote ? '<div class="ws-nq">“' + esc(n.quote) + "”</div>" : "") +
      '<div class="ws-nc">' + esc(n.comment) + "</div></div>").join("")
      : '<div class="ws-empty">No notes yet. Select a passage on the page and choose Note; it rides into the next rewrite.</div>';
    $("wsNotes").querySelectorAll(".ws-note").forEach((el) => {
      el.querySelector(".ws-nx").onclick = async () => {
        try { st = merge(await api("DELETE", "/jobs/" + JOB + "/notes/" + el.dataset.note)); paintAll(); }
        catch (e) { say(explain(e, "Removing the note")); }
      };
    });
    paintMarks();
  }

  $("wsNotesToggle").onclick = () => {
    const open = $("wsNotes").hidden;
    $("wsNotes").hidden = !open;
    $("wsNotesToggle").setAttribute("aria-expanded", String(open));
  };

  async function saveNote(section, q, comment) {
    try {
      st = merge(await api("POST", "/jobs/" + JOB + "/notes", {section: section, quote: q, comment: comment}));
      paintAll();
      $("wsNotes").hidden = false; $("wsNotesToggle").setAttribute("aria-expanded", "true");
      say("Note saved. It rides into the next rewrite.", true);
      return true;
    } catch (e) { say(explain(e, "Saving the note")); return false; }
  }

  /* The notes are painted onto the report itself: the first occurrence of each quoted
     passage is wrapped in a mark, and clicking it opens the notes list. Text only: the
     walker never crosses into the sidebar or a mark already made. */
  function paintMarks() {
    document.querySelectorAll("mark.ws-mark").forEach((m) => { const p = m.parentNode; while (m.firstChild) p.insertBefore(m.firstChild, m); p.removeChild(m); p.normalize(); });
    const notes = ((st && st.notes) || []).filter((n) => (n.quote || "").trim().length >= 6);
    notes.forEach((n) => wrapFirst(n.quote.trim(), n.id));
    document.querySelectorAll("mark.ws-mark").forEach((m) => {
      m.onclick = () => { open(true); $("wsNotes").hidden = false; $("wsNotesToggle").setAttribute("aria-expanded", "true");
        const el = $("wsNotes").querySelector('[data-note="' + m.dataset.note + '"]'); if (el) { el.scrollIntoView({block: "nearest"}); el.classList.add("ws-flash"); } };
    });
  }
  function wrapFirst(q, id) {
    const root = document.querySelector("main") || document.body;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {acceptNode: (n) => {
      const p = n.parentElement;
      if (!p || p.closest(".ws, .ws-sel, script, style, mark.ws-mark, .chrome-bar")) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT; }});
    let n;
    while ((n = walker.nextNode())) {
      const i = n.nodeValue.indexOf(q);
      if (i < 0) continue;
      const range = document.createRange();
      range.setStart(n, i); range.setEnd(n, i + q.length);
      const m = document.createElement("mark"); m.className = "ws-mark"; m.dataset.note = String(id);
      try { range.surroundContents(m); } catch (e) { /* a quote across nodes: left unpainted */ }
      return;
    }
  }

  /* ---- selecting a passage ------------------------------------------------------------ */
  function sectionFor(node) {
    let el = node.nodeType === 1 ? node : node.parentElement;
    while (el && el !== document.body) {
      let p = el.previousElementSibling;
      while (p) { if (/^H[123]$/.test(p.tagName)) return p.textContent.trim().slice(0, 80); p = p.previousElementSibling; }
      el = el.parentElement;
    }
    return "General";
  }
  let selRange = null;
  function showSel() {
    const sel = window.getSelection();
    const pill = $("wsSel");
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) { pill.hidden = true; return; }
    const text = sel.toString().trim();
    const anchor = sel.anchorNode && (sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement);
    if (text.length < 8 || text.length > 1200 || !anchor || anchor.closest(".ws, .ws-sel, .chrome-bar")) { pill.hidden = true; return; }
    selRange = sel.getRangeAt(0).cloneRange();
    const r = selRange.getBoundingClientRect();
    pill.hidden = false;
    const w = pill.offsetWidth, h = pill.offsetHeight;
    let left = window.scrollX + r.left + r.width / 2 - w / 2;
    left = Math.max(window.scrollX + 8, Math.min(left, window.scrollX + document.documentElement.clientWidth - w - 8));
    let top = window.scrollY + r.top - h - 8;
    if (r.top - h - 8 < 0) top = window.scrollY + r.bottom + 8;
    pill.style.left = left + "px"; pill.style.top = top + "px";
  }
  document.addEventListener("mouseup", () => setTimeout(showSel, 0));
  document.addEventListener("keyup", (e) => { if (e.shiftKey || e.key === "Shift") setTimeout(showSel, 0); });
  document.addEventListener("selectionchange", () => { const s = window.getSelection(); if (!s || s.isCollapsed) $("wsSel").hidden = true; });
  $("wsSel").querySelectorAll("button").forEach((b) => {
    b.onmousedown = (e) => e.preventDefault();          // keep the selection
    b.onclick = () => {
      const sel = window.getSelection();
      const text = (selRange ? selRange.toString() : (sel ? sel.toString() : "")).trim().slice(0, 1200);
      const section = selRange ? sectionFor(selRange.startContainer) : "General";
      $("wsSel").hidden = true;
      takeQuote(text, section, b.dataset.verb);
      if (sel) sel.removeAllRanges();
    };
  });

  let quoteSection = "General";
  function takeQuote(text, section, verb) {
    quote = text || null; quoteSection = section || "General";
    $("wsQbox").hidden = !quote; $("wsQtext").textContent = quote ? "“" + quote + "”" : "";
    $("wsMode").hidden = !quote;
    const radio = document.querySelector('input[name="wsVerb"][value="' + (verb === "note" ? "note" : "explain") + '"]');
    if (radio) radio.checked = true;
    open(true);
    const inp = $("wsInput");
    inp.placeholder = verb === "note" ? "What should change here, and why" : "Ask about this passage";
    if (verb === "explain" && !inp.value.trim()) inp.value = "Explain this passage.";
    if (verb === "note") inp.value = "";
    inp.focus(); autosize(); paintBalance();
  }
  $("wsQdrop").onclick = () => { quote = null; $("wsQbox").hidden = true; $("wsMode").hidden = true; $("wsInput").placeholder = "Ask about this report"; paintBalance(); };
  document.querySelectorAll('input[name="wsVerb"]').forEach((r) => { r.onchange = () => { const v = r.value;
    $("wsInput").placeholder = v === "note" ? "What should change here, and why" : "Ask about this passage";
    if (v === "explain" && !$("wsInput").value.trim()) $("wsInput").value = "Explain this passage.";
    if (v === "note" && $("wsInput").value.trim() === "Explain this passage.") $("wsInput").value = "";
    paintBalance(); }; });

  /* ---- the composer ------------------------------------------------------------------ */
  function autosize() { const t = $("wsInput"); t.style.height = "auto"; t.style.height = Math.min(140, t.scrollHeight) + "px"; }
  $("wsInput").addEventListener("input", autosize);
  $("wsInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("wsForm").requestSubmit(); } });
  $("wsForm").onsubmit = async (e) => {
    e.preventDefault();
    const text = $("wsInput").value.trim();
    if (!text) { $("wsInput").focus(); return; }
    const verb = document.querySelector('input[name="wsVerb"]:checked');
    if (quote && verb && verb.value === "note") {
      if (await saveNote(quoteSection, quote, text)) { $("wsInput").value = ""; $("wsQdrop").onclick(); autosize(); }
      return;
    }
    const q = quote;
    $("wsInput").value = ""; autosize();
    if (q) $("wsQdrop").onclick();
    await send(text, q);
  };

  /* ---- the rewrite ------------------------------------------------------------------
     Ten credits, about three minutes, and the panel says so while it runs. A withheld
     rewrite (the writing check refused it) keeps the previous draft and the credits come
     back; the panel says that too. A rewrite that stood reloads the page, because the
     analyst report on it is what changed. */
  function paintRewrite() {
    const notes = (st && st.notes) || [];
    const rewrites = (st && st.rewrites) || [];
    const stood = rewrites.filter((r) => !r.withheld);
    const from = stood.length ? (stood[stood.length - 1].chat_read || 0) : 0;
    const asked = ((st && st.chat) || []).slice(from).some((t) => t.role === "founder" && !t.refused);
    const sel = $("wsStyle");
    if (!sel.options.length) {
      sel.innerHTML = '<option value="">' + (CURRENT_STYLE ? "Keep the " + (STYLE_NAMES[CURRENT_STYLE] || CURRENT_STYLE).toLowerCase() : "Keep the style") + "</option>" +
        Object.keys(STYLE_NAMES).filter((k) => k !== CURRENT_STYLE).map((k) => '<option value="' + k + '">As a ' + STYLE_NAMES[k].toLowerCase() + "</option>").join("");
      sel.onchange = paintRewrite;
    }
    const restyle = !!sel.value;
    const can = HAS_WRITING && (notes.length || asked || restyle);
    const rw = cost("rewrite", 10);
    $("wsRewrite").disabled = busy || !can || bal() < rw;
    $("wsRewriteHint").textContent = !HAS_WRITING ? "This report has no written analysis to rewrite."
      : !can ? "Leave a note, ask for a change, or pick another style first."
      : bal() < rw ? "Needs " + rw + " credits; you have " + bal() + "."
      : "The facts stay; the writing changes. About three minutes.";
    $("wsHistory").hidden = !stood.length;
    $("wsHistory").textContent = stood.length ? "Rewritten " + stood.length + (stood.length === 1 ? " time" : " times") + " · previous drafts kept" : "";
  }

  $("wsRewrite").onclick = async () => {
    if (busy) return;
    const style = $("wsStyle").value || undefined;
    busy = true; say(""); paintBalance(); paintRewrite();
    const btn = $("wsRewrite"), label = btn.textContent;
    const t0 = Date.now();
    btn.disabled = true;
    const tick = setInterval(() => { btn.textContent = "Rewriting · " + Math.round((Date.now() - t0) / 1000) + "s"; }, 1000);
    $("wsRewriteHint").textContent = "Writing the report again with your notes. About three minutes; keep this page open.";
    try {
      const out = await api("POST", "/jobs/" + JOB + "/rewrite", style ? {style: style} : {});
      if (out.withheld) {
        say("The rewrite was withheld (" + (out.reason || "the writing check refused it") + "). The previous draft is kept and the " + (out.refunded || cost("rewrite", 10)) + " credits came back.");
        st = merge(await api("GET", "/jobs/" + JOB + "/iteration"));
      } else {
        try { sessionStorage.setItem("ws-rewritten-" + JOB, "1"); } catch (e) { /* no storage: the page still reloads */ }
        location.reload();
        return;
      }
    } catch (e) {
      if (e.status === 402 && e.data) { st.workshop.balance = e.data.balance; if (e.data.pack) st.workshop.pack = e.data.pack; }
      else if (e.status === 409) say(e.message || "A rewrite of this report is already running; wait for it to finish.");
      else say(explain(e, "The rewrite"));
    } finally {
      clearInterval(tick); btn.textContent = label; busy = false; paintBalance(); paintRewrite();
    }
  };

  /* ---- final ---------------------------------------------------------------------------
     THE FOUNDER SAYS WHEN IT IS DONE. A final report is what the library takes (the share
     offer below the report waits for it) and what the feedback box is about; rewrite and
     re-run stand down, the chat and the notes do not. A re-run's parent is settled the
     same way, by having spent its re-run. */
  const settled = () => !!(st && (st.status === "final" || st.status === "revised" || st.revised_to));
  function paintFinal() {
    const done = settled();
    $("wsFinal").hidden = done;
    $("wsFinalHint").textContent = done
      ? (st.status === "final" ? "This report is final." : "This report was re-run; the new one supersedes it.")
      : "When you are happy with it. Rewrite and re-run stand down; the library takes it.";
    if (done) {
      $("wsRewrite").disabled = true;
      $("wsRewriteHint").textContent = st.status === "final" ? "This report is final." : "This report was superseded by its re-run.";
      $("wsRerunGo").disabled = true;
      $("wsRerunCost").textContent = st.status === "final" ? "This report is final." : "Already re-run; open the new report.";
    }
    document.dispatchEvent(new CustomEvent("ws:state", {detail: st}));
  }
  $("wsFinal").onclick = async () => {
    if (busy) return;
    say("");
    try { st = merge(await api("POST", "/jobs/" + JOB + "/finalize")); paintAll(); say("Marked as final. The share offer is at the foot of the report.", true); }
    catch (e) { say(e.status === 422 ? e.message : explain(e, "Marking it final")); }
  };

  /* ---- re-run --------------------------------------------------------------------------- */
  const label = (f) => f.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  function paintRerun() {
    const left = (st && typeof st.reruns_left === "number") ? st.reruns_left : 1;
    const credits = (billing && typeof billing.report_credits === "number") ? billing.report_credits : null;
    $("wsRerunHint").textContent = left > 0 ? left + " included" : "1 report credit";
    $("wsRerunCost").textContent = left > 0
      ? "One re-run is included with this report."
      : (credits === null ? "After the included one, a re-run costs one report credit."
         : credits > 0 ? "Costs one report credit; you have " + credits + "."
         : "Costs one report credit; you have none.");
    const edits = (st && st.input_edits) || {};
    const fields = Object.keys(FACTS || {});
    $("wsFixes").innerHTML = fields.length ? fields.map((f) =>
      '<div class="ws-fix"><span class="ws-lab">' + esc(label(f)) + (edits[f] !== undefined ? " · corrected" : "") + "</span>" +
      '<input data-fix="' + esc(f) + '" class="' + (edits[f] !== undefined ? "edited" : "") + '" value="' + esc(edits[f] !== undefined ? edits[f] : FACTS[f]) + '"></div>').join("")
      : '<div class="ws-empty">This report was run from a written brief, so there are no survey answers to correct; your notes still ride the re-run.</div>';
    $("wsFixes").querySelectorAll("[data-fix]").forEach((el) => {
      el.onchange = async () => {
        const v = el.value.trim();
        if (v === String(FACTS[el.dataset.fix] == null ? "" : FACTS[el.dataset.fix])) return;
        try { st = merge(await api("PATCH", "/jobs/" + JOB + "/input-edits", {field: el.dataset.fix, value: v})); paintRerun(); }
        catch (e) { say(explain(e, "That correction")); }
      };
    });
    const settled = st && (st.status === "revised" || st.revised_to);
    $("wsRerunGo").disabled = !!settled && left <= 0 && !(credits > 0);
    $("wsRerunGo").textContent = left > 0 || credits === null || credits > 0 ? "Re-run" : "Buy a report credit";
  }

  $("wsRerunGo").onclick = async () => {
    const left = (st && typeof st.reruns_left === "number") ? st.reruns_left : 1;
    const credits = (billing && typeof billing.report_credits === "number") ? billing.report_credits : null;
    if (left <= 0 && credits === 0) { await checkout("report"); return; }
    const box = $("wsRerunConfirm");
    const notes = ((st && st.notes) || []).length, edits = Object.keys((st && st.input_edits) || {}).length;
    box.innerHTML = '<div class="ws-confirm">This starts a new run with ' + edits + " corrected input" + (edits === 1 ? "" : "s") +
      " and " + notes + " note" + (notes === 1 ? "" : "s") + " as corrections. About ten minutes, and it is " +
      (left > 0 ? "the re-run included with this report." : "one report credit.") +
      '<div class="ws-row"><button class="ws-btn quiet" type="button" data-no>Not yet</button>' +
      '<button class="ws-btn" type="button" data-yes>Start the re-run</button></div></div>';
    box.querySelector("[data-no]").onclick = () => { box.innerHTML = ""; };
    box.querySelector("[data-yes]").onclick = async (e) => {
      e.target.disabled = true; e.target.textContent = "Starting";
      try {
        const r = await api("POST", "/jobs/" + JOB + "/revise");
        location.href = "/progress.html?job=" + encodeURIComponent(r.job_id || JOB);
      } catch (err) {
        box.innerHTML = "";
        say(err.status === 402 ? (err.message || "This report has used its included re-run; another costs a report credit.") : explain(err, "The re-run"));
      }
    };
  };

  /* ---- buying credits ------------------------------------------------------------------
     Stripe Checkout when the instance can sell; the operator override otherwise. Nothing
     is granted by coming back from Stripe: the signed webhook or GET /billing/confirm
     does that, so the return trip below confirms the session and then reads the balance. */
  async function checkout(kind) {
    say("");
    try {
      billing = billing || await api("GET", "/billing/status");
      if (billing.configured && billing.buyable && billing.buyable[kind]) {
        try { sessionStorage.setItem("ws-was-" + JOB, String(bal())); } catch (e) { /* no baseline: the return says the count */ }
        const out = await api("POST", "/billing/checkout", {kind: kind, job_id: JOB});
        location.href = out.url;
        return;
      }
      if (kind !== "workshop") { say("Report credits are not on sale on this deployment."); return; }
      st = merge(await api("POST", "/jobs/" + JOB + "/credits", {kind: "workshop", packs: 1}));
      paintAll(); say(pack().credits + " credits added.", true);
    } catch (e) {
      say(e.status === 402 ? (e.message || "Checkout is not connected on this deployment yet.") : explain(e, "Buying credits"));
    }
  }
  function buy() { return checkout("workshop"); }

  async function afterReturn() {
    const q = new URLSearchParams(location.search);
    const paid = q.get("paid");
    if (!paid) return;
    history.replaceState(null, "", location.pathname);
    if (paid === "cancelled") { say("Checkout cancelled. You were not charged and your credits are unchanged.", true); open(true); return; }
    if (paid !== "workshop") return;
    open(true);
    let was = null;
    try { was = parseInt(sessionStorage.getItem("ws-was-" + JOB) || "", 10); sessionStorage.removeItem("ws-was-" + JOB); if (isNaN(was)) was = null; } catch (e) { was = null; }
    const sid = q.get("session_id");
    if (sid) { try { await api("GET", "/billing/confirm?session_id=" + encodeURIComponent(sid)); } catch (e) { /* the webhook may land instead */ } }
    for (let i = 0; i < 6; i++) {
      try { st = merge(await api("GET", "/jobs/" + JOB + "/iteration")); } catch (e) { /* try again */ }
      paintAll();
      if (was === null || bal() > was) { say("Payment confirmed. This report now has " + bal() + " credits.", true); return; }
      await new Promise((r) => setTimeout(r, 1500));
    }
    say("Payment received, and the credits have not landed yet. They arrive on their own; reload in a minute.", true);
  }

  /* ---- opening and closing ------------------------------------------------------------- */
  function open(yes) {
    html.classList.toggle("ws-open", !!yes);
    try { localStorage.setItem("ws-open", yes ? "1" : "0"); } catch (e) { /* remembered for this page only */ }
    const tog = document.querySelector(".ws-tog"); if (tog) tog.setAttribute("aria-expanded", String(!!yes));
    // The box takes focus on a desktop; on a phone that would raise the keyboard over
    // the sheet the founder just opened to read, so there they tap it themselves.
    if (yes && window.innerWidth > 720) setTimeout(() => $("wsInput").focus({preventScroll: true}), 60);
  }
  $("wsClose").onclick = () => open(false);
  const tog = document.querySelector(".ws-tog");
  if (tog) tog.onclick = (e) => { e.preventDefault(); open(!html.classList.contains("ws-open")); };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && html.classList.contains("ws-open")) { open(false); if (tog) tog.focus(); } });

  /* ---- load ------------------------------------------------------------------------------- */
  function merge(body) {
    // a route that returns the state without the view (notes, credits) keeps the pool
    // and the counters the last full read carried
    if (!body) return st;
    const out = Object.assign({}, st || {}, body);
    if (!body.workshop && st && st.workshop) out.workshop = st.workshop;
    if (body.workshop && typeof body.workshop.balance !== "number" && st && st.workshop) {
      // POST /credits carries the view; a bare pool is normalised the same way
      out.workshop = Object.assign({}, st.workshop, body.workshop, {balance: Math.max(0, (body.workshop.granted || 0) - (body.workshop.spent || 0))});
    }
    if (typeof body.reruns_left !== "number" && st && typeof st.reruns_left === "number") out.reruns_left = st.reruns_left;
    return out;
  }
  function paintAll() { paintBalance(); paintTurns(); paintNotes(); paintRewrite(); paintRerun(); paintFinal(); }

  async function load() {
    try {
      st = await api("GET", "/jobs/" + JOB + "/iteration");
    } catch (e) {
      st = {workshop: {balance: 0, costs: {}, pack: {}}, chat: [], notes: [], rewrites: [], input_edits: {}};
      say("Could not load the workshop: " + (e.message || "unknown error") + ". Reload the page to try again.");
    }
    try { billing = await api("GET", "/billing/status"); } catch (e) { billing = null; }
    paintAll();
    try {
      if (sessionStorage.getItem("ws-rewritten-" + JOB)) {
        sessionStorage.removeItem("ws-rewritten-" + JOB);
        say("Rewritten. The analyst report above is the new draft; the previous one is kept.", true);
        const s = document.getElementById("synthesis"); if (s) s.scrollIntoView({behavior: "smooth", block: "start"});
      }
    } catch (e) { /* no storage */ }
    let want = window.innerWidth >= 1180;
    try { const m = localStorage.getItem("ws-open"); if (m === "1") want = true; if (m === "0") want = false; } catch (e) { /* default by width */ }
    html.classList.toggle("ws-open", want);
    if (tog) tog.setAttribute("aria-expanded", String(want));
    await afterReturn();
  }
  load();
})();
