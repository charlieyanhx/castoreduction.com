/* survey.js — the intake survey, four stages.
 *
 *   0  tell us about it        one text box. Voice and text-file drop both land in it.
 *   1  how the money comes in  asked ONLY when the classifier is not sure.
 *   2  the reading + 4 asks    what we read you as, what would decide it, and the few
 *                              numbers only you have.
 *   3  the reveal + the ask    the founder's own arithmetic, its limits stated, then the
 *                              call to run the report.
 *   4  the extras             the remaining questions, asked once they have committed and
 *                              before the run launches. All skippable.
 *
 * WHY THIS ORDER. The expensive thing is the report. Everything before it is nearly free,
 * and until now that free budget bought the founder nothing: they filled a survey and were
 * asked for money holding only a promise. Stage 3 spends it on an artifact instead, and
 * stage 2 asks only the four questions that artifact needs.
 *
 * The rest go in stage 4 rather than into the six minute wait, and that is not a taste
 * call: run_plan stamps the intake record before step one and never re-reads the session,
 * so an answer given while the run is going cannot reach it. Asking during the wait would
 * feel considerate and change nothing, which is the worst of both.
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
  var card = null;            // the /preview payload
  var forkQ = null;           // the money question, when one is being asked
  var prose = "";

  var $ = function (id) { return document.getElementById(id); };
  var form = $("form"), go = $("go"), back = $("back"), err = $("err"),
    tally = $("tally"), skip = $("skip");

  var STAGES = ["Your venture", "How you charge", "The few we need",
              "What we found", "Last extras"];
  var MIN_PROSE = 40;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;      // textContent, never innerHTML
    return n;
  }

  function setSteps() {
    $("steps").innerHTML = "";
    STAGES.forEach(function (name, i) {
      if (i === 1 && !forkQ) return;
      var d = el("span", "step-dot" + (i === stage ? " on" : (i < stage ? " done" : "")), name);
      $("steps").appendChild(d);
    });
  }

  function paint(headline, sub) {
    $("head").textContent = headline;
    $("sub").textContent = sub;
    setSteps();
    back.hidden = stage === 0;
    if (stage !== 4) skip.hidden = true;
    err.textContent = "";
    window.scrollTo({ top: 0, behavior: "smooth" });
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
    ta.placeholder = "What is it, who is it for, how do people pay you, and where? " +
      "Anything you already know about price, costs or competitors is useful too. " +
      "Do not worry about structure.";
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
    paint("Tell us about your venture",
      "Whatever you have. A paragraph, a rough pitch, notes you already wrote. The more " +
      "you say, the fewer questions we have to ask you afterwards.");
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

  /* ============================================ stage 1 and 2: questions and the read == */
  function renderQuestion(spec, index) {
    var q = el("div", "q");
    q.appendChild(el("div", "q-num", String(index + 1)));
    var body = el("div", "q-body");
    var ask = el("div", "q-ask", spec.question);
    ask.id = "ask-" + spec.field;
    if (spec.optional) ask.appendChild(el("span", "q-opt", "optional"));
    body.appendChild(ask);
    body.appendChild(control(spec));
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
    var pre = (current && typeof current === "object")
      ? (current.value != null && typeof current.value !== "object" ? String(current.value) : "")
      : (current || "");

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
          ps.appendChild(pill(spec.field + "__period", p, "opt opt--period", false));
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
    box.appendChild(f);
    box.dataset.kind = "text";
    return box;
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
      input.type = "radio"; input.name = spec.field; input.value = o.label;
      if (pre && pre === o.label) input.checked = true;
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

  /* Correcting the money kind re-derives the whole screen: the consequence sentence, what
     would decide it, and WHICH questions are asked, because a cafe and a SaaS are asked
     different things. It costs one round trip and no model call, so the page can afford to
     be live. `lenient` keeps a half-typed number question from blocking the correction. */
  async function changeKind() {
    var answers = readAnswers(true);
    go.disabled = true;
    err.textContent = "";
    try {
      await api("POST", "/intake/" + session + "/form", { answers: answers });
      await loadCard();
      renderBrief();
    } catch (e) {
      err.textContent = "Could not update that: " + e.message;
    } finally { go.disabled = false; }
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

  /* -------------------------------------------------- stage 2: the reading + the asks -- */
  function renderBrief() {
    form.innerHTML = "";
    var brief = el("div", "brief");

    var read = el("div", "reading");
    read.appendChild(el("span", "reading-txt", "How the money comes in"));
    read.appendChild(el("span", "prov prov-" + card.provenance,
      card.provenance === "stated" ? "you picked this" : "we worked this out"));
    brief.appendChild(read);

    // THE KIND IS A PICKER, PRE-SELECTED TO OUR READING, ALWAYS VISIBLE.
    //
    // Not a hidden "not right?" toggle, and not a question we only ask when unsure. Two
    // measured reasons. First, the classifier is confidently wrong on a whole class of
    // ventures: "a scheduling tool for small physio clinics" was read as a walk-in venue
    // off the word "clinic" sitting in the product sentence, with explicit=True, so no
    // fork fired and nothing would have caught it. Second, this is the one branch that
    // reshapes every financial table in the report, so a wrong answer here is not one
    // wrong section, it is all of them.
    //
    // Pre-selecting costs the founder nothing when we are right (they read one line and
    // move on) and costs one click when we are wrong. That is the cheapest possible
    // insurance on the most expensive possible mistake.
    var kindSpec = { field: "kind_fork", question: "How the money comes in",
                     input_kind: "choice", options: card.kind_options, write_in: true,
                     value: card.kind_said };
    var kctl = control(kindSpec);
    kctl.querySelectorAll("input[type=radio]").forEach(function (r) {
      r.addEventListener("change", changeKind);
    });
    brief.appendChild(kctl);

    brief.appendChild(el("div", "consequence", card.headline));

    var dec = el("div", "decides");
    dec.appendChild(el("div", "dec-lab", "What actually decides this"));
    (card.decides || []).forEach(function (d) {
      var row = el("div", "dec");
      row.appendChild(el("div", "who who-" + d.who, d.who === "us" ? "we find" : "you tell us"));
      var body = el("div");
      body.appendChild(el("div", "dec-q", d.q));
      body.appendChild(el("div", "dec-how", d.how));
      row.appendChild(body);
      dec.appendChild(row);
    });
    brief.appendChild(dec);
    form.appendChild(brief);

    (card.questions || []).forEach(function (q, i) { form.appendChild(renderQuestion(q, i)); });

    go.textContent = "Show me the numbers";
    tally.textContent = (card.questions || []).length + " questions. Blanks become " +
      "disclosed assumptions, never silent ones.";
    paint("Here is what we read, and what would settle it",
      "We have not looked anything up yet. This is what your description tells us, and " +
      "the few figures only you can give us.");
  }

  /* --------------------------------------------------------------- stage 3: the reveal -- */
  function renderReveal() {
    form.innerHTML = "";
    var wrap = el("div", "reveal");
    var be = card.break_even, ceil = card.ceiling, util = card.utilisation;

    if (be) {
      var stat = el("div", "stat");
      stat.appendChild(el("div", "stat-lab", "Your break-even"));
      stat.appendChild(el("div", "stat-big", be.shape === "recurring"
        ? be.units.toLocaleString() + " " + be.unit_noun
        : be.per_day.toLocaleString() + " " + be.unit_noun + " a day"));
      stat.appendChild(el("div", "stat-sub", be.shape === "recurring"
        ? "That is how many paying customers cover your running costs before you earn anything."
        : "That is " + be.units.toLocaleString() + " a month, every month, before you earn " +
          "a thing."));
      var w = el("div", "workings");
      w.appendChild(el("b", null, "How: "));
      w.appendChild(document.createTextNode(be.workings));
      stat.appendChild(w);

      if (util && ceil) {
        var bars = el("div", "bars");
        var fill = el("div", util.over_capacity ? "bar-over" : "bar-fill");
        fill.style.width = Math.min(100, util.percent) + "%";
        bars.appendChild(fill);
        stat.appendChild(bars);
        stat.appendChild(el("div", "stat-sub", util.over_capacity
          ? "That is more than the room can serve. " + util.workings + ", so the space " +
            "itself is the binding constraint, not demand."
          : "About " + util.percent + "% of what the room can serve. " + util.workings + "."));
        var w2 = el("div", "workings");
        w2.appendChild(el("b", null, "Capacity: "));
        w2.appendChild(document.createTextNode(ceil.workings));
        stat.appendChild(w2);
      }
      wrap.appendChild(stat);
    } else {
      var none = el("div", "stat");
      none.appendChild(el("div", "stat-lab", "Your break-even"));
      none.appendChild(el("div", "stat-big", "Not yet"));
      none.appendChild(el("div", "stat-sub",
        "We need both a price and a monthly running cost to work this out, and we would " +
        "rather say so than put up a number we made up."));
      wrap.appendChild(none);
    }

    if ((card.limits || []).length) {
      var lim = el("div", "limits");
      lim.appendChild(el("div", "lim-lab", "What these numbers do not include"));
      var ul = document.createElement("ul");
      card.limits.forEach(function (l) { ul.appendChild(el("li", null, l)); });
      lim.appendChild(ul);
      wrap.appendChild(lim);
    }

    var next = el("div", "next");
    next.appendChild(el("h3", null, "Everything above is your own arithmetic"));
    next.appendChild(el("p", null,
      "We have not looked anything up yet. The report is where we go outside and measure:"));
    var ul2 = document.createElement("ul");
    (card.decides || []).filter(function (d) { return d.who === "us"; })
      .forEach(function (d) { ul2.appendChild(el("li", null, d.q + ", from " + d.how)); });
    next.appendChild(ul2);
    var promise = el("div", "promise");
    promise.appendChild(el("b", null, "If we cannot source it, we do not publish it. "));
    promise.appendChild(document.createTextNode(
      "Every figure in the report carries where it came from, and anything we cannot " +
      "stand behind is withheld and labelled rather than guessed at."));
    next.appendChild(promise);
    wrap.appendChild(next);
    form.appendChild(wrap);

    go.textContent = "Run the full report";
    tally.textContent = card.deferred_count
      ? card.deferred_count + " optional questions come next, then it runs."
      : "";
    paint(be ? "Your numbers, before we look anything up"
             : "Here is where you stand so far",
      "This is arithmetic on what you told us, with the workings shown so you can check " +
      "it. Nothing here has been looked up yet.");
  }

  /* THE EXTRAS, asked after the founder commits and before the run starts.
     Placement is the whole point. These questions make the REPORT better and do nothing
     for the preview, so asking them earlier is friction spent before anyone has seen a
     number. Asking them during the six minute wait would be worse than useless: run_plan
     stamps the intake record before step one and never re-reads the session, so a late
     answer cannot reach the run and telling the founder it sharpens their report would be
     a lie. Here they have already decided, the answers still land, and skipping is one
     click and equally fine. */
  function renderExtras() {
    form.innerHTML = "";
    (card.deferred || []).forEach(function (q, i) {
      form.appendChild(renderQuestion(q, i));
    });
    go.textContent = "Start the report";
    skip.hidden = false;
    tally.textContent = "All optional. Blanks become disclosed assumptions, never silent ones.";
    paint("Anything else before it goes?",
      "These do not change what you have already seen. They make the report itself better, "
      + "and every one you leave blank is estimated and labelled as an estimate.");
  }

  /* ---- flow --------------------------------------------------------------------- */
  async function loadCard() {
    card = await api("GET", "/intake/" + session + "/preview");
    forkQ = null;
    if (!card.confident) {
      var plan = await api("GET", "/intake/" + session + "/form");
      forkQ = (plan.questions || []).filter(function (q) { return q.field === "kind_fork"; })[0] || null;
    }
  }

  async function submit() {
    err.textContent = "";
    go.disabled = true;
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
        // ONE model call in the whole free funnel: this extraction pass.
        var s = await api("POST", "/intake/start", { initial_message: prose });
        session = s.session_id;
        var url = new URL(location.href);
        url.searchParams.set("s", session);
        history.replaceState(null, "", url.toString());
        await loadCard();
        stage = forkQ ? 1 : 2;
        showStage();
        return;
      }

      var answers = readAnswers();
      if (answers._needPeriod) {
        answers._needPeriod.classList.add("need");
        setTimeout(function () { answers._needPeriod.classList.remove("need"); }, 420);
        err.textContent = "Pick a period for that number.";
        return;
      }

      if (stage === 3) {
        if ((card.deferred || []).length) { stage = 4; showStage(); return; }
        await launch();
        return;
      }
      if (stage === 4) {
        await api("POST", "/intake/" + session + "/form", { answers: answers });
        await launch();
        return;
      }

      await api("POST", "/intake/" + session + "/form", { answers: answers });
      await loadCard();
      stage = stage === 1 ? 2 : 3;
      showStage();
    } catch (e) {
      // Stage-appropriate copy. "Could not save that" is wrong at the CTA, where nothing
      // was being saved and the founder is committing.
      err.textContent = (stage === 3 ? "Could not start the report: "
                                     : "Could not save that: ") + e.message;
    } finally { go.disabled = false; }
  }

  function showStage() {
    if (stage === 0) return renderProse();
    if (stage === 1) {
      form.innerHTML = "";
      form.appendChild(renderQuestion(forkQ, 0));
      go.textContent = "Continue";
      tally.textContent = "";
      return paint("How does the money come in?",
        "This changes the shape of every financial table in the report, so it is the one " +
        "thing we will not guess at.");
    }
    if (stage === 2) return renderBrief();
    if (stage === 3) return renderReveal();
    return renderExtras();
  }

  async function launch() {
    var res = await api("POST", "/intake/" + session + "/confirm", { corrections: {} });
    var description = (res && res.final_description) || prose;
    if (!description || description.length < 30) {
      err.textContent = "The description is too short to research. Go back and say a " +
                        "little more about what the venture does.";
      return;
    }
    // ===== THE PAYWALL SEAM =====================================================
    // Everything up to here is free: one extraction call, then code. The report is the
    // expensive step (metered tools, the LLM chain, about six minutes), so this is where
    // a charge belongs. No processor is wired yet and no price is set, so for now this
    // launches the run directly. Insert the checkout between these two lines.
    var body = { description: description, operator_weights: {} };
    if (res && res.intake_record) body.intake = res.intake_record;
    try {
      var job = await api("POST", "/plan", body);
      location.href = "/progress.html?job=" + encodeURIComponent(job.job_id);
    } catch (e) {
      if (e.status === 429) { await blocked(e.detail); return; }
      throw e;
    }
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
          && /limit/i.test(detail || "")) {
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
            var out = await api("POST", "/billing/checkout", { kind: "report" });
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
    go.disabled = true;
    skip.disabled = true;
    try { await launch(); }
    catch (e) { err.textContent = "Could not start the report: " + e.message; }
    finally { go.disabled = false; skip.disabled = false; }
  };
  back.onclick = function () {
    if (stage === 4) stage = 3;
    else if (stage === 3) stage = 2;
    else if (stage === 2) stage = forkQ ? 1 : 0;
    else stage = 0;
    showStage();
  };
  form.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") { e.preventDefault(); submit(); }
  });

  /* Back from Stripe with a report credit: the founder pressed the button before paying,
     so pressing it for them on return is what they already asked for. */
  async function resumeAfterPurchase() {
    var paid = new URL(location.href).searchParams.get("paid");
    if (!paid || paid === "cancelled" || !session) return false;
    await loadCard();
    stage = 3;
    showStage();
    err.textContent = "Payment received. Starting your report…";
    await launch();
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
        if (await resumeAfterPurchase()) return;
        await loadCard();
        stage = forkQ ? 1 : 2;
        return showStage();
      } catch (e) { session = null; }
    }
    renderProse();
  })();
})();
