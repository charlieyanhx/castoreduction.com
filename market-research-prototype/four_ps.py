"""
Steps 13 & 14 of spec: Assemble 4Ps marketing plan + Viability score.

Synthesizes everything the pipeline has learned:
  - Company profile (step 2)
  - Competitive landscape (step 3) → via discover
  - Target audience (step 6) → via taste profiles
  - Pricing (step 9/10) → via Van Westendorp + Max-Diff
  - Channels (step 11) → via place analysis

Output: Product / Price / Place / Promotion writeups + Viability score 0-100.
"""
from __future__ import annotations
import json

from context.blobs import json_blob
from context.reminders import Reminders, reminder
from llm import call_json
from logger import get

log = get("four_ps")


# ---------------------------------------------------------------------------
# The section contract (run8's lesson)
# ---------------------------------------------------------------------------
# MEASURED on out/live/run8.json — a FRESH run, after the truncation guard and cache-poisoning
# fixes, so this was not the frozen-cache defect recurring. The place section parsed CLEANLY as:
#
#     citations: [{"id": 1}]        one citation carrying an id and NOTHING else
#     markers in prose: 1, 2, 3     three superscripts a reader will try to follow
#
# and dangling_citations rightly BLOCKED the report — the fourth consecutive run made
# unpublishable by a citation defect, each a different hole in the same missing contract: the
# section calls were schemaless, so "is it JSON" was the only bar. call_json has carried
# `response_model` (schema shown to the model + corrective re-ask with the exact validation
# error) since W1; these calls simply never used it.
#
# Marker parsing REUSES report/citation._marker_ids — the same function the verifier uses — so
# the contract and the detector cannot disagree about what a marker is (a run of superscripts
# is ONE id: ¹² is 12). Two owners for that rule is how the footnote-renderer bug happened.
from pydantic import BaseModel, Field, field_validator, model_validator


class SectionCitation(BaseModel):
    id: int
    source: str
    claim: str = ""

    @field_validator("source")
    @classmethod
    def _source_is_real(cls, v: str) -> str:
        if len((v or "").strip()) < 3:
            raise ValueError(
                "citation source is empty — an id alone is not a source; name the actual "
                "artifact this cites (e.g. 'PSM Pricing Output'), or drop the citation AND "
                "its superscript marker from the prose")
        return v


class SectionPayload(BaseModel):
    # Field order is deliberate and mirrors the prompt: narrative LAST, so a max_tokens cutoff
    # lands in the narrative rather than silently amputating the structured fields.
    key_takeaways: list[str] = Field(default_factory=list)
    citations: list[SectionCitation] = Field(default_factory=list)
    narrative: str

    @model_validator(mode="after")
    def _prose_markers_resolve(self) -> "SectionPayload":
        if not self.narrative.strip():
            raise ValueError("narrative is empty — write the narrative (it comes last in "
                             "the JSON so the other fields survive a cutoff)")
        from report.citation import _marker_ids
        text = " ".join([self.narrative] + [str(t) for t in self.key_takeaways])
        known = {c.id for c in self.citations}
        missing = sorted({m for m in _marker_ids(text) if m not in known})
        if missing:
            raise ValueError(
                f"the prose contains superscript citation markers {missing} with no matching "
                f"citations[] entry — every marker must have a citation with that id and a "
                f"real source, or remove the marker from the prose")
        return self


def _run_section(section_name: str, prompt_text: str) -> dict:
    """One 4Ps section call, under the SectionPayload contract.

    call_json validates against the schema and RE-ASKS with the exact error on a
    non-conforming response (W1), which replaces the old blind second attempt — the model is
    told what to fix instead of being asked the same question twice. A model that never
    conforms degrades to the visible failure placeholder, never to half a payload shipped as
    whole.
    """
    out = call_json(
        system=(f"You write the {section_name.capitalize()} section of paid-grade 4Ps "
                "plans. Return only JSON."),
        user=prompt_text,
        max_tokens=3500,  # iter 41: bumped 2000→3500. Narrative was truncating mid-sentence.
        response_model=SectionPayload,
    )
    if "_parse_error" in out:
        log.warning("[4Ps split] %s section failed the contract after re-asks: %s",
                    section_name, str(out.get("_parse_error"))[:160])
        return {"narrative": f"(Section generation failed for {section_name})",
                "key_takeaways": [], "citations": []}
    # Belt-and-braces fallbacks for the degraded paths (they cannot fire on a validated
    # payload, which is the point — validation makes them dead code on the happy path).
    if not out.get("key_takeaways"):
        out["key_takeaways"] = _derive_takeaways_from_narrative(out.get("narrative", ""))
    if not out.get("citations"):
        out["citations"] = []
    nar = out.get("narrative", "") or ""
    if nar and not nar.rstrip().endswith((".", "!", "?", '"', "”", "’", ")", "*", ":")):
        out["narrative"] = nar.rstrip() + " …[truncated]"
        log.warning("[4Ps split] %s narrative appears truncated (no terminal punctuation)",
                    section_name)
    return out


def model_directive(business_model_kind: str | None, economics: dict | None = None) -> str:
    """A hard guardrail injected into every 4Ps section + the viability prompt so the
    narrative layers stop inventing a monetization model the numbers spine never computed
    (audit M4: a $6/drink cafe described with '$12K MRR / 500 subscribers'). General +
    deterministic — the text is selected purely by the resolved model kind; no per-venture
    casing, no hardcoded figures."""
    kind = (business_model_kind or "").lower()
    _NO_SUB = ("HARD RULE: do NOT introduce subscription framing — no MRR, no 'monthly recurring "
               "revenue', no 'subscribers'/'subscriber target', no churn, no CLV:CAC, no 'per "
               "account', no SaaS benchmarks. A subscription may appear ONLY as an explicitly-"
               "labeled OPTIONAL SECONDARY line, never as the headline revenue.")
    # R4 rank 19: HYBRID is per-unit PRIMARY plus a real recurring leg the profile
    # defines. The old code routed it through the pure per-unit branch under _NO_SUB —
    # whose blanket "no MRR / no subscribers / no churn / no CLV:CAC" ban erased the
    # recurring leg the same directive's `extra` line told the model to show. Give it its
    # own directive that permits the recurring leg as a clearly-labeled SECONDARY line.
    if kind == "hybrid":
        unit = ((economics or {}).get("unit")) or "unit"
        return (f"\n\nMONETIZATION MODEL — HYBRID (one-time {unit} sale + a real secondary "
                f"recurring leg). The one-time leg (the device/product) is the PRIMARY, "
                f"headline revenue = {unit}s sold × price per {unit}. The recurring leg is "
                f"REAL and defined by the profile — show it as a clearly LABELED SECONDARY "
                f"line (its retention / recurring revenue belong there, never as the "
                f"headline, and never dropped). Do NOT collapse the venture into a pure "
                f"subscription, and do NOT erase the recurring half the profile defines.")
    if kind in ("transactional", "ecommerce", "services"):
        unit = ((economics or {}).get("unit")) or "unit"
        kindlabel = {
            "transactional": "TRANSACTIONAL RETAIL", "ecommerce": "ECOMMERCE (one-time product sale)",
            "services": "SERVICES (project/retainer)",
        }[kind]
        return (f"\n\nMONETIZATION MODEL — {kindlabel} (revenue = {unit}s sold × price per {unit}). "
                f"{_NO_SUB} Frame all revenue, pricing, place and viability as per-{unit} volume × "
                f"contribution margin.")
    if kind == "subscription":
        return (
            "\n\nMONETIZATION MODEL — SUBSCRIPTION (recurring): MRR, churn, and CLV:CAC apply. "
            "Do NOT reframe it as one-time per-unit retail. Use ONE consistent CAC figure.")
    if kind == "marketplace":
        return (
            "\n\nMONETIZATION MODEL — MARKETPLACE (take-rate). Platform revenue = GMV × take-rate, "
            "NOT the full transaction value. Do NOT count merchant GMV as company revenue and do "
            "NOT apply subscriber CLV:CAC. Model both sides (buyer + seller acquisition).")
    if kind == "ad_supported":
        return (
            "\n\nMONETIZATION MODEL — AD-SUPPORTED (free to the user). There is NO subscriber "
            "price: do NOT invent a subscription fee, MRR, or subscriber CLV:CAC. Frame economics "
            "on users/engagement (DAU/MAU, sessions, eCPM/RPM, fill rate) and ad revenue per user.")
    return (
        "\n\nMONETIZATION MODEL — match the venture's stated business model exactly. If it does "
        "NOT charge a recurring fee, do NOT invent a subscription/MRR model; if it is free / "
        "ad-supported, frame economics on users/engagement, not subscriber CLV:CAC.")


def price_anchor_directive(business_model_kind: str | None, economics: dict | None,
                           van_westendorp: dict | None) -> str:
    """C2/D21: a hard guardrail injected into every 4Ps section (mirrors model_directive's
    pattern) so Place/Product/Promotion — which get NO pricing context in their own
    prompts, only Price does — cannot invent a different average order/job/booking/
    transaction dollar figure than the one number the numbers spine actually uses.
    Real R4 critical: a marketplace's Price section correctly used $450 (the PSM
    optimal / average booking value); Place invented $200 "average job size"; Product
    invented $100 "average order" — three numbers, none anchored to anything.

    Per-unit models (transactional/ecommerce/services/hybrid) prefer economics.
    price_per_unit — the REAL transaction price (B2: this can differ from the PSM
    monthly optimal_price_point, which is the wrong number for those models). Returns
    "" when no price is available (nothing to anchor)."""
    kind = (business_model_kind or "").lower()
    econ = economics or {}
    vw = van_westendorp or {}
    if kind in ("transactional", "ecommerce", "services", "hybrid"):
        price = econ.get("price_per_unit")
        unit = econ.get("unit") or "unit"
        label = f"${price:,.2f} per {unit}" if price else None
    elif kind == "marketplace":
        price = vw.get("optimal_price_point")
        label = (f"${price:,.2f} average transaction/booking value (platform revenue "
                 f"= this × take-rate, NOT the full amount)") if price else None
    elif kind == "subscription":
        price = vw.get("optimal_price_point")
        label = f"${price:,.2f}/mo" if price else None
    else:
        price = vw.get("optimal_price_point")
        label = f"${price:,.2f}" if price else None
    if not price:
        return ""
    return (
        "\n\nPRICE ANCHOR — the ONE canonical transaction/order/job/booking value for "
        f"this venture: {label}. If you cite ANY average order/job/booking/transaction "
        "dollar figure anywhere in this section, it MUST be this exact number — do NOT "
        "invent, estimate, or restate a different figure."
    )


def competitive_density_directive(density: int | None, active_density: int | None) -> str:
    """D22 item 1: a hard guardrail injected into every 4Ps section (same pattern as
    price_anchor_directive/model_directive) so Place/Product/Promotion — which never
    received a competitor-count number in their own prompts at all — cannot invent or
    echo a stale competitor count that later contradicts the number Viability is given.

    Real R4 critical: a report claimed "1 meaningful competitor" in the Market
    Opportunity reasoning while its own Competitors section listed 248 comparable
    venues — the density number never reached the 4Ps sections that fed that
    reasoning, only Viability's own prompt saw it. Returns "" when no density is
    available yet (nothing to anchor).

    KNOWN LIMITATION: for a physical-local venture where the real competitor set is
    only surfaced LATE (the F3 hyperlocal sizing override, after 4Ps has already been
    dispatched — see _surface_late_geo_competitors), this directive sees the
    pre-override density. D22's gate (d22_viability_reasoning_density_coherent) is the
    safety net for that residual case, checked against the FINAL report."""
    if density is None:
        return ""
    n = f"{density} competitor{'s' if density != 1 else ''}"
    if active_density is not None and active_density != density:
        n += f" ({active_density} with active web-momentum signal)"
    return (
        "\n\nCOMPETITIVE DENSITY — the ONE canonical competitor count for this venture: "
        f"{n}. If you cite ANY number of competitors/rivals anywhere in this section, it "
        "MUST be this exact count — do NOT invent, estimate, or restate a different number."
    )


def unit_economics_rubric(business_model_kind: str | None) -> str:
    """D22 item 2: VIABILITY_PROMPT's DIMENSION 3 rubric was a single hardcoded
    CLV:CAC-ratio band for EVERY business_model_kind, but the only real_metrics ever
    fed to it (economics_evc/economics_clv) are subscription-only keys — every other
    kind was being scored against a rubric it had zero data to satisfy (R11 root
    cause). Mirrors model_directive()'s branch-by-kind pattern (four_ps.py:22-62):
    the rubric TEXT itself, not just the guardrail appended after it, now matches the
    venture's real revenue basis."""
    kind = (business_model_kind or "").lower()
    if kind == "subscription":
        return (
            "DIMENSION 3: UNIT ECONOMICS HEALTH (CLV/CAC, gross margin, payback)\n"
            "  1-25:   Negative gross margin OR CLV/CAC < 1:1 OR payback >24mo\n"
            "  26-50:  Marginal: CLV/CAC 1-3:1, payback 12-24mo, requires scale to work\n"
            "  51-75:  Healthy: CLV/CAC 3-5:1, payback 6-12mo, proven elsewhere in category\n"
            "  76-100: Exceptional: CLV/CAC 5:1+, payback <6mo, capital-efficient"
        )
    if kind in ("transactional", "ecommerce", "services", "hybrid"):
        return (
            "DIMENSION 3: UNIT ECONOMICS HEALTH (contribution margin, break-even volume)\n"
            "  This is a PER-UNIT venture (one-time sale, not a subscription) — CLV:CAC and\n"
            "  payback-month language do NOT apply. Score against contribution margin and\n"
            "  break-even volume instead:\n"
            "  1-25:   Contribution margin ≤0% OR the break-even volume is implausible for the venue/channel\n"
            "  26-50:  Thin margin (<20%) OR break-even requires a stretch volume\n"
            "  51-75:  Healthy margin (20-40%) with a realistic break-even volume\n"
            "  76-100: Strong margin (40%+) with break-even reached at a modest, low-risk volume"
        )
    if kind == "marketplace":
        return (
            "DIMENSION 3: UNIT ECONOMICS HEALTH (take-rate economics, two-sided CAC)\n"
            "  This is a MARKETPLACE (take-rate on GMV) — there is no per-subscriber CLV:CAC.\n"
            "  Score against take-rate viability and two-sided acquisition cost instead:\n"
            "  1-25:   Take-rate too thin to cover two-sided CAC, or one side (supply/demand) has no acquisition plan\n"
            "  26-50:  Plausible take-rate but unproven liquidity — CAC for both sides unmodeled\n"
            "  51-75:  Healthy take-rate with a credible plan to acquire and retain both sides\n"
            "  76-100: Take-rate + liquidity strategy already proven in an analogous marketplace at this stage"
        )
    if kind == "ad_supported":
        return (
            "DIMENSION 3: UNIT ECONOMICS HEALTH (ad revenue per user vs cost-to-serve)\n"
            "  This venture is FREE to the user (ad-supported) — there is no subscriber price, so\n"
            "  subscriber CLV:CAC does not apply. Score against ad revenue per active user vs\n"
            "  cost-to-serve instead:\n"
            "  1-25:   Cost-to-serve exceeds plausible ad revenue per user (eCPM x engagement)\n"
            "  26-50:  Roughly break-even per user; needs scale or a higher-eCPM niche to work\n"
            "  51-75:  Ad revenue per user comfortably covers cost-to-serve at realistic eCPM/fill-rate\n"
            "  76-100: High-value audience/niche commands premium eCPM; wide margin per user"
        )
    # Unknown/unclassified kind — stay generic rather than assume subscription.
    return (
        "DIMENSION 3: UNIT ECONOMICS HEALTH (margin and payback, whatever the revenue basis)\n"
        "  1-25:   Negative or unclear margin; no credible path to profitability per unit of value delivered\n"
        "  26-50:  Marginal profitability; requires scale or favorable assumptions to work\n"
        "  51-75:  Healthy margin with a realistic path to break-even\n"
        "  76-100: Strong, capital-efficient margin with a fast path to break-even"
    )


FOUR_PS_PROMPT = """You are writing a paid-grade 4Ps marketing plan for a new venture. Output goes into a McKinsey-style report. Follow these rules:

1. Every claim must be grounded in observable signals (traffic momentum, real customer voice, competitor homepage scrape, PSM/Max-Diff outputs).
2. Every recommendation must cite source evidence using numbered superscripts: ¹ ² ³ etc.
3. Where data is thin, say so — do NOT fabricate conviction you don't have.
4. Quote actual customer vocabulary in Promotion. Cite competitor channel data in Place. Reference Max-Diff rankings in Product. Reference PSM findings in Price.
5. Each section should be 2-3 short paragraphs PLUS a "Key takeaways" bullet list at the end.

COMPANY PROFILE:
{profile}

TOP COMPETITORS (from market research):
{competitors}

TARGET AUDIENCE (from decoded taste profile):
{audience}

TOP FEATURES (from Max-Diff simulation):
{features}

PRICING ANALYSIS (from Van Westendorp simulation):
{pricing}

CHANNEL STRATEGY (from Place analysis):
{place}

Return JSON with these sections:
{{
  "executive_summary": "3-5 bullet points capturing the most important findings. Each bullet ≤25 words. The 'so-what' a founder needs in 60 seconds.",
  "product": {{
    "narrative": "Product section as 2-3 short paragraphs. Use ¹² etc. citations.",
    "key_takeaways": ["3-4 bullets, each ≤15 words"]
  }},
  "price": {{
    "narrative": "Price section as 2-3 short paragraphs with citations.",
    "key_takeaways": ["3-4 bullets"]
  }},
  "place": {{
    "narrative": "Place section as 2-3 short paragraphs with citations.",
    "key_takeaways": ["3-4 bullets"]
  }},
  "promotion": {{
    "narrative": "Promotion section as 2-3 short paragraphs with citations.",
    "key_takeaways": ["3-4 bullets"]
  }},
  "citations": [
    {{"id": 1, "source": "Trustpilot reviews of Brand X", "claim": "Customers complain about Y"}},
    {{"id": 2, "source": "Van Westendorp PSM simulation", "claim": "Optimal price point is $Z"}},
    {{"id": 3, "source": "Wayback Machine", "claim": "Competitor X is updating site Y times/month"}}
  ]
}}

Write in crisp declarative sentences. No fluff, no 'leveraging synergies'. Quote real phrases from the taste profile where impactful."""


VIABILITY_PROMPT = """You are evaluating commercial viability with structured per-dimension scoring.

DO NOT pick a single overall number. Instead, score each of the 5 dimensions
independently against the calibrated anchors below, then we'll compute the
final score deterministically as a weighted sum. This eliminates "score drift"
where different runs of the same venture produce wildly different numbers.

COMPANY: {company}
CATEGORY: {category}

4Ps PLAN:
PRODUCT: {product}
PRICE: {price}
PLACE: {place}
PROMOTION: {promotion}

KEY METRICS:
- Competitive density: {density} competitors identified ({active_density} with active web-momentum signals — reviews, trend, social)
- Avg competitor opportunity score: {avg_score}
- Target audience confidence: {audience_confidence}
- Signals collected: {signal_count}

═══════════════════════════════════════════════════════════════════
SCORING RUBRIC — score each dimension 1-100 against THESE anchors.
Be precise. If you'd give 47, give 47, not "around 50".
═══════════════════════════════════════════════════════════════════

DIMENSION 1: MARKET OPPORTUNITY (size × growth × saturation)
  These $-buckets are for NATIONAL / digital-scale ventures:
  1-25:   Niche/declining; <$50M TAM or shrinking demand
  26-50:  Stable mid-size; $50M-$1B TAM, flat-to-modest growth
  51-75:  Large + growing; $1B-$10B TAM, secular tailwind
  76-100: Massive + accelerating; $10B+ TAM, structural growth (AI, climate, demographic shift)
  ⚠ SCALE-AWARENESS (read the MARKET SIZING block below FIRST): if the sizing method is
  "trade_area_catchment" (a single physical location — a cafe, gym, salon), the venture is
  HYPERLOCAL and the national $-buckets DO NOT APPLY. Do NOT map a single neighborhood
  cafe to "<$50M = niche/declining". Instead score market opportunity on the LOCAL picture:
   - obtainable SOM vs a viable single-unit revenue (is the trade area big enough to
     support one healthy location?),
   - local competitive density and saturation,
   - whether differentiation can win share in THIS trade area.
  A single location with a healthy obtainable SOM (e.g. several hundred $K/yr) in a dense,
  growing neighborhood is a 55-70 opportunity, NOT a 20. Judge it as "can ONE location
  thrive here?", never "is the national category $1B+?".

DIMENSION 2: DIFFERENTIATION STRENGTH
  1-25:   Pure copycat; no defensible angle; "me too"
  26-50:  1-2 weak differentiators, contested by ≥1 competitor cluster
  51-75:  2-3 strong differentiators with concrete evidence (IP, data, brand, network)
  76-100: Novel category creator OR structural moat (regulatory, network effect, proprietary data)

{unit_economics_rubric}

DIMENSION 4: GTM FEASIBILITY (channel access, sales motion, time-to-first-customer)
  1-25:   Buyer hard to reach, long enterprise sales cycle, no warm channel
  26-50:  Identifiable but expensive channels (paid only); 6-12mo to first 10 customers
  51-75:  Clear PLG or channel partner motion; 1-3mo to first 10 customers
  76-100: Founder-led demand exists; pre-orders or LOIs already; <1mo to first 10

DIMENSION 5: EXECUTION + DATA CONFIDENCE
  1-25:   No founder-market fit signals; data quality very thin (<10 signals)
  26-50:  Mixed: some relevant background, but key metrics unverified
  51-75:  Strong founder-market fit OR rich data (50+ signals, multiple sources agree)
  76-100: Both: domain experts + abundant validation across multiple methods

═══════════════════════════════════════════════════════════════════

Return JSON (every score must be an integer 1-100; explain WHY for each):

{{
  "scores": {{
    "market_opportunity":      {{"score": <1-100>, "reasoning": "1 sentence anchored to the rubric"}},
    "differentiation_strength":{{"score": <1-100>, "reasoning": "1 sentence"}},
    "unit_economics_health":   {{"score": <1-100>, "reasoning": "1 sentence"}},
    "gtm_feasibility":         {{"score": <1-100>, "reasoning": "1 sentence"}},
    "execution_data_confidence":{{"score": <1-100>, "reasoning": "1 sentence"}}
  }},
  "headline": "one ≤12 word phrase summarizing the verdict",
  "summary": "one short paragraph (≤80 words) describing the venture's outlook",
  "strengths": ["top 3 strengths — specific, ≤20 words each"],
  "risks": [
    {{"risk": "specific risk", "likelihood": "low|med|high", "impact": "low|med|high"}}
  ],
  "critical_assumptions": ["2-3 assumptions that, if wrong, tank the score"],
  "recommended_next_steps": [
    {{"horizon": "30d|60d|90d", "action": "verb-first action ≤15 words", "owner_role": "founder|head_of_growth|engineering|other"}}
  ],
  "kill_criteria": [
    "What evidence in the next 90 days would make you abandon this venture? 2-3 falsifiable bullets."
  ],
  "regulatory_considerations": "1-2 sentences on relevant regulations (HIPAA, FDA, GDPR, FTC, etc) — or 'none material' if not applicable.",
  "confidence_in_score": "low | medium | high"
}}

Anchor every score to the rubric. If two dimensions deserve the same score, give them the same; do not artificially spread."""


def assemble_4ps(
    profile: dict,
    competitors: list[dict],
    top_audience: dict,
    max_diff: dict,
    van_westendorp: dict,
    place: dict,
) -> dict:
    """Synthesize the 4Ps marketing plan from all pipeline outputs."""

    # Build compact blobs
    profile_blob = json_blob({
        "name": profile.get("name"),
        "summary": profile.get("summary"),
        "category": profile.get("category"),
        "core_features": profile.get("core_features", [])[:8],
        "target_pain_points": profile.get("target_pain_points", [])[:6],
        "apparent_target_customer": profile.get("apparent_target_customer"),
        "business_model": profile.get("business_model"),
    }, 2000)

    # web-momentum, not competitive strength — see the note in market_sizing's comp_blob.
    competitors_blob = ("\n".join(
        f"  - {c.get('brand')} ({c.get('domain')}) — web-momentum score "
        f"{c.get('opportunity_score', '?')}: {c.get('thesis', '')[:120]}"
        for c in competitors[:5]
    ) + "\n  (score = public-signal momentum 0-100; low = thin public footprint,"
        "\n   not a weak rival)")[:2000]

    audience_blob = json_blob({
        "brand": top_audience.get("brand"),
        "confidence": top_audience.get("confidence"),
        "purchase_motivation": top_audience.get("purchase_motivation"),
        "celebrated": top_audience.get("emotional_triggers", {}).get("celebrated", [])[:5],
        "complained": top_audience.get("emotional_triggers", {}).get("complained", [])[:5],
        "life_context": top_audience.get("life_context", [])[:4],
        "hook_angles": top_audience.get("hook_angles_that_would_work", [])[:3],
    }, 2000)

    features_blob = json_blob(max_diff.get("ranked_features", [])[:10], 1000)
    # 1600, not 1000: the tier out-of-range annotations (#80) took this payload to a
    # measured 1,228 characters, and the qualification on a tier is the part a buyer most
    # needs. json_blob would now shrink it honestly rather than corrupt it, but shrinking
    # a decision-critical payload when the budget is the arbitrary part is the wrong trade.
    pricing_blob = json_blob({
        "optimal_price_point": van_westendorp.get("optimal_price_point"),
        "acceptable_range": van_westendorp.get("acceptable_range"),
        "recommended_tiers": van_westendorp.get("recommended_tiers", []),
    }, 1600)
    place_blob = json_blob({
        "primary_channel": place.get("primary_channel"),
        "secondary_channels": place.get("secondary_channels", []),
        "gtm_motion": place.get("gtm_motion"),
        "whitespace_opportunity": place.get("whitespace_opportunity"),
    }, 1000)

    plan = call_json(
        system="You write sharp, founder-grade marketing plans. No fluff.",
        user=FOUR_PS_PROMPT.format(
            profile=profile_blob,
            competitors=competitors_blob,
            audience=audience_blob,
            features=features_blob,
            pricing=pricing_blob,
            place=place_blob,
        ),
        max_tokens=4000,
    )
    if "_parse_error" in plan:
        return {"error": "4Ps synthesis returned malformed JSON", "_raw": plan.get("_raw", "")[:500]}
    plan["citation_audit"] = _audit_citations(plan)
    return plan


#
# ---------------------------------------------------------------------------
# Iter 35 step 6: 4Ps split into 4 focused prompts (spec step 13 alignment)
# Each P runs in parallel with ONLY the context it needs.
# Benefits: shorter inputs per call, tighter outputs, better per-section depth.
# ---------------------------------------------------------------------------

_P_BASE = """You are a senior partner at McKinsey writing the {section_label} section
of a marketing plan that will be reviewed by a board.

PROSE RULES (each bullet is a SCORE-LIFTING constraint — the LLM-judge will
penalize if you violate any one):
1. EVERY paragraph opens with a recommendation (verb-first: "Adopt", "Shift",
   "Stop", "Pilot", "Test"), NOT a description. Description = 0 action_orientation.
2. EVERY paragraph contains ≥1 specific number (% / $ / count / months / x-multiple).
3. EVERY claim has a ¹ ² ³ citation from the evidence pool below.
4. SHORT SENTENCES. Average ≤20 words per sentence. Long compound sentences =
   readability penalty.
5. NO BUZZWORDS — zero tolerance for any of: leverage, synergies, holistic, robust,
   best-in-class, paradigm, streamline, cutting-edge, world-class, transformational,
   unlock, proactively, journey. The LLM judge's regex will catch these.
6. WHEN DATA IS THIN, say so explicitly: "Data is thin on X — operator should
   validate via Y." This LIFTS the hedging-discipline score; faking conviction LOWERS it.
7. NO FABRICATED CITATIONS. Only cite the artifacts in the evidence pool. Don't
   invent "HR Leader Interviews (N=20)" or quarterly date stamps. Operator-validate
   tag is fine.
8. ARITHMETIC MUST CHECK OUT. Any comparative claim — "discount", "cheaper",
   "savings", "X% more", "Nx" — must be arithmetically true against the OTHER numbers
   you state in this same section. Before writing "a discount vs two bags", compute
   2 × the bag price and confirm your bundle is actually lower. A self-contradicting
   number (e.g. "$45 for two $18 bags is a discount" — it is $9 more) is an instant
   trust failure the judge will penalize hardest.

COMPANY PROFILE:
{profile}

{section_context}

Return JSON in EXACTLY this field order. The narrative MUST come last so that
if your output gets cut off, the structured fields above survive intact.

{{
  "key_takeaways": [
    "Imperative-verb opener · ≤18 words · ≥1 number · ¹ citation",
    "Bullet 2", "Bullet 3", "Bullet 4"
  ],
  "citations": [
    {{"id": 1, "source": "e.g. PSM simulation", "claim": "what it supports"}}
  ],
  "narrative": "3-4 paragraphs (250-400 words). Each paragraph: imperative opener, ≥1 number, ≥1 citation, short sentences. Banned word check before submitting."
}}

Output ALL FOUR fields. The narrative is the ONLY place the LLM judge will read for
prose-quality scoring — make every sentence earn its place."""


# --------------------------------------------------------------------------
# W5-5: the four cross-section guardrails, registered once.
#
# Each exists because a section that never RECEIVES a fact invents one, and two
# sections inventing independently contradict each other. Registering them means a
# fifth guardrail is added in ONE place instead of at every prompt.
# --------------------------------------------------------------------------
@reminder("monetization_model", requires=("business_model_kind",), order=10)
def _r_model(facts: dict) -> str:
    return model_directive(facts.get("business_model_kind"), facts.get("economics"))


@reminder("price_anchor", requires=("business_model_kind", "van_westendorp"), order=20)
def _r_price(facts: dict) -> str:
    return price_anchor_directive(facts.get("business_model_kind"),
                                  facts.get("economics"), facts.get("van_westendorp"))


@reminder("competitive_density", requires=("competitor_density",), order=30)
def _r_density(facts: dict) -> str:
    base = competitive_density_directive(facts.get("competitor_density"),
                                         facts.get("active_signal_density"))
    # BOTH competitor counts, or the prose lies by omission. MEASURED on run12: the prompts
    # carried only the 30-venue profiled roster, so the narrative asserted "30 competitors"
    # THIRTEEN times — including a "Competitor Density Census" citation that is false by the
    # pipeline's own census — while the SOM quietly divided by the real catchment count
    # (102, OSM). The sizing note reconciling the two was one sentence against thirteen.
    # The model can only write the honest pair if every section is handed the honest pair.
    ms = facts.get("market_sizing") or {}
    catchment_n = ms.get("competitors")
    roster_n = facts.get("competitor_density")
    if (isinstance(catchment_n, (int, float)) and not isinstance(catchment_n, bool)
            and roster_n and catchment_n > roster_n):
        base += (f"\nCOMPETITOR COUNTS — HARD RULE: there are {catchment_n:,.0f} venues of "
                 f"this type in the trade area (OpenStreetMap census; the market-share math "
                 f"divides by this full count). The {roster_n:,.0f} profiled in this report "
                 f"are the strongest subset. NEVER present {roster_n:,.0f} as the total "
                 f"competition — say '{catchment_n:,.0f} venues in the trade area' for "
                 f"density/saturation claims, and '{roster_n:,.0f} profiled competitors' "
                 f"only for the named roster.")
    return base


@reminder("volume_ladder", requires=("economics",), order=40)
def _r_volume_ladder(facts: dict) -> str:
    """ONE volume ladder for every section. MEASURED on run9: the report stated FIVE
    incompatible daily-volume targets — sizing ceiling 13.5/day, break-even 51/day, Product
    'target 400 units daily', Place 200/day (enshrined as a critical assumption), Promotion
    ~33/day — and the viability verdict straddled the contradiction. A buyer cannot act on a
    report whose plan and market model live in different universes. The pipeline computes the
    ladder once, in Python; the sections write prose around it, never their own volumes."""
    econ = facts.get("economics") or {}
    unit = econ.get("unit") or "unit"
    parts = []
    be_day = econ.get("break_even_units_per_day")
    if isinstance(be_day, (int, float)) and not isinstance(be_day, bool) and be_day > 0:
        parts.append(f"break-even ≈ {be_day:g} {unit}s/day")
    ms = facts.get("market_sizing") or {}
    som = (ms.get("som") or {}).get("mid") or ms.get("som_usd")
    price = econ.get("price_per_unit")
    # THE TARGET RUNG. A range is not a plan: MEASURED, run17's price section targeted 250
    # /day while place and promotion targeted 150 — 67% apart and BOTH obeying the old
    # "between break-even and the ceiling" rule — and run18's sections, declining to invent
    # one, left the operator with a floor and a roof and nothing in between. The model
    # already knows the answer; it was simply never handed over. Computed in financials.py
    # beside the ramp it depends on, so there is one owner rather than two.
    target = None
    try:
        from financials import planning_target_units_per_day
        target = planning_target_units_per_day(
            som_usd=som, price_per_unit=price, market_scale=ms.get("scale"),
            model=facts.get("business_model_kind") or "transactional")
    except Exception:                                        # noqa: BLE001
        target = None
    if target:
        parts.append(f"PLANNING TARGET ≈ {target['units_per_day']:,.0f} {unit}s/day "
                     f"({target['basis']})")
    if (isinstance(som, (int, float)) and som > 0
            and isinstance(price, (int, float)) and price > 0):
        parts.append(f"obtainable ceiling (SOM) ≈ {som / price / 365:,.0f} {unit}s/day")
    if not parts:
        return ""
    rule = ("HARD RULE: the ONLY daily volumes you may state are the ones above. Quote the "
            "PLANNING TARGET when you need an operating number — never a figure of your "
            "own, and never a different one from another section's. "
            if target else
            "HARD RULE: any daily/monthly volume target you state MUST be consistent with "
            "this ladder (between break-even and the obtainable ceiling, or explicitly "
            "labelled as requiring share beyond the fair-share model). ")
    return ("CANONICAL DAILY-VOLUME LADDER — " + " · ".join(parts) + ". " + rule
            + "NEVER invent a volume target — the five contradictory targets a prior "
              "report shipped made it unusable, and two sections of a later one still "
              "disagreed by 67% while each obeyed a range.")


@reminder("tier_range", requires=("van_westendorp",), order=25)
def _r_tier_range(facts: dict) -> str:
    """Tell every section which recommended tiers the instrument itself disagrees with.

    The annotation lands on the tier dicts (pricing.annotate_tiers_against_range), and the
    price prompt does json.dumps them — but MEASURED on run15 the annotated blob is 1,228
    characters against a [:1000] slice, so two of three notes were cut and the JSON in the
    prompt was left mid-structure. A guardrail delivered by truncation is not delivered.

    The registry is the reliable channel: byte-stable, every section, and recorded in
    _reminders_fired so the artifact can attest it. Kept short for exactly that reason.
    """
    tiers = (facts.get("van_westendorp") or {}).get("recommended_tiers") or []
    flagged = [t for t in tiers
               if isinstance(t, dict) and str(t.get("range_note") or "").strip()]
    if not flagged:
        return ""
    parts = []
    for t in flagged:
        where = ("below the acceptable floor" if t.get("range_status") == "below_floor"
                 else "above the acceptable ceiling")
        parts.append(f"{t.get('name') or '?'} ${t.get('price')} is {where}")
    return ("TIER RANGE — HARD RULE: " + "; ".join(parts) + ". Whenever you state one of "
            "these prices, say in the same sentence that it falls outside the PSM's own "
            "acceptable range and what that means (a loss-leader below the floor, a "
            "low-volume halo SKU above the ceiling). Never present it as a core price "
            "point, and never size volume from it.")


@reminder("allowed_attributes", requires=("core_features",), order=15)
def _r_allowed_attributes(facts: dict) -> str:
    """The venture's actual feature set, so sections stop inventing product specs.

    THE MEASURED CASE (#79's finding a): "pour-overs served in under 3 minutes" appeared
    in Product, drove the Differentiation Strength reasoning — 22% of the viability
    composite — and became one of two critical assumptions, while profile.core_features
    contained no speed claim at all and the differentiators step had returned 0 of 5. A
    fabricated product spec circulating through three load-bearing sections.

    The numeric claim-support check (report/claim_support.py) cannot see this class: a
    fabricated ATTRIBUTE carries no number, or borrows one that happens to be handed
    elsewhere. And a deterministic detector over prose is not defensible — MEASURED on the
    four stored runs, a bag-of-words takeaway/narrative divergence check flags 25% with
    obvious false positives (a fair summary in different words), while a proper-noun
    variant flags 2% and misses the panel's own example. Both are worse than nothing as a
    gate, because noise buries real findings.

    So this constrains the GENERATOR, where being wrong costs nothing: the model is handed
    the real attribute set and told that anything outside it is a proposal, not a property.
    """
    feats = [f for f in (facts.get("core_features") or []) if isinstance(f, str) and f.strip()]
    if not feats:
        return ""
    listed = "; ".join(f.strip() for f in feats[:12])
    return (
        "PRODUCT ATTRIBUTES — HARD RULE: the venture's established attributes are exactly "
        f"[{listed}]. Do NOT assert any other product property — speed, provenance, "
        "temperature, grade, capacity, certification — as a fact about this business. If "
        "you are RECOMMENDING one, write it as a recommendation the operator has not yet "
        "committed to, never as a current capability, and never with a citation marker. A "
        "prior report invented a 3-minute service standard that then drove its "
        "differentiation score and two critical assumptions.")


@reminder("citation_discipline", requires=("economics",), order=45)
def _r_citation_discipline(facts: dict) -> str:
    """A footnote may only sit on a number we actually have.

    MEASURED across runs 12-15: 28 of 190 footnoted 4Ps sentences carried a figure that
    appears in NO deterministic input — "150 drinks/day", "500 local workers within 0.5
    miles", "$0.45 per click", "150 monthly high-intent searches".

    The volume_ladder reminder already forbade inventing volume targets — and the model
    was OBEYING it. run15's break-even is 47.7/day and its ceiling 324/day, so "150
    drinks/day" sits inside the permitted band. The rule bounded the MAGNITUDE and said
    nothing about the MARKER, so the model wrote "Target 150 drinks per day, sitting
    above the 47.7 break-even threshold at a $5.50 price anchor ³" — one invented number
    and two computed ones under a single footnote, the invented one inheriting the
    others' authority. A footnote's entire value is letting a reader tell those apart.

    So this rule is about markers, and it supplies the alternative form: propose freely,
    just do it uncited and say who owns the number. A prohibition with no permitted
    phrasing only moves the invention somewhere else.
    """
    unit = ((facts.get("economics") or {}).get("unit")) or "unit"
    return (
        "CITATION DISCIPLINE — HARD RULE: a ¹ marker may ONLY sit on a sentence whose "
        "numbers appear in the facts given to you above or in the cited source itself. "
        "Any target, quota, radius, headcount or spend you are PROPOSING is a "
        f"recommendation, not a measurement: write it WITHOUT a footnote and name it as "
        f"one — e.g. \"we recommend an opening target of N {unit}s/day (operator "
        "decision)\" — never \"N {unit}s/day ¹\". Do not attach a marker to an invented "
        "figure merely because a real figure sits beside it in the same sentence.")


def _volume_target_for_artifact(economics, market_sizing, business_model_kind):
    """The planning target as the ladder computed it, or None. Same call, same inputs, so
    the artifact cannot disagree with the prompt."""
    econ, ms = economics or {}, market_sizing or {}
    try:
        from financials import planning_target_units_per_day
        t = planning_target_units_per_day(
            som_usd=(ms.get("som") or {}).get("mid") or ms.get("som_usd"),
            price_per_unit=econ.get("price_per_unit"),
            market_scale=ms.get("scale"),
            model=business_model_kind or "transactional")
        return (t or {}).get("units_per_day")
    except Exception:                                        # noqa: BLE001
        return None


def section_reminders(business_model_kind=None, economics=None, van_westendorp=None,
                      competitor_density=None, active_signal_density=None,
                      market_sizing=None, core_features=None) -> str:
    """The guardrail block every 4Ps section prompt carries."""
    return Reminders.assemble({
        "business_model_kind": business_model_kind,
        "economics": economics,
        "van_westendorp": van_westendorp,
        "competitor_density": competitor_density,
        "active_signal_density": active_signal_density,
        "market_sizing": market_sizing,
        "core_features": core_features,
    })


def build_section_prompts(bodies: dict, reminders: str) -> dict:
    """Append the reminder block to every section prompt — uniformly, by construction."""
    suffix = f"\n\n{reminders}" if reminders and reminders.strip() else ""
    return {name: body + suffix for name, body in bodies.items()}


def _audit_citations(plan: dict) -> dict:
    """Post-draft citation pass (W4-2): which factual claims are actually attributed?

    The prompts DEMAND a ¹ citation on every claim; nothing verified compliance, so a
    dated or dollar claim with no marker — or a ⁷ pointing at a citation that was never
    emitted — shipped looking exactly as sourced as a real one. Advisory metadata: it
    annotates the report, it does not block it.
    """
    try:
        from report.citation import audit_sections
        sections = {p: plan.get(p) for p in ("product", "price", "place", "promotion")
                    if isinstance(plan.get(p), dict)}
        return audit_sections(sections, plan.get("citations") or [])
    except Exception as e:  # never let an advisory counter break synthesis
        log.warning("[4Ps] citation audit failed: %s", e)
        return {}


def _product_prompt(profile_blob, features_blob, competitors_blob, audience_celebrated):
    ctx = (
        f"TOP FEATURES (Max-Diff importance ranking):\n{features_blob}\n\n"
        f"COMPETITOR LANDSCAPE (what they offer):\n{competitors_blob}\n\n"
        f"WHAT CUSTOMERS CELEBRATE ABOUT ALTERNATIVES: {audience_celebrated}\n\n"
        "Focus the Product section on: core value proposition, MVP feature set anchored to the top 3-5 Max-Diff features, "
        "MVP vs future roadmap split, and a one-sentence positioning statement."
    )
    return _P_BASE.format(section_label="Product", profile=profile_blob, section_context=ctx)


def _price_prompt(profile_blob, pricing_blob, benchmark_blob, economics_blob, psm_ok=True):
    if psm_ok:
        psm_ctx = f"PSM PRICING OUTPUT:\n{pricing_blob}\n\n"
    else:
        # The Van Westendorp PSM simulation produced no usable output. Do NOT let the
        # model narrate tier numbers and attribute them to a method that never ran —
        # that is false provenance (audit cycle36).
        psm_ctx = (
            "PSM PRICING: UNAVAILABLE — the Van Westendorp simulation did not return "
            "usable output this run. DO NOT cite 'PSM simulation' or 'Van Westendorp' "
            "as a source anywhere. Derive any price points from the COMPETITOR "
            "BENCHMARK below and label them explicitly as estimates the operator must "
            "validate (e.g. 'estimate — no PSM backing this run').\n\n")
    ctx = (
        f"{psm_ctx}"
        f"COMPETITOR BENCHMARK (normalized per-unit):\n{benchmark_blob}\n\n"
        f"UNIT ECONOMICS (CLV / CAC / EVC):\n{economics_blob}\n\n"
        "Focus the Price section on the PRIMARY product FIRST, in its natural transaction "
        "unit. If the COMPANY PROFILE states a price (e.g. '$6 per drink', '$15 per visit'), "
        "that price IS the headline of this section: analyze it directly — contribution "
        "margin and break-even per unit, and how it compares to LOCAL competitor norms for "
        "the SAME unit (benchmark each price against its own unit — a per-cup price against "
        "per-cup prices, a per-booking fee against per-booking fees — never against a "
        "differently-packaged or differently-billed line). Only AFTER the core product is "
        "priced may you cover secondary/retail lines "
        "(e.g. packaged goods, subscriptions) — and label them as secondary. State every "
        "UNIT explicitly (per drink, per visit, per seat, per box, per month). Cover the "
        "CLV:CAC implication and how EVC shapes the verdict."
    )
    return _P_BASE.format(section_label="Price", profile=profile_blob, section_context=ctx)


def _place_prompt(profile_blob, place_blob, audience_life_context):
    ctx = (
        f"PLACE ANALYSIS (competitor channels + outliers):\n{place_blob}\n\n"
        f"CUSTOMER LIFE-CONTEXT (where they live online): {audience_life_context}\n\n"
        # cycle30: place prose was scoring 47/100 (worst of all 4Ps). Tighten the brief.
        "Write the Place section as ACTION-ORIENTED RECOMMENDATIONS, not description.\n"
        "EVERY paragraph must:\n"
        "  • OPEN with an imperative verb (Sell, Partner, Recruit, Build, Test, Pilot)\n"
        "  • NAME the specific channel — not 'B2B partnerships' but 'benefits brokers like Mercer, "
        "Aon, NFP', not 'content marketing' but 'guest-posts on First Round Review + 3 SHRM webinars'\n"
        "  • Include a CONCRETE METRIC TO HIT (e.g. '6 broker meetings/quarter', '$0.30 CPM', "
        "'NPS ≥ 40 from initial pilots')\n"
        "  • Cite at least one ¹² ³ source from the evidence pool\n\n"
        "Cover: (1) PRIMARY channel — pick ONE, justify with competitor density data + ACV "
        "math, (2) 2-3 SECONDARY channels with explicit role (lead-gen / awareness / retention), "
        "(3) GTM MOTION — pick exactly one of sales-led / product-led / community-led / "
        "partner-led and explain why with an ACV/cycle-time argument, (4) one OUTLIER bet — "
        "what would competitors NEVER do that we should test?\n\n"
        "BANNED phrases (zero tolerance — reject 'leverage', 'synergies', 'holistic', "
        "'best-in-class', 'unlock', 'streamline', 'cutting-edge', 'transformational', "
        "'proactively', 'robust', 'world-class', 'paradigm')."
    )
    return _P_BASE.format(section_label="Place", profile=profile_blob, section_context=ctx)


def _promotion_prompt(profile_blob, audience_blob, reddit_themes_blob):
    ctx = (
        f"TARGET AUDIENCE (decoded from real customer voice):\n{audience_blob}\n\n"
        f"REDDIT CONVERSATION THEMES (what they actually say):\n{reddit_themes_blob}\n\n"
        "Focus the Promotion section on: primary messaging angle anchored in customer vocabulary (quote real phrases), "
        "recommended channels (content / paid / outbound / partnerships / community), and one concrete ICP-targeted "
        "campaign concept — not an abstract idea but a specific execution."
    )
    return _P_BASE.format(section_label="Promotion", profile=profile_blob, section_context=ctx)


def assemble_4ps_split(
    profile: dict,
    competitors: list[dict],
    top_audience: dict,
    max_diff: dict,
    van_westendorp: dict,
    place: dict,
    pricing_benchmark: dict | None = None,
    economics: dict | None = None,
    reddit_signal: dict | None = None,
    business_model_kind: str | None = None,
    competitor_density: int | None = None,
    active_signal_density: int | None = None,
    market_sizing: dict | None = None,
) -> dict:
    """
    Iter 35 step 6: run the 4Ps as 4 parallel focused prompts instead of one
    giant prompt. Each P sees ONLY the context it needs. Total tokens roughly
    equivalent to the single-prompt version but:
      - each output is deeper (tighter context = more room for nuance)
      - wall-clock time is ~max(Ps) instead of sum — 4× faster
      - failures of one P don't kill the others
    """
    from concurrent.futures import ThreadPoolExecutor

    # Build compact shared blobs once
    profile_blob = json_blob({
        "name": profile.get("name"),
        "summary": profile.get("summary"),
        "category": profile.get("category"),
        "business_model": profile.get("business_model"),
        "apparent_target_customer": profile.get("apparent_target_customer"),
    }, 900)

    competitors_blob = "\n".join(
        f"  - {c.get('brand')} ({c.get('domain')}): {(c.get('thesis') or '')[:100]}"
        for c in (competitors or [])[:5]
    )[:1200]

    features_blob = json_blob([
        {"feature": f.get("feature"), "importance": f.get("importance_score")}
        for f in (max_diff or {}).get("ranked_features", [])[:8]
    ], 700)

    pricing_blob = json_blob({
        "optimal_price_point": (van_westendorp or {}).get("optimal_price_point"),
        "acceptable_range": (van_westendorp or {}).get("acceptable_range"),
        "recommended_tiers": (van_westendorp or {}).get("recommended_tiers", []),
    }, 700)

    benchmark_blob = json_blob({
        "pricing_unit": (pricing_benchmark or {}).get("pricing_unit"),
        "our_pro_price_label": (pricing_benchmark or {}).get("our_pro_price_label"),
        "vs_category_median_pct": (pricing_benchmark or {}).get("vs_category_median_pct"),
        "rows": [
            {"brand": r.get("brand"), "price_label": r.get("price_label"),
             "multiple_of_pro": r.get("multiple_of_pro"), "verdict": r.get("cheaper_or_pricier")}
            for r in ((pricing_benchmark or {}).get("rows") or [])[:5]
        ],
    }, 700) if pricing_benchmark else "(not yet computed)"

    economics_blob = json_blob({
        "clv_usd": (economics or {}).get("clv", {}).get("clv_usd"),
        "max_sustainable_cac_usd": (economics or {}).get("cac_target", {}).get("max_sustainable_cac_usd"),
        "evc_verdict": (economics or {}).get("evc", {}).get("verdict"),
        "price_as_pct_of_evc": (economics or {}).get("evc", {}).get("price_as_pct_of_evc"),
        "customer_annual_roi_usd": (economics or {}).get("evc", {}).get("customer_annual_roi_usd"),
        "differentiation_reasoning": (economics or {}).get("evc", {}).get("differentiation_reasoning"),
    }, 700) if economics else "(not yet computed)"

    place_blob = json_blob({
        "primary_channel": (place or {}).get("primary_channel"),
        "secondary_channels": (place or {}).get("secondary_channels", []),
        "gtm_motion": (place or {}).get("gtm_motion"),
        "whitespace_opportunity": (place or {}).get("whitespace_opportunity"),
    }, 700)

    audience_blob = json_blob({
        "purchase_motivation": top_audience.get("purchase_motivation"),
        "celebrated": (top_audience.get("emotional_triggers") or {}).get("celebrated", [])[:5],
        "complained": (top_audience.get("emotional_triggers") or {}).get("complained", [])[:5],
        "hook_angles": top_audience.get("hook_angles_that_would_work", [])[:3],
    }, 900)

    audience_celebrated = ", ".join(
        (top_audience.get("emotional_triggers") or {}).get("celebrated", [])[:4]
    ) or "(unknown)"

    audience_life_context = ", ".join(
        (top_audience.get("life_context") or [])[:4]
    ) or "(unknown)"

    reddit_themes_blob = json_blob({
        "complaint_themes": ((reddit_signal or {}).get("themes") or {}).get("complaint_themes", []),
        "praise_themes": ((reddit_signal or {}).get("themes") or {}).get("praise_themes", []),
        "powerful_quotes": ((reddit_signal or {}).get("themes") or {}).get("powerful_quotes", []),
    }, 900) if reddit_signal else "(no Reddit signal available)"

    # The per-section call lives at module level (_run_section) under the SectionPayload
    # contract — see the block at the top of this file for the run8 measurement that forced
    # it. The old nested closure here retried blindly on parse errors and then papered over
    # missing fields (a synthesized "narrative", an empty citations list), which is exactly
    # how a sourceless citation shipped.
    psm_ok = bool((van_westendorp or {}).get("optimal_price_point")) and not (van_westendorp or {}).get("error")
    # W5-5: the guardrails now come from one registry instead of `+ _md + _pa + _cd`
    # repeated per prompt. Adding a fifth used to mean editing four call sites and
    # hoping none was missed — and a missed site is exactly the contradiction these
    # exist to prevent. test_reminders pins that every section carries every one.
    reminders = section_reminders(business_model_kind, economics, van_westendorp,
                                  competitor_density, active_signal_density,
                                  market_sizing=market_sizing,
                                  core_features=(profile or {}).get("core_features"))
    tasks = build_section_prompts({
        "product": _product_prompt(profile_blob, features_blob, competitors_blob, audience_celebrated),
        "price": _price_prompt(profile_blob, pricing_blob, benchmark_blob, economics_blob, psm_ok=psm_ok),
        "place": _place_prompt(profile_blob, place_blob, audience_life_context),
        "promotion": _promotion_prompt(profile_blob, audience_blob, reddit_themes_blob),
    }, reminders)

    results: dict = {}
    # copy_context() so the ledger's current-step ContextVar survives into the pool threads.
    # MEASURED on run13: all 4Ps LLM events traced as step=None, which is why the cache-hit
    # forensics took byte-identity comparison instead of one trace query.
    import contextvars
    _ctx = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {name: pool.submit(_ctx.copy().run, _run_section, name, prompt)
                for name, prompt in tasks.items()}
        for name, fut in futs.items():
            try:
                results[name] = fut.result(timeout=90)
            except Exception as e:
                log.warning("[4Ps split] %s failed: %s", name, e)
                results[name] = {"narrative": f"({name} section timed out)", "key_takeaways": [], "citations": []}

    # Assemble in the same shape as assemble_4ps so downstream (viability, report) stays unchanged
    all_citations = []
    for sect in ("product", "price", "place", "promotion"):
        for c in results.get(sect, {}).get("citations", []):
            all_citations.append({**c, "_section": sect})

    # Iter 42 (issue 1): emit HTML <strong> directly, not raw markdown `**` —
    # the template uses `| safe` for narrative HTML rendering, and the executive
    # summary is text-rendered, so `**` was showing as literal asterisks.
    exec_bullets = []
    for sect in ("product", "price", "place", "promotion"):
        sect_data = results.get(sect, {})
        tks = sect_data.get("key_takeaways", [])
        if tks:
            exec_bullets.append(f"<strong>{sect.capitalize()}.</strong> {tks[0]}")
        else:
            # Fall back to first sentence of narrative
            nar = sect_data.get("narrative") or ""
            first = _first_sentence(nar)
            if first:
                exec_bullets.append(f"<strong>{sect.capitalize()}.</strong> {first}")
    executive_summary = " ".join(exec_bullets) if exec_bullets else "See individual sections below."

    plan = {
        "executive_summary": executive_summary,
        "product": results.get("product", {}),
        "price": results.get("price", {}),
        "place": results.get("place", {}),
        "promotion": results.get("promotion", {}),
        "citations": all_citations,
        "_mode": "split",  # for debugging / auditing
        # WHAT THE PROMPTS ACTUALLY CARRIED, in the artifact. run13's paired-count rule
        # silently never reached the prompts, and proving that took byte-identity forensics
        # against run12 because nothing recorded the assembled reminder block. A prompt-side
        # fix is invisible under LLM-cache replay unless the artifact states what fired.
        # THE NUMBER THE SECTIONS WERE TOLD TO PLAN AROUND, in the artifact. D61 checks
        # every volume the prose states against the ladder's rungs, and a gate that has to
        # re-derive the target would be a second owner of it — the failure this whole fix
        # is about. Recording what was actually handed over also makes a prompt-side
        # regression visible without byte-identity forensics, the way _reminders_fired does.
        "_volume_target_units_per_day": (
            _volume_target_for_artifact(economics, market_sizing, business_model_kind)),
        "_reminders_fired": {
            "volume_ladder": "CANONICAL DAILY-VOLUME LADDER" in reminders,
            "competitor_counts_pair": "COMPETITOR COUNTS — HARD RULE" in reminders,
            "competitive_density": "competitor" in reminders.lower(),
            "monetization_model": "MONETIZATION MODEL" in reminders,
            "citation_discipline": "CITATION DISCIPLINE" in reminders,
            "tier_range": "TIER RANGE — HARD RULE" in reminders,
            "allowed_attributes": "PRODUCT ATTRIBUTES — HARD RULE" in reminders,
        },
        "_reminder_facts": {
            "competitor_density": competitor_density,
            "active_signal_density": active_signal_density,
            "ms_competitors": (market_sizing or {}).get("competitors"),
            "ms_som_mid": ((market_sizing or {}).get("som") or {}).get("mid")
                          or (market_sizing or {}).get("som_usd"),
        },
    }
    plan["citation_audit"] = _audit_citations(plan)
    return plan


REGENERATE_SECTION_PROMPT = """You are revising ONE section of a paid-grade 4Ps marketing plan. The operator was unhappy with the existing section and asked for a regeneration.

SECTION TO REVISE: {section_name}

OPERATOR STEERING (incorporate this — it is the reason for regeneration):
{steering}

EXISTING SECTION (this is what the operator rejected — write something materially different):
{current}

CONTEXT YOU MAY DRAW FROM:
- Company profile: {profile}
- Top competitors: {competitors}
- Target audience signals: {audience}
- Max-Diff feature ranking: {features}
- Van Westendorp pricing: {pricing}
- Channel analysis: {place}

Rules:
1. Stay grounded in the same observable signals as the original — do not invent new data.
2. Address the operator's steering EXPLICITLY. If they asked for "more concrete pricing", give numbers, not theory.
3. Keep the structure: 2-3 short paragraphs PLUS a "key_takeaways" bullet list.
4. Use ¹² ³ citations referencing the same evidence pool.

Return JSON:
{{
  "narrative": "Revised section text — 2-3 short paragraphs with citations.",
  "key_takeaways": ["3-4 bullets, each ≤15 words"]
}}"""


def regenerate_section(
    section_name: str,
    steering: str,
    current_section: dict,
    profile: dict,
    competitors: list[dict],
    top_audience: dict,
    max_diff: dict,
    van_westendorp: dict,
    place: dict,
) -> dict:
    """Regenerate ONE 4P section with operator steering. Returns {narrative, key_takeaways}."""
    if section_name not in ("product", "price", "place", "promotion"):
        return {"error": f"Invalid section '{section_name}'. Must be one of: product, price, place, promotion."}

    profile_blob = json_blob({
        "name": profile.get("name"),
        "summary": profile.get("summary"),
        "category": profile.get("category"),
        "business_model": profile.get("business_model"),
    }, 1200)
    competitors_blob = "\n".join(
        f"  - {c.get('brand')} ({c.get('domain')}) — {c.get('thesis', '')[:100]}"
        for c in (competitors or [])[:5]
    )[:1500]
    audience_blob = json_blob({
        "brand": top_audience.get("brand"),
        "purchase_motivation": top_audience.get("purchase_motivation"),
        "celebrated": (top_audience.get("emotional_triggers") or {}).get("celebrated", [])[:5],
        "complained": (top_audience.get("emotional_triggers") or {}).get("complained", [])[:5],
    }, 1500)
    features_blob = json_blob((max_diff or {}).get("ranked_features", [])[:8], 800)
    pricing_blob = json_blob({
        "optimal_price_point": (van_westendorp or {}).get("optimal_price_point"),
        "acceptable_range": (van_westendorp or {}).get("acceptable_range"),
        "recommended_tiers": (van_westendorp or {}).get("recommended_tiers", []),
    }, 800)
    place_blob = json_blob({
        "primary_channel": (place or {}).get("primary_channel"),
        "secondary_channels": (place or {}).get("secondary_channels", []),
        "gtm_motion": (place or {}).get("gtm_motion"),
    }, 800)

    current_blob = json_blob(current_section or {}, 1500)

    revised = call_json(
        system="You revise marketing-plan sections sharply, taking operator steering seriously. Return only JSON.",
        user=REGENERATE_SECTION_PROMPT.format(
            section_name=section_name,
            steering=(steering or "(no steering provided — improve clarity, sharpen takeaways, eliminate fluff)")[:600],
            current=current_blob,
            profile=profile_blob,
            competitors=competitors_blob,
            audience=audience_blob,
            features=features_blob,
            pricing=pricing_blob,
            place=place_blob,
        ),
        max_tokens=2000,
    )
    if "_parse_error" in revised:
        return {"error": "Section regeneration returned malformed JSON", "_raw": revised.get("_raw", "")[:500]}
    # Make sure the shape is right
    if "narrative" not in revised:
        return {"error": "Regenerated section missing 'narrative' field", "_raw": json_blob(revised, 500)}
    if "key_takeaways" not in revised or not isinstance(revised.get("key_takeaways"), list):
        revised["key_takeaways"] = []
    return revised


def _first_sentence(text: str) -> str:
    """Iter 41: first sentence (≤200 chars) of a narrative — used as exec-summary fallback."""
    if not text:
        return ""
    import re
    text = text.strip()
    # Strip markdown citations like ¹² to keep the summary clean
    text = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", text)
    m = re.search(r"^(.{20,200}?[\.!?])(\s|$)", text, re.S)
    if m:
        return m.group(1).strip()
    # Fall back to first 180 chars + ellipsis
    return (text[:180] + "…") if len(text) > 180 else text


def _derive_takeaways_from_narrative(narrative: str) -> list[str]:
    """
    Iter 41 (#2): when LLM truncated before key_takeaways, salvage 3 bullets
    from the narrative itself by taking the first sentence of each
    paragraph (which usually is the topic sentence).
    """
    if not narrative or len(narrative) < 50:
        return []
    import re
    # Strip citation marks
    clean = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰]+", "", narrative)
    # Split on double newlines first; if none, on sentence terminators
    paras = [p.strip() for p in clean.split("\n\n") if p.strip()]
    if len(paras) < 2:
        # Fallback: split into sentences (lowered threshold to 12 chars to keep terse bullets)
        sentences = re.split(r"(?<=[.!?])\s+", clean)
        sentences = [s.strip() for s in sentences if 12 < len(s.strip()) < 200]
        out = sentences[:4]
    else:
        out = []
        for p in paras[:4]:
            first = _first_sentence(p)
            if first and 20 < len(first) < 200:
                out.append(first)
    return out[:4] if out else []


def _section_text(section) -> str:
    """4Ps sections are now {narrative, key_takeaways} dicts; legacy were strings."""
    if isinstance(section, dict):
        return section.get("narrative", "") or ""
    return str(section or "")


# Ceiling for unit_economics_health while the cost structure is a placeholder: below the
# 50 midpoint (it cannot read as a strength), above the floor (a placeholder is uncertainty,
# not evidence of BAD economics).
_PLACEHOLDER_COST_SCORE_CAP = 45


def score_viability(
    profile: dict,
    four_ps: dict,
    density: int,
    avg_score: float,
    audience_confidence: float,
    signal_count: int,
    active_density: int = 0,
    # Iter 43 (issue I): real cross-pipeline data rather than LLM guessing
    differentiators_strength: str | None = None,
    differentiators_count: int | None = None,
    customer_universe_count: int | None = None,
    economics_evc: str | None = None,
    economics_clv: float | None = None,
    market_sizing: dict | None = None,
    business_model_kind: str | None = None,
    economics: dict | None = None,
) -> dict:
    """Score viability 1-100 from the completed 4Ps plan + supporting metrics."""

    # Build a "REAL pipeline metrics" addendum the LLM should anchor against
    real_metrics = []
    # cycle36: feed the AUTHORITATIVE market sizing so market_opportunity is scored on the
    # venture's real TAM/SOM and scale — not the LLM's guess of the national category size
    # (which made a single cafe score against "$1B-$10B" national buckets).
    ms = market_sizing or {}
    if ms.get("publishable") is False:
        # R4 rank 5: the sizing failed its own integrity gate. Feeding the withheld
        # TAM here labelled "authoritative" is how all four blocked corpus reports
        # scored Market Opportunity (22% of the composite) on a number the same page
        # says "do not rely on" — one wrote "a massive $1.22B TAM" into its summary.
        # The model cannot restate a number it never receives.
        real_metrics.append(
            "- MARKET SIZING: FAILED ITS INTEGRITY GATE — the TAM/SAM/SOM figures are "
            "withheld and MUST NOT appear anywhere in your reasoning or summary. "
            "Score market_opportunity as UNKNOWN: use a neutral 50 and state plainly "
            "that the sizing failed validation, so the score reflects uncertainty, "
            "not opportunity.")
    elif ms.get("tam") or ms.get("method"):
        _t = (ms.get("tam") or {}).get("mid")
        _s = (ms.get("som") or {}).get("mid")
        _method = ms.get("method") or "national/digital"
        _scale = (ms.get("scale_decision") or {}).get("scale") or ("hyperlocal" if _method == "trade_area_catchment" else "national")
        real_metrics.append(
            f"- MARKET SIZING (authoritative — score market_opportunity against THIS, not "
            f"the national category): scale='{_scale}', method='{_method}', "
            f"TAM={_t}, obtainable SOM={_s}, confidence='{ms.get('data_quality') or ms.get('confidence')}'. "
            + ("This is a HYPERLOCAL single-location venture — apply the scale-awareness "
               "rule in DIMENSION 1; do NOT use national $-buckets."
               if _method == "trade_area_catchment" else
               "National/digital scale — the standard $-buckets apply."))
    if differentiators_strength is not None:
        real_metrics.append(f"- Differentiators block: strength='{differentiators_strength}', {differentiators_count or 0} concrete differentiators found across the 5-dimension analysis. **Anchor differentiation_strength score against THIS finding** — if 0 differentiators were found by the dimension-by-dimension audit, do NOT score >40 for differentiation.")
    if customer_universe_count is not None:
        real_metrics.append(f"- Customer universe: {customer_universe_count} real companies identified as ICP-matching. **Anchor execution_data_confidence to this** — <5 is genuinely thin.")
    if economics_evc is not None:
        real_metrics.append(f"- EVC verdict: '{economics_evc}'. **Anchor unit_economics_health to this** — 'data-thin' or 'over-priced' should pull score below 50.")
    if economics_clv is not None:
        real_metrics.append(f"- CLV: ${economics_clv}.")
    # D22 item 2: economics_evc/economics_clv are subscription-only keys — for every
    # other kind, real_metrics had NOTHING for unit_economics_health. Surface the
    # actual computed economics object (retail_unit_economics' contribution margin,
    # or the honest marketplace/ad_supported revenue_basis disclosure) instead.
    _econ = economics or {}
    _ue_anchor = None
    # R4 rank 14: the computed per-unit contribution margin was surfaced only for
    # model=='transactional', so hybrid/services/ecommerce (also per-unit, also carrying
    # a real contribution_margin_pct) got NOTHING and viability invented a margin —
    # 28d0ec61 computed 65.5% but viability called it "thin on unit-level contribution
    # margins" and scored 40. Surface it for every per-unit kind and record the exact
    # anchor so it is both fed AND verifiable.
    from business_model import is_per_unit
    if is_per_unit(_econ.get("model")) and _econ.get("contribution_margin_pct") is not None:
        real_metrics.append(
            f"- Unit economics (per-unit, {_econ.get('unit', 'unit')}): contribution margin "
            f"{_econ['contribution_margin_pct']}%, break-even {_econ.get('break_even_units_per_month', '?')} "
            f"{_econ.get('unit', 'units')}/month. **Anchor unit_economics_health to this** — do NOT "
            "invent a CLV:CAC ratio.")
        _ue_anchor = {"contribution_margin_pct": _econ["contribution_margin_pct"],
                      "unit": _econ.get("unit"), "source": "economics"}
    elif business_model_kind in ("marketplace", "ad_supported") and _econ.get("revenue_basis"):
        _needs = ", ".join(_econ.get("needs_operator_input") or [])
        real_metrics.append(
            f"- Unit economics ({business_model_kind}): {_econ['revenue_basis']}."
            + (f" Still needs operator input: {_needs}." if _needs else "")
            + " **Anchor unit_economics_health to this revenue basis** — do NOT invent a "
              "subscriber CLV:CAC ratio.")
    real_metrics_blob = "\n".join(real_metrics) if real_metrics else "(no cross-pipeline metrics passed)"

    result = call_json(
        system="You evaluate commercial viability rigorously. Return only JSON. Be CONCISE — total response ≤500 words.",
        user=VIABILITY_PROMPT.format(
            company=profile.get("name", "Unknown"),
            category=profile.get("category", "unknown"),
            # Trim 4Ps inputs to keep viability prompt small enough that response fits in tokens
            product=_section_text(four_ps.get("product"))[:800],
            price=_section_text(four_ps.get("price"))[:600],
            place=_section_text(four_ps.get("place"))[:600],
            promotion=_section_text(four_ps.get("promotion"))[:800],
            density=density,
            active_density=active_density,
            avg_score=avg_score,
            audience_confidence=audience_confidence,
            signal_count=signal_count,
            unit_economics_rubric=unit_economics_rubric(business_model_kind),
        ) + "\n\nREAL PIPELINE METRICS (anchor scoring to these — they are authoritative over your guesses):\n" + real_metrics_blob
          + model_directive(business_model_kind, economics),  # M4: no subscription bleed in viability
        max_tokens=4500,  # iter 40: bumped from 3000 — added 5-dim per-dimension scoring with reasoning, was truncating to 2/5 dims
    )
    if "_parse_error" in result:
        return {"error": "Viability scoring returned malformed JSON", "_raw": result.get("_raw", "")[:500]}

    # A PLACEHOLDER COST STRUCTURE CANNOT SCORE "STRONG". MEASURED on run9: the verdict's #1
    # strength was unit_economics_health 82/100 — the largest slice of the 54/100 composite —
    # resting entirely on an admitted "$5,000/mo generic placeholder" for TOTAL fixed costs,
    # in a city where rent alone commonly exceeds it (realistic SF costs put break-even at
    # 200-350 drinks/day, not 51). The fine print disclosed the placeholder; the headline,
    # the strengths list and a fifth of the composite promoted it to established fact.
    # Disclosure in a footnote does not license the headline. Deterministic clamp, applied to
    # the LLM's sub-score before composition, with the reason written into the dimension's
    # own reasoning so the report says WHY the score is capped.
    _cost_src = str((economics or {}).get("cost_source") or "").lower()
    if "placeholder" in _cost_src or "unsourced" in _cost_src:
        _ue = (result.get("scores") or {}).get("unit_economics_health")
        if (isinstance(_ue, dict) and isinstance(_ue.get("score"), (int, float))
                and not isinstance(_ue.get("score"), bool)
                and _ue["score"] > _PLACEHOLDER_COST_SCORE_CAP):
            _ue["reasoning"] = (
                f"capped at {_PLACEHOLDER_COST_SCORE_CAP}: the cost structure is "
                f"'{(economics or {}).get('cost_source')}' — unit economics cannot be a "
                f"strength until real costs replace it. LLM had scored "
                f"{_ue['score']:.0f}: " + str(_ue.get("reasoning") or ""))
            _ue["score"] = _PLACEHOLDER_COST_SCORE_CAP

    # Iter 40: compose final viability_score deterministically from per-dimension scores.
    # The LLM gives 5 anchored sub-scores; we weight + sum. Same input → same output.
    final = _compose_viability_score(result.get("scores") or {})
    if final is not None:
        result["viability_score"] = final["score"]
        result["tier"] = final["tier"]
        result["score_composition"] = final["composition"]
    elif "viability_score" not in result:
        # LLM didn't populate scores nor a final value — degrade gracefully
        result["viability_score"] = None
        result["tier"] = "unknown"

    # Sanity defaults
    if not result.get("strengths"):
        result["strengths"] = ["(LLM did not populate — likely truncation; see raw)"]
    if not result.get("risks"):
        result["risks"] = ["(LLM did not populate — likely truncation; see raw)"]
    # R4 rank 14: record the real per-unit margin viability was anchored to, so the
    # anchoring is verifiable (gate D37) rather than an unprovable prompt claim.
    if _ue_anchor is not None:
        result["unit_economics_anchor"] = _ue_anchor
    return result


# Iter 40: weights for the 5-dimension composite. Equal-weight by default;
# rationale: each is a distinct go/no-go axis and we don't yet have data to
# justify uneven weighting. Operator weights (segment scoring) are a separate
# system that scores SEGMENTS within a venture; this is the venture-level.
_VIABILITY_WEIGHTS = {
    "market_opportunity":       0.22,  # bigger but slow
    "differentiation_strength": 0.22,
    "unit_economics_health":    0.22,
    "gtm_feasibility":          0.20,
    "execution_data_confidence":0.14,  # less than the others — confidence-in-data adjusts the others
}


def _compose_viability_score(scores: dict) -> dict | None:
    """
    Iter 40: deterministic composition of per-dimension scores into final 1-100.

    Returns {score, tier, composition: [{dim, raw, weight, contribution}]} or None
    if scores dict is empty/malformed.
    """
    if not scores or not isinstance(scores, dict):
        return None
    composition = []
    weighted_sum = 0.0
    total_weight = 0.0
    for dim, weight in _VIABILITY_WEIGHTS.items():
        entry = scores.get(dim)
        if not entry:
            continue
        raw = entry.get("score") if isinstance(entry, dict) else entry
        try:
            raw_v = float(raw)
        except (TypeError, ValueError):
            continue
        if not (0 < raw_v <= 100):
            continue
        contribution = raw_v * weight
        weighted_sum += contribution
        total_weight += weight
        composition.append({
            "dimension": dim,
            "raw": int(round(raw_v)),
            "weight": weight,
            "contribution": round(contribution, 2),
            "reasoning": entry.get("reasoning", "") if isinstance(entry, dict) else "",
        })
    if total_weight == 0:
        return None
    final = round(weighted_sum / total_weight)
    # Snap to anchor bands matching the rubric
    if final <= 30:    tier = "high-risk"
    elif final <= 60:  tier = "moderate"
    elif final <= 80:  tier = "strong"
    else:              tier = "exceptional"
    return {"score": final, "tier": tier, "composition": composition}
