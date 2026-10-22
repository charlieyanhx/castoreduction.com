"""Bridge authoritative survey input to a reviewed, bounded business context.

The recursive builder's candidate and quarantine are deliberately absent from this
boundary. Consumers receive the accepted graph, source statements, and explicit
coverage limitations; a support review establishes input support, not outside truth.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Callable

from treadle.execution_limits import check_budget

from .builder import build_business_object
from .readiness import assess_readiness
from .schema import node_depths, normalize_statements, validate_document


SCHEMA_VERSION = "market_research.business_context/1"
_CONTEXT_FIELDS = {
    "schema_version", "object", "statements", "statement_paths", "readiness",
    "construction", "limitations",
}
_CONSTRUCTION_FIELDS = {
    "calls", "max_calls", "max_depth", "revision", "stop_reason",
    "expansion_complete", "pending", "support_review_status", "quarantined_count",
}
_STRUCTURED_INPUT_RULES = (
    "\nSupplied statements contain customer JSON fields with their original paths. "
    "Preserve nested business scopes, multiple locations, channels, and revenue "
    "streams. An explicit unknown is a constraint and a question, never permission "
    "to derive a stated or inferred value from another field. These statements "
    "already apply survey precedence; do not reconstruct superseded values."
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _pointer(parts: tuple[str | int, ...]) -> str:
    return "/" + "/".join(str(part).replace("~", "~0").replace("/", "~1")
                          for part in parts)


def _sources(brief: dict) -> tuple[list[dict], dict[str, str]]:
    # These imports are local so standalone PREP can in turn import this bridge.
    from ..prep import VENTURE_FIELDS, _ALIASES, _customer_inputs, _provided_attributes, _unknown

    original, _ = _customer_inputs(brief)
    supplied = _provided_attributes(original)
    aliases = {name for field in VENTURE_FIELDS for name in _ALIASES.get(field, (field,))}
    intake = original.get("intake", {})
    unanswered = set(intake.get("unanswered", []))
    entries: dict[str, str] = {}

    def add(parts: tuple[str | int, ...], value: Any, *, field: str | None = None,
            unknown: bool = False) -> None:
        path = _pointer(parts)
        label = f" for {field}" if field else ""
        entries[path] = (
            f"Customer explicitly marks{label} at {path} as unknown. "
            "Do not infer or invent a value."
            if unknown else f"Customer supplied JSON{label} at {path}: {_json(value)}"
        )

    # Known fields follow the same typed-slot/explicit-unknown precedence as PREP.
    # Never convert an unconfirmed or reconstructed intake extraction into a source.
    for field, record in supplied.items():
        if record["basis"] == "inferred":
            continue
        for source in record["source_fields"]:
            if source.startswith("intake.unknowns."):
                name = source.removeprefix("intake.unknowns.")
                parts = ("intake", "unknowns", intake["unknowns"].index(name))
            else:
                parts = tuple(source.split("."))
            add(parts, record["value"], field=field, unknown=record["basis"] == "unknown")

    # Resolve each additional field before admitting a statement. Keep structured
    # values whole, and apply the same precedence as known venture attributes:
    # authoritative explicit unknowns win, then slots, then facts, then direct input.
    authoritative_intake = (intake.get("confirmed") is not False
                            and intake.get("reconstructed_from_brief") is not True)
    layers = [((), original)]
    unknowns = set()
    if authoritative_intake:
        layers.extend([(("intake", "facts"), intake.get("facts", {})),
                       (("intake", "slots"), intake.get("slots", {}))])
        unknowns = set(intake.get("unknowns", [])) - unanswered
    keys = set().union(*(values for _, values in layers), unknowns) - aliases - {"intake"}
    for key in sorted(keys):
        held = None
        explicit_unknowns = []
        for prefix, values in layers:
            if key not in values:
                continue
            if prefix and key in unanswered:
                continue  # Skipped structured answers leave the opening source available.
            parts, value = (*prefix, key), values[key]
            if _unknown(value):
                explicit_unknowns.append(parts)
            else:
                held = parts, value
        if key in unknowns:
            explicit_unknowns.append(("intake", "unknowns", intake["unknowns"].index(key)))
        if explicit_unknowns:
            for parts in explicit_unknowns:
                add(parts, None, field=key, unknown=True)
        elif held is not None:
            add(*held)

    statements, paths = [], {}
    for path, text in sorted(entries.items()):
        identifier = "input_" + hashlib.sha256(path.encode("utf-8")).hexdigest()
        statements.append({"id": identifier, "text": text})
        paths[identifier] = path
    if not statements:
        raise ValueError("Business context requires authoritative customer input")
    return normalize_statements(statements), paths


def _readiness(document: dict, construction: dict) -> dict:
    return assess_readiness({
        "accepted": document,
        "support_review": {"status": construction["support_review_status"]},
        "expansion_complete": construction["expansion_complete"],
        "inferred_ids": [item["id"] for key in ("facts", "relationships")
                         for item in document[key] if item["basis"] == "inferred"],
    })


def _limitations(readiness: dict, construction: dict) -> list[str]:
    result = list(readiness["limitations"])
    if not construction["expansion_complete"]:
        boundary = "call budget" if construction["stop_reason"] == "call_budget" else "depth limit"
        result.append(f"Business-object expansion reached its {boundary}; supplied details "
                      "may remain uncaptured. Treat the accepted object as partial input.")
    count = construction["quarantined_count"]
    if count:
        result.append(f"Support review excluded {count} unsupported, uncertain, or dependent "
                      "records. Their claims must not be used as research premises.")
    return result


def build_context(brief: dict, *, model: Callable[..., dict], max_calls: int = 6,
                  max_depth: int = 6) -> dict:
    """Mine authoritative inputs, then expose only reviewed, structurally valid data.

    A call/depth boundary may return partial input after a successful review.
    Provider failures, invalid patches, or failed support review fail the PREP task.
    """
    statements, paths = _sources(brief)

    def structured_model(*, system: str, user: str) -> dict:
        return model(system=system + _STRUCTURED_INPUT_RULES, user=user)

    check_budget()
    artifact = build_business_object(statements, model=structured_model,
                                     max_calls=max_calls, max_depth=max_depth)
    # The builder records provider failures; a swallowed budget failure must still
    # reach the scheduler's sticky budget gate instead of looking like valid input.
    check_budget()
    if artifact["support_review"]["status"] != "complete":
        raise ValueError("Business-object support review did not complete")
    if artifact["stop_reason"] not in {"complete", "call_budget", "depth_limit"}:
        raise ValueError("Business-object extraction failed: " + artifact["stop_reason"])
    document = deepcopy(artifact["accepted"])
    accepted_ids = {node["id"] for node in document["nodes"]}
    construction = {
        "calls": artifact["calls"], "max_calls": max_calls, "max_depth": max_depth,
        "revision": artifact["revision"], "stop_reason": artifact["stop_reason"],
        "expansion_complete": artifact["expansion_complete"],
        "pending": [identifier for identifier in artifact["pending"] if identifier in accepted_ids],
        "support_review_status": "complete", "quarantined_count": len(artifact["quarantined"]),
    }
    readiness = _readiness(document, construction)
    context = {
        "schema_version": SCHEMA_VERSION, "object": document,
        "statements": statements, "statement_paths": paths, "readiness": readiness,
        "construction": construction, "limitations": _limitations(readiness, construction),
    }
    validate_context(context, brief)
    return context


def validate_context(context: dict, brief: dict) -> None:
    """Reject malformed context and source/coverage drift at the pipeline boundary."""
    if not isinstance(context, dict) or set(context) != _CONTEXT_FIELDS:
        raise ValueError("Business context has unexpected fields")
    if context["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Business context schema version is unsupported")
    # Reject Python-only/nonfinite values before comparing canonical source records.
    _json(context)
    statements, paths = _sources(brief)
    if context["statements"] != statements or context["statement_paths"] != paths:
        raise ValueError("Business context sources do not match authoritative customer input")
    document = context["object"]
    validate_document(document, statements)
    construction = context["construction"]
    if not isinstance(construction, dict) or set(construction) != _CONSTRUCTION_FIELDS:
        raise ValueError("Business context construction metadata is invalid")
    for key in ("calls", "max_calls", "max_depth", "revision", "quarantined_count"):
        if type(construction[key]) is not int or construction[key] < 0:
            raise ValueError("Business context construction counters must be nonnegative integers")
    if not 2 <= construction["calls"] <= construction["max_calls"] <= 10:
        raise ValueError("Business context construction call limits are invalid")
    if not 1 <= construction["max_depth"] <= 12:
        raise ValueError("Business context construction depth limit is invalid")
    if construction["revision"] > construction["calls"] - 1:
        raise ValueError("Business context revision exceeds its expansion calls")
    depths = node_depths(document)
    if max(depths.values()) > construction["max_depth"]:
        raise ValueError("Business context exceeds its configured depth")
    if construction["support_review_status"] != "complete":
        raise ValueError("Business context requires a completed support review")
    stop = construction["stop_reason"]
    if stop not in {"complete", "call_budget", "depth_limit"}:
        raise ValueError("Business context contains a failed extraction")
    if type(construction["expansion_complete"]) is not bool or (
        construction["expansion_complete"] != (stop == "complete")
    ):
        raise ValueError("Business context expansion status is inconsistent")
    pending = construction["pending"]
    if not isinstance(pending, list) or any(not isinstance(item, str) for item in pending):
        raise ValueError("Business context pending targets must be node IDs")
    if len(set(pending)) != len(pending) or not set(pending) <= set(depths):
        raise ValueError("Business context pending targets are invalid")
    if stop == "complete" and pending:
        raise ValueError("Complete business context cannot have pending targets")
    if stop == "call_budget" and construction["calls"] != construction["max_calls"]:
        raise ValueError("Business context stopped before its declared call budget")
    readiness = _readiness(document, construction)
    if context["readiness"] != readiness:
        raise ValueError("Business context readiness does not match its accepted object")
    if context["limitations"] != _limitations(readiness, construction):
        raise ValueError("Business context limitations do not match construction status")
