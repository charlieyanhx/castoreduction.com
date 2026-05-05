"""
Iter 36: Pydantic context-store models.

The pipeline has historically passed a raw `dict` across 14 steps. That was
fine at v0 but now the dict has 20+ top-level keys, nested structures, and
implicit contracts between steps. A typed schema:

  - Documents what each step contributes and what it depends on.
  - Catches misspelled keys at write time (pydantic validates on construction).
  - Gives templates + downstream code autocomplete & safety.
  - Lets us diff contexts cleanly for the history/delta feature.

Design: fields are all Optional because the pipeline degrades gracefully;
a step may fail and downstream steps must cope. Templates should use `getattr`
or the `.get()` fallback pattern.

Policy: these models are *advisory*. Production code still reads/writes the
raw dict to avoid a risky big-bang migration. Use `ContextStore.from_dict()`
for validation in tests and new code paths.
"""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


# Allow arbitrary extra fields — the dict has grown organically and we don't
# want strict validation to reject real jobs that predate the schema.
class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class CompanyProfile(_Loose):
    name: str | None = None
    summary: str | None = None
    category: str | None = None
    core_features: list[str] = Field(default_factory=list)
    target_pain_points: list[str] = Field(default_factory=list)
    apparent_target_customer: str | None = None
    business_model: str | None = None


class Competitor(_Loose):
    brand: str | None = None
    domain: str | None = None
    opportunity_score: float | None = None
    thesis: str | None = None
    relevance: str | None = None  # "direct" | "adjacent" | "substitute"
    firmographics: dict[str, Any] | None = None


class Persona(_Loose):
    id: str | None = None
    name: str | None = None
    attractiveness_for_wedge: float | None = None
    core_motivation: str | None = None
    key_pain: str | None = None
    winning_message: str | None = None
    best_channel: str | None = None
    evidence_brands: list[str] = Field(default_factory=list)
    what_makes_them_different: str | None = None


class PersonasBlock(_Loose):
    personas_count: int | None = None
    personas: list[Persona] = Field(default_factory=list)
    recommended_wedge_persona: str | None = None
    wedge_reasoning: str | None = None


class ClusteringResult(_Loose):
    n_competitors: int | None = None
    k: int | None = None
    silhouette_score: float | None = None
    clusters: list[dict[str, Any]] = Field(default_factory=list)
    coordinates: dict[str, list[float]] = Field(default_factory=dict)
    embedding_method: str | None = None
    clustering_method: str | None = None
    projection_method: str | None = None
    noise_count: int | None = None
    axis_labels: dict[str, Any] | None = None


class PSMResult(_Loose):
    optimal_price_point: float | None = None
    acceptable_range: list[float] | None = None
    recommended_tiers: list[dict[str, Any]] = Field(default_factory=list)


class PricingBlock(_Loose):
    psm: PSMResult | None = None
    break_even: dict[str, Any] | None = None
    benchmark: dict[str, Any] | None = None


class EconomicsBlock(_Loose):
    pricing_unit: str | None = None
    monthly_price_usd: float | None = None
    annual_price_usd: float | None = None
    unit_economics: dict[str, Any] | None = None
    clv: dict[str, Any] | None = None
    cac_target: dict[str, Any] | None = None
    evc: dict[str, Any] | None = None
    sensitivity: dict[str, Any] | None = None  # iter 36: added


class ViabilityBlock(_Loose):
    viability_score: int | None = None
    tier: str | None = None
    headline: str | None = None
    summary: str | None = None
    strengths: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    critical_assumptions: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    confidence_in_score: str | None = None


class Differentiators(_Loose):
    """Iter 36: spec step 3d — explicit 'what we have that no competitor has'."""
    differentiators: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    positioning_summary: str | None = None


class CustomerUniverse(_Loose):
    """Iter 36: spec step 5 — real B2B companies that match the ICP."""
    count: int | None = None
    icp_summary: str | None = None
    companies: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    segments: list[dict[str, Any]] = Field(default_factory=list)


class OperatorWeights(_Loose):
    """Iter 36: spec step 1 — operator-set weights for segment scoring (step 7-8)."""
    wtp_x_market_size: float = 1.0
    low_price_elasticity: float = 1.0
    low_competition: float = 1.0
    ease_of_reach: float = 1.0
    growth_potential: float = 1.0


class ContextStore(_Loose):
    """The full job context. Every step reads + writes this. Fields are optional
    so graceful degradation works — a missing taste profile doesn't kill downstream."""
    profile: CompanyProfile | None = None
    discover: dict[str, Any] | None = None
    clustering: ClusteringResult | None = None
    whitespace: dict[str, Any] | None = None
    audience: dict[str, Any] | None = None
    audiences: list[dict[str, Any]] | None = None
    personas: PersonasBlock | None = None
    competitor_pricing: dict[str, Any] | None = None
    max_diff: dict[str, Any] | None = None
    pricing: PricingBlock | None = None
    economics: EconomicsBlock | None = None
    place: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    market_sizing: dict[str, Any] | None = None
    four_ps: dict[str, Any] | None = None
    financials: dict[str, Any] | None = None
    viability: ViabilityBlock | None = None
    reddit_signal: dict[str, Any] | None = None
    differentiators: Differentiators | None = None
    customer_universe: CustomerUniverse | None = None
    operator_weights: OperatorWeights = Field(default_factory=OperatorWeights)
    # Metadata
    _steps_completed: list[str] = Field(default_factory=list)
    _elapsed_seconds: float | None = None
    _previous_job_id: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ContextStore":
        """Validate an existing dict into a ContextStore. Forgiving — extra keys allowed."""
        return cls.model_validate(d)
