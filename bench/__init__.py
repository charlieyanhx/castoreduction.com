"""bench/ -- exercise one capability, or all 63, without building a report.

THE PROBLEM THIS EXISTS FOR. There are 43 tools, 13 skills and 7 agents in the
registries, and until now the only way to find out whether one of them still worked was
`python -m tools.run_live`: a full end-to-end report, several minutes, a chain of LLM
calls, and a verdict that says the report is thin without saying which of the 63 pieces
went quiet. A tool that silently returns [] and a tool that raises look identical from
the far end of a pipeline. Feedback that slow and that indirect means nobody runs it
after a small change, which is precisely when it would be cheapest to find the break.

WHAT THIS IS. One door onto every registered capability:

    .venv/bin/python -m bench list                     what is registered
    .venv/bin/python -m bench call hackernews_mentions query="pour over"
    .venv/bin/python -m bench smoke                    exercise everything
    .venv/bin/python -m bench doctor                   which ones bench cannot call

WHY IT IS CHEAP, and it is cheap for two reasons that already existed in this repo:

  HTTP    scrape/__init__.py installs requests-cache globally with a 24h TTL, so a
          second sweep of the tool surface re-reads sqlite instead of the internet.
  LLM     llm.call_json caches on the full prompt in .cache.sqlite. Fixture arguments
          are FIXED, so every bench run asks the same questions -- which means run one
          pays and every run after it is free. That is what `--llm cached` enforces:
          an LLM call is allowed only when the cache can answer it, and a miss is
          reported as a miss rather than quietly becoming a bill.

THE VERDICTS ARE NOT COLLAPSED, and that is the design. `empty`, `skeleton`, `refused`
and `error` mean four different things -- found nothing, inferred instead of fetched,
would not accept the arguments, blew up -- and a bench that printed "4 failed" would
throw away the only information worth having. They are counted and printed separately,
every time.
"""
from __future__ import annotations

from bench.capability import Capability, discover
from bench.runner import Result, run_many, run_one

__all__ = ["Capability", "discover", "Result", "run_one", "run_many"]
