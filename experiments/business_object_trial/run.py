"""Small live extraction trial; independent of the production report pipeline.

Only an alias and input statements are sent to the model. Source references and
expected checks stay local for evaluation. No tool use, automatic repair or fallback.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.business_object_trial.contract import PROMPT, validate_business_object


def decode_response(raw: str) -> tuple[dict, str]:
    """Allow one complete Markdown wrapper; never repair JSON or discard prose."""
    text = raw.strip()
    normalization = "none"
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
        normalization = "removed_complete_json_code_fence"
    return json.loads(text), normalization


def run_case(path: Path, output: Path, model: str, api_key: str, alias: str) -> dict:
    import anthropic

    fixture = json.loads(path.read_text())
    statements = [{"id": item["id"], "text": item["text"]}
                  for item in fixture["input_statements"]]
    payload = {"company_alias": alias, "input_statements": statements}
    request = json.dumps(payload, ensure_ascii=False)
    destination = output / path.stem
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "request.json").write_text(json.dumps(payload, indent=2))
    (destination / "prompt.txt").write_text(PROMPT)
    metadata = {"company": fixture["company"], "model": model,
                "input_sha256": hashlib.sha256(request.encode()).hexdigest(),
                "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
                "fixture": str(path.resolve()), "automatic_retries": 0}
    start = time.monotonic()
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=180, max_retries=0)
        response = client.messages.create(
            model=model, max_tokens=12000, system=PROMPT,
            messages=[{"role": "user", "content": request}],
        )
        metadata["usage"] = {"input_tokens": response.usage.input_tokens,
                             "output_tokens": response.usage.output_tokens}
        metadata["stop_reason"] = response.stop_reason
        raw = "".join(block.text for block in response.content if block.type == "text")
        (destination / "response.txt").write_text(raw)
        if response.stop_reason != "end_turn":
            raise ValueError("Model output did not finish")
        document, metadata["format_normalization"] = decode_response(raw)
        (destination / "object.json").write_text(json.dumps(document, indent=2))
        metadata["structure"] = validate_business_object(document, statements)
        metadata["structural_status"] = "passed"
    except Exception as exc:
        metadata["structural_status"] = "failed"
        metadata["error_type"] = type(exc).__name__
        if isinstance(exc, ValueError):
            metadata["validation_error"] = str(exc)[:500]
    metadata["elapsed_seconds"] = round(time.monotonic() - start, 2)
    (destination / "run.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if len(args.inputs) > 4:
        parser.error("This small trial is limited to four cases per invocation")
    if len({p.stem for p in args.inputs}) != len(args.inputs):
        parser.error("Input filenames must have distinct stems")
    from dotenv import dotenv_values

    settings = {**dotenv_values(ROOT / ".env"), **os.environ}
    allowed = str(settings.get("LLM_ALLOW_PAID", "")).lower() in ("1", "true", "yes")
    if not allowed and settings.get("LLM_BACKEND") != "anthropic":
        parser.error("The existing paid-backend opt-in is required")
    key = settings.get("ANTHROPIC_API_KEY")
    if not key:
        parser.error("Configured Anthropic access is required")
    if any((args.out / p.stem).exists() for p in args.inputs):
        parser.error("Use a new output directory to preserve previous runs")
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run_case, path, args.out, args.model, key,
                               f"Company {chr(65 + index)}")
                   for index, path in enumerate(args.inputs)]
        results = [future.result() for future in futures]
    (args.out / "summary.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    return 0 if all(result["structural_status"] == "passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
