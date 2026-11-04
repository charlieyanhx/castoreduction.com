"""Run the bounded business-object prototype on local, withheld-check fixtures."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.business_object_trial.run import decode_response
from packs.market_research_next.business_object.builder import build_business_object
from packs.market_research_next.business_object.readiness import assess_readiness
from packs.market_research_next.business_object.schema import validate_document


def save_json(path: Path, value: object) -> None:
    """Keep requests, results, and failures inspectable without overwriting runs."""
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


class RecordedModel:
    """Provider adapter with explicit configuration, bounded output, and no retries."""

    def __init__(self, client, model: str, destination: Path):
        self.client = client
        self.model = model
        self.destination = destination
        self.calls: list[dict] = []

    def __call__(self, *, system: str, user: str) -> dict:
        directory = self.destination / f"call_{len(self.calls) + 1:02d}"
        directory.mkdir(exist_ok=False)
        (directory / "system.txt").write_text(system, encoding="utf-8")
        (directory / "user.txt").write_text(user, encoding="utf-8")
        entry = {"model": self.model, "status": "started", "automatic_retries": 0}
        self.calls.append(entry)
        started = time.monotonic()
        try:
            response = self.client.messages.create(
                model=self.model, max_tokens=12000, system=system,
                messages=[{"role": "user", "content": user}],
            )
            entry["usage"] = {"input_tokens": response.usage.input_tokens,
                              "output_tokens": response.usage.output_tokens}
            entry["stop_reason"] = response.stop_reason
            raw = "".join(block.text for block in response.content if block.type == "text")
            (directory / "response.txt").write_text(raw, encoding="utf-8")
            if response.stop_reason != "end_turn":
                raise ValueError("Model output did not finish")
            document, entry["format_normalization"] = decode_response(raw)
            if not isinstance(document, dict):
                raise ValueError("Model output must be an object")
            entry["status"] = "parsed"
            return document
        except Exception as exc:
            entry["status"] = "failed"
            entry["error_type"] = type(exc).__name__
            # Provider exceptions may include request material; never serialize them.
            if isinstance(exc, ValueError):
                entry["validation_error"] = str(exc)[:500]
            raise
        finally:
            entry["elapsed_seconds"] = round(time.monotonic() - started, 2)
            save_json(directory / "run.json", entry)


def run_case(path: Path, output: Path, model: str, api_key: str,
             alias: str, max_calls: int, max_depth: int) -> dict:
    """Expose only anonymous supplied statements to the builder and its reviewer."""
    import anthropic

    fixture = json.loads(path.read_text(encoding="utf-8"))
    statements = [{"id": item["id"], "text": item["text"]}
                  for item in fixture["input_statements"]]
    destination = output / path.stem
    destination.mkdir(parents=True, exist_ok=False)
    save_json(destination / "input.json", {"company_alias": alias,
                                           "input_statements": statements})
    client = anthropic.Anthropic(api_key=api_key, timeout=180, max_retries=0)
    adapter = RecordedModel(client, model, destination)
    metadata = {"company": fixture["company"], "model": model,
                "fixture": str(path.resolve()), "max_calls": max_calls,
                "max_depth": max_depth, "status": "failed"}
    started = time.monotonic()
    try:
        artifact = build_business_object(statements, model=adapter,
                                         company_alias=alias, max_calls=max_calls,
                                         max_depth=max_depth)
        readiness = assess_readiness(artifact)
        save_json(destination / "artifact.json", artifact)
        save_json(destination / "candidate.json", artifact["candidate"])
        save_json(destination / "accepted.json", artifact["accepted"])
        save_json(destination / "readiness.json", readiness)
        metadata.update(
            status="completed", candidate_structure=validate_document(artifact["candidate"], statements),
            accepted_structure=validate_document(artifact["accepted"], statements),
            support_review=artifact["support_review"]["status"],
            expansion_complete=artifact["expansion_complete"],
            stop_reason=artifact["stop_reason"], pending=artifact["pending"],
            quarantined_count=len(artifact["quarantined"]),
        )
    except Exception as exc:
        metadata["error_type"] = type(exc).__name__
        if isinstance(exc, ValueError):
            metadata["validation_error"] = str(exc)[:500]
    finally:
        client.close()
    metadata["calls"] = adapter.calls
    metadata["usage"] = {
        key: sum(call.get("usage", {}).get(key, 0) for call in adapter.calls)
        for key in ("input_tokens", "output_tokens")
    }
    metadata["elapsed_seconds"] = round(time.monotonic() - started, 2)
    save_json(destination / "run.json", metadata)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=6)
    parser.add_argument("--max-depth", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= len(args.inputs) <= 4:
        parser.error("This small trial is limited to four cases per invocation")
    if not 2 <= args.max_calls <= 10 or not 1 <= args.max_depth <= 12:
        parser.error("Calls must be 2–10 and depth 1–12")
    if len({path.stem for path in args.inputs}) != len(args.inputs):
        parser.error("Input filenames must have distinct stems")
    if any((args.out / path.stem).exists() for path in args.inputs):
        parser.error("Use a new output directory to preserve previous runs")
    from dotenv import dotenv_values

    settings = {**dotenv_values(ROOT / ".env"), **os.environ}
    allowed = str(settings.get("LLM_ALLOW_PAID", "")).lower() in ("1", "true", "yes")
    if not allowed and settings.get("LLM_BACKEND") != "anthropic":
        parser.error("The existing paid-backend opt-in is required")
    key = settings.get("ANTHROPIC_API_KEY")
    if not key:
        parser.error("Configured Anthropic access is required")
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run_case, path, args.out, args.model, key,
                               f"Company {chr(65 + index)}", args.max_calls, args.max_depth)
                   for index, path in enumerate(args.inputs)]
        results = [future.result() for future in futures]
    save_json(args.out / "summary.json", results)
    print(json.dumps(results, indent=2))
    return 0 if all(item["status"] == "completed" and item["support_review"] == "complete"
                    and item["expansion_complete"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
