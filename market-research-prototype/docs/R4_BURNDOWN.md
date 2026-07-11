# Wave 2.75 — R4 criticals burn-down (D7–8) — EXECUTOR BRIEF

Self-contained brief for a lower-tier executor (Sonnet-class; Haiku OK for B1/B4).
Planned + root-caused on Fable 5 against the verified Wave-2 R4 panel
(`docs/baselines/wave2_r4.json`: 35.9% pass, 31 CRITICAL). D15 (already shipped,
2c94c13) removed the largest cluster. The four items here address ~19 of the
remaining criticals with deterministic fixes. Wave 3 (ledger/resume) shifts +2 days —
rationale: these move report trust now; persistence doesn't.

## Environment facts (do not re-derive)

- Repo: `/Users/charlieyan/Downloads/castor-advisories/market-research-prototype`
- Python: `.venv/bin/python` — tests: `.venv/bin/python -m pytest <files> -q -p no:cacheprovider`
- Full sweep: `.venv/bin/python -m pytest $(ls test_*.py | grep -v live | tr '\n' ' ') -q -p no:cacheprovider`
  — must end `N passed, 0 failed`, no skips. Baseline N=779; each item only adds.
- Report gates: `.venv/bin/python gates.py --corpus out/wave2_corpus --gate all`
- Stored 16-venture corpus (JSON+HTML): `out/wave2_corpus/` (do not regenerate per item)
- The 16 ventures' inputs live in `.jobs.sqlite` (newest 16 complete `plan` jobs)

## Rules (from CC_HARNESS_PLAN.md §5a — binding)

1. One item per commit. RED first: write the test, RUN it, confirm it FAILS for the
   stated reason. If RED does not reproduce, STOP and report — do not force it.
2. Then the minimal fix → full sweep green → corpus SIMULATION (given per item; no
   LLM, no regen) → commit with the evidence in the message.
3. Never loosen a gate/threshold. New detectors are severity `"fail"`, N/A generous.
4. Do not touch files outside the item's list. Do not "improve" nearby code.
5. Commit message template:
   `fix(<detector>/<area>): <one line>` + body: baseline failure → root cause →
   fix → RED/GREEN evidence → simulation result → `full sweep N/N`.

---

## B1 — competitor_density counts signals, not competitors  → detector D16
**Kills:** R11 criticals (955a4b3b, 94008e7c) + e8baf9dd R8's "density=1 vs 30". 14/16 ventures affected.

Root cause (verified): `discover.py:511-516` — `density = sum(1 for e in enriched_sorted
if e._score > 20)`. That is a *web-momentum* count; a cafe with 30 real OSM venues and no
web signals gets density=1, and `four_ps.py` viability (prompt line ~137 "Competitive
density: {density} meaningful competitors") faithfully renders "only 1 meaningful
competitor" → wrong score reasoning.

Fix:
1. `discover.py:516`: `result["competitor_density"] = len(enriched_sorted)`; keep the
   old count as NEW field `result["active_signal_density"] = <the _score>20 count>`.
2. `plan.py:69` and `plan.py:1978`: pass BOTH through (add `active_signal_density`
   kwarg; follow the existing `density=` pattern).
3. `four_ps.py` viability prompt (~line 137): render
   `"- Competitive density: {density} competitors identified ({active_density} with active momentum signals)"`.
   Thread `active_density` through `score_viability`'s signature with default 0.
4. Detector in `gates.py` (append after d15, register in INVARIANTS, severity fail):
   `D16 "competitor_density matches the ranked set"` — FAIL when
   `discover.competitor_density` < half of `len(ranked_opportunities)` (ranked from
   `discover.ranked_opportunities` or `discover.synthesis.ranked_opportunities`);
   N/A when no ranked list. (Half, not exact: synthesis may trim.)

RED tests (`test_report_data_fixes.py`, new class `TestCompetitorDensity`):
- D16 fires on this real shape: `{"discover": {"competitor_density": 1, "synthesis":
  {"ranked_opportunities": [{}]*30}}}` → ok False.
- After-fix unit: build 9 enriched entries, 2 with `_score>20` → `competitor_density==9`,
  `active_signal_density==2` (call the discover step or extract its density block into
  a small pure function `_density_counts(enriched) -> tuple[int,int]` and test that).

Simulation (paste as-is, expect **14 → 0** D16 failures after recomputing density):
```python
# sim: recompute density from stored ranked lists across out/wave2_corpus
import json, glob
from gates import d16_density_matches_ranked  # your new detector
for f in glob.glob("out/wave2_corpus/*.json"):
    r = json.load(open(f))["result"]; d = r.get("discover") or {}
    ops = d.get("ranked_opportunities") or (d.get("synthesis") or {}).get("ranked_opportunities") or []
    d["competitor_density"] = len(ops)   # what the fixed pipeline would store
    assert d16_density_matches_ranked({"discover": d}, None).ok is not False, f
```

## B2 — hybrid picks the /mo component as the device price  → detector D17
**Kills:** all four 8add1fa2 criticals (R2/R6/R7/R12 chain).

Root cause (verified): `plan.py:1663` `_price_per_unit = float(_unit_price or _stated
or _opt)`; for "A $199 device plus a $5 per month premium app subscription",
`extract_unit_price → None`, `extract_stated_price → 5.0` (the /mo component). Then:
price $5 vs $45 COGS → economics `{"error": ...}` → `financials.project_three_year`
falls back to the SUBSCRIPTION path (it requires error-free economics for the
transactional branch, `financials.py:99-107`) → one-time hardware churn-annualized.

Fix:
1. `plan.py`, next to `extract_unit_price` (~line 262): new
   `extract_device_price(text) -> float | None` — regex for a $ amount whose 6
   trailing words contain `device|hardware|unit|kit|sensor|monitor` and that is NOT
   followed by `/mo|per month|a month|monthly`. Must return 199.0 for the fixture
   above and None for "a SaaS at $99/month".
2. `plan.py:1661-1665`: when `biz_kind == "hybrid"`:
   `_price_per_unit = float(_unit_price or extract_device_price(description) or ...)`
   — and if the ONLY price found is monthly (device price None, `_stated` came from a
   `/mo` phrase — reuse `extract_unit_price`/`extract_stated_price` outputs to tell),
   do NOT run retail economics on it; leave economics to the cycle38 disclosure path
   (see the `else` branch below plan.py:1637 for marketplace/ad_supported — mirror it).
3. Detector `D17 "per-unit venture never on the subscription fallback"` in gates.py:
   FAIL when `business_model_kind` is per-unit (`transactional|ecommerce|services|hybrid`)
   AND `economics.model == "transactional"` AND `financials.scenarios.base.year_3`
   contains key `customers` (the subscription shape). N/A when financials absent.

RED tests (`test_report_data_fixes.py`, class `TestHybridDevicePrice`):
- `extract_device_price("A smart home air-quality monitor — $199 device plus a $5 per month premium app subscription") == 199.0`
- `extract_device_price("a B2B SaaS at $99/month") is None`
- D17 fires on the stored 8add1fa2: load `out/wave2_corpus/8add1fa2.json`, run your
  d17 on `result` → ok False (its financials year_3 has `customers`).

Simulation: after the fix, rebuild the price pick for the 8add1fa2 description
(fixture string above) → assert chosen price == 199.0; then
`retail_unit_economics(199.0, 45.0, <fixed>)` has no `"error"` →
`project_three_year(..., model="transactional", economics=that)` returns
`proj["model"] == "transactional"` (no `customers` keys).

## B3 — WTP band never reconciled with the recommended price  → detector D18
**Kills:** R9 criticals (800c261b, e55db08e, 4a755faa, 8add1fa2 partially) — 83x gaps.

Root cause: no reconciliation exists anywhere (verified by grep). Consumer-style WTP
($150–1.5K) renders beside a $125,000 PSM recommendation with no comment.

Fix (shallow + honest — do NOT fabricate agreement):
1. `plan.py`: right after the consumer_research + psm results both exist (grep
   `consumer_research` assignment in the main flow; add immediately after), compute:
   `ratio = psm_optimal / wtp_median` (wtp median or point from
   `consumer_research.synthesis.willingness_to_pay`; only when both numbers exist).
   If `ratio > 10 or ratio < 0.1`: set
   `synthesis["wtp_price_mismatch"] = {"wtp": <median|point>, "recommended": psm_optimal,
   "ratio": round(ratio,1), "note": "WTP simulation and recommended price differ by
   >10x — likely different buyer framings (consumer vs business budget); do not
   average them. Validate willingness-to-pay with real buyer interviews."}`
2. `templates/report.html`: in the consumer-research WTP block (grep
   `willingness_to_pay`), render the mismatch note as a visible warning box when
   present (follow the style of existing warn boxes, e.g. the validation banner).
3. Detector `D18 "WTP band reconciled with recommended price"`: FAIL when both
   numbers exist, same nominal unit (compare `wtp.unit` to the psm unit phrase if
   stored; if units aren't comparable, N/A), ratio > 10 or < 0.1, AND
   `wtp_price_mismatch` absent. N/A when either number missing.

RED tests (`test_report_data_fixes.py`, class `TestWtpPriceReconciliation`):
- D18 fires on stored 800c261b (`out/wave2_corpus/800c261b.json`): WTP band high 1500,
  psm optimal 125000, no flag → ok False.
- Unit test for the flag-setter: wtp median 1500, psm 125000 → mismatch dict with
  ratio 83.3; wtp 7.5, psm 6.5 → no flag.

Simulation: for every corpus venture, run the flag-setter over stored synthesis+psm,
then D18 → 0 failures.

## B4 — off-category domains rank as "direct" competitors  → detector D19
**Kills:** R8 critical (e55db08e: crypto-SaaS "Theon Technology" = #1 direct rival).

Root cause (verified): `discover._gather_signals` (line ~292) resolves the domain via
`validate_domain(..., category=category)` (W2-5 added relevance/off_category to the
verdict) but DISCARDS those fields — entries reach synthesis/ranking with no relevance
signal, so a 183-day-old crypto domain ranks #1 on domain_age alone.

Fix:
1. In `_gather_signals`, wherever a validate_domain verdict `v` is accepted (the
   `llm_validated` branch ~plan of discover.py:312 and the probe/DDG branches if they
   validate), attach `out["relevance_score"] = v.get("relevance")` and
   `out["off_category"] = bool(v.get("off_category"))`.
2. In the ranking/synthesis assembly (where `ranked_opportunities` entries are built
   from enriched — grep `ranked_opportunities` in discover.py): entries with
   `off_category=True` are (a) sorted below all on-category entries regardless of
   `_score`, and (b) forced `"relevance": "reference"` (never "direct").
3. Detector `D19 "no off-category 'direct' competitor in the top 3"`: FAIL when any of
   the first 3 `ranked_opportunities` has `off_category == True` and
   `relevance == "direct"`. N/A when the fields are absent (old corpora).

RED tests (`test_discovery.py`, class `TestOffCategoryRanking`):
- Build 3 fake enriched entries, one `off_category=True` with the highest `_score` →
  after the ranking step it must be last and not "direct".
- D19 unit: synthetic record with off_category direct at rank 1 → ok False; absent
  fields → ok None.
(The stored corpus has no relevance fields, so D19 is N/A on it — that is expected;
say so in the commit. The live proof lands at the close-out regen.)

---

## Close-out (after B1–B4 land)

1. Regenerate the 16-venture corpus ONCE (script pattern:
   `scratchpad/gen_wave0.py` from this session; or re-run the newest 16 jobs from
   `.jobs.sqlite` through `plan.run_plan`, writing `<id8>.json` + rendered HTML).
   ~40 min warm. Write to `out/wave275_corpus/`.
2. `gates.py --corpus out/wave275_corpus --gate all --out docs/baselines/wave275.json`
   — expect: core 100% including D15–D19; only D11 (G5, deferred) may warn.
3. Scoped R4 mini-panel — `benchmarks/r4_panel.js` via the Workflow tool, but ONLY the
   previously-critical ventures (extract:
   `python -c "import json;d=json.load(open('docs/baselines/wave2_r4.json'));print([k for k,v in d['per_venture_pass_of_12'].items()])"`
   → filter to ventures with ≥1 CRITICAL in wave2_r4.json). Run on a Sonnet-class
   session. Target: **criticals 31 → ≤10**, `valid: true, verification_gaps: 0`.
4. `docs/baselines/wave275_r4.json` + §5d entry + push.

## Out of scope here (do not attempt)

- Deep WTP re-framing for B2B buyers (Wave 6 research crew).
- report/forecast.py one-model rewrite (Wave 4) — B1–B4 are surgical predecessors.
- G5/D11 non-US sources (needs CENSUS/BLS keys).
- Tavily live smoke (needs TAVILY_API_KEY in .env).
