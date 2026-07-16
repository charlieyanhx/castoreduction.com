"""orchestrator/ — the pipeline's control flow (Wave 3, item 5 onward).

  steps/  individual pipeline steps, extracted from run_plan one wave at a time.

run_plan remains the caller; steps move out only in the wave that already touches them
(the plan's no-big-bang rule), each behind a re-export shim so existing imports hold.
"""
