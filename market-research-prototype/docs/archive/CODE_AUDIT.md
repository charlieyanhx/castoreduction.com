# Code Audit — Hardcoding & Harness Purity (cycle33)

> Two questions: (1) is anything hardcoded? (2) is everything written strictly as
> agents / a harness? Honest, evidence-based answers below. **Both answers are
> "not really."**

---

## Q2 first — is it strictly a harness / agents? **NO. It's a hybrid.**

Measured from the code:

| Fact | Evidence |
|---|---|
| The live `/plan` pipeline imports **10 legacy modules directly** | `plan.py` `from discover/taste/pricing/place/four_ps/clustering/competitor_pricing/market_sizing/financials/personas/segment_scoring import …` |
| `plan.py` makes **0 registry/agent calls** | no `get_tool`/`get_skill`/`get_agent`/`run_agent`/`run_research_crew` |
| Only **4 skills** are reached, and via **direct import**, not the registry | `validate_numbers`, `consumer_research_skill`, `grounded_bottom_up`, `classify_market_scale` |
| Legacy bespoke code ≈ harness code by size | legacy core **4,462 LOC** vs harness layer **4,187 LOC** |
| The 7 agents are **idle** | 0 calls in the live path (see METHOD_AUDIT H2) |

**Verdict:** there are two systems living side by side. The **runtime product is the
legacy deterministic 22-step pipeline** calling bespoke modules directly. The
**harness (tools/skills/agents) is a parallel layer** that's only lightly wired in
(4 sizing/perspective skills) and whose agents nothing invokes. So "everything is
strictly agents/harness" is **false** — it's aspirational architecture sitting on
top of a pre-harness pipeline.

This isn't necessarily wrong (the deterministic spine is where numbers should live),
but the *claim* and the *code* disagree. To make it true you'd either (a) route the
legacy steps through the registry as skills, or (b) restate the architecture as
"deterministic pipeline + opt-in harness/agents," which is what it actually is.

---

## Q1 — is anything hardcoded? **YES, extensively.** (Beyond the NAICS/BLS tables already removed.)

### Legitimate constants — KEEP in code (infrastructure, not domain specificity)
- `net.py`: `RETRY_STATUS`, `DEFAULT_HEADERS` — HTTP plumbing.
- `llm.py`: `PRICING`, `BACKEND_DEFAULTS`, `_BACKENDS` — provider config.
- `competitor_pricing.py`: `PRICE_PATHS` — scrape heuristics.
- registry dicts, `__all__`, caches — framework internals.

These are fine. Constants ≠ hardcoding when they're infra.

### Hardcoded DOMAIN assumptions — these are the real problem
The "code generic, config specific" principle is violated here: domain knowledge is
baked into module code instead of config/sourced data.

| Where | Hardcoded | Why it's a problem |
|---|---|---|
| `skills/sizing/hyperlocal.py` | `serviceable_fraction=0.35`, `ramp_factor=0.6`, `supply_turns_per_day=2.0`, `supply_avg_check=35.0`, `supply_days_per_year=360` | **Restaurant-flavored defaults inside a "generic" skill.** A gym/clinic gets restaurant turns & a $35 check unless the caller overrides. Should be per-category config or resolved. |
| `financials.py` | `monthly_churn_pct=5.0`, scenarios `0.05/0.20/0.60`, S-curve `0.08/0.35` | Financial assumptions fixed in code; not venture- or stage-specific. |
| `skills/sizing/validate.py` | `DEFAULT_MAX_SHARE=0.40`, `spread>0.5`, `ratio>10/<0.1`, `>1.25/<0.8`, `seg_ratio<0.5/>1.5` | Tuning thresholds hardcoded; defensible as config, but not externalized. |
| `four_ps.py` | `_VIABILITY_WEIGHTS` | The viability score's dimension weights are fixed in code. |
| `segment_scoring.py` | `DEFAULT_WEIGHTS` | 5-metric weights hardcoded (operator can override, but default is in code). |
| `macro_anchors.py` | `VERTICAL_ANCHORS`, `SERIES` | Curated per-vertical benchmark tables baked in. |
| `discover.py` | `MEGABRAND_NAMES` | Hardcoded brand blacklist. |
| `customer_universe.py` | `VERTICAL_SEEDS`, `COMPANY_PATTERNS`, `_BLACKLIST_TOKENS`, `_BUYER_ROLE_HEURISTIC_RULES` | Vertical seed lists + heuristics hardcoded. |
| `place.py` | `CHANNEL_PATTERNS` | Channel-detection keywords hardcoded. |
| `differentiators.py` | `DIMENSION_PROMPTS` | Prompt templates fixed (acceptable, but they encode the 5 fixed dimensions). |
| `daily_check.py` | `CATEGORIES` | Fixed category list for the cron check. |

### Severity
- **Highest:** the hyperlocal sizing defaults (restaurant turns/check/days inside a
  generic skill) and the financial assumptions — these silently inject one vertical's
  economics into every venture's numbers. That's the same class of bug as the NAICS
  hardcode, just less visible.
- **Medium:** viability/segment weights, validate thresholds — should live in the
  config profiles (`config/profiles/*.yaml` already exists) so they're tunable without
  code edits.
- **Low/acceptable:** prompt templates and scrape heuristics.

---

## Remediation (matches "code generic, config specific")

1. **Move sizing/financial assumptions to config** (`config/profiles/default.yaml`):
   `serviceable_fraction`, `ramp_factor`, supply defaults, churn, scenario splits,
   viability/segment weights, validate thresholds. Code reads them via `config.get(...)`.
2. **Per-category economics, not restaurant defaults.** Hyperlocal supply params
   (turns, check, days) should come from a category profile or be resolved, like spend.
3. **Decide the harness story honestly:** either route the legacy steps through the
   skill registry (so `plan.py` calls `get_skill(...)`, making the harness claim true),
   or update ARCHITECTURE.md to describe the real shape (deterministic pipeline + opt-in
   harness/agents). Don't claim "strictly agents."
4. **Then re-run this audit** — target: zero hardcoded *domain* assumptions outside
   config; all infra constants clearly separated.

---

## One-line verdict
**It is not a pure harness, and it is not free of hardcoding.** It's a solid
deterministic pipeline with a good harness layer bolted alongside — but the live
numbers still carry restaurant-flavored hardcoded economics, and the agents/harness
are mostly unused at runtime. The fixes are config-extraction + an honest architecture
statement, not a rewrite.
