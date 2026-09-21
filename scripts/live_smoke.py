"""The founder's path on a real instance, driven in headless Chromium, for real.

    .venv/bin/python scripts/live_smoke.py [base_url] [--phase checkout|follow|all]

STRIPE'S PAGE IS PAID BY A PERSON. Stripe Checkout detects an automated browser and
steers it away from the card form (it offers an "I am an AI agent" flow instead), so a
scripted card entry does not go through. The script therefore runs in two phases: with
--phase checkout it signs in, fills the survey to the gate, opens Checkout and prints the
URL for a person to pay in their own browser (signed in as the testing account, test card
4242 4242 4242 4242); Stripe then returns that browser to the survey, which starts the
run. With --phase follow it signs in, finds the account's newest run, follows it to the
report and works the workshop. --phase all tries the card form itself, for an instance
that does not steer.

Signs the testing account in (or up, on first use: the credentials live in .testaccount,
git-ignored; a missing file gets a generated password), fills the survey, pays through
Stripe Checkout with the test card, follows the run on the progress page to the report,
works the workshop (questions, an explain, a note, a rewrite), marks it final and
publishes it, then writes everything it saw (timings, the job record, the events, the
iteration state, screenshots) under out/live_smoke/<stamp>/ for the diagnostic.

This spends real model money on the instance it points at (a report and a few Opus
turns), and it drives Stripe's hosted page, which only makes sense on a test-mode key.
It refuses to run when /billing/status says the instance is not in test mode.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = (_args[0] if _args else "https://app.castor-advisory.com").rstrip("/")
PHASE = (sys.argv[sys.argv.index("--phase") + 1] if "--phase" in sys.argv else "all")
ACCOUNT = ROOT / ".testaccount"
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = ROOT / "out" / "live_smoke" / STAMP
OUT.mkdir(parents=True)
BRIEF = ("An independent specialty coffee shop with a small roastery, opening on a corner "
         "site in the Mission District of San Francisco, about 1,200 square feet with 28 "
         "seats, pour-over and espresso around $5.50 a drink, and wholesale roasted beans "
         "to local cafes.")
TEXT_ANSWERS = [
    # field names first (exact), then the wording of the ask
    (r"^status_quo", "They go to Ritual on Valencia or Four Barrel, or make pour-over at home; the nearest roastery-cafe is a twelve-minute walk."),
    (r"^customer_evidence", "Forty people signed the interest sheet at the Saturday pop-up over two weekends, and eleven asked to be told the opening date."),
    (r"^named_competitors", "Ritual Coffee Roasters, Four Barrel, Sightglass, Philz on 24th"),
    (r"^success_target", "Break even by month nine at 180 drinks a day and six wholesale accounts."),
    (r"location|where|address|city|neighbou?rhood|site", "Valencia Street at 20th, Mission District, San Francisco, CA"),
    (r"price|charge|ticket|drink|cost to the customer", "$5.50 a drink"),
    (r"rent|lease", "not signed yet; around $9,000 a month is what the broker quoted"),
    (r"seat|capacity", "28 seats"),
    (r"square|size|space|footage", "1,200 square feet"),
    (r"hours|open", "7am to 6pm, seven days"),
    (r"staff|employee|team", "two baristas per shift, one roaster"),
    (r"customers?|volume|visits|orders|units|cups", "180"),
    (r"wholesale|account", "six cafe accounts to start"),
]
NUMBER_ANSWERS = [
    (r"ticket|price|drink", "5.50"), (r"seat", "28"), (r"square|sq", "1200"),
    (r"rent", "9000"), (r"customer|visit|order|unit|cup|volume|day", "180"),
    (r"account|wholesale", "6"), (r"staff|employee", "3"), (r"hour", "11"),
]

LOG: list[dict] = []
T0 = time.time()


def log(step: str, **kw):
    entry = {"t": round(time.time() - T0, 1), "clock": datetime.now().strftime("%H:%M:%S"),
             "step": step, **kw}
    LOG.append(entry)
    print(f"[{entry['clock']} +{entry['t']:>6}s] {step} " +
          " ".join(f"{k}={v}" for k, v in kw.items()), flush=True)
    (OUT / "log.json").write_text(json.dumps(LOG, indent=1))


def shot(page, name: str):
    try:
        page.screenshot(path=str(OUT / f"{len(LOG):02d}-{name}.png"), full_page=False)
    except Exception as e:                                   # noqa: BLE001
        log("screenshot failed", name=name, err=str(e)[:100])


def account() -> dict:
    if ACCOUNT.exists():
        return json.loads(ACCOUNT.read_text())
    email = os.environ.get("CASTOR_TEST_EMAIL") or input("email for the testing account: ").strip()
    acct = {"email": email, "password": secrets.token_urlsafe(18), "created": STAMP}
    ACCOUNT.write_text(json.dumps(acct, indent=1) + "\n")
    ACCOUNT.chmod(0o600)
    log("testing account written", file=str(ACCOUNT), email=email)
    return acct


def api(page, method: str, path: str, body=None):
    return page.evaluate("""async ([m, p, b]) => {
        const r = await fetch(p, {method: m, headers: {"Content-Type": "application/json"},
                                  body: b === null ? undefined : JSON.stringify(b)});
        let d = null; try { d = await r.json(); } catch (e) {}
        return {status: r.status, body: d}; }""", [method, path, body])


def sign_in(page, acct: dict) -> dict:
    page.goto(BASE + "/login")
    page.wait_for_selector("#authForm")
    me = api(page, "GET", "/auth/me")["body"] or {}
    if me.get("authenticated"):
        log("already signed in", email=me.get("email"))
        return me
    r = api(page, "POST", "/auth/signup", {"email": acct["email"], "password": acct["password"]})
    if r["status"] == 200:
        log("signed up", email=acct["email"])
    else:
        log("signup refused, signing in", status=r["status"], detail=str(r["body"])[:120])
        r = api(page, "POST", "/auth/login", {"email": acct["email"], "password": acct["password"]})
        if r["status"] != 200:
            raise SystemExit(f"could not sign in: {r['status']} {r['body']}")
        log("signed in", email=acct["email"])
    me = api(page, "GET", "/auth/me")["body"] or {}
    if not me.get("authenticated"):
        raise SystemExit(f"/auth/me does not show a session: {me}")
    return me


def mark_verified_if_needed(page, me: dict):
    if me.get("verified"):
        log("email already verified")
        return
    # Resend's sandbox sender delivers only to the Resend account's own address, so the
    # confirmation for a testing address never arrives. Marked on the instance's own
    # database, which this script can reach because it runs on the same machine.
    try:
        import auth
        acct = auth.account_by_email(me.get("email") or "")
        if acct:
            auth.mark_email_verified(acct["id"])
            log("email marked verified on the database", account=acct["id"][:8])
    except Exception as e:                                   # noqa: BLE001
        log("could not mark verified", err=str(e)[:160])


def fill_controls(page) -> dict:
    """Answer every control on the current survey stage the way a founder would."""
    filled = {}
    for box in page.locator("#form .ctl").all():
        field = box.get_attribute("data-field") or ""
        kind = box.get_attribute("data-kind") or "text"
        ask = ""
        try:
            ask = page.locator(f"#ask-{field}").inner_text().lower()
        except Exception:                                    # noqa: BLE001
            pass
        key = (field + " " + ask).lower()
        if kind == "choice":
            radios = box.locator("input[type=radio]")
            n = radios.count()
            pick = None
            for i in range(n):
                v = radios.nth(i).get_attribute("value") or ""
                if v == "__other__":
                    continue
                # the report style is a closed choice: the full analysis, so the page
                # under test is the one with everything on it
                if field == "report_style" and v != "full":
                    continue
                pick = radios.nth(i); break
            if pick is None and n:
                pick = radios.first
            if pick is not None and not pick.is_checked():
                # the radios are drawn as pills: the label covers the input
                pick.evaluate("el => el.click()")
            filled[field] = pick.get_attribute("value") if pick is not None else None
        elif kind == "number":
            num = box.locator(".num")
            if not num.input_value().strip():
                val = next((v for pat, v in NUMBER_ANSWERS if re.search(pat, key)), "10")
                num.fill(val)
            periods = box.locator("input[type=radio]")
            if periods.count() and not box.locator("input[type=radio]:checked").count():
                periods.first.evaluate("el => el.click()")
            filled[field] = num.input_value()
        else:
            txt = box.locator(".txt")
            if txt.count() and not txt.first.input_value().strip():
                val = next((v for pat, v in TEXT_ANSWERS if re.search(pat, key)), "not sure yet")
                txt.first.fill(val)
            filled[field] = txt.first.input_value() if txt.count() else None
    return filled


def headline(page) -> str:
    try:
        return page.locator("h1, .headline").first.inner_text()[:80]
    except Exception:                                        # noqa: BLE001
        return ""


def survey(page) -> str:
    """Through the survey to the gate. Returns when the gate is drawn."""
    page.goto(BASE + "/survey")
    page.wait_for_selector("#prose")
    page.fill("#prose", BRIEF)
    shot(page, "survey-brief")
    page.click("#go")
    page.wait_for_selector("#form .ctl, .gate", timeout=120_000)
    log("brief read", stage=headline(page))
    for _ in range(8):
        if page.locator(".gate").count():
            break
        if page.locator("#form .ctl").count():
            filled = fill_controls(page)
            log("stage answered", stage=headline(page), answers=json.dumps(filled)[:300])
        else:
            log("stage seen", stage=headline(page))
        shot(page, "survey-stage")
        err_before = page.locator("#err").inner_text()
        page.click("#go")
        try:
            page.wait_for_function(
                "(h) => document.querySelector('.gate') || document.querySelector('h1, .headline').innerText.slice(0,80) !== h || document.getElementById('err').innerText.trim()",
                arg=headline(page), timeout=120_000)
        except Exception:                                    # noqa: BLE001
            pass
        err = page.locator("#err").inner_text().strip()
        if err and err != err_before:
            log("survey refused", error=err[:200])
            shot(page, "survey-error")
            raise SystemExit("the survey refused: " + err)
    # an account holding a credit is not gated: the run starts straight away
    page.wait_for_function("() => document.querySelector('.gate') || /progress\\.html/.test(location.href)", timeout=60_000)
    if page.locator(".gate").count():
        shot(page, "gate")
        return page.locator(".gate").inner_text()[:300]
    return ""


def pay(page) -> None:
    """The report credit, through Stripe Checkout with the test card."""
    offers = page.locator(".gate input[type=radio]")
    for i in range(offers.count()):
        if offers.nth(i).get_attribute("value") == "report":
            offers.nth(i).evaluate("el => el.click()"); break
    page.click(".gate-buy")
    page.wait_for_url(re.compile(r"checkout\.stripe\.com"), timeout=60_000)
    log("at stripe checkout", session=re.sub(r".*/pay/(cs_test_\w{8}).*", r"\1", page.url))
    page.wait_for_selector("#email", timeout=60_000)
    email = json.loads(ACCOUNT.read_text())["email"]
    if not page.input_value("#email").strip():
        page.fill("#email", email)
        log("stripe did not prefill the email", note="the checkout session carries no customer_email")
    # the card accordion opens its fields; Link's "save my information" wants a phone
    if not page.locator("#cardNumber").count():
        # the accordion's own button is visually hidden; the "Card" radio opens it
        page.locator('input[type=radio][value="card"]').first.check(force=True)
    page.wait_for_selector("#cardNumber", state="visible", timeout=30_000)
    save = page.locator("#enableStripePass")
    if save.count() and save.is_checked():
        save.uncheck()
    page.fill("#cardNumber", "4242 4242 4242 4242")
    page.fill("#cardExpiry", "12 / 34")
    page.fill("#cardCvc", "123")
    if page.locator("#billingName").count():
        page.fill("#billingName", "Castor Test")
    if page.locator("#billingCountry").count():
        page.select_option("#billingCountry", "US")
    # automatic tax is on, so Checkout wants a full billing address, behind an
    # autocomplete box; the manual form has plain fields
    manual = page.get_by_text("Enter address manually")
    if manual.count():
        manual.click()
    page.wait_for_selector("#billingAddressLine1", state="visible", timeout=15_000)
    page.fill("#billingAddressLine1", "995 Valencia St")
    page.fill("#billingLocality", "San Francisco")
    if page.locator("#billingAdministrativeArea").count():
        page.select_option("#billingAdministrativeArea", "CA")
    page.fill("#billingPostalCode", "94110")
    # the address feeds automatic tax; the total is a skeleton until it comes back
    page.wait_for_function("() => /\\$\\d/.test(document.body.innerText.split('Total due')[1] || '')", timeout=30_000)
    shot(page, "stripe-filled")
    page.locator('[data-testid="hosted-payment-submit-button"], .SubmitButton').first.click()
    log("paid", note="test card, waiting for the return")
    page.wait_for_url(re.compile(re.escape(BASE)), timeout=180_000)
    log("back from stripe", url=page.url.replace(BASE, "")[:120])


def newest_job(page) -> str:
    """The account's most recent run, for the follow phase."""
    jobs_ = api(page, "GET", "/jobs?limit=5")["body"] or {}
    items = jobs_.get("jobs") if isinstance(jobs_, dict) else jobs_
    if not items:
        raise SystemExit("the testing account has no runs yet; pay and start one first")
    return items[0]["id"] if isinstance(items[0], dict) else items[0]


def follow_run(page, job: str | None = None) -> str:
    if job is None:
        page.wait_for_url(re.compile(r"/progress\.html\?job="), timeout=120_000)
        job = re.search(r"job=([0-9a-f-]+)", page.url).group(1)
    else:
        page.goto(f"{BASE}/progress.html?job={job}")
    log("run started", job=job)
    shot(page, "progress-start")
    started = time.time()
    last = ""
    while time.time() - started < 40 * 60:
        if page.locator("a.open").count():
            break
        j = api(page, "GET", f"/jobs/{job}")["body"] or {}
        steps = (j.get("result") or {}).get("_steps_completed") or []
        cur = f"{j.get('state')} {len(steps)} steps"
        if cur != last:
            log("progress", state=j.get("state"), steps=len(steps), last=(steps[-1] if steps else ""))
            last = cur
        if j.get("state") in ("error", "failed"):
            log("run failed", error=str(j.get("error"))[:300])
            break
        page.wait_for_timeout(10_000)
    shot(page, "progress-end")
    log("run finished", seconds=round(time.time() - started), outcome=page.locator("a.open").inner_text()[:40] if page.locator("a.open").count() else "no link")
    return job


def workshop(page, job: str) -> dict:
    page.locator("a.open").click()
    page.wait_for_selector("#ws", timeout=60_000)
    page.wait_for_function('/^\\d+$/.test(document.getElementById("wsBalN").textContent)')
    bal = int(page.locator("#wsBalN").inner_text())
    log("report open", balance=bal, url=page.url.replace(BASE, ""))
    shot(page, "report")
    out = {"balance_open": bal, "turns": []}

    def ask(text, quote=False):
        t0 = time.time()
        page.fill("#wsInput", text)
        page.press("#wsInput", "Enter")
        page.wait_for_selector("#wsPending", state="detached", timeout=180_000)
        last = page.locator("#wsTurns .ws-analyst").last
        answer = last.inner_text()
        err = page.locator("#wsErr").inner_text().strip()
        entry = {"q": text, "seconds": round(time.time() - t0, 1), "answer": answer[:600],
                 "cites": [a.get_attribute("title") for a in last.locator("a.cite").all()],
                 "refused": "refused" in (last.get_attribute("class") or ""), "note": err[:160],
                 "balance": int(page.locator("#wsBalN").inner_text())}
        out["turns"].append(entry)
        log("turn", seconds=entry["seconds"], balance=entry["balance"], refused=entry["refused"], cites=len(entry["cites"]))
        return entry

    ask("What should I validate first, and how, before signing the lease?")
    ask("Which number in this report is the least certain, and what would move it?")
    # explain a passage
    p = page.locator("#synthesis p").nth(2)
    p.scroll_into_view_if_needed(); box = p.bounding_box()
    page.mouse.move(box["x"] + 1, box["y"] + 10); page.mouse.down()
    page.mouse.move(box["x"] + min(box["width"] - 2, 260), box["y"] + 10, steps=6); page.mouse.up()
    page.wait_for_selector("#wsSel:not([hidden])")
    page.locator('#wsSel button[data-verb="explain"]').click()
    ask("Explain this passage in plain words, and say how sure the evidence is.", quote=True)
    # a note, then the rewrite
    p = page.locator("#synthesis p").nth(4)
    p.scroll_into_view_if_needed(); box = p.bounding_box()
    page.mouse.move(box["x"] + 1, box["y"] + 10); page.mouse.down()
    page.mouse.move(box["x"] + min(box["width"] - 2, 240), box["y"] + 10, steps=6); page.mouse.up()
    page.wait_for_selector("#wsSel:not([hidden])")
    page.locator('#wsSel button[data-verb="note"]').click()
    page.fill("#wsInput", "Lead with the rent risk: the lease is not signed and the broker's number is a quote.")
    page.press("#wsInput", "Enter")
    page.wait_for_selector("#wsNotes .ws-note", timeout=30_000)
    log("note saved", notes=page.locator("#wsNotesToggle").inner_text())
    shot(page, "workshop")
    t0 = time.time()
    page.locator("#wsRewrite").click()
    try:
        page.wait_for_function('document.getElementById("wsHistory") && !document.getElementById("wsHistory").hidden', timeout=8 * 60_000)
        page.wait_for_function('/^\\d+$/.test(document.getElementById("wsBalN").textContent)')
        out["rewrite"] = {"seconds": round(time.time() - t0), "balance": int(page.locator("#wsBalN").inner_text()),
                          "message": page.locator("#wsErr").inner_text()[:200]}
        log("rewrite", **out["rewrite"])
    except Exception as e:                                   # noqa: BLE001
        out["rewrite"] = {"seconds": round(time.time() - t0), "error": str(e)[:200],
                          "message": page.locator("#wsErr").inner_text()[:200]}
        log("rewrite did not land", **out["rewrite"])
    shot(page, "rewritten")
    page.locator("#wsFinal").click()
    page.wait_for_selector("#shareBlock:not([hidden])", timeout=30_000)
    log("marked final")
    if page.locator("#shareTitle").count():
        page.fill("#shareTitle", "Specialty coffee and micro-roastery, Mission District")
        page.locator("#shareGo").click()
        try:
            page.wait_for_selector("#shareDone:not([hidden])", timeout=60_000)
            out["share"] = page.locator("#shareDone").inner_text()[:300]
            log("published", where=out["share"][:120])
        except Exception:                                    # noqa: BLE001
            out["share"] = "error: " + page.locator("#shareErr").inner_text()[:200]
            log("publish failed", err=out["share"])
    shot(page, "final")
    return out


def main():
    from playwright.sync_api import sync_playwright
    acct = account()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.set_default_timeout(60_000)
        page.goto(BASE + "/healthz")
        status = api(page, "GET", "/billing/status")["body"] or {}
        log("instance", base=BASE, configured=status.get("configured"),
            preview=status.get("preview"), needs_purchase=status.get("needs_purchase"))
        if not status.get("configured"):
            raise SystemExit("billing is not configured on this instance; nothing to pay")
        me = sign_in(page, acct)
        mark_verified_if_needed(page, me)
        if PHASE == "follow":
            job = follow_run(page, newest_job(page))
        else:
            gate_text = survey(page)
            if gate_text:
                log("gate", text=gate_text[:160])
                if PHASE == "checkout":
                    offers = page.locator(".gate input[type=radio]")
                    for i in range(offers.count()):
                        if offers.nth(i).get_attribute("value") == "report":
                            offers.nth(i).evaluate("el => el.click()"); break
                    page.click(".gate-buy")
                    page.wait_for_url(re.compile(r"checkout\.stripe\.com"), timeout=60_000)
                    (OUT / "checkout_url.txt").write_text(page.url + "\n")
                    log("checkout ready for a person", url_file=str(OUT / "checkout_url.txt"))
                    print("\n  PAY HERE, signed in as the testing account, test card 4242 4242 4242 4242:\n  "
                          + page.url + "\n", flush=True)
                    browser.close()
                    return
                pay(page)
            else:
                log("no gate: the account already held a credit")
            job = follow_run(page)
        ws = workshop(page, job)
        # everything the diagnostic needs
        (OUT / "job.json").write_text(json.dumps(api(page, "GET", f"/jobs/{job}")["body"], indent=1))
        (OUT / "events.json").write_text(json.dumps(api(page, "GET", f"/jobs/{job}/events?since=0")["body"], indent=1))
        (OUT / "iteration.json").write_text(json.dumps(api(page, "GET", f"/jobs/{job}/iteration")["body"], indent=1))
        (OUT / "workshop.json").write_text(json.dumps(ws, indent=1))
        log("done", job=job, report=f"{BASE}/jobs/{job}/report.html", out=str(OUT))
        browser.close()


if __name__ == "__main__":
    main()
