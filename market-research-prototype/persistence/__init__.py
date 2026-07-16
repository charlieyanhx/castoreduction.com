"""persistence/ — durable run state (Wave 3).

  ledger.py     append-only RunLedger: the ordered record of what a run DID
  transcript.py per-run JSONL serialization of the ledger (replay → identical state)
  resume.py     resume(job_id): skip steps already recorded complete

provenance.py is a thin view/shim over ledger, so the existing Data-Provenance panel
and the D12 gate keep working unchanged.
"""
