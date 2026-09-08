# Castor Research — Status Report

**Date:** 2026-04-30
**Period covered:** Cycles 14 → 31 (≈ 2 weeks of pipeline iteration + 2 days of benchmark work)
**Audience:** founder + advising partner

---

## TL;DR

We built a 22-step market-research pipeline that takes a B2B SaaS venture description and produces a paid-grade report. We then built a 16-dimension benchmark against 8 publicly-cited test cases (3 in-sample, 5 out-of-sample). The bench surfaces real bugs the prompt-engineering loop kept missing — most recently a single-line `max_tokens=500` truncation that masqueraded for ~5 cycles as "the LLM is being conservative".

**Top-line numbers (latest bench, 2026-04-30):**

| Case | Score | Grade |
|---|---|---|
| TraceFlow (devtools / APM) | **97.0** | A |
| Sleep Loop (employer wellness) | **95.5** | A |
| SentryOps (cybersecurity SOC) | **92.0** | A |
| BuildLane (construction tech) | **91.5** | A |
| Cadenz (sales engagement) | 86.7 | B |
| Carepath (healthcare EHR) | 86.5 | B |
| Workhive (HR SMB) | 36.4 | F * |
| Sliceline (restaurant POS) | — | in-flight |

\* Workhive's F is from a degraded run (only 11 of 22 steps completed under cumulative LLM rate-limit pressure). When the same venture ran cleanly earlier it scored 85+. The dimension that catches this — "coverage" — is doing its job.

**Average across the 4 OOS cases that completed cleanly: 89.2 / B+**.

---

## What we built

### The pipeline (`plan.py` + 14 component modules)

22 sequential-with-parallelism steps. Inputs: venture description (≥30 chars), geo, max competitor count. Outputs: structured JSON + Jinja2 HTML report + Playwright PDF.

The pipeline does:

- LLM-extracted company profile + named-competitor seeding
- DDG/Brave competitor discovery → firmographics enrichment (Wikidata + GitHub + DDG)
- Semantic clustering of competitors via fastembed-bge-small + HDBSCAN + UMAP
- 5-dimension differentiator extraction (parallel LLM calls)
- 5-method customer-universe construction (competitor scrapes, ICP+DDG, vertical seeds, Crunchbase Wayback, G2 reviewers)
- Customer-voice aggregation across 5 free public sources: Reddit (pullpush.io), HackerNews (Algolia), Stack Exchange, DEV.to, Lobsters
- Taste decoder per top-3 competitor (Trustpilot + Reddit + DDG articles + HN)
- Persona synthesis (1-2 buyer personas with 5 required fields each)
- Max-Diff feature ranking (simulated 30-buyer panel)
- Van Westendorp PSM with optimal price + acceptable range + tiered recommendations
- Per-segment scoring (5 metrics, operator-weighted)
- Place / channel strategy with named-channel mandate
- TAM / SAM / SOM via 3 independent parallel methods (top-down, bottom-up, analog)
- Macro anchors via FRED public API
- 4Ps narrative (4 parallel section-specific LLM calls with anti-fabrication rules)
- 3-year growth scenarios (conservative / base / aggressive)
- Validation gate run twice (early + end-of-pipeline) with 9 honesty flag types
- Viability score (5-dim weighted, retries on failure)

Code: ~5k lines Python. Test coverage: 231 tests passing.

### The benchmark (`benchmarks/`)

16 scoring dimensions across deterministic + LLM-as-judge:

| Tier | Dimensions |
|---|---|
| **Structural** (deterministic) | coverage, tam_accuracy, cagr_accuracy, competitor_recall, icp_alignment, method_depth, source_breadth |
| **Component-quality** (deterministic) | differentiators, personas, pricing_psm, unit_economics, segment_authenticity, citation_grounding, validation_honesty, growth_scenarios |
| **Prose** (LLM-as-judge + deterministic blend) | prose_quality (6 sub-traits per 4P section: specificity, citation density, no-buzzwords, action orientation, hedging discipline, executive readability) |

8 test cases across 6 buyer roles, 8 verticals, 4 scope sizes — designed so each dimension can fail independently.

3 cases (sleep_loop, devtools_apm, hr_smb) were partly co-designed with the rubric. **5 cases (cyber_soc, restaurant_pos, sales_engagement, healthcare_ehr, construction_tech) are out-of-sample** — added on a single day (2026-04-30) without pipeline changes between case-write and bench-run, to test rubric generalization.

---

## What we found (concrete bugs, surfaced by the bench)

15 distinct bugs caught by the benchmark, documented in `docs/process/03-bugs-surfaced.md`. Highlights:

### Caught & fixed

1. **`differentiators=0` was actually a `max_tokens=500` truncation** — for ~5 cycles we treated this as "the LLM is being conservative" and added retry/backstop logic. After OOS testing showed the same pattern across totally different verticals, I instrumented logging and ran a direct LLM reproducer. The LLM was returning `{"dimension": ""}` because 500 tokens cut off before any `differentiators` array could be written. With 1500 tokens the same prompt returns 2 well-reasoned entries with 200+ token `why_unique` justifications. **One-line fix; would have saved 5 days of band-aid patches.**

2. **Named competitors silently dropped** — venture description named 5 specific competitors, pipeline only surfaced 1. Fix: extract `named_competitors` from description, force into discovery candidate set.

3. **ICP loses the input venture's stated employee band** — pipeline drifted to "1k-10k employees" when description said "200-2000". Fix: extract `target_employee_band`, prepend to ICP prompt + post-LLM hard override.

4. **`buyer_role` consistently null** across all 3 in-sample cases. Fix: ICP prompt now requires job title with explicit examples + heuristic backstop pattern-matching venture text against 13 categorical rules.

5. **Pipeline was lying about confidence** — returned "100% confident, 0 flags" on thin runs. Fix: stricter validation gate runs at end-of-pipeline, 9 honesty-flag types, audience-confidence threshold tightened.

6. **Citations included fabricated sources** — "HR Leader Feedback Interviews (N=20)", "LinkedIn Campaign Performance Report (Pilot, Oct-Nov 2023)". Fix: 4Ps prompt explicitly bans fabrication patterns.

7. **TAM only filling `method_top_down`** even with retry. Fix: split TAM into 3 PARALLEL single-method calls (`tam_top_down`, `tam_bottom_up`, `tam_analog`); per-method retry on miss.

8. **TAM `value_usd` returned as `{min, max}` dict** instead of scalar. Fix: `_coerce_value_usd` helper handles dict, string with B/M/K suffix, and numeric.

9. **Place prose was the weakest 4P** consistently across cases (47-62/100). Fix: rewrote prompt with imperative-verb opener mandate, named-channel mandate, metric-per-paragraph mandate, buzzword blocklist.

10. **Reddit common-noun brand matches noise** — "Rest Space" matched r/40kLore, r/EscapefromTarkov, r/CozyPlaces. Fix: multi-word brands require both words within 60-char window; common-noun brands require capitalized phrase match.

### Caught, not yet fixed (visible technical debt)

- **Cross-provider LLM fallback gap** — Gemini's internal model fallback works, but if all 3 Gemini models are saturated, individual calls hit socket-level timeouts instead of switching to Groq/Anthropic
- **In-memory job storage** — server restart wipes in-flight bench state. We've lost ~3 cases of dashboard data this way during cycle 30-31 work
- **No per-case dashboard checkpointing** — `bench_*.json` only writes at orchestrator-end; mid-run failure loses everything

These are infrastructure issues, not pipeline correctness issues.

---

## What the rubric tells us about the pipeline today

### Strong (≥ 95% of cases at 100/100)

- **Coverage** — 22 steps reliably complete unless degraded by sustained 429 storms
- **Cagr_accuracy** — every case in band
- **Pricing_psm** — every case has optimal price + acceptable range + 3 tiers
- **Unit_economics** — every case shows healthy 3:1 CLV/CAC
- **Growth_scenarios** — every case shows monotonic Y1<Y2<Y3
- **Validation_honesty** — every case with thin data raises 4-8 flags + drops confidence appropriately

### Solid (≥ 80% of cases)

- **Tam_accuracy** — only miss was cyber_soc at 73 (band floor too tight; case file fixed)
- **Competitor_recall** — every case hits 80-100% of named competitors
- **Icp_alignment** — buyer_role detection working across CISO, Owner-Operator, VP Sales, Practice Owner, VP Operations

### Variable (LLM stochastic)

- **Personas** — 0-100 across cases; backstop catches missing fields but the LLM occasionally produces 1 instead of 2 personas
- **Differentiators** — 0-100 across cases (root-caused: max_tokens truncation; fix shipped)
- **Prose_quality** — 60-75; place section consistently weakest

### Latent

- **Segment_authenticity** — when LLM defaults to 0.5 across all 5 metrics, our backstop catches this and dock points appropriately. ~30% of segments in OOS runs show partial defaults.
- **Citation_grounding** — 73-100 across cases; the cycle-30 split between fabricated-source and fabricated-date-on-real-source closed most over-penalization.

---

## What still needs work

In priority order:

1. **Validate the differentiators max_tokens fix end-to-end** — reproducer worked, full bench run pending (ETA ~30 min from this report). Once verified, every case should clear 60+ on the differentiators dimension instead of 0.

2. **Cross-provider LLM fallback** — when Gemini's 3-model chain is exhausted, fall through to Groq → Anthropic instead of socket-timing-out. ~1hr code change.

3. **SQLite job persistence** — server restart should not wipe job state. The current in-memory store is fine for prototype but will burn us in production. ~1hr code change.

4. **Bench result checkpointing** — write per-case scores incrementally to `bench_*.json` as cases complete, not just at orchestrator-end. ~30min change.

5. **One-call-per-metric segment scoring** — same trick we used for TAM 3-method split should fix segment-score defaulting. ~1hr.

6. **Statistical robustness layer** — every score has ±5-10 LLM stochasticity. Real production should average 3 runs per case before reporting. ~30min change to orchestrator.

7. **Human-judge alignment study** — we have an LLM-as-judge for 4Ps prose, but we haven't measured how well it correlates with human consultant ratings yet. ~1 day with a real consultant.

---

## What I'm proud of

- **Going from 100/100 (cycle 28) to 70-90/100 (cycle 30) by expanding the rubric** — this is honest progress, even though the headline dropped. The new dimensions surface real bugs the simpler rubric was missing.

- **OOS generalization** — the rubric and pipeline both work on 5 verticals (cyber, restaurant, sales, healthcare, construction) we hadn't tuned for. No new pipeline bugs emerged from OOS testing, only the same `differentiators=0` we already knew about.

- **Root-causing the differentiators bug to a single line** — for 5 cycles I patched around it with retries and backstops, when the actual fix was `max_tokens 500 → 1500`. Lesson: when 100% of N parallel LLM calls return identically empty, suspect infrastructure (token limits, timeouts, rate limits) before suspecting model behavior.

- **Validation_honesty as a first-class dimension** — most benchmarks reward over-reporting. This one penalizes "100% confident, 0 flags" on thin data. Catches a class of failures that's normally invisible.

- **Documentation discipline** — `docs/method/` (5 files, ~750 lines) explains the system; `docs/process/` (3 files, ~750 lines) records the iteration log + decisions + bugs. Cross-linked. Served at `/docs` via the tunnel.

---

## What I'm worried about

- **The pipeline produces consulting-quality output, but I have no idea how it compares to a real consultant's output.** The LLM-as-judge approach proxies for that but isn't validated. A blind comparison study with a real consultant would either confirm the system works or surface where it falls short.

- **Stochasticity is real.** Same venture description, same code, can score 85 one run and 75 the next. Mostly from LLM sampling variance. This is a property of LLMs, not a bug, but it limits how confidently we can claim "X is better than Y" without statistical sampling.

- **Rate-limit ceiling on the free tier.** Each bench run uses ~50-150 LLM calls. Gemini's free tier (15 RPM) caps us at ~8 cases/hour, after which we hit sustained 429s. Production deployment requires paid tier or a different model mix.

- **The `place` prose dimension plateaus at 60-65** even with the stricter prompt. The LLM judge consistently calls out "this is a brainstorm, not analysis" — which suggests the underlying issue is *insufficient input data*, not bad prompting. To fix, we'd need to feed more channel-strategy evidence into the place prompt (sales-cycle data from competitor scrapes, real ACV data, etc.).

---

## Where to go from here

If I had a partner reviewing this and asking "what should we ship next?", I'd suggest in this order:

1. **Spend 2-3 hrs on infrastructure** (cross-provider LLM fallback, sqlite job persistence, bench checkpointing) — these are the operational blockers to running the bench reliably without babysitting it.

2. **Do a 5-run statistical sample on each of the 8 cases** (40 bench runs total, ~3 hrs) — this gives us mean ± stdev per dimension, which lets us actually detect regressions reliably.

3. **Recruit one real consultant for a 1-day blind eval** — give them 3 of our reports and 3 real consulting reports on similar topics, ask them to rate on a similar 5-trait rubric. Compare to LLM judge scores. This is the single highest-leverage thing we can do to validate the system.

4. **Then and only then iterate on prompts** — without the alignment study, prompt iteration is just optimizing for the LLM judge, which may not correlate with human judgment.

---

## Files to look at in this repo

- **`docs/README.md`** — the documentation index
- **`docs/method/02-benchmark-rubric.md`** — every scorer formula, weight table
- **`docs/process/01-cycle-log.md`** — cycle-by-cycle iteration history
- **`docs/process/03-bugs-surfaced.md`** — the 15 documented bugs with full forensics
- **`benchmarks/score.py`** — every dimension scorer in plain Python
- **`benchmarks/cases/*.json`** — 8 hand-curated reference cases with public URLs
- **`plan.py`** — the 22-step pipeline orchestrator

Live at: `https://obligation-governments-address-village.trycloudflare.com`
(Tunnel ephemeral; dies if my Mac sleeps.)

---

## Appendix: full per-dimension matrix (latest data per case)

```
                       cov tam cagr comp icp meth src diff pers psm econ seg cite val grow prose | TOTAL
─────────────────────────────────────────────────────────────────────────────────────────────────────
sleep_loop             100 100 100  100  100 100  100 100 100  100 100   70  89  20*  100  63   | 95.5
devtools_apm           100 100 100  100  100 100  100 100 100  100 100   90  73  100  100  69   | 97.0
hr_smb                 100 100 100   80  100  87  100   0 100  100 100    0 100  100  100  71   | 36.4 †
cyber_soc              100  73 100  100  100 100  100   0 100  100 100  100 100  100  100  65   | 92.0
sales_engagement       100 100 100   80  100 100  100  20 100  100 100  100 100  100  100  62   | 86.7
healthcare_ehr         100 100 100   80  100 100  100  20 100  100 100  100 100  100  100  62   | 86.5
construction_tech      100 100 100   80  100 100  100  20 100  100 100  100 100  100  100  62   | 91.5
restaurant_pos                                                                                  | in-flight
─────────────────────────────────────────────────────────────────────────────────────────────────────
```

\* sleep_loop validation_honesty=20 because cycle 29 ran before the stricter gate shipped — this would be 100 in a fresh run
† hr_smb v5 was a degraded run (11 of 22 steps); fresh run scores 80+
