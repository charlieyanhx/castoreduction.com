"""
gates.py — deterministic milestone gate runner.

Every detector here is a MACHINE-CHECKABLE invariant distilled from a confirmed audit finding
(docs/AUDIT_RESULTS.md M1-M15) or a milestone gate (docs/TESTING_MILESTONES.md). No LLM, no
network, no randomness: same corpus in → same verdict out, so milestones are claimable by a
program, not an opinion. The LLM audit panel (ring R4) remains only for what cannot be
deterministic (prose quality); everything below is ring R3.

Usage:
  python -m gates --corpus /tmp/audit/run1              # dir of <slug>.json (+ <slug>.html)
  python -m gates --db .jobs.sqlite --latest 16         # newest complete plan jobs (no HTML checks)
  python -m gates --corpus DIR --gate core --out docs/baselines/M3.json

Exit code: 0 if the selected gate passes, 1 otherwise (CI-able).
"""

# gates.py was 2,671 lines: 61 independent detectors, their private helpers and regexes,
# a registry and a CLI, in one file. The detectors share no state, so the split is by
# SUBJECT and the dependency closure was computed rather than guessed: only Finding,
# Invariant, not_applicable and _num are used by more than one group, and they are in
# gates/common.py. Everything else belongs to exactly one module.
#
# Every name this module exported still resolves as `gates.X`. ~20 test modules address
# detectors that way (gates.d33_..., gates.INVARIANTS, gates.run_gate), and a split that
# moved those addresses would be a rename wearing a refactor's clothes.
from __future__ import annotations

from gates.common import Finding, Invariant, not_applicable, _num
from gates.sizing import (  # noqa: F401
    _MIN_TAM_PER_COMPETITOR_USD, _SAM_FIG_RE, _SOM_SHARE_RE, _TAM_FIG_RE, _sam_figures, _tam_figures, d03_single_som, d04_funnel_order, d15_tam_coherent_across_sections, d20_sam_self_consistent, d23_at_som_matches_its_label, d27_som_share_claims_possible, d35_tam_method_divergence_disclosed, d38_sam_slice_authoritative, d40_hyperlocal_som_basis_honest, d49_trade_area_matches_its_radius, d50_no_publishable_sizing_without_numbers, d52_chosen_sizing_skill_actually_ran, d56_local_spend_is_grounded_or_says_it_is_not, d57_market_supports_its_competitors, d59_som_anchor_discloses_its_method, d60_area_average_is_labelled,
)
from gates.pricing import (  # noqa: F401
    _AVG_ORDER_RE, _GENERIC_UNIT_NOUNS, _PER_DAY, _PER_MONTH, _WTP_GAP_FOLD, _avg_order_figures, _canonical_arpu, _singular, _stated_volumes, _volume_claim_re, d08_profit_coherent, d10_wtp_band_sane, d13_benchmark_not_fabricated, d18_wtp_price_reconciled, d21_arpu_coherent_across_sections, d24_withheld_profit_not_fabricated, d26_pnl_cost_side_honest, d31_benchmark_prices_coherent, d32_wtp_aggregation_honest, d37_viability_anchored_to_real_margin, d39_price_reconcile_unit_honest, d41_no_empty_price_per_customer, d58_psm_tiers_disclose_their_own_range, d61_volume_targets_match_the_ladder,
)
from gates.competitors import (  # noqa: F401
    _DENSITY_CLAIM_RE, _DIFF_PRICE_LANG, _NUMBER_WORDS, _NUM_TOKEN, _competitor_count_claims, d07_geo_competitors, d16_density_matches_ranked, d19_no_off_category_direct_competitor, d22_viability_reasoning_density_coherent, d28_domain_identity_verified, d30_differentiators_evidence_backed, d33_competitor_counts_reconcile, d34_roster_excludes_references, d42_no_near_dupe_competitors, d44_vertical_anchors_match_tags, d46_ranked_score_is_pythons, d51_momentum_count_measured_on_the_shown_roster,
)
from gates.model import (  # noqa: F401
    MONTHLY_UNITS, PER_UNIT_KINDS, d05_unit_no_monthly, d06_html_no_saas_bleed, d17_per_unit_not_on_subscription_fallback,
)
from gates.provenance import (  # noqa: F401
    NON_US_MARKERS, _AGENCIES, _DISCLOSED, _PROVEN_ORIGINS, _origin_of, _shows_its_agency_operand, d11_currency_sources, d12_provenance, d25_provenance_chip_not_fabricated, d47_trace_belongs_to_one_run, d48_shipped_report_attributes_its_sections, d53_no_fabricated_agency_citation,
)
from gates.surface import (  # noqa: F401
    _LEDGER_KEY_ALIASES, _MIN_COVERAGE_PCT, _fig_patterns, _path_present, _withheld_figures_asserted_in_prose, d01_complete, d02_renders, d09_publishable_gated, d14_no_failed_sections, d29_withhold_propagates, d36_validation_warns_surfaced, d43_no_dead_in_page_anchors, d45_cannot_decode_notice_not_self_refuting, d54_produced_output_reaches_the_report, d55_report_is_complete_enough_to_have_been_checked,
)
# The TABLE (domain data) and the ENGINE (frame) are separate modules now; both are
# re-exported here so `gates.INVARIANTS` and `gates.run_gate` stay the addresses they were.
from gates.invariants import GATES, INVARIANTS  # noqa: F401
from gates.runner import load_corpus, main, run_gate  # noqa: F401

if __name__ == "__main__":
    import sys
    sys.exit(main())
