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
import re

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

EVIDENCE GATHERED BY THE PIPELINE (prices, review themes, channels — cite these):
{evidence}

DIMENSION TO EVALUATE: **{dimension_label}**

{dimension_brief}

GUIDANCE — EVIDENCE FIRST:
- A differentiator is a claim about the MARKET, and it must stand on the evidence
  above. Every entry MUST carry an `evidence_ref` quoting the specific line it
  stands on (e.g. "pricing: Souvla $40 per bowl", "reviews: 'no organic option'").
- Returning an empty list is a CORRECT answer when the evidence says nothing about
  this dimension. An empty dimension renders honestly in the report; an invented
  entry becomes an unhedged fact a buyer acts on.
- Do NOT assert competitor pricing, booking behaviour, or feature specifics that do
  not appear in the inputs above.

Return JSON:
{{
  "dimension": "{dimension_key}",
  "differentiators": [
    {{"feature": "short concrete phrase", "why_unique": "which clusters lack this and why",
      "evidence_ref": "the evidence line this stands on"}}
  ]
}}

Return 0-2 entries. Zero is legitimate; an entry without evidence is not."""


GAPS_AND_POSITIONING_PROMPT = """Given the competitive landscape and our differentiators per dimension below,
identify market gaps and write a positioning summary.

OUR COMPANY:
{profile}

COMPETITORS:
{competitors}

DIFFERENTIATORS WE FOUND (across 5 dimensions):
{differentiators_summary}

EVIDENCE GATHERED BY THE PIPELINE (ground the gaps and positioning in these):
{evidence}

Return JSON:
{{
  "gaps": [
    {{"need": "short phrase", "why_unmet": "why current competitors don't address it"}},
    ...3-4 entries
  ],
  "positioning_summary": "one paragraph (≤80 words) on how to position vs this landscape, anchored in the differentiators + gaps"
}}"""


_PRICE_LANG = re.compile(r"\$\s?\d|\d+(?:\.\d+)?%\s*(?:cheaper|below|above|less|lower|premium)|price[ds]? at \d", re.I)


def _dedupe_by_jaccard(entries: list[dict], threshold: float = 0.5) -> list[dict]:
    """Collapse near-duplicate differentiators by token-Jaccard on `feature`.

    The five dimension prompts routinely restate one idea five ways ("certified
    organic single-origin beans" / "single-origin certified organic beans"), and
    strength was a function of the COUNT — so restating inflated the score. First
    occurrence wins; empty features are dropped."""
    kept: list[dict] = []
    kept_tokens: list[set] = []
    for e in entries or []:
        feat = str((e or {}).get("feature") or "").strip()
        if not feat:
            continue
        toks = set(re.findall(r"[a-z0-9]+", feat.lower()))
        if not toks:
            continue
        dup = any(len(toks & t) / len(toks | t) >= threshold for t in kept_tokens)
        if dup:
            continue
        kept.append(e)
        kept_tokens.append(toks)
    return kept


def _strength_from(entries: list[dict]) -> tuple[str, str]:
    """(strength, reasoning) from DISTINCT, EVIDENCE-BACKED entries.

    The old rating was a pure function of entry count, and the prompt mandate pinned
    the count at 8-10 — so differentiation_strength was "high" on 16/16 corpus
    ventures and viability anchored to it. An entry that cites nothing counts for
    nothing here: it is still listed (a hypothesis is useful), but it cannot move
    the number a scorer anchors to."""
    entries = entries or []
    backed = [e for e in entries if str(e.get("evidence_ref") or "").strip()]
    n = len(backed)
    n_dims = len({e.get("dimension") for e in backed if e.get("dimension")})
    if n >= 4 and n_dims >= 3:
        strength = "high"
    elif n >= 2 and n_dims >= 2:
        strength = "moderate"
    elif n >= 1:
        strength = "moderate-low"
    else:
        strength = "low"
    reasoning = (f"{n} evidence-backed differentiators across {n_dims}/5 dimensions "
                 f"({len(entries)} listed)")
    return strength, reasoning


def _strip_unevidenced_price_claims(entries: list[dict], has_pricing_evidence: bool) -> list[dict]:
    """Drop entries asserting price comparisons no pricing evidence supports.

    The 16/16 panel finding: pricing-dimension prose asserting specific competitor
    dollar figures on ventures where competitor_pricing never ran. A price claim
    with nothing under it is not a differentiator, it is fiction with a $ sign."""
    if has_pricing_evidence:
        return entries
    out = []
    for e in entries or []:
        text = f"{e.get('feature') or ''} {e.get('why_unique') or ''}"
        if _PRICE_LANG.search(text):
            log.info("[differentiators] dropped unevidenced price claim: %s",
                     str(e.get("feature"))[:80])
            continue
        out.append(e)
    return out


def _build_evidence_blob(evidence: dict | None) -> tuple[str, bool]:
    """(prompt block, has_pricing) from the evidence the pipeline actually gathered."""
    ev = evidence or {}
    lines: list[str] = []
    pricing = ev.get("competitor_pricing") or {}
    for brand, d in list(pricing.items())[:8]:
        if isinstance(d, dict) and d.get("price") is not None:
            unit = d.get("unit") or "unit"
            lines.append(f"  - pricing: {brand} ${d['price']} per {unit}")
    for theme in (ev.get("review_themes") or [])[:6]:
        lines.append(f"  - reviews: {str(theme)[:140]}")
    for ch in (ev.get("channels") or [])[:4]:
        lines.append(f"  - channels: {str(ch)[:140]}")
    has_pricing = any(l.startswith("  - pricing:") for l in lines)
    if not lines:
        return ("  (no evidence available — return [] for any dimension you cannot "
                "ground in the inputs above)", False)
    return "\n".join(lines), has_pricing


def extract_differentiators(
    profile: dict,
    our_features: list[str],
    clustering: dict,
    competitors: list[dict],
    evidence: dict | None = None,
    founder_claim: str | None = None,
) -> dict:
    """
    Spec step 3d. Returns structured differentiators + gaps + positioning summary.
    Degrades to empty dict on LLM failure — non-fatal.

    `founder_claim` (R3, 88b416f6): the differentiation the FOUNDER declared in the
    brief. It is a hypothesis to verify, never auto-accepted — but it must also never
    vanish: that run printed "0 differentiators ... commodity copycat" while the
    founder's claim ("Bespoke RAG pipelines ... versus generic standard LLMs") sat
    unread in the description. The claim rides the output verbatim so the report can
    say "claimed, not confirmed against the roster" instead of insulting the founder
    with their own dropped input.
    """
    profile_blob = json.dumps({
        # THE seam where a placeholder became prose: a live report read "As an emerging
        # orbital solar reflection infrastructure provider, Unknown can capture the market".
        # company_profile now normalises the sentinel to None; this says what to write
        # instead, because a bare null invites the model to invent one.
        "name": profile.get("name") or "the company (no name given in the brief — "
                                        "refer to it generically, never by a placeholder)",
        "summary": profile.get("summary"),
        "category": profile.get("category"),
        "business_model": profile.get("business_model"),
    }, indent=2)[:800]

    our_features_blob = "\n".join(f"  - {f}" for f in (our_features or [])[:10]) or "  (no features listed)"
    if founder_claim:
        our_features_blob += (
            f"\n  - FOUNDER-CLAIMED DIFFERENTIATION (verify against the competitor "
            f"evidence; keep only if the evidence supports it): {founder_claim[:300]}")

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

    # R4 rank 6: the evidence the pipeline gathered, injected into every dimension
    # prompt. This step used to run BEFORE any of it existed — name + 120-char blobs
    # in, ten "differentiators" out, asserting competitor pricing and booking
    # behaviour as unhedged fact for products that do not exist yet.
    evidence_blob, _has_pricing_evidence = _build_evidence_blob(evidence)

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
                    evidence=evidence_blob,
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

    # R4 rank 6: the backstop and the heuristic-placeholder fallback are GONE.
    # Both existed to enforce "never ship 0 diffs" — synthesizing entries when all
    # five dimensions honestly returned nothing. Under the inverted prompt an empty
    # dimension is a CORRECT answer, and the report's empty case renders honestly.
    # A fabricated hypothesis dressed as a market finding is strictly worse than a
    # visible gap.
    all_diffs = _strip_unevidenced_price_claims(all_diffs, _has_pricing_evidence)
    all_diffs = _dedupe_by_jaccard(all_diffs)
    # Rebuild per_dim from the SURVIVING entries, keeping every dimension key —
    # an empty dimension is a finding ("the evidence says nothing here") and the
    # template renders it as such; dropping the key entirely would hide it.
    per_dim = {k: [] for k in DIMENSION_PROMPTS}
    for e in all_diffs:
        per_dim.setdefault(e.get("dimension") or "feature", []).append(e)

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
                evidence=evidence_blob,
            ),
            max_tokens=600,
        )
        if "_parse_error" in gp:
            gp = {}
    except Exception:
        gp = {}

    # R4 rank 6: strength derives from DISTINCT, EVIDENCE-BACKED entries — not a
    # count the prompt structure used to pin at 8-10 ("high" on 16/16 ventures,
    # anchoring the viability score).
    strength, strength_reasoning = _strength_from(all_diffs)

    result = {
        "differentiators": all_diffs,
        "differentiators_per_dimension": per_dim,
        "gaps": gp.get("gaps") or [],
        "positioning_summary": gp.get("positioning_summary") or "",
        "differentiation_strength": strength,
        "strength_reasoning": strength_reasoning,
    }
    if founder_claim:
        result["founder_claimed"] = founder_claim
        result["founder_claim_confirmed"] = bool(all_diffs)

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
