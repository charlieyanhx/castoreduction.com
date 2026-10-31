/* account.js — the one sign-in control, on every page of the site.
 *
 * WHY A SCRIPT AND NOT MARKUP IN SIX FILES. The survey, the progress screen, the library,
 * the landing page, the report and the console are six separately authored documents with
 * four different header structures and, in the report's case, no header at all. Six copies
 * of a login button is six chances for one of them to drift, and the drift that matters is
 * the page that quietly has no way to sign in — which is the state the whole site was in.
 *
 * THE BUTTON IS ALWAYS THERE AND IT ALWAYS GOES TO THE SAME PLACE. /login serves both
 * halves: it opens on Sign in and swaps to Sign up in place, so one control answers both
 * "I have an account" and "I need one". It carries ?next= so signing in returns the
 * visitor to the page they were reading rather than dumping them at the front door.
 */
(function () {
  "use strict";
  if (window.__castorAccountMounted) return;
  window.__castorAccountMounted = true;

  var CSS =
    '.ca-wrap{display:inline-flex;align-items:center;gap:8px;font-family:inherit;' +
      'font-size:13px;line-height:1;white-space:nowrap}' +
    '.ca-wrap.ca-float{position:fixed;top:12px;right:14px;z-index:9999;' +
      'background:var(--surface,#fff);border:1px solid var(--line,#e5e7eb);' +
      'border-radius:999px;padding:6px 8px;' +
      'box-shadow:0 1px 2px rgba(0,0,0,.05),0 6px 20px -8px rgba(0,0,0,.14)}' +
    // The landing page's own CTA fill (#2B3C2B), so the button does not change colour
    // when a visitor crosses from the marketing page into the app.
    '.ca-btn{font:inherit;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;' +
      'padding:7px 14px;border-radius:8px;' +
      'border:1px solid var(--brand-action,#2B3C2B);' +
      'background:var(--brand-action,#2B3C2B);color:#fff}' +
    '.ca-btn:hover{opacity:.92;color:#fff}' +
    '.ca-who{color:var(--text-dim,#6b7280);max-width:19ch;overflow:hidden;' +
      'text-overflow:ellipsis}' +
    '.ca-out{font:inherit;font-size:12.5px;font-weight:600;cursor:pointer;padding:6px 11px;' +
      'border-radius:8px;border:1px solid var(--line,#e5e7eb);' +
      'background:var(--surface,#fff);color:var(--text-dim,#6b7280)}' +
    '.ca-out:hover{border-color:var(--accent,#0f766e);color:var(--accent,#0f766e)}' +
    '@media print{.ca-wrap,.ca-nudge{display:none!important}}' +
    '.ca-nudge{position:fixed;right:16px;bottom:16px;z-index:9998;max-width:330px;' +
      'display:flex;gap:12px;align-items:flex-start;padding:14px 15px;' +
      'border-radius:13px;border:1px solid var(--line,#e5e7eb);' +
      'background:var(--surface,#fff);color:var(--text,#1f2937);' +
      'font-family:inherit;font-size:13.5px;line-height:1.5;' +
      'box-shadow:0 2px 6px rgba(0,0,0,.06),0 14px 40px -14px rgba(0,0,0,.28)}' +
    '.ca-nudge b{display:block;font-size:14.5px;margin-bottom:3px}' +
    '.ca-nudge p{margin:0 0 10px;color:var(--text-dim,#6b7280)}' +
    '.ca-nudge-x{position:absolute;top:8px;right:10px;border:0;background:none;' +
      'cursor:pointer;font-size:15px;line-height:1;color:var(--text-faint,#9ca3af);padding:2px}' +
    '.ca-nudge-x:hover{color:var(--text,#1f2937)}' +
    '@media (max-width:520px){.ca-nudge{left:12px;right:12px;max-width:none}}';

  function styles() {
    if (document.getElementById("ca-style")) return;
    var s = document.createElement("style");
    s.id = "ca-style";
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  /* Four header shapes and a fallback. .rail-acts is the library's existing action group,
     so the control joins it rather than sitting beside it; the report has no header at
     all, which is what the floating pill is for. */
  function mount() {
    var host = document.querySelector(".rail-acts")
            || document.querySelector(".nav-cta")
            || document.querySelector("header.rail")
            || document.querySelector("header.topbar")
            || document.querySelector("header");
    /* A HARDCODED "Sign in" IS RIGHT UNTIL SOMEONE SIGNS IN. The marketing page ships one
       so the door exists without JavaScript, and it then told returning customers to sign
       in on every visit. Marked links stand down the moment this control mounts, so the
       no-JS fallback survives and the stale copy does not. */
    [].forEach.call(document.querySelectorAll("[data-account-fallback]"), function (el) {
      el.hidden = true;
      el.style.display = "none";      // [hidden] loses to any author display rule
    });

    var w = document.createElement("span");
    w.className = "ca-wrap";
    w.setAttribute("data-account-control", "");
    if (host) {
      if (!host.classList.contains("rail-acts")) w.style.marginLeft = "auto";
      host.appendChild(w);
    } else {
      w.classList.add("ca-float");
      document.body.appendChild(w);
    }
    return w;
  }

  function here() {
    return encodeURIComponent(location.pathname + location.search);
  }

  function draw(w, me) {
    // A page that ships its own signed-in row (the operator console) keeps it; this
    // control still supplies the half that page never had, which is the way IN.
    var native = document.querySelector("[data-account-native]");
    w.textContent = "";

    if (!me || !me.authenticated) {
      // A guest who has already made something is not being asked to "sign in" — they
      // are being told their work is unsaved. The label says which of the two it is.
      var n = (me && me.reports) || 0;
      var a = document.createElement("a");
      a.className = "ca-btn";
      a.href = "/login?next=" + here();
      a.textContent = n > 0 ? "Save your work" : "Sign in";
      w.appendChild(a);
      if (n > 0) nudge(n);
      return;
    }
    if (native) { w.remove(); return; }

    var who = document.createElement("span");
    who.className = "ca-who";
    who.textContent = me.email || "Signed in";
    var out = document.createElement("button");
    out.className = "ca-out";
    out.type = "button";
    out.textContent = "Sign out";
    out.addEventListener("click", function () {
      out.disabled = true;
      fetch("/auth/logout", {method: "POST"})
        .then(function () { location.href = "/login"; })
        .catch(function () { out.disabled = false; });
    });
    w.appendChild(who);
    w.appendChild(out);
  }

  /* THE ENCOURAGEMENT, AND ITS LIMIT.
   *
   * A guest can use the whole product and is never blocked, so the only honest reason to
   * ask them to register is that their work is at risk — which is true, and specific:
   * their reports are reachable by one cookie in one browser. So the card says the number
   * and what happens to it, and does not pretend to be an offer.
   *
   * It is dismissible, and the dismissal remembers HOW MUCH they had when they waved it
   * away. Ask again when they have more to lose, not on the next page load: a nudge that
   * reappears after being closed is not encouragement, it is nagging, and it teaches
   * people to close things without reading them.
   */
  var SEEN = "castor.nudge.dismissed.at";

  function dismissedAt() {
    try { return parseInt(localStorage.getItem(SEEN) || "0", 10) || 0; }
    catch (e) { return 0; }
  }

  function nudge(count) {
    if (document.querySelector(".ca-nudge")) return;
    if (count <= dismissedAt()) return;
    // NOT WHILE A REPORT IS RUNNING. The progress screen is six minutes the reader is
    // already committed to, and a card telling them their work is unsaved reads as though
    // something is wrong with the run they are watching. It will still be there when they
    // land on the report.
    if (location.pathname === "/progress.html") return;
    var box = document.createElement("aside");
    box.className = "ca-nudge";
    box.setAttribute("role", "note");
    box.style.position = "fixed";

    var body = document.createElement("div");
    var head = document.createElement("b");
    head.textContent = count === 1 ? "Your report is not saved yet"
                                   : "Your " + count + " reports are not saved yet";
    var p = document.createElement("p");
    p.textContent = "They live in this browser only. Create a free account and they "
                  + "move with you.";
    var go = document.createElement("a");
    go.className = "ca-btn";
    go.href = "/login?next=" + here();
    go.textContent = "Create an account";
    body.appendChild(head); body.appendChild(p); body.appendChild(go);

    var x = document.createElement("button");
    x.className = "ca-nudge-x";
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss");
    x.textContent = "\u00d7";
    x.addEventListener("click", function () {
      try { localStorage.setItem(SEEN, String(count)); } catch (e) { /* private mode */ }
      box.remove();
    });

    box.appendChild(body);
    box.appendChild(x);
    document.body.appendChild(box);
  }

  function boot() {
    if (location.pathname === "/login") return;      // the page is the button
    styles();
    var w = mount();
    fetch("/auth/me", {headers: {accept: "application/json"}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (me) { draw(w, me); })
      // A failed /auth/me must still leave a way in: offering the door is never wrong,
      // and a silent header is the exact failure this file exists to remove.
      .catch(function () { draw(w, null); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
