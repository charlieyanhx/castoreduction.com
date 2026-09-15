"""A report page with a FAKE analyst, for working on the workshop sidebar.

Runs the real app on a throwaway database with one finished report seeded from the
synthesis fixture (a real fact layer with the real Opus report on it), and patches the
Anthropic client at the constructor the long-text path builds, exactly as the tests do,
so a chat turn or a rewrite costs nothing and reaches nothing. Every route, credit and
gate is the real one; only the model is a stand-in that answers after a short pause with
a cited sentence, or refuses, or fails, depending on what the founder types:

    "invent"   an answer with a number the evidence does not hold (the D62 refusal)
    "fail"     the backend raises (the credit comes back, the sidebar says so)
    "slow"     a twelve-second answer, to see the clock

Start it, open the URL it prints, and the browser's guest cookie owns the report (the
first request to /dev/claim hands it over). Never run this on a public port.

    .venv/bin/python scripts/dev_workshop_server.py [port]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Request     # module level: the route's annotation must resolve

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
FIXTURE = ROOT / "tests" / "fixtures" / "synthesis" / "diag01_result.json"
REPORT = ROOT / "tests" / "fixtures" / "synthesis" / "diag01_opus.md"
BRIEF = ("An independent specialty coffee shop with a small roastery, opening on a corner "
         "site in the Mission District of San Francisco, twelve hundred square feet, "
         "espresso around $5.50.")

os.environ["JOBS_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="castor-dev-"), "dev.sqlite")
os.environ["CASTOR_DAILY_RUNS"] = "50"
os.environ["ANTHROPIC_API_KEY"] = "sk-test-fake"
os.environ["LLM_ALLOW_PAID"] = "1"
os.environ["CASTOR_ALLOW_UNPAID_CREDITS"] = "1"       # the Buy button grants, no Stripe
for k in ("CASTOR_REQUIRE_LOGIN", "CASTOR_STUB_REPORT"):
    os.environ.pop(k, None)
# NO STRIPE, EVER, FROM HERE. api loads .env without overriding what is already set, so
# an empty key here beats the real one in .env and the Buy button takes the operator
# override instead of opening a checkout.
for k in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_WORKSHOP",
          "STRIPE_PRICE_REPORT", "RESEND_API_KEY"):
    os.environ[k] = ""


def _message(text: str):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=2000, output_tokens=200,
                              cache_read_input_tokens=44000, cache_creation_input_tokens=0))


class _Stream:
    def __init__(self, msg, delay):
        self.msg, self.delay = msg, delay

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        time.sleep(self.delay)
        return self.msg


ANSWER = ("The obtainable revenue is about $645,289 a year [market_sizing.som.mid]. Validate it "
          "first by counting foot traffic on the corner at the peak hours for a week; the "
          "report's anchor is a county-wide average, not this block.\n\n"
          "- The least certain input is rent, which the brief did not state.\n"
          "- The verdict would change if the average ticket fell below the low case "
          "[market_sizing.som.low].")
INVENTED = "Rent on that block runs about $9,400 a month, which the model did not include."


class _FakeAnthropic:
    """anthropic.Anthropic, as the long-text path builds it: messages.stream(**kw)."""

    def __init__(self, **kw):
        assert "sk-" in (kw.get("api_key") or "")
        self.messages = self

    def stream(self, **kw):
        if kw.get("max_tokens", 0) > 4000:
            # a rewrite: hand the same report back with one visible change
            text = REPORT.read_text(encoding="utf-8").replace("# ", "# (rewritten) ", 1)
            return _Stream(_message(text), 6)
        last = ""
        for m in reversed(kw.get("messages") or []):
            if m.get("role") == "user":
                last = str(m.get("content") or "").lower()
                break
        if "fail" in last:
            raise RuntimeError("the fake backend was asked to fail")
        if "invent" in last:
            return _Stream(_message(INVENTED), 2)
        return _Stream(_message(ANSWER), 12 if "slow" in last else 2.5)


def main() -> None:
    import uvicorn

    import api
    import iteration
    import jobs

    jobs._reset_for_tests()
    result = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result["synthesis"] = {"markdown": REPORT.read_text(encoding="utf-8"),
                           "model": "claude-opus-5", "style": "full",
                           "usd": 0.56, "seconds": 159}
    result["intake"] = {"facts": {"avg_ticket": "$5.50", "square_feet": "1200",
                                  "seats": "28"}, "unknowns": ["rent"]}
    jid = jobs.create("plan", {"description": BRIEF, "intake": result["intake"]})
    jobs.update(jid, state="complete", result=result)
    iteration.endow(jid, paid=True)

    @api.app.get("/dev/claim")
    def claim(request: Request, spend: int = 0):
        """Hand the seeded report to whoever is asking, then send them to it. `spend`
        drains that many credits first, to see the empty-pool state."""
        from fastapi.responses import RedirectResponse
        owner = api._current_owner(request)
        if spend:
            iteration.spend(jid, spend, "dev: drained")
        c = jobs._conn()
        c.execute("UPDATE jobs SET owner_id = ? WHERE id = ?", (owner, jid))
        c.commit(); c.close()
        return RedirectResponse(f"/jobs/{jid}/report.html")

    print(f"\n  open  http://127.0.0.1:{PORT}/dev/claim\n", flush=True)
    with patch("anthropic.Anthropic", _FakeAnthropic):
        uvicorn.run(api.app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
