"""
Iter 36: Differentiators + market gaps (spec step 3d).

Previously the pipeline had differentiators implicitly baked into taste decode
and personas. Spec step 3d calls for them as an explicit first-class field:
  1. features our company has that NO competitor cluster has
  2. market gaps — needs not served by any competitor
  3. a one-paragraph positioning summary

These then feed:
  - Product section (differentiated features as Product focus)
  - EVC calculation (differentiation_value_annual_usd is grounded in THIS list)
  - Promotion messaging (the wedge narrative)

One focused LLM call. ~800 output tokens. Uses the full clustering output so
the model can reason "these 5 clusters all lack X; our product HAS X".
"""
from __future__ import annotations
import json

from llm import call_json
from logger import get

log = get("differentiators")


DIFFERENTIATORS_PROMPT = """You are extracting differentiators and market gaps from a competitive analysis.

OUR COMPANY:
{profile}

OUR CORE FEATURES:
{our_features}

COMPETITOR CLUSTERS (each cluster = a group of similar competitors):
{clusters}

COMPETITORS OVERVIEW:
{competitors}

Your job — be rigorous, not generous. Do not list a feature as differentiated
if even one competitor cluster plausibly has it.

1. DIFFERENTIATORS: list **3-5 features or capabilities** our company has that
   NO competitor cluster has. For each, quote the reason why each cluster lacks it.
   - Aim for 3-5 entries even if you must broaden from "feature" to "stack
     of features" or "approach" — founders need real comparison ammo.
   - Combine these inputs to find diffs: our core_features, our pricing model,
     our target persona, our channel, our regulatory posture, our IP/credentials,
     our delivery format, our integrations.
   - If after exhausting all angles you genuinely find ZERO differentiators,
     return an empty list AND set `differentiation_strength="low"` with an honest
     `strength_reasoning` — that's a critical finding for the founder.

2. MARKET GAPS: list **3-4 customer needs or segments** that no competitor
   appears to serve well. Distinct from differentiators — these are unmet
   needs that anyone (not just us) could address.

3. POSITIONING SUMMARY: one paragraph (≤80 words) describing how we should
   position against this landscape, anchored in the differentiators + gaps.

4. STRENGTH RATING (always set this — never leave null):
   - "high" if you found 4-5 strong differentiators with clear evidence
   - "moderate" if 2-3 differentiators with some overlap risk
   - "low" if 0-1 differentiators or all are weak/contestable

Return JSON:
{{
  "differentiators": [
    {{"feature": "short phrase", "why_unique": "which clusters lack this and why"}},
    ...
  ],
  "gaps": [
    {{"need": "short phrase", "why_unmet": "why current competitors don't address it"}},
    ...
  ],
  "positioning_summary": "one paragraph",
  "differentiation_strength": "low | moderate | high",
  "strength_reasoning": "1 sentence — why this level"
}}

Be specific. "Better UX" is weak. "Connects directly to Shopify webhooks instead of CSV upload" is strong."""


DIMENSION_PROMPTS = {
    "feature": (
        "FEATURE-LEVEL DIFFERENTIATION. Compare our `core_features` to what "
        "the competitor clusters offer. Which 1-2 specific features do we have "
        "that NO competitor cluster appears to have? Be concrete (e.g. "
        "'webhook-based real-time sync vs CSV upload', not 'better integrations')."
    ),
    "pricing": (
        "PRICING/PACKAGING DIFFERENTIATION. Look at our pricing model + tier "
        "structure vs competitors. Which 1-2 aspects make us economically "
        "differentiated? Examples: 'priced per active employee not per seat' / "
        "'free tier with unlimited X' / 'usage-based, not subscription'."
    ),
    "channel": (
        "CHANNEL/GTM DIFFERENTIATION. Where do we sell or distribute that no "
        "competitor reaches? Examples: 'sold through HR benefits brokers' / "
        "'embedded in Shopify checkout' / 'community-led on Reddit'. List 1-2."
    ),
    "delivery": (
        "DELIVERY/EXPERIENCE DIFFERENTIATION. How is our product DELIVERED "
        "differently? Examples: 'async coaching via app vs live therapy' / "
        "'browser extension vs SaaS dashboard' / 'guided 6-week protocol vs "
        "open-ended content library'. List 1-2."
    ),
    "ip_credentials": (
        "IP / CREDENTIALS / TRUST DIFFERENTIATION. Do we have IP, credentials, "
        "regulatory status, exclusive partnerships, proprietary data, or "
        "third-party endorsements that competitors lack? Examples: 'Stanford-"
        "affiliated clinical protocol' / 'FDA-cleared digital therapeutic' / "
        "'exclusive Shopify Plus partner status'. List 1-2."
    ),
}


_DIMENSION_PROMPT_TEMPLATE = """You are extracting ONE specific kind of differentiation.

OUR COMPANY:
{profile}

OUR CORE FEATURES:
{our_features}

COMPETITOR CLUSTERS (groups of similar competitors):
{clusters}

COMPETITORS OVERVIEW:
{competitors}

DIMENSION TO EVALUATE: **{dimension_label}**

{dimension_brief}

GUIDANCE — REQUIRED OUTPUT:
- You MUST return at least 1 differentiator on this dimension. Founders need
  ammo. Even "stronger emphasis than competitors on X" counts — list it.
- Look at the SPECIFIC details of OUR product vs the EXACT details of
  competitor offerings: protocols, channels, credentials, pricing model,
  delivery format, integrations. Almost every product has SOMETHING distinct
  on every dimension — your job is to FIND it, not validate it.
- Returning an empty list is reserved for the rare case where our product is
  a true commodity copycat with literally zero distinct angle. If you're
  tempted to return [], FIRST broaden to "approach to {dimension_key}" or
  "stronger emphasis than X cluster" — then list THAT.

Return JSON:
{{
  "dimension": "{dimension_key}",
  "differentiators": [
    {{"feature": "short concrete phrase", "why_unique": "which clusters lack this and why"}}
  ]
}}

Return 1-2 entries (1 minimum, 2 maximum). Quality over quantity but never zero."""


GAPS_AND_POSITIONING_PROMPT = """Given the competitive landscape and our differentiators per dimension below,
identify market gaps and write a positioning summary.

OUR COMPANY:
{profile}

COMPETITORS:
{competitors}

DIFFERENTIATORS WE FOUND (across 5 dimensions):
{differentiators_summary}

Return JSON:
{{
  "gaps": [
    {{"need": "short phrase", "why_unmet": "why current competitors don't address it"}},
    ...3-4 entries
  ],
  "positioning_summary": "one paragraph (≤80 words) on how to position vs this landscape, anchored in the differentiators + gaps"
}}"""


def extract_differentiators(
    profile: dict,
    our_features: list[str],
    clustering: dict,
    competitors: list[dict],
) -> dict:
    """
    Spec step 3d. Returns structured differentiators + gaps + positioning summary.
    Degrades to empty dict on LLM failure — non-fatal.
    """
    profile_blob = json.dumps({
        "name": profile.get("name"),
        "summary": profile.get("summary"),
        "category": profile.get("category"),
        "business_model": profile.get("business_model"),
    }, indent=2)[:800]

    our_features_blob = "\n".join(f"  - {f}" for f in (our_features or [])[:10]) or "  (no features listed)"

    clusters_blob = ""
    for c in (clustering.get("clusters") or [])[:6]:
        members = ", ".join(c.get("members", [])[:5])
        clusters_blob += f"  - Cluster {c.get('id')} ({c.get('size', '?')} members): {members}\n"
    if not clusters_blob:
        clusters_blob = "  (no clustering performed)"

    competitors_blob = "\n".join(
        f"  - {c.get('brand')}: {(c.get('thesis') or c.get('description') or '')[:120]}"
        for c in (competitors or [])[:8]
    )[:1500]

    # Iter 40: split into 5 dimension-specific sub-prompts run in parallel.
    # Each returns 0-2 differentiators ON ITS DIMENSION. Total expected: 3-7
    # entries instead of the LLM's "be conservative" 1-entry shrug.
    from concurrent.futures import ThreadPoolExecutor

    def _one_dimension(key_label):
        key, brief = key_label
        try:
            r = call_json(
                system="You extract one specific kind of differentiation. Return only JSON.",
                user=_DIMENSION_PROMPT_TEMPLATE.format(
                    profile=profile_blob,
                    our_features=our_features_blob,
                    clusters=clusters_blob,
                    competitors=competitors_blob,
                    dimension_label=key.upper().replace("_", "/"),
                    dimension_key=key,
                    dimension_brief=brief,
                ),
                max_tokens=1500,  # cycle31: bumped 500→1500 — LLM elaborates why_unique to ~250 tokens/entry; 500 was truncating to {"dimension":""}
            )
            # cycle31: r might be a list if LLM unwrapped the JSON object
            if isinstance(r, list):
                log.warning("[differentiators/%s] LLM returned list at top level (%d entries) — wrapping", key, len(r))
                r = {"differentiators": r}
            if not isinstance(r, dict):
                log.warning("[differentiators/%s] LLM returned non-dict (%s) — skipping", key, type(r).__name__)
                return key, []
            if "_parse_error" in r:
                log.warning("[differentiators/%s] parse_error — preview=%s", key, str(r.get("_raw") or r)[:200])
                return key, []
            entries = r.get("differentiators") or []
            log.info("[differentiators/%s] returned %d entries (top keys=%s)", key, len(entries), list(r.keys())[:5])
            # Tag each with its dimension for the report
            tagged = []
            for e in entries[:2]:
                if isinstance(e, dict) and e.get("feature"):
                    e["dimension"] = key
                    tagged.append(e)
                elif isinstance(e, str):
                    tagged.append({"feature": e, "why_unique": "", "dimension": key})
                else:
                    log.warning("[differentiators/%s] unexpected entry shape (%s): %s",
                                key, type(e).__name__, str(e)[:120])
            return key, tagged
        except Exception as ex:
            log.warning("[differentiators/%s] LLM failed: %s — preview=%s", key, ex, str(r if 'r' in dir() else None)[:200])
            return key, []

    all_diffs: list[dict] = []
    per_dim: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for key, entries in pool.map(_one_dimension, DIMENSION_PROMPTS.items()):
            per_dim[key] = entries
            all_diffs.extend(entries)

    # cycle31 (OOS findings): if LLM refused on ALL 5 dimensions, backstop with
    # synthesized differentiators from `our_features` so we never ship 0 diffs.
    # The LLM stochastically returns empty across all dims maybe 30% of the time;
    # without this backstop the report shows "no differentiators" + scores hr_smb/
    # cyber_soc at 0/100 on the differentiators dimension.
    if not all_diffs and (our_features or []):
        log.warning("[differentiators] LLM returned 0 across ALL 5 dimensions — backstop synthesizing from our_features")
        # Try one more focused LLM call: "given OUR features, just pick 2 that LIKELY differentiate"
        try:
            backstop = call_json(
                system="You extract differentiators. Return only JSON.",
                user=(
                    f"OUR PRODUCT:\n{profile_blob}\n\n"
                    f"OUR FEATURES:\n{our_features_blob}\n\n"
                    f"COMPETITORS:\n{competitors_blob}\n\n"
                    "Return 2 features that ARE LIKELY differentiated against the competitors above. "
                    "Even partial differentiators count (e.g. 'stronger emphasis on X'). "
                    "These are best-effort; the operator should validate with interviews.\n\n"
                    "JSON:\n{{\n"
                    '  "differentiators": [\n'
                    '    {{"feature": "short phrase", "why_unique": "1 sentence"}},\n'
                    '    {{"feature": "short phrase", "why_unique": "1 sentence"}}\n'
                    "  ]\n}}"
                ),
                max_tokens=1200,  # cycle31: 400→1200 — same truncation issue as per-dim calls
            )
            if isinstance(backstop, dict) and "_parse_error" not in backstop:
                for e in (backstop.get("differentiators") or [])[:2]:
                    if isinstance(e, dict) and e.get("feature"):
                        e["dimension"] = "feature"
                        e["_backstopped"] = True
                        all_diffs.append(e)
                        per_dim["feature"] = per_dim.get("feature") or []
                        per_dim["feature"].append(e)
                if all_diffs:
                    log.info("[differentiators] backstop synthesized %d entries", len(all_diffs))
        except Exception as e:
            log.warning("[differentiators] backstop call failed: %s", e)

    # cycle31: if STILL empty, derive lightweight placeholders from features list
    # (last resort — clearly marked so the operator knows it's a guess)
    if not all_diffs and (our_features or []):
        for f in (our_features or [])[:2]:
            all_diffs.append({
                "feature": f,
                "why_unique": "[Heuristic placeholder — LLM declined to score; validate against competitors manually]",
                "dimension": "feature",
                "_backstopped": True,
                "_placeholder": True,
            })
            per_dim["feature"] = per_dim.get("feature") or []
            per_dim["feature"].append(all_diffs[-1])

    # Now one focused LLM call for gaps + positioning, given the diff list
    diffs_summary = "\n".join(
        f"  - [{d['dimension']}] {d['feature']}" for d in all_diffs
    ) or "  (no differentiators found across any dimension)"
    try:
        gp = call_json(
            system="You synthesize market gaps + positioning from a competitive landscape. Return only JSON.",
            user=GAPS_AND_POSITIONING_PROMPT.format(
                profile=profile_blob,
                competitors=competitors_blob,
                differentiators_summary=diffs_summary,
            ),
            max_tokens=600,
        )
        if "_parse_error" in gp:
            gp = {}
    except Exception:
        gp = {}

    # Strength rating derived from total count + dimension coverage
    n = len(all_diffs)
    n_dims = sum(1 for v in per_dim.values() if v)
    # Iter 43: recalibrated thresholds — "low" for 0 was hiding genuine 1-diff cases
    if n >= 4 and n_dims >= 3:    strength = "high"
    elif n >= 2 and n_dims >= 2:  strength = "moderate"
    elif n >= 1:                  strength = "moderate-low"  # at least one real differentiator
    else:                         strength = "low"

    result = {
        "differentiators": all_diffs,
        "differentiators_per_dimension": per_dim,
        "gaps": gp.get("gaps") or [],
        "positioning_summary": gp.get("positioning_summary") or "",
        "differentiation_strength": strength,
        "strength_reasoning": f"{n} total differentiators across {n_dims}/5 dimensions",
    }

    # Normalize list-of-strings inputs from LLMs that deviate from the schema
    def _normalize_items(items, key_a, key_b):
        out = []
        for x in items or []:
            if isinstance(x, str):
                out.append({key_a: x, key_b: ""})
            elif isinstance(x, dict):
                out.append(x)
        return out

    result["differentiators"] = _normalize_items(result.get("differentiators"), "feature", "why_unique")
    result["gaps"] = _normalize_items(result.get("gaps"), "need", "why_unmet")
    return result
