"""Code-owned boundary checks for the selectable report workflow.

These checks establish usable, finite structured input/output. They do not establish
the truth of prose or turn missing market evidence into a completed calculation.
The workflow runs them inside the same isolated deadline as the producer.
"""
from __future__ import annotations

from functools import partial
import hashlib
import json
import math

from treadle.execution_limits import TaskContract

from .analysis import registry as analysis_registry
from .prep import REPORT_SECTIONS, RECURSIVE_SCHEMA_VERSION, SCHEMA_VERSION, VENTURE_FIELDS


def _finite_json(value, path="value") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} must have string keys")
            _finite_json(child, f"{path}.{key}")
    elif isinstance(value, list):
        for child in value:
            _finite_json(child, path)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite values")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise ValueError(f"{path} must contain JSON values")


def _text(value, field) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} requires nonempty text")


def _texts(value, field, *, nonempty=False) -> None:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{field} requires a list")
    for item in value:
        _text(item, field)


def validate_prep_input(context: dict) -> None:
    _finite_json(context)
    if not isinstance(context, dict):
        raise ValueError("PREP requires an input object")
    brief = context.get("brief")
    if not isinstance(brief, dict):
        raise ValueError("PREP requires a brief object")
    _text(brief.get("description"), "brief.description")


def validate_prep_output(value: dict) -> None:
    _finite_json(value)
    if not isinstance(value, dict) or value.get("schema_version") not in (
            SCHEMA_VERSION, RECURSIVE_SCHEMA_VERSION):
        raise ValueError("PREP must return the declared versioned artifact")
    if value.get("source_tracing_enabled") is not False:
        raise ValueError("Source tracing must remain disabled for this report path")
    venture = value.get("venture")
    if not isinstance(venture, dict) or not isinstance(venture.get("customer_inputs"), dict):
        raise ValueError("PREP requires preserved customer inputs")
    attributes = venture.get("attributes")
    if not isinstance(attributes, dict) or set(attributes) != set(VENTURE_FIELDS):
        raise ValueError("PREP requires every declared venture attribute")
    for name, record in attributes.items():
        if not isinstance(record, dict) or set(record) != {"value", "basis", "source_fields"}:
            raise ValueError(f"PREP attribute {name} has an invalid shape")
        if record["basis"] not in ("customer_stated", "inferred", "unknown"):
            raise ValueError(f"PREP attribute {name} has an invalid basis")
        if record["basis"] == "unknown" and record["value"] is not None:
            raise ValueError(f"PREP unknown attribute {name} must have no asserted value")
        _texts(record["source_fields"], f"PREP attribute {name} source fields")
        if record["basis"] == "customer_stated" and not record["source_fields"]:
            raise ValueError(f"PREP customer-stated attribute {name} requires its input field")
    spec = value.get("research_spec")
    if not isinstance(spec, dict) or set(spec) != set(REPORT_SECTIONS):
        raise ValueError("PREP requires the fixed report section specifications")
    for name, section in spec.items():
        if not isinstance(section, dict):
            raise ValueError(f"PREP {name} specification must be an object")
        _text(section.get("scope"), f"PREP {name} scope")
        _texts(section.get("questions"), f"PREP {name} questions", nonempty=True)
        _texts(section.get("assumptions_to_test"), f"PREP {name} assumptions")
        if section.get("required_deliverables") != REPORT_SECTIONS[name]["required_deliverables"]:
            raise ValueError(f"PREP {name} must preserve the code-owned deliverables")
    _text(value.get("input_fingerprint"), "PREP input fingerprint")
    # Check the fingerprint against the preserved original input, not model prose.
    serialized = json.dumps(venture["customer_inputs"], sort_keys=True,
                            ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if value["input_fingerprint"] != hashlib.sha256(serialized.encode()).hexdigest():
        raise ValueError("PREP input fingerprint does not match its customer inputs")
    if value["schema_version"] == RECURSIVE_SCHEMA_VERSION:
        from .business_object.integration import validate_context

        context = value.get("business_context")
        validate_context(context, venture["customer_inputs"])
        for name, section in spec.items():
            if section.get("input_readiness") != context["readiness"]["sections"][name]:
                raise ValueError(f"PREP {name} must preserve code-owned input readiness")
    elif "business_context" in value or any("input_readiness" in section for section in spec.values()):
        raise ValueError("Recursive business context requires PREP version 2")


def validate_recursive_prep_output(value: dict) -> None:
    validate_prep_output(value)
    if value["schema_version"] != RECURSIVE_SCHEMA_VERSION:
        raise ValueError("This report path requires recursive business-object PREP")


def validate_section_output(value: dict) -> None:
    _finite_json(value)
    if not isinstance(value, dict) or any(key in value for key in ("error", "_error", "_parse_error")):
        raise ValueError("Research must return a section object without error markers")
    for field in ("title", "narrative"):
        _text(value.get(field), field)
    for field in ("findings", "assumptions", "uncertainties"):
        _texts(value.get(field), field)
    if "metrics" in value and not isinstance(value["metrics"], dict):
        raise ValueError("metrics must be a structured object")
    if "research_status" in value and value["research_status"] not in {
            "derived", "completed", "partial", "simulated"}:
        raise ValueError("Unknown research status")
    if value.get("research_status") == "partial" and not value["uncertainties"]:
        raise ValueError("Partial research must disclose its uncertainty")


def validate_analysis_output(section_key: str, value: dict) -> None:
    """The shared envelope, then the section's own typed payload."""
    validate_section_output(value)
    if not isinstance(value.get("data"), dict):
        raise ValueError("An analysis section must return a data object")
    analysis_registry.module(section_key).validate_data(value["data"])


def validate_section_input(section_key: str, context: dict, *, require_business=False) -> None:
    _finite_json(context)
    if not isinstance(context, dict):
        raise ValueError("Research requires an input object")
    if section_key not in REPORT_SECTIONS and section_key not in analysis_registry.ANALYSIS:
        raise ValueError("Unknown report section")
    validator = validate_recursive_prep_output if require_business else validate_prep_output
    validator(context.get("prep"))
    for name, value in context.items():
        if name == "prep":
            continue
        if name in REPORT_SECTIONS:
            validate_section_output(value)
        elif name in analysis_registry.ANALYSIS:
            validate_analysis_output(name, value)
        else:
            raise ValueError("Research received an undeclared upstream section")


def task_contracts(analysis: tuple[str, ...] = ()) -> dict[str, TaskContract]:
    """Return fresh policy objects; models cannot alter validation or dependencies."""
    return {
        "prep": TaskContract(validate_prep_input, validate_recursive_prep_output),
        **{name: TaskContract(partial(validate_section_input, name, require_business=True),
                              validate_section_output)
           for name in REPORT_SECTIONS},
        **{name: TaskContract(partial(validate_section_input, name, require_business=True),
                              partial(validate_analysis_output, name))
           for name in analysis},
    }
