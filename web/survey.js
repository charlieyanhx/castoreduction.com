/* survey.js — opening, Core Pack, More Specific Questions, then review and launch.
 * Questions come from the fixed intake form catalog. Classification stays internal;
 * customer answers and unconfirmed extraction suggestions remain distinct.
 */
(function () {
  "use strict";

  /* ---- transport. From workspace.js, including the exclusion that matters: a retried
     POST /plan double-launches a job that takes about six minutes. -------------------- */
  var RETRY = { 502: 1, 503: 1, 504: 1 };
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  async function api(method, path, body, attempt) {
    attempt = attempt || 0;
    var idempotent = !(method === "POST" && path === "/plan");
    var opt = { method: method, headers: { "Content-Type": "application/json" } };
    if (body) opt.body = JSON.stringify(body);
    try {
      var r = await fetch(path, opt);
      if (RETRY[r.status] && attempt < 3) {
        await sleep(500 * (attempt + 1));
        return api(method, path, body, attempt + 1);
      }
      if (!r.ok) {
        // The server's own explanation, not the status code. The quota refusal already
        // says which limit was hit and what to do about it ("a report is already running,
        // wait or open it from the library"), and throwing that away left the founder
        // reading "POST /plan returned 429" at the exact moment they committed.
        var detail = "";
        try {
          var body = await r.json();
          var d = body && body.detail;
          detail = Array.isArray(d)
            ? d.map(function (x) { return x && x.msg ? x.msg : String(x); }).join("; ")
            : (d ? String(d) : "");
        } catch (_) { /* not JSON; fall back to the status */ }
        var e = new Error(detail || (method + " " + path + " returned " + r.status));
        e.status = r.status;
        e.detail = detail;
        throw e;
      }
      return r.json();
    } catch (e) {
      if (e && e.name === "TypeError" && idempotent && attempt < 3) {
        await sleep(500 * (attempt + 1));
        return api(method, path, body, attempt + 1);
      }
      throw e;
    }
  }

  /* ---- state ------------------------------------------------------------------- */
  var session = null;
  var stage = 0;
  var questionnaire = null;   // fixed /form catalog and saved progress
  var draftAnswers = {};     // only values edited by the customer on this device
  var savedStage = null;
  var originalProse = "";
  var prose = "";
  var launchInFlight = null;
  var launchAccepted = false;

  var $ = function (id) { return document.getElementById(id); };
  var form = $("form"), go = $("go"), back = $("back"), err = $("err"),
    tally = $("tally"), skip = $("skip");

  var STAGES = [
    { id: 0, label: "Your venture" }, { id: 2, label: "Core Pack" },
    { id: 4, label: "More Specific Questions" }, { id: 3, label: "Review and run" }
  ];
  var OPENING_QUESTION = "Hi! I'll help you put together a market-research report. " +
    "To start: in as much detail as you have, what does your product do and who is it for?";
  var CORE_INTRO = "Some additional questions will help us better understand your idea. " +
    "No worries if you don't have the answer right now. Feel free to skip any question " +
    "and we will estimate the answer from your earlier description.";
  var MIN_PROSE = 40;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;      // textContent, never innerHTML
    return n;
  }

  function setSteps() {
    $("steps").innerHTML = "";
    var position = STAGES.findIndex(function (step) { return step.id === stage; });
    STAGES.forEach(function (step, i) {
      var d = el("span", "step-dot" + (i === position ? " on" : (i < position ? " done" : "")),
                 step.label);
      if (i === position) d.setAttribute("aria-current", "step");
      $("steps").appendChild(d);
    });
  }

  function paint(headline, sub) {
    $("head").textContent = headline;
    $("sub").textContent = sub;
    setSteps();
    back.hidden = stage === 0;
    skip.hidden = stage !== 2 && stage !== 4;
    skip.textContent = "Skip these questions";
    err.textContent = "";
    persistDraft();
    window.scrollTo({ top: 0, behavior: "smooth" });
    $("head").focus({ preventScroll: true });
  }

  function persistDraft() {
    if (!session) return;
    try {
      sessionStorage.setItem("castor-survey:" + session, JSON.stringify({
        answers: draftAnswers, prose: prose, stage: stage,
        report_style: "full", pipeline: "treadle"
      }));
    } catch (_) { /* Saved server answers still work if browser storage is unavailable. */ }
  }

  function restoreDraft() {
    try {
      var draft = JSON.parse(sessionStorage.getItem("castor-survey:" + session) || "null");
      if (!draft || typeof draft !== "object") return;
      draftAnswers = draft.answers && typeof draft.answers === "object" ? draft.answers : {};
      if (typeof draft.prose === "string") prose = draft.prose;
      savedStage = draft.stage;
      // Earlier drafts may contain another pipeline or output style. Restore the
      // customer's answers, while every new run uses Treadle's full analysis.
    } catch (_) { /* A damaged local draft must not hide the saved server version. */ }
  }

  function submittedGroups() {
    return (questionnaire && questionnaire.submitted_groups) || [];
  }

  function resumeStage() {
    var groups = submittedGroups();
    var furthest = groups.indexOf("core") < 0 ? 2 : (groups.indexOf("specific") < 0 ? 4 : 3);
    var allowed = STAGES.slice(0, STAGES.findIndex(function (s) { return s.id === furthest; }) + 1);
    return allowed.some(function (s) { return s.id === savedStage; }) ? savedStage : furthest;
  }

  /* A standing line ABOVE the fold, for the things the footer error slot cannot say.
     `err` is warn ink at 12.5px in the fixed bar, and paint() wipes it on every stage, so
     anything put there reads as a failure and only survives until the next screen. Two
     messages need neither: a payment clearing, and a draft link that did not open. */
  function notice(text, tone) {
    var n = el("div", "note", text);
    var ok = tone === "ok";
    n.setAttribute("role", ok ? "status" : "alert");
    n.style.cssText = "margin:0 0 22px;padding:13px 16px;max-width:760px;" +
      "border-radius:var(--radius);font-size:13.5px;line-height:1.55;border:1px solid " +
      (ok ? "color-mix(in srgb, var(--accent) 25%, transparent)" : "var(--warn-line)") +
      ";background:" + (ok ? "var(--accent-soft)" : "var(--warn-bg)") +
      ";color:" + (ok ? "var(--accent)" : "var(--warn-ink)");
    form.insertBefore(n, form.firstChild);
    return n;
  }

  /* ================================================================= stage 0: prose == */
  var EXAMPLES = [
    { t: "a walk-in place", s: "A specialty coffee shop on NW 23rd in Portland. Espresso " +
      "and pour-over, a small pastry case, about 15 seats. Mostly locals and people " +
      "working on laptops. I think drinks land around six dollars." },
    { t: "something you sell to businesses", s: "A scheduling tool for small physio " +
      "clinics. They pay monthly per practitioner. Right now they run on paper diaries " +
      "and a lot of phone calls, and no-shows cost them a fortune." },
    { t: "a marketplace", s: "An app where dog owners book local walkers. We take a cut " +
      "of each booking. The hard part is getting enough walkers signed up in one " +
      "neighbourhood before owners will trust it." }
  ];

  function renderProse() {
    form.innerHTML = "";
    var wrap = el("div", "tell");

    var ta = document.createElement("textarea");
    ta.id = "prose";
    ta.value = prose;
    ta.setAttribute("aria-label", "Describe your venture");
    ta.placeholder = "Describe your product and its customers in your own words. Notes and rough ideas are welcome.";
    wrap.appendChild(ta);

    var tools = el("div", "tools");

    // Voice. The browser's own recogniser: no audio leaves the machine through us, and it
    // costs nothing. People say three to five times more than they type, and more input
    // means fewer questions afterwards, so this is a content lever, not a novelty.
    var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SR) {
      var mic = el("button", "ghost mic", "Speak instead");
      mic.type = "button";
      var rec = null;
      mic.onclick = function () {
        if (rec) { rec.stop(); return; }
        rec = new SR();
        rec.continuous = true; rec.interimResults = false; rec.lang = "en-US";
        var base = ta.value;
        rec.onresult = function (e) {
          var said = "";
          for (var i = e.resultIndex; i < e.results.length; i++) said += e.results[i][0].transcript;
          ta.value = (base + " " + said).trim();
          base = ta.value;
          countUp();
        };
        rec.onend = function () { rec = null; mic.classList.remove("rec");
                                  mic.textContent = "Speak instead"; };
        rec.onerror = rec.onend;
        rec.start();
        mic.classList.add("rec");
        mic.textContent = "Listening, tap to stop";
      };
      tools.appendChild(mic);
    }

    // Text upload. A .txt or .md is just a large paste, so it is read here and appended:
    // no server round trip, no model call, nothing deferred. Formats that need a vision
    // pass are a different question and are not accepted yet.
    var pick = el("button", "ghost", "Attach a text file");
    pick.type = "button";
    var file = document.createElement("input");
    file.type = "file";
    file.accept = ".txt,.md,.markdown,text/plain,text/markdown";
    file.style.display = "none";
    file.onchange = function () { if (file.files[0]) readText(file.files[0], ta); };
    pick.onclick = function () { file.click(); };
    tools.appendChild(pick);
    tools.appendChild(file);

    var count = el("span", "count", "");
    tools.appendChild(count);
    wrap.appendChild(tools);

    function countUp() {
      var n = ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0;
      count.textContent = n ? n + (n === 1 ? " word" : " words") : "";
      prose = ta.value;
      persistDraft();
    }
    ta.addEventListener("input", countUp);
    countUp();

    // Drag a text file straight onto the box.
    ["dragover", "dragenter"].forEach(function (ev) {
      ta.addEventListener(ev, function (e) { e.preventDefault(); ta.classList.add("drop"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      ta.addEventListener(ev, function () { ta.classList.remove("drop"); });
    });
    ta.addEventListener("drop", function (e) {
      e.preventDefault();
      var f = e.dataTransfer && e.dataTransfer.files[0];
      if (f) readText(f, ta, countUp);
    });

    var ex = el("div", "examples");
    ex.appendChild(el("div", "ex-lab", "Not sure where to start? Tap one to use it"));
    EXAMPLES.forEach(function (e) {
      var b = el("div", "ex");
      b.appendChild(el("b", null, e.t));
      b.appendChild(document.createTextNode(e.s));
      b.onclick = function () { ta.value = e.s; ta.focus(); countUp(); };
      ex.appendChild(b);
    });
    wrap.appendChild(ex);

    form.appendChild(wrap);
    go.textContent = "Continue";
    tally.textContent = "";
    paint("Tell us about your venture", OPENING_QUESTION);
  }

  function readText(f, ta, after) {
    if (!/\.(txt|md|markdown)$/i.test(f.name)) {
      err.textContent = "Text files only for now (.txt or .md). Paste anything else in.";
      return;
    }
    if (f.size > 400000) { err.textContent = "That file is very large. Paste the relevant part instead."; return; }
    var fr = new FileReader();
    fr.onload = function () {
      ta.value = (ta.value ? ta.value + "\n\n" : "") + String(fr.result || "").trim();
      err.textContent = "";
      if (after) after();
      ta.dispatchEvent(new Event("input"));
    };
    fr.readAsText(f);
  }

  /* ---- questions -------------------------------------------------------------- */
  function renderQuestion(spec, index) {
    var q = el("div", "q");
    q.appendChild(el("div", "q-num", String(index + 1)));
    var body = el("div", "q-body");
    var ask = el("div", "q-ask", spec.question);
    ask.id = "ask-" + spec.field;
    if (spec.optional) ask.appendChild(el("span", "q-opt", "optional"));
    body.appendChild(ask);
    body.appendChild(control(spec));
    if (spec.source === "inferred" && answerText(spec.value)) {
      body.appendChild(el("p", "hint", "From your description (unconfirmed): " +
        answerText(spec.value) + ". Add your own answer above to confirm or correct it."));
    }
    q.appendChild(body);
    if (spec.drives) {
      var note = el("div", "q-note");
      note.appendChild(el("b", null, "What this decides"));
      note.appendChild(document.createTextNode(spec.drives));
      q.appendChild(note);
    }
    return q;
  }

  function control(spec) {
    var box = el("div", "ctl");
    box.dataset.field = spec.field;
    var kind = spec.input_kind || "text";
    var current = spec.value;
    var hasDraft = Object.prototype.hasOwnProperty.call(draftAnswers, spec.field);
    var pre = hasDraft ? draftAnswers[spec.field]
      : (spec.source === "inferred" ? "" : questionAnswerText(spec));
    // The period rides in a sibling key of the typed slot, and the pre-fill read only
    // `value`, so a resumed draft came back with the number and none of its period.
    var prePeriod = (current && typeof current === "object") ? current.period : null;

    if (kind === "choice" && spec.options) {
      box.appendChild(radios(spec, pre, box));
      box.dataset.kind = "choice";
      return box;
    }
    if (kind === "number") {
      var row = el("div", "row");
      var n = el("input", "num");
      n.type = "text"; n.inputMode = "decimal"; n.placeholder = "number"; n.value = pre;
      n.setAttribute("aria-label", spec.unit_hint || "number");
      row.appendChild(n);
      if (spec.unit_hint) row.appendChild(el("span", "unit", spec.unit_hint));
      box.appendChild(row);
      if (spec.period_choices && spec.period_choices.length) {
        var ps = el("div", "opts");
        ps.setAttribute("role", "radiogroup");
        ps.setAttribute("aria-label", "period for " + (spec.unit_hint || "this number"));
        ps.style.marginTop = "10px";
        spec.period_choices.forEach(function (p) {
          ps.appendChild(pill(spec.field + "__period", p, "opt opt--period",
                              samePeriod(p, prePeriod)));
        });
        box.appendChild(ps);
        box.appendChild(el("div", "hint",
          "Pick the period. A number without one cannot be turned into a year."));
      }
      box.dataset.kind = "number";
      return box;
    }
    if (kind === "location") {
      var t = el("input", "txt");
      t.type = "text"; t.value = pre;
      t.placeholder = "a city, a zip, cross streets, or a full address";
      t.setAttribute("aria-label", spec.question);
      box.appendChild(t);
      var row2 = el("div", "row"); row2.style.marginTop = "9px";
      var btn = el("button", "ghost", "Check what this resolves to");
      btn.type = "button";
      var echo = el("div", "echo");
      btn.onclick = function () { resolve(t, echo, btn); };
      row2.appendChild(btn);
      box.appendChild(row2); box.appendChild(echo);
      box.dataset.kind = "text";
      return box;
    }
    var f = el(kind === "para" ? "textarea" : "input", "txt");
    if (kind !== "para") f.type = "text";
    f.value = pre;
    f.setAttribute("aria-label", spec.question);
    if (spec.optional) f.placeholder = "leave blank if you do not know";
    f.addEventListener("input", function () {
      draftAnswers[spec.field] = f.value;
      persistDraft();
    });
    box.appendChild(f);
    box.dataset.kind = "text";
    return box;
  }

  function answerText(value) {
    if (value == null) return "";
    if (typeof value !== "object") return String(value);
    if (value.unknown) return "";
    return value.value != null && typeof value.value !== "object" ? String(value.value) : "";
  }

  function questionAnswerText(spec) {
    return typeof spec.display_value === "string" ? spec.display_value : answerText(spec.value);
  }

  /* Does this pill carry the period the founder already picked? The two spellings differ
     on purpose: the pill says "per day" and slots.py stores the normalised "day". Nothing
     compared them, `checked` went in hardcoded false, and so resuming a draft dropped the
     period off every number. readAnswers then refused the submit with "Pick a period for
     that number" on a number that had one, with no way to clear it but retyping. */
  function samePeriod(choice, stored) {
    var key = function (s) {
      return String(s == null ? "" : s).toLowerCase()
        .replace(/^(?:per\s+|an?\s+|\/\s*)/, "").trim();
    };
    var want = key(stored);
    return !!want && key(choice) === want;
  }

  function pill(name, value, cls, checked) {
    var lab = el("label", cls);
    var input = document.createElement("input");
    input.type = "radio"; input.name = name; input.value = value;
    if (checked) input.checked = true;
    lab.appendChild(input);
    lab.appendChild(el("span", null, value));
    return lab;
  }

  function radios(spec, pre, box) {
    var opts = el("div", "opts");
    opts.setAttribute("role", "radiogroup");
    opts.setAttribute("aria-labelledby", "ask-" + spec.field);
    var writein = el("div", "writein");
    var wi = el("input", "txt");
    wi.type = "text"; wi.placeholder = "describe it in your own words";
    wi.setAttribute("aria-label", "your own answer");
    writein.appendChild(wi);

    spec.options.forEach(function (o) {
      var lab = el("label", "opt");
      var input = document.createElement("input");
      input.type = "radio"; input.name = spec.field;
      // The money fork posts the LABEL: those are the founder's own payment words, and
      // the classifier reads them verbatim. A keyed choice (post_value) posts the option's
      // VALUE instead, because the server validates it against a fixed list and the words
      // are only there to be read. The pre-fill compares against whichever one is posted.
      var posted = spec.post_value ? o.value : o.label;
      input.value = posted;
      if (pre && pre === posted) input.checked = true;
      input.onchange = function () { writein.classList.remove("show"); };
      lab.appendChild(input);
      lab.appendChild(el("span", null, o.label));
      opts.appendChild(lab);
    });
    if (spec.write_in) {
      var other = el("label", "opt opt--other");
      var oi = document.createElement("input");
      oi.type = "radio"; oi.name = spec.field; oi.value = "__other__";
      oi.onchange = function () { writein.classList.add("show"); wi.focus(); };
      other.appendChild(oi);
      other.appendChild(el("span", null, "something else"));
      opts.appendChild(other);
    }
    var frag = document.createDocumentFragment();
    frag.appendChild(opts);
    if (spec.write_in) frag.appendChild(writein);
    return frag;
  }

  async function resolve(input, echo, btn) {
    var q = input.value.trim();
    if (!q) { input.focus(); return; }
    btn.disabled = true;
    echo.className = "echo"; echo.textContent = "Checking…";
    try {
      var r = await api("POST", "/intake/" + session + "/locate", { q: q });
      echo.className = "echo ok";
      echo.textContent = r.echo || r.matched || "Resolved.";
    } catch (e) {
      echo.className = "echo bad";
      echo.textContent = "Could not check that right now. Your answer is still recorded.";
    } finally { btn.disabled = false; }
  }

  function readAnswers(lenient) {
    var out = {};
    var boxes = form.querySelectorAll(".ctl");
    for (var i = 0; i < boxes.length; i++) {
      var box = boxes[i], field = box.dataset.field;
      if (box.dataset.kind === "choice") {
        var picked = box.querySelector("input[type=radio]:checked");
        if (!picked) { out[field] = ""; continue; }
        out[field] = picked.value === "__other__"
          ? ((box.querySelector(".writein .txt") || {}).value || "")
          : picked.value;
        continue;
      }
      if (box.dataset.kind === "number") {
        var n = box.querySelector(".num").value.trim();
        var p = box.querySelector("input[type=radio]:checked");
        var needsPeriod = !!box.querySelector("input[type=radio]");
        if (!n) { out[field] = ""; continue; }
        // A volume with no period is not a volume. Refuse rather than annualize a guess.
        if (needsPeriod && !p) {
          if (lenient) { out[field] = ""; continue; }
          return { _needPeriod: box };
        }
        out[field] = p ? { value: n, period: p.value } : n;
        continue;
      }
      var t = box.querySelector(".txt");
      out[field] = t ? t.value.trim() : "";
    }
    return out;
  }

  function groupQuestions(group) {
    return ((questionnaire && questionnaire.questions) || []).filter(function (q) {
      return q.group === group;
    });
  }

  function renderGroup(group) {
    form.innerHTML = "";
    var questions = groupQuestions(group);
    questions.forEach(function (q, i) { form.appendChild(renderQuestion(q, i)); });
    go.textContent = group === "core" ? "Continue" : "Review and run";
    tally.textContent = "All questions are optional. Estimates will be labelled as assumptions.";
    paint(group === "core" ? "Core Pack" : "More Specific Questions",
      group === "core" ? CORE_INTRO : "Add any details you know. You can skip any question.");
  }

  /* --------------------------------------------------------------- stage 3: the reveal -- */
  function renderReveal() {
    form.innerHTML = "";
    var wrap = el("div", "reveal");
    var summary = el("div", "answer-review");
    summary.appendChild(el("h2", null, "Your business description"));
    summary.appendChild(el("p", "answer-copy", originalProse || prose));
    ["core", "specific"].forEach(function (group) {
      summary.appendChild(el("h2", null, group === "core" ? "Core Pack" : "More Specific Questions"));
      var list = el("dl");
      groupQuestions(group).forEach(function (q) {
        list.appendChild(el("dt", null, q.question));
        var confirmed = q.source === "founder" || q.confirmed;
        list.appendChild(el("dd", "answer-copy", confirmed && questionAnswerText(q)
          ? questionAnswerText(q) : "Not supplied — any estimate will be labelled as an assumption."));
      });
      summary.appendChild(list);
    });
    wrap.appendChild(summary);

    var next = el("div", "next");
    next.appendChild(el("h3", null, "What happens next"));
    next.appendChild(el("p", null,
      "First we build a detailed picture of your business and the questions to research. " +
      "Then we prepare a full analysis of the market, competitors, and business " +
      "opportunity. Your own inputs, research findings, and any assumptions will remain distinct."));
    wrap.appendChild(next);
    form.appendChild(wrap);

    go.textContent = "Run the full report";
    tally.textContent = "Review your answers, then start the research.";
    paint("Review your business",
      "Use Back to change an answer. Anything you skipped remains an unknown; " +
      "research estimates will be labelled as assumptions.");
  }

  /* ---- flow --------------------------------------------------------------------- */
  async function loadCard() {
    questionnaire = await api("GET", "/intake/" + session + "/form");
    if (groupQuestions("core").length !== 7 || groupQuestions("specific").length !== 7) {
      throw new Error("The survey questions could not be loaded. Please reload this page.");
    }
    originalProse = questionnaire.original_description || originalProse;
    if (!prose) prose = originalProse;
  }

  async function saveGroup(skipQuestions) {
    var group = stage === 2 ? "core" : "specific";
    var answers = readAnswers();
    groupQuestions(group).forEach(function (q) {
      if (skipQuestions) {
        answers[q.field] = q.source === "founder" || q.confirmed ? q.value : "";
        delete draftAnswers[q.field];
      } else if ((q.source === "founder" || q.confirmed) &&
                 q.value && typeof q.value === "object" &&
                 answers[q.field] === questionAnswerText(q)) {
        // Preserve units and currency when an old typed answer is left unchanged.
        answers[q.field] = q.value;
      }
    });
    await api("POST", "/intake/" + session + "/form", {
      answers: answers, survey_version: "2026-10"
    });
    await loadCard();
    stage = group === "core" ? 4 : 3;
    showStage();
  }

  async function submit() {
    if (go.disabled) return;
    err.textContent = "";
    go.disabled = true;
    skip.disabled = true;
    back.disabled = true;
    try {
      if (stage === 0) {
        var ta = $("prose");
        prose = (ta ? ta.value : "").trim();
        if (prose.length < MIN_PROSE) {
          err.textContent = "A sentence or two more, so we have something to work from.";
          if (ta) ta.focus();
          return;
        }
        go.textContent = "Reading…";
        if (!session || prose !== originalProse) {
          var s = await api("POST", "/intake/start", { initial_message: prose });
          session = s.session_id;
          originalProse = prose;
          draftAnswers = {};
          var url = new URL(location.href);
          url.searchParams.set("s", session);
          history.replaceState(null, "", url.toString());
        }
        await loadCard();
        stage = 2;
        showStage();
        return;
      }
      if (stage === 3) {
        await launch();
        return;
      }
      await saveGroup(false);
    } catch (e) {
      err.textContent = (stage === 3 ? "Could not start the report: "
                                     : "Could not save that: ") + e.message;
      if (stage === 0) go.textContent = "Continue";
    } finally { go.disabled = false; skip.disabled = false; back.disabled = false; }
  }

  function showStage() {
    if (stage === 0) return renderProse();
    if (stage === 2) return renderGroup("core");
    if (stage === 4) return renderGroup("specific");
    return renderReveal();
  }

  function guardedLaunch(action) {
    if (launchInFlight) return launchInFlight;
    if (launchAccepted) return Promise.resolve();
    launchInFlight = Promise.resolve().then(action).finally(function () {
      launchInFlight = null;
    });
    return launchInFlight;
  }

  function launch() {
    return guardedLaunch(prepareAndLaunch);
  }

  async function prepareAndLaunch() {
    // CONFIRM READS, IT DOES NOT COMMIT. The endpoint returns the assembled description
    // and the intake record, and it also flags the session confirmed — which is what
    // drafts() uses to decide a session became a report. Calling it before /plan meant a
    // refusal at the paywall left the founder with no run AND no draft: the notebook
    // dropped it, while the refusal card told them their answers were saved.
    var res = await api("POST", "/intake/" + session + "/confirm",
                        { corrections: {}, commit: false });
    var description = (res && res.final_description) || prose;
    if (!description || description.length < 30) {
      err.textContent = "The description is too short to research. Go back and say a " +
                        "little more about what the venture does.";
      return;
    }
    // ===== THE PAYWALL ==========================================================
    // Everything up to here is free: one extraction call, then code. The report is the
    // expensive step (metered tools, the LLM chain, about six minutes), so this is where
    // the charge belongs, BEFORE the run rather than after a refusal. The old seam sold on
    // a 429, which meant the only people ever asked to pay were the ones who had already
    // had free reports. gate() returns false when it has drawn the offer, and the founder
    // comes back through resumeAfterPurchase() with the description still in the session.
    if (!(await gate(description, res && res.intake_record))) return;
    await startPaidReport(description, res && res.intake_record);
  }

  /* The run itself, once it is entitled to happen. Separate from launch() because the
     gate has two exits into it (paid, and the preview switch) and neither should have
     to repeat the confirm/spend/redirect sequence. */
  function launchPaid(description, intake) {
    return guardedLaunch(function () { return startPaidReport(description, intake); });
  }

  async function startPaidReport(description, intake) {
    var body = { description: description, operator_weights: {},
                 pipeline: "treadle", report_style: "full" };
    // A resumed draft can retain an old presentation preference. Keep all of its
    // business answers and normalize the new run without mutating the saved record.
    if (intake) body.intake = Object.assign({}, intake, { report_style: "full" });
    try {
      var job = await api("POST", "/plan", body);
      launchAccepted = true;
      // Accepted. NOW the interview is spent, so it leaves the notebook.
      try { await api("POST", "/intake/" + session + "/confirm", { corrections: {} }); }
      catch (e) { /* the run is what matters; a stale draft is cosmetic */ }
      location.href = "/progress.html?job=" + encodeURIComponent(job.job_id);
    } catch (e) {
      if (e.status === 429) { await blocked(e.detail); return; }
      throw e;
    }
  }

  /* ======================================================================== the gate ==
     Asked once, at the moment of commitment, and it is the only thing standing between
     the interview and a six-minute metered run.

     WHO SEES IT is decided by the server (/billing/status needs_purchase), so the rule
     lives in one place: a run costs a credit once the instance can sell, and an instance
     with no processor wired never walls anyone off. Returns true to proceed. */
  async function gate(description, intake) {
    var st;
    try { st = await api("GET", "/billing/status"); }
    catch (e) {
      // A BILLING OUTAGE MUST NOT EAT THE RUN. If we cannot ask whether to charge, the
      // founder has done ten minutes of work and the honest failure is to let it through,
      // not to invent a wall we cannot take money at.
      return true;
    }
    if (!st.needs_purchase) return true;

    var me = {};
    try { me = await api("GET", "/auth/me"); } catch (e) { /* treat as signed out */ }
    drawGate(st, me, description, intake);
    return false;
  }

  function drawGate(st, me, description, gateIntake) {
    var signedIn = !!me.authenticated;
    var box = el("div", "gate");
    box.appendChild(el("div", "gate-lab", signedIn
      ? "You are out of report credits"
      : "One step left"));
    box.appendChild(el("h3", "gate-head", "Your report is ready to run"));
    box.appendChild(el("p", "gate-sub", signedIn
      ? "Every report is a fresh run of live research, so each one is bought separately. "
        + "Credits never expire and the packs are the cheaper way in."
      : "We start the research the moment this is paid. You do not need an account: we "
        + "will email the report, and you can claim it into an account any time after."));

    /* WHERE A FAILURE GOES. It used to go to `err`, the shared slot in the fixed footer
       bar: 12.5px warn ink, measured at y = -180 while the founder was looking at a card
       in the middle of the page. So clicking a price did nothing at all, as far as anyone
       could tell, and the screen read as broken. A refusal belongs beside the button that
       caused it. */
    var msg = el("p", "gate-msg");
    msg.hidden = true;
    msg.setAttribute("role", "alert");

    /* PREVIEW SAYS SO BEFORE THE CLICK, not after. These buttons cannot reach a processor,
       so letting someone click one and discover that is wasting the only action on screen
       that looks primary. */
    if (st.preview) {
      box.appendChild(el("p", "gate-preview",
        "Test mode. No card is ever charged here. Picking a price completes a simulated "
        + "purchase and carries you through the real flow that follows it: the credits "
        + "land, you are asked to make an account, and then the report runs."));
    }

    // PURCHASE FIRST. The buy buttons come before any mention of registering, because a
    // founder who is ready to pay should not have to make a second decision to do it.
    var offers = st.offers || [];
    if (!offers.length) {
      box.appendChild(el("p", "gate-sub",
        "This instance cannot take payment yet, so there is nothing to buy here."));
    }
    /* PICK, THEN PAY. Each price was its own submit button, so choosing was the same
       keystroke as committing: no way to compare, no way to change your mind, and the
       most expensive option one misclick away. They are a radio group now, and a single
       action at the bottom carries the choice, which is also what makes the button able
       to say what it is about to charge.

       A real <input type=radio> rather than divs with click handlers, so the group is
       arrow-key navigable and announces itself to a screen reader for free. */
    var chosen = offers.length ? offers[0].kind : null;
    var group = el("div", "gate-opts");
    group.setAttribute("role", "radiogroup");
    group.setAttribute("aria-label", "What to buy");
    var buttons = [];

    function priceOf(kind) {
      for (var i = 0; i < offers.length; i++) {
        if (offers[i].kind === kind) return offers[i].price_usd;
      }
      return null;
    }

    function repaint() {
      buttons.forEach(function (b) {
        var on = b.dataset.kind === chosen;
        b.classList.toggle("on", on);
        b.querySelector("input").checked = on;
      });
      var p = priceOf(chosen);
      goLabel.textContent = p != null
        ? "Continue to payment \u2014 $" + p.toFixed(0)
        : "Continue to payment";
    }

    offers.forEach(function (o) {
      var lab = el("label", "gate-opt");
      lab.dataset.kind = o.kind;

      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "castor_offer";
      radio.value = o.kind;
      radio.className = "gate-opt-radio";
      radio.onchange = function () { chosen = o.kind; repaint(); };
      lab.appendChild(radio);

      var tick = el("span", "gate-tick");
      tick.setAttribute("aria-hidden", "true");
      lab.appendChild(tick);

      var body = el("span", "gate-opt-body");
      body.appendChild(el("span", "gate-opt-name", o.label));
      if (o.credits > 1 && o.price_usd) {
        body.appendChild(el("span", "gate-opt-note",
          "$" + (o.price_usd / o.credits).toFixed(0) + " each \u00b7 credits never expire"));
      } else {
        body.appendChild(el("span", "gate-opt-note", "One full report"));
      }
      lab.appendChild(body);

      lab.appendChild(el("span", "gate-opt-price",
        o.price_usd != null ? "$" + o.price_usd.toFixed(0) : ""));

      if (o.credits >= 10) {
        lab.appendChild(el("span", "gate-flag", "Best value"));
      }
      group.appendChild(lab);
      buttons.push(lab);
    });
    box.appendChild(group);
    box.appendChild(msg);

    /* ONE ACTION, and it names the amount. A button reading "Continue to payment" with no
       number is a button somebody presses to find out what it costs. */
    var goBtn = el("button", "gate-buy primary");
    goBtn.type = "button";
    var goLabel = el("span", "gate-buy-name", "Continue to payment");
    goBtn.appendChild(goLabel);
    goBtn.onclick = function () {
      if (!chosen) return;
      buy(chosen, goBtn, msg, offers, box);
    };
    if (offers.length) box.appendChild(goBtn);
    repaint();

    // NOTHING ABOUT REGISTERING ON THIS SCREEN. It used to offer "create an account first
    // so the extra credits are waiting for you", which is the wrong order twice over: it
    // puts a second decision in front of somebody who has already decided to pay, and it
    // asks for the commitment at the one moment they have nothing invested yet, so the
    // rational move is to skip it. The ask moves to claimStep(), after the money, when
    // they are holding credits they would have to abandon. One screen, one action.

    // The second button they asked for: somewhere to go that is not a card form.
    // POINTED AT THE LIBRARY, NOT /sample. /sample is one report the operator named by
    // hand, and it 404s when they have not named one, which is the state every fresh
    // instance is in. The library is always there, always current, and a shelf of real
    // reports argues better than a single specimen.
    var alt = el("p", "gate-alt");
    alt.appendChild(document.createTextNode("Not sure yet? "));
    var sa = document.createElement("a");
    sa.href = "/library";
    sa.target = "_blank";
    sa.rel = "noopener";
    sa.textContent = "Read finished reports first";
    alt.appendChild(sa);
    alt.appendChild(document.createTextNode(
      ". Real ones, start to finish, published by the founders who ran them. Your " +
      "answers stay saved on this page."));
    box.appendChild(alt);

    if (st.preview) {
      /* A REAL BUTTON, because in test mode it is the ONLY thing on this card that works.
         It was faint dashed 12.5px text under three dead-looking price buttons, which is
         how a screen with a working way forward reads as a dead end. */
      var skipBtn = el("button", "gate-skip");
      skipBtn.type = "button";
      skipBtn.appendChild(el("span", "gate-buy-name",
        "Skip the purchase and just run the report"));
      skipBtn.onclick = async function () {
        skipBtn.disabled = true;
        skipBtn.querySelector(".gate-buy-name").textContent = "Starting your report\u2026";
        box.remove();
        try { await launchPaid(description, gateIntake); }
        catch (e) { err.textContent = "Could not start the report: " + e.message; }
      };
      box.appendChild(skipBtn);
    }

    var host = form.querySelector(".reveal") || form;
    host.appendChild(box);
    box.scrollIntoView({ block: "center", behavior: "smooth" });
    go.textContent = "Run the full report";
  }

  async function buy(kind, btn, msg, offers, box) {
    /* THE CLICK HAS TO LAND SOMEWHERE VISIBLE. Every exit from here either navigates to
       Stripe or says why it did not, inside the card, in text the founder can actually
       read. The old version reset the label and wrote 12.5px into the fixed footer, so a
       failed click was indistinguishable from a click that did nothing. */
    var label = btn.querySelector(".gate-buy-name");
    var was = label ? label.textContent : btn.textContent;
    var setLabel = function (t) {
      if (label) { label.textContent = t; } else { btn.textContent = t; }
    };
    // Only one purchase can be in flight, so nothing else invites a second click: the
    // action itself and every radio in the group.
    var all = box
      ? [].slice.call(box.querySelectorAll(".gate-buy, .gate-opt-radio"))
      : [btn];
    all.forEach(function (b) { b.disabled = true; });
    if (box) {
      [].slice.call(box.querySelectorAll(".gate-opt")).forEach(function (o) {
        o.classList.add("busy");
      });
    }
    msg.hidden = true;
    setLabel("Opening checkout\u2026");
    try {
      var out = await api("POST", "/billing/checkout",
                          { kind: kind, session_id: session });
      location.href = out.url;
    } catch (e) {
      all.forEach(function (b) { b.disabled = false; });
      if (box) {
        [].slice.call(box.querySelectorAll(".gate-opt")).forEach(function (o) {
          o.classList.remove("busy");
        });
      }
      setLabel(was);
      msg.hidden = false;
      msg.textContent = "We could not open checkout: " + e.message
        + ". Nothing was charged, and your answers are still here.";
      msg.scrollIntoView({ block: "nearest", behavior: "smooth" });
      err.textContent = "";      // the footer copy of this is now redundant and off-screen
    }
  }

  /* ================================================================= claim the credits ==
     THE REGISTRATION ASK, AND IT COMES AFTER THE MONEY.

     It used to sit on the gate as "create an account first so the extra credits are
     waiting for you". That is the wrong order: it puts a second decision in front of
     somebody who has already decided to buy, at the one moment they have nothing invested
     and skipping costs them nothing. Here, they have paid. A pack buyer is holding four
     or nine credits they would be walking away from, and the address is already known
     because Stripe collected it, so the form is one field.

     IT NEVER BLOCKS THE REPORT. They paid for a run and the run is the thing they came
     for, so both buttons start it. The account is what the credits attach to, not a toll.
  */
  async function claimStep(kind, status, signedIn) {
    var packs = { bundle5: 5, bundle10: 10 };
    var extra = (packs[kind] || 1) - 1;          // one is about to be spent on this run
    var email = (status && status.prepaid_email) || "";

    var bought = packs[kind] || 1;
    var box = el("div", "gate celebrate");

    /* THE MOMENT THEY PAID, AND IT SHOULD READ LIKE ONE. This was a grey label saying
       "Payment received" over a password form, which is a receipt: the founder has just
       committed money to a stranger's website and the screen answered like a card
       terminal. The mark draws itself in, the count of what they bought is the largest
       thing on the card, and only then does it ask for anything.

       The animation is CSS and respects prefers-reduced-motion, so it is a flourish for
       people who want one and simply the finished state for everyone else. */
    var seal = el("div", "gate-seal");
    seal.setAttribute("aria-hidden", "true");
    seal.innerHTML = '<svg viewBox="0 0 52 52" width="52" height="52">'
      + '<circle class="seal-ring" cx="26" cy="26" r="23" fill="none" stroke-width="2.5"/>'
      + '<path class="seal-tick" fill="none" stroke-width="3.2" stroke-linecap="round"'
      + ' stroke-linejoin="round" d="M15 27 l8 8 l15 -16"/></svg>';
    box.appendChild(seal);

    box.appendChild(el("div", "gate-lab", "Payment received"));
    box.appendChild(el("h3", "gate-head gate-head-big", bought > 1
      ? bought + " reports are yours"
      : "Your report is paid for"));
    box.appendChild(el("p", "gate-sub", extra > 0
      ? "One starts running the moment you continue. The other " + extra + " keep, with "
        + "no expiry, for whenever the next idea turns up."
      : "It starts running the moment you continue."));

    /* A BUYER WITH AN ACCOUNT HAS NOTHING TO ANSWER. The card began life as the
       registration ask, so it ran only for guests and a signed-in buyer went straight from
       Stripe to a progress bar: they paid and the product said nothing. The moment belongs
       to everyone who just paid; the form below belongs to people with nowhere to put the
       credits. */
    if (signedIn) {
      var goNow = el("button", "gate-buy primary");
      goNow.type = "button";
      var goNowLab = el("span", "gate-buy-name",
        extra > 0 ? "Start my report" : "Start my report");
      goNow.appendChild(goNowLab);
      box.appendChild(goNow);
      var hostS = form.querySelector(".reveal") || form;
      hostS.appendChild(box);
      box.scrollIntoView({ block: "center", behavior: "smooth" });
      go.textContent = "Run the full report";
      return await new Promise(function (resolve) {
        goNow.onclick = function () {
          goNow.disabled = true;
          goNowLab.textContent = "Starting\u2026";
          box.remove();
          notice("Starting your report now.", "ok");
          resolve(launch());
        };
      });
    }

    box.appendChild(el("div", "gate-rule"));
    box.appendChild(el("div", "gate-lab", extra > 0
      ? "Where should the other " + extra + " live?"
      : "Where should this report live?"));
    box.appendChild(el("p", "gate-sub", extra > 0
      ? "Set a password and they sit on an account you can come back to. Without one they "
        + "are held against " + (email || "your email address") + " and you would have to "
        + "ask us for them."
      : "Set a password and it lands in a library you can come back to. Without an account "
        + "it lives in this browser, and clearing your cookies loses the link."));

    var ef = el("div", "gate-field");
    var el1 = el("label", "gate-lab-sm", "Your email");
    var ei = document.createElement("input");
    ei.type = "email";
    ei.value = email;
    ei.placeholder = "you@example.com";
    ei.className = "gate-input";
    ei.autocomplete = "email";
    ef.appendChild(el1);
    ef.appendChild(ei);
    box.appendChild(ef);

    var pf = el("div", "gate-field");
    var pl = el("label", "gate-lab-sm", "Choose a password");
    var pi = document.createElement("input");
    pi.type = "password";
    pi.placeholder = "at least 12 characters";
    pi.className = "gate-input";
    pi.autocomplete = "new-password";
    pf.appendChild(pl);
    pf.appendChild(pi);
    box.appendChild(pf);

    var msg = el("p", "gate-fine");
    msg.style.color = "var(--warn-ink)";
    box.appendChild(msg);

    var go2 = el("button", "gate-buy primary");
    go2.type = "button";
    go2.appendChild(el("span", "gate-buy-name", "Create account and start my report"));
    box.appendChild(go2);

    var skipReg = el("button", "gate-skip", extra > 0
      ? "Just start my report (the credits stay against your email)"
      : "Just start my report");
    skipReg.type = "button";
    box.appendChild(skipReg);

    var host = form.querySelector(".reveal") || form;
    host.appendChild(box);
    box.scrollIntoView({ block: "center", behavior: "smooth" });
    go.textContent = "Run the full report";

    /* Both exits run the report. Wrapped so a failure anywhere in here still ends with
       the founder's paid run started rather than on a card with a dead button. */
    function start(insideLaunch) {
      box.remove();
      notice("Starting your report now.", "ok");
      return insideLaunch ? prepareAndLaunch() : launch();
    }

    return await new Promise(function (resolve, reject) {
      skipReg.onclick = function () {
        skipReg.disabled = true;
        go2.disabled = true;
        resolve(start());
      };
      go2.onclick = async function () {
        msg.textContent = "";
        var e2 = ei.value.trim(), p2 = pi.value;
        if (!e2 || e2.indexOf("@") < 0) { msg.textContent = "That email does not look right."; return; }
        if (p2.length < 12) { msg.textContent = "Use at least 12 characters."; return; }
        go2.disabled = true;
        skipReg.disabled = true;
        var lab = go2.querySelector(".gate-buy-name");
        lab.textContent = "Creating your account…";
        guardedLaunch(async function () {
          try {
            await api("POST", "/auth/signup", { email: e2, password: p2 });
          } catch (err2) {
            // A signup failure must leave both choices available without spending a run.
            go2.disabled = false;
            skipReg.disabled = false;
            lab.textContent = "Create account and start my report";
            msg.textContent = "Could not create the account: " + err2.message
              + ". Your payment is safe and your credits are held against " + e2
              + ". You can start the report and register later.";
            return false;
          }
          // Continue inside the existing guard after ownership transfers to the account.
          await start(true);
          return true;
        }).then(function (started) {
          if (started) resolve();
        }, reject);
      };
    });
  }

  /* A refusal at the CTA is the most expensive error in the product: the founder has done
     the work and is committing. It must say which limit was hit, in the server's own
     words, and end somewhere they can go rather than on a dead status code. Their answers
     are safe either way; the session is in the URL. */
  async function blocked(detail) {
    err.textContent = "";
    var box = el("div", "limits");
    box.appendChild(el("div", "lim-lab", "We cannot start this run right now"));
    var ul = document.createElement("ul");
    ul.appendChild(el("li", null, detail || "You have reached your run limit for now."));

    /* OUT OF FREE RUNS IS A PURCHASE, NOT A WALL, once the instance can sell. This is the
       one moment the founder is most committed: they have answered the questions, seen
       their own break-even, and pressed the button. Showing them only a refusal there
       throws away the whole funnel. When Stripe is not configured the block reads exactly
       as before, because an instance that cannot take money must not imply it can. */
    try {
      var status = await api("GET", "/billing/status");
      if (status.configured && status.buyable && status.buyable.report
          // DAILY limit only. The concurrency refusal also contains the word "limit"
          // ("a report is already running for this account (limit 1)"), and
          // routes/research.py explicitly declines to spend a credit on that case, so
          // offering checkout there took money for a run that still would not start.
          && /daily limit/i.test(detail || "")) {
        var buy = el("li");
        buy.appendChild(document.createTextNode("You have used your free runs for today. "));
        var a = document.createElement("a");
        a.href = "#";
        a.textContent = "Buy this report";
        a.style.color = "inherit";
        a.onclick = async function (ev) {
          ev.preventDefault();
          a.textContent = "Opening checkout…";
          try {
            var out = await api("POST", "/billing/checkout",
                                { kind: "report", session_id: session });
            location.href = out.url;
          } catch (e) {
            a.textContent = "Checkout unavailable: " + e.message;
          }
        };
        buy.appendChild(a);
        buy.appendChild(document.createTextNode(" and it runs straight away."));
        ul.appendChild(buy);
      }
    } catch (e) { /* an unreachable billing endpoint must not swallow the refusal */ }
    var li = el("li");
    li.appendChild(document.createTextNode("Your answers are saved. This page will pick " +
      "up where you left off, and finished reports are in "));
    var a = document.createElement("a");
    a.href = "/dashboard.html";
    a.textContent = "your library";
    a.style.color = "inherit";
    li.appendChild(a);
    li.appendChild(document.createTextNode("."));
    ul.appendChild(li);
    box.appendChild(ul);
    var host = form.querySelector(".reveal") || form;
    host.appendChild(box);
    box.scrollIntoView({ block: "center", behavior: "smooth" });
    go.textContent = "Try again";
  }

  /* ---- boot --------------------------------------------------------------------- */
  go.onclick = submit;
  skip.onclick = async function () {
    if (stage !== 2 && stage !== 4) return;
    go.disabled = true;
    skip.disabled = true;
    back.disabled = true;
    try { await saveGroup(true); }
    catch (e) { err.textContent = "Could not save that: " + e.message; }
    finally { go.disabled = false; skip.disabled = false; back.disabled = false; }
  };
  back.onclick = function () {
    if (stage === 3) stage = 4;
    else if (stage === 4) stage = 2;
    else stage = 0;
    showStage();
  };
  // Native buttons keep their Enter action. Typing in a claim or checkout field must
  // never trigger the report CTA through the surrounding survey form.
  form.addEventListener("submit", function (e) { e.preventDefault(); });

  /* Back from Stripe with a report credit: the founder pressed the button before paying,
     so pressing it for them on return is what they already asked for.

     IT HAS TO SAY SO. Stripe drops them back on the same reveal screen they left, and the
     only acknowledgement was one line in the footer error slot, warn-inked, set while the
     page was smooth-scrolling back to the top. A cleared payment is not a failure and did
     not read as one down there: the return read as a page reloading itself. The banner
     goes above the fold instead, and `paid` comes out of the URL so a reload is a plain
     resume rather than a second launch. */
  async function resumeAfterPurchase() {
    var url = new URL(location.href);
    var paid = url.searchParams.get("paid");
    if (!paid || !session) return false;
    var checkout = url.searchParams.get("session_id");
    url.searchParams.delete("paid");
    url.searchParams.delete("session_id");
    history.replaceState(null, "", url.toString());

    if (paid === "cancelled") {
      // SAME GUARD AS THE SUCCESS BRANCH BELOW. Without it a 404 on the session throws
      // out to boot(), which renders "that saved draft is gone" at somebody who merely
      // backed out of checkout. Nothing was charged, and telling them they lost their
      // work is still the wrong answer.
      try { await loadCard(); } catch (e) { /* fall through to the stage below */ }
      stage = resumeStage();
      showStage();
      notice("Checkout cancelled. Nothing was charged, and every answer you gave is "
             + "still here.");
      return true;
    }

    /* THE GRANT THAT DOES NOT WAIT FOR THE WEBHOOK. Stripe substitutes the session id
       into the success URL, and /billing/confirm retrieves that session with the secret
       key and fulfils it through the same idempotent path the webhook uses. Asked BEFORE
       /billing/status is read, so the status that decides whether the gate is redrawn
       already reflects the purchase; and before loadCard, so the "credit is on your
       account" copy in the failure branch below is true when it is shown. A failure here
       is swallowed: the webhook is the other path, and the status read says which landed. */
    if (checkout) {
      try { await api("GET", "/billing/confirm?session_id=" + encodeURIComponent(checkout)); }
      catch (e) { /* the webhook may still grant it; /billing/status below is the verdict */ }
    }

    try {
      await loadCard();
      stage = resumeStage();
      showStage();
      if (stage !== 3) {
        notice("Payment received. Your credit is saved. Complete or skip the remaining " +
               "questions, then review your answers to start the report.", "ok");
        return true;
      }
    } catch (e) {
      // They have already paid. Falling through to the stale-draft notice here would read
      // as losing the money and the interview at once, so name what is actually true: the
      // credit sits on the account and the next run spends it.
      renderProse();
      notice("Payment received. We could not reopen this draft to start the run: "
             + e.message + ". The credit is on your account, so your next report uses it "
             + "and costs nothing.", "ok");
      return true;
    }

    /* EVERY BUYER SEES THE MOMENT. What differs is what the card asks for afterwards:
       somewhere to keep the credits, or just the button that starts the run. */
    var whoami = {};
    try { whoami = await api("GET", "/auth/me"); } catch (e) { /* treat as a guest */ }
    var bought = null;
    try { bought = await api("GET", "/billing/status"); } catch (e) { /* prefill is optional */ }
    try {
      await claimStep(paid, bought, !!whoami.authenticated);
      return true;
    } catch (e) {
      // Never leave a paid founder on a broken card. Fall through to the plain launch.
      err.textContent = "";
    }

    notice("Payment received. Starting your report now.", "ok");
    try {
      await launch();
    } catch (e) {
      // launch() throwing used to escape into boot's catch, which dropped the session and
      // painted an empty stage 0 over a run the founder had just paid for.
      err.textContent = "Could not start the report: " + e.message;
      go.textContent = "Run the full report";
    }
    return true;
  }

  (async function boot() {
    try {
      var me = await api("GET", "/auth/me");
      // Production refuses unauthenticated requests outright, and a 401 on POST /plan
      // after ten minutes of typing is a dead end. Send them to the door first.
      if (me && !me.authenticated && me.local === false) { location.href = "/login"; return; }
    } catch (e) { /* local installs keep going straight in */ }

    var existing = new URL(location.href).searchParams.get("s");
    if (existing) {
      try {
        session = existing;
        restoreDraft();
        if (await resumeAfterPurchase()) return;
        await loadCard();
        stage = resumeStage();
        return showStage();
      } catch (e) {
        // A DEAD DRAFT LINK USED TO BE A BLANK SURVEY. The session went null and the page
        // fell through to stage 0, so a link that had expired, or that was mailed between
        // two accounts, opened an empty box with nothing said: indistinguishable from the
        // product having thrown the founder's answers away. Say which it was, drop the
        // dead id so a reload is a clean start, and leave them on the box that starts one.
        session = null;
        var dead = new URL(location.href);
        dead.searchParams.delete("s");
        dead.searchParams.delete("paid");
        dead.searchParams.delete("session_id");
        history.replaceState(null, "", dead.toString());
        renderProse();
        notice(e && (e.status === 404 || e.status === 403)
          ? "That saved draft is gone. It has either expired or it was started on a "
            + "different account. Describe your venture below to start a new one, and "
            + "anything you already ran is in your library."
          : "We could not load that saved draft: "
            + ((e && e.message) || "the server did not answer")
            + ". Reload the page to try it again, or describe your venture below to "
            + "start a new one.");
        return;
      }
    }
    renderProse();
  })();
})();
