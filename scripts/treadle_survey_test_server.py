"""Serve the real customer survey on throwaway storage with no external calls.

The model and research boundaries are deterministic test doubles. The business
object builder, PREP, bounded scheduler, customer adapter, persistence, delivery,
and survey are application code. This is a test harness only.
"""
from __future__ import annotations

import argparse
from functools import partial
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def offline_business_model(*, system, user):
    """Expand real survey statements through the actual recursive builder."""
    from treadle.execution_limits import charge_model_call

    charge_model_call()
    payload = json.loads(user)
    if "assessment_ids" in payload:
        return {"assessments": [
            {"id": item, "status": "supported", "reason": "Present in the test survey."}
            for item in payload["assessment_ids"]
        ]}
    source = next(item["id"] for item in payload["statements"]
                  if "at /description:" in item["text"])
    patch_value = {
        "base_revision": payload["base_revision"], "target_id": payload["target_id"],
        "nodes": [], "facts": [], "relationships": [], "gaps": [], "expand_node_ids": [],
    }
    if payload["target_id"] == "company":
        patch_value["nodes"] = [{
            "id": "delivery", "kind": "group", "label": "Learning delivery",
            "parent_id": "company", "statement_ids": [],
        }]
        patch_value["expand_node_ids"] = ["delivery"]
    elif payload["target_id"] == "delivery":
        patch_value["nodes"] = [
            {"id": key, "kind": kind, "label": label,
             "parent_id": "delivery", "statement_ids": [source]}
            for key, kind, label in (
                ("toronto", "capability", "Toronto tutoring centre"),
                ("vancouver", "capability", "Vancouver tutoring centre"),
                ("online", "capability", "Online learning across Canada"),
                ("tutoring", "revenue_stream", "Tutoring sessions"),
                ("subscriptions", "revenue_stream", "Self-service subscription"),
                ("licenses", "revenue_stream", "School curriculum licenses"),
            )
        ]
    return patch_value


def offline_prep_model(*, system, user):
    from packs.market_research_next.prep import REPORT_SECTIONS, VENTURE_FIELDS
    from treadle.execution_limits import charge_model_call

    charge_model_call()
    assert "ACCEPTED BUSINESS STRUCTURE" in user
    return {
        "venture": {field: None for field in VENTURE_FIELDS},
        "research_spec": {
            name: {"scope": "Preserve hybrid delivery, locations, and revenue streams",
                   "questions": ["Which input gaps must be researched for " + name + "?"],
                   "assumptions_to_test": []}
            for name in REPORT_SECTIONS
        },
    }


def offline_research(name, context, *, issues=()):
    from treadle.execution_limits import charge_model_call

    charge_model_call()
    prep = context["prep"]
    if (name == "market_size" and "E2E_PARTIAL_REPORT" in
            prep["venture"]["customer_inputs"]["description"]):
        raise ValueError("The deterministic market-size boundary failed")
    business = prep["business_context"]
    assert prep["research_spec"][name]["input_readiness"] == business["readiness"]["sections"][name]
    return {
        "title": name, "narrative": "Offline connected analysis for " + name,
        "findings": ["The real business object and prepared specification reached this section."],
        "assumptions": [], "uncertainties": business["limitations"],
        "metrics": {"business_nodes": len(business["object"]["nodes"]),
                    "correction": bool(issues)},
    }


def offline_review(result, verdicts):
    from treadle.execution_limits import charge_model_call

    charge_model_call()
    return ()


def _offline_connect(original):
    def connect(sock, address):
        if sock.family == socket.AF_UNIX:
            return original(sock, address)
        host = address[0] if isinstance(address, tuple) else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise OSError("External network is disabled in the survey test server")
        return original(sock, address)

    return connect


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="castor-survey-e2e-") as temporary:
        os.chdir(temporary)
        for name in tuple(os.environ):
            if name.endswith(("_API_KEY", "_SECRET", "_TOKEN")):
                os.environ.pop(name, None)
        os.environ.update({
            "JOBS_DB_PATH": str(Path(temporary) / "jobs.sqlite"),
            "HTTP_CACHE_PATH": str(Path(temporary) / "http.sqlite"),
            "CASTOR_GEO_CACHE_DIR": str(Path(temporary) / "geocache"),
            "SESSION_SECRET": secrets.token_hex(32),
            "CASTOR_ENV": "development",
            "CASTOR_REQUIRE_LOGIN": "0",
            "CASTOR_PAYWALL_PREVIEW": "0",
            "CASTOR_PAYWALL_OFF": "1",
            "CASTOR_DAILY_RUNS": "50",
            "CASTOR_STUB_REPORT": "",
            "LLM_ALLOW_PAID": "0",
            "STRIPE_SECRET_KEY": "",
            "STRIPE_WEBHOOK_SECRET": "",
            "STRIPE_PRICE_REPORT": "",
            "RESEND_API_KEY": "",
        })
        with patch("dotenv.load_dotenv", return_value=False), \
                patch("dotenv.main.load_dotenv", return_value=False), \
                patch("socket.socket.connect", _offline_connect(socket.socket.connect)):
            import api
            import intake
            import plan
            import uvicorn
            from packs.market_research_next import customer_pipeline
            from treadle.execution_limits import ExecutionLimits

            def fake_extract(**_kwargs):
                return {
                    "extracted": {
                        "product": "Hybrid tutoring centres and an online learning platform.",
                        "target_customer": "Families and independent schools.",
                    },
                    "next_action": "ask",
                    "next_question": "Tell us more about your business.",
                }

            def reject_legacy_report(*args, **kwargs):
                raise AssertionError("A new survey report must use the Treadle pipeline")

            with patch.object(intake, "call_json", fake_extract), \
                    patch.object(plan, "run_plan", reject_legacy_report), \
                    patch.object(customer_pipeline, "validate_customer_pipeline_config"), \
                    patch.object(customer_pipeline, "run_customer_pipeline", partial(
                        customer_pipeline.run_customer_pipeline,
                        prep_model=offline_prep_model, business_model=offline_business_model,
                        research=offline_research, review=offline_review,
                        limits=ExecutionLimits(run_timeout_s=60, task_timeout_s=20,
                                               review_timeout_s=10),
                    )):
                print("Survey test server: real Treadle pipeline; isolated storage; model doubles; "
                      "external networking blocked.", flush=True)
                uvicorn.run(
                    api.app, host="127.0.0.1", port=args.port, log_level="warning"
                )


if __name__ == "__main__":
    main()
