"""gates/invariants.py — the table: which detector, which failure it catches, what it costs.

THIS IS DOMAIN DATA, not engine. Each row names a market-research detector, the historical
failure it exists to catch, and whether its verdict blocks a gate or is merely reported.
It lives beside the detectors rather than inside runner.py so that the sweep engine --
which is true of any report a harness could produce -- imports nothing about markets.

THE ORDER IS HISTORICAL AND LOAD-BEARING. It is not sorted (D61 precedes D60), it decides
the column order of every scorecard, and the baselines under docs/baselines were written
against it. Append; do not tidy.
"""
from __future__ import annotations

from gates.common import Invariant
from gates.competitors import (
    d07_geo_competitors,
    d16_density_matches_ranked,
    d19_no_off_category_direct_competitor,
    d22_viability_reasoning_density_coherent,
    d28_domain_identity_verified,
    d30_differentiators_evidence_backed,
    d33_competitor_counts_reconcile,
    d34_roster_excludes_references,
    d42_no_near_dupe_competitors,
    d44_vertical_anchors_match_tags,
    d46_ranked_score_is_pythons,
    d51_momentum_count_measured_on_the_shown_roster,
)
from gates.model import (
    d05_unit_no_monthly,
    d06_html_no_saas_bleed,
    d17_per_unit_not_on_subscription_fallback,
)
from gates.pricing import (
    d08_profit_coherent,
    d10_wtp_band_sane,
    d13_benchmark_not_fabricated,
    d18_wtp_price_reconciled,
    d21_arpu_coherent_across_sections,
    d24_withheld_profit_not_fabricated,
    d26_pnl_cost_side_honest,
    d31_benchmark_prices_coherent,
    d32_wtp_aggregation_honest,
    d37_viability_anchored_to_real_margin,
    d39_price_reconcile_unit_honest,
    d41_no_empty_price_per_customer,
    d58_psm_tiers_disclose_their_own_range,
    d61_volume_targets_match_the_ladder,
)
from gates.provenance import (
    d11_currency_sources,
    d12_provenance,
    d25_provenance_chip_not_fabricated,
    d47_trace_belongs_to_one_run,
    d48_shipped_report_attributes_its_sections,
    d53_no_fabricated_agency_citation,
)
from gates.sizing import (
    d03_single_som,
    d04_funnel_order,
    d15_tam_coherent_across_sections,
    d20_sam_self_consistent,
    d23_at_som_matches_its_label,
    d27_som_share_claims_possible,
    d35_tam_method_divergence_disclosed,
    d38_sam_slice_authoritative,
    d40_hyperlocal_som_basis_honest,
    d49_trade_area_matches_its_radius,
    d50_no_publishable_sizing_without_numbers,
    d52_chosen_sizing_skill_actually_ran,
    d56_local_spend_is_grounded_or_says_it_is_not,
    d57_market_supports_its_competitors,
    d59_som_anchor_discloses_its_method,
    d60_area_average_is_labelled,
)
from gates.surface import (
    d01_complete,
    d02_renders,
    d09_publishable_gated,
    d14_no_failed_sections,
    d29_withhold_propagates,
    d36_validation_warns_surfaced,
    d43_no_dead_in_page_anchors,
    d45_cannot_decode_notice_not_self_refuting,
    d54_produced_output_reaches_the_report,
    d55_report_is_complete_enough_to_have_been_checked,
)
from gates.synthesis import (
    d62_synthesis_numbers_are_in_the_evidence_it_cites,
    d63_synthesis_citations_resolve,
)
INVARIANTS: list[Invariant] = [
    Invariant("D01", "pipeline completes (>=12 steps)", "M2/M11 blank-or-degraded run", "fail", d01_complete),
    Invariant("D02", "report renders (>1KB HTML)", "M2 0-byte deliverable", "fail", d02_renders),
    Invariant("D03", "single canonical SOM", "M3 dual SOM", "fail", d03_single_som),
    Invariant("D04", "funnel ordered SOM<=SAM<=TAM", "sizing incoherence", "fail", d04_funnel_order),
    Invariant("D05", "per-unit model has per-unit units", "M4 subscription bleed (data)", "fail", d05_unit_no_monthly),
    Invariant("D06", "rendered report free of SaaS phrasing", "M5 subscription bleed (render)", "fail", d06_html_no_saas_bleed),
    Invariant("D07", "geo competitors for physical-local", "M1 wrong competitor set", "fail", d07_geo_competitors),
    Invariant("D08", "profitability claims coherent", "profitable-at-SOM contradiction", "fail", d08_profit_coherent),
    Invariant("D09", "failed validation withholds numbers", "unpublishable TAM reused", "fail", d09_publishable_gated),
    Invariant("D10", "WTP band sane", "degenerate/fabricated band", "fail", d10_wtp_band_sane),
    Invariant("D11", "non-US venture avoids US-only sources", "M6 currency/provenance mismatch", "warn", d11_currency_sources),
    Invariant("D12", "provenance trace present", "debuggability invariant", "warn", d12_provenance),
    Invariant("D13", "no scraped benchmark on geo-sourced set", "fabricated price benchmark", "fail", d13_benchmark_not_fabricated),
    Invariant("D14", "no failed 4Ps sections", "silent section failure", "warn", d14_no_failed_sections),
    Invariant("D15", "TAM coherent across sections", "same number, two values (audit C1)", "fail", d15_tam_coherent_across_sections),
    Invariant("D16", "competitor_density matches ranked set", "wrong density input to viability", "fail", d16_density_matches_ranked),
    Invariant("D17", "per-unit venture never on subscription fallback", "hybrid device price mis-extracted", "fail", d17_per_unit_not_on_subscription_fallback),
    Invariant("D18", "WTP reconciled with recommended price", "83x gap rendered uncommented", "fail", d18_wtp_price_reconciled),
    Invariant("D19", "no off-category 'direct' competitor in top 3", "wrong-industry rival ranked #1", "fail", d19_no_off_category_direct_competitor),
    Invariant("D20", "SAM narrative coherent with headline", "same SAM, two values", "fail", d20_sam_self_consistent),
    Invariant("D21", "ARPU coherent across 4Ps sections", "invented order/job/booking value", "fail", d21_arpu_coherent_across_sections),
    Invariant("D22", "viability reasoning coherent with real competitor density", "invented competitor-count claim (audit item 3)", "fail", d22_viability_reasoning_density_coherent),
    Invariant("D23", "at-SOM claim matches its own label", "R12: aggressive ceiling sold as the obtainable volume", "fail", d23_at_som_matches_its_label),
    Invariant("D24", "withheld profit never rendered as a number", "R12: a suppressed verdict published as a fabricated $0", "fail", d24_withheld_profit_not_fabricated),
    Invariant("D25", "provenance chip never claims sourcing it lacks", "R4 rank 1: model-asserted citations sold as fetched data", "fail", d25_provenance_chip_not_fabricated),
    Invariant("D26", "P&L cost side honest (withhold holds; CAC-feasible break-even; margin bound)", "R4 rank 2: single-site scalar + ignored CAC", "fail", d26_pnl_cost_side_honest),
    Invariant("D27", "no impossible share-of-SOM claim; scenario basis rendered", "R4 rank 3: ceilings ARE the band, ratio sold as capture", "fail", d27_som_share_claims_possible),
    Invariant("D28", "competitor domains are identities, not lookalikes", "R4 rank 4: pattern-probed squatter poisoned prices", "fail", d28_domain_identity_verified),
    Invariant("D29", "withheld sizing binds derived surfaces (scenarios, viability)", "R4 rank 5: unflagged revenue table below a do-not-rely banner", "fail", d29_withhold_propagates),
    Invariant("D30", "differentiators evidence-backed, distinct, honestly rated", "R4 rank 6: fabricated before evidence, strength pinned high", "fail", d30_differentiators_evidence_backed),
    Invariant("D31", "benchmark prices coherent (same-unit, >=3 domains)", "R4 rank 7: mixed-SKU median fabricated as a category price", "fail", d31_benchmark_prices_coherent),
    Invariant("D32", "WTP aggregation honest (real median, no $0 payer, n>=3 band)", "R4 rank 8: upper-middle order statistic, $0 payer, 2-answer median", "fail", d32_wtp_aggregation_honest),
    Invariant("D33", "competitor counts reconcile (density==roster==map input)", "R4 rank 9: 4 competitor counts on 4 surfaces, none canonical", "fail", d33_competitor_counts_reconcile),
    Invariant("D34", "roster is only real competitors (references partitioned out)", "R4 rank 10: self-flagged junk relabeled not excluded", "fail", d34_roster_excludes_references),
    Invariant("D35", "TAM method divergence disclosed (no fake 0% spread)", "R4 rank 12: single-origin collapse hides 8-28x method spread", "fail", d35_tam_method_divergence_disclosed),
    Invariant("D36", "validation warns surfaced (not under a green chip)", "R4 rank 13: advisory warns computed, rendered nowhere", "fail", d36_validation_warns_surfaced),
    Invariant("D37", "viability anchored to the real per-unit margin", "R4 rank 14: unit-econ anchor gated on transactional only", "fail", d37_viability_anchored_to_real_margin),
    Invariant("D38", "SAM serviceable slice is authoritative (sam/tam), rendered", "R4 rank 15: slice back-formed, key_assumption contradicts it", "fail", d38_sam_slice_authoritative),
    Invariant("D39", "price reconciliation priced in the venture's unit", "R4 rank 16: hardcoded /mo on a per-unit venture", "fail", d39_price_reconcile_unit_honest),
    Invariant("D40", "hyperlocal SOM basis honest (capacity vs unsourced estimate)", "R4 rank 18: 'capacity-based' claimed over an LLM guess", "fail", d40_hyperlocal_som_basis_honest),
    Invariant("D41", "no empty per-customer price (non-priced fall-through)", "R4 rank 20: ad_supported hits the subscription else-branch", "fail", d41_no_empty_price_per_customer),
    Invariant("D42", "no near-duplicate competitors (geo set collapsed too)", "R4 rank 22: near-dupe collapse skipped the geo set", "fail", d42_no_near_dupe_competitors),
    Invariant("D43", "no dead in-page nav anchors", "R4 rank 24: nav linked to conditionally-rendered sections", "fail", d43_no_dead_in_page_anchors),
    Invariant("D44", "vertical macro anchors match the venture's tags", "R4 rank 24: b2b substring pulled saas anchors onto b2b hardware", "fail", d44_vertical_anchors_match_tags),
    Invariant("D45", "cannot-decode notice not self-refuting", "R4 rank 24: 'N signals found ... no review surface'", "fail", d45_cannot_decode_notice_not_self_refuting),
    Invariant("D46", "ranked score is Python's, not the model's", "audit critical #1: opportunity_score == enriched _score", "fail", d46_ranked_score_is_pythons),
    Invariant("D47", "trace belongs to one run", "audit criticals #2/#3: no duplicate/foreign step events, one run_id", "fail", d47_trace_belongs_to_one_run),
    Invariant("D48", "shipped report attributes its sections", "provenance a buyer cannot see is not provenance", "fail", d48_shipped_report_attributes_its_sections),
    Invariant("D49", "trade area matches its radius", "audit high #4: no county-scale household count as a trade area", "fail", d49_trade_area_matches_its_radius),
    Invariant("D50", "no publishable sizing without numbers", "an empty sizing passed the gate vacuously and shipped", "fail", d50_no_publishable_sizing_without_numbers),
    Invariant("D51", "momentum count measured on the shown roster", "audit critical: 6/6 geo reports cited an active count from the discarded set", "fail", d51_momentum_count_measured_on_the_shown_roster),
    Invariant("D52", "chosen sizing skill actually ran", "harness item 1: the classifier named size_hyperlocal and the LLM sized it instead", "fail", d52_chosen_sizing_skill_actually_ran),
    Invariant("D53", "no fabricated agency citation", "harness item 2: 14/15 agency-citing figures had no origin proving a call", "fail", d53_no_fabricated_agency_citation),
    Invariant("D54", "produced output reaches the report", "harness item 7: 3 sections the ledger recorded as produced vanished silently", "fail", d54_produced_output_reaches_the_report),
    Invariant("D55", "complete enough to have been checked", "the scorecard rewarded emptiness: an empty report scored 23 pass / 0 fail", "fail", d55_report_is_complete_enough_to_have_been_checked),
    Invariant("D56", "local spend is grounded or says it is not", "a trade-area TAM priced every neighbourhood at the $3,945 national average while local income sat fetched and unread", "fail", d56_local_spend_is_grounded_or_says_it_is_not),
    Invariant("D57", "market supports its own competitors", "run9 published $122K of market per existing cafe — 102 real venues were surviving on a TAM the report said could not sustain one", "fail", d57_market_supports_its_competitors),
    Invariant("D58", "PSM tiers disclose when they fall outside their own acceptable range", "run12-15 recommended $3.85 and $9.50 against a $4.25-$6.75 range, flat and unqualified", "fail", d58_psm_tiers_disclose_their_own_range),
    Invariant("D59", "SOM anchor discloses its method", "run14 $390K vs run15 $650K for the same venture — an unsourced single-unit revenue guess published as the headline with no alternative beside it", "fail", d59_som_anchor_discloses_its_method),
    Invariant("D61", "4Ps volume targets are ladder rungs, not inventions", "run17 recommended 250 drinks/day in Price and 150/day in Place and Promotion — 67% apart, both obeying a rule that only pinned a range", "fail", d61_volume_targets_match_the_ladder),
    Invariant("D60", "area-average SOM is labelled as one", "the sourced anchor is a mean across 525 county establishments; rendered under the old 'single-unit revenue' label it reads as this one store, now carrying a Census citation", "fail", d60_area_average_is_labelled),
    Invariant("D62", "synthesis numbers are in the evidence it cites", "Sonnet 4.5 on the diag01 fact layer invented a BEA 21% price-level figure and a $12,000 / $4,500 / $2,000 / $23,000 cost sketch; a whole-blob number match passed both; pooling only the values the report cites and matching each number at the scale its own suffix names fails three of the four cost figures, and the paragraph rule for small integers fails the 21%", "fail", d62_synthesis_numbers_are_in_the_evidence_it_cites),
    Invariant("D63", "synthesis citations resolve", "a [path] the reader cannot follow is a footnote that looks sourced and is not; advisory because the model may cite a dropped section by its name", "warn", d63_synthesis_citations_resolve),
]


# Named gates: which invariants must be 100% pass (severity 'fail' ones) for the claim.
GATES: dict[str, list[str]] = {
    "core": [i.id for i in INVARIANTS if i.severity == "fail"],
    "all": [i.id for i in INVARIANTS],
    "M1-fixes": ["D01", "D02", "D03", "D07"],       # the first root-fix wave
    "M4-models": ["D05", "D06", "D08", "D10", "D13"],  # business-model correctness wave
}
