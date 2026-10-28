"""Market sizing in code from sourced figures (methods adapted from market-recon, MIT).

Three ways to a TAM (top-down, bottom-up, value theory), then SAM and SOM by stated
shares. No model is asked: every number is arithmetic on figures the market_size section
cited, the founder's own price, or another section's output, and each carries its formula,
inputs, citations and a confidence the citations earn. A method without its inputs says
which input is missing instead of guessing one.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from . import base

KEY = "sizing"
LABEL = "Sizing calculation"
STALE_DAYS = 180
METHODS = ("bottom_up", "top_down", "value_theory")  # also the anchor preference
FIGURE_KINDS = frozenset({"market_value", "customer_count", "price", "serviceable_share",
                          "growth_rate", "other"})
STAGE_BASE = {"pre_launch": 0.02, "early_customers": 0.05, "scaling": 0.10}
SATURATION = {"low": 1.25, "medium": 1.0, "high": 0.6}
DEFAULT_SHARE = 0.30
DEFAULT_SHARE_WARNING = ("No sourced serviceable share was found, so SAM uses the default "
                         "30% of TAM.")
SOURCE_TYPES = frozenset({"web", "founder", "section"})
CONFIDENCES = ("high", "medium", "low", None)
# Stage words, checked in this order; anything else is pre_launch, the cautious base.
STAGE_WORDS = (
    ("pre_launch", r"pre[- ]?(launch|revenue)|not (yet )?launched|idea|concept|planning|prototype"),
    ("scaling", r"scal(e|ing)|expanding|growth stage|established|profitable|series [a-c]|"
                r"multiple (locations|sites|markets)"),
    ("early_customers", r"pilot|beta|launched|first (paying )?customers|early customers|"
                        r"paying customers|revenue|traction"),
)
_PERIOD = ((12, r"month|\bmo\b|/mo"), (1, r"year|annual|\byr\b|/yr"), (52, r"week"))
_SLOT_PERIOD = {"month": 12, "year": 1, "week": 52}
_SCALE = {"thousand": 1e3, "million": 1e6, "mn": 1e6, "billion": 1e9, "bn": 1e9, "trillion": 1e12}
_FOREIGN = re.compile(r"\b(EUR|GBP|CAD|AUD|JPY|CNY|INR|CHF|NZD|SEK|MXN|BRL)\b|[€£¥]")
_AMOUNT = re.compile(r"(?:\$|\bUSD\s?)\s?(\d[\d,]*(?:\.\d+)?)", re.I)
#: The billing period written right after a founder's amount: "$29/month", "$29 a month",
#: "$29 per month", "$29 monthly". A period elsewhere in the sentence is not the price's:
#: "$5 per latte, about 300 a week" is a per-item price and a volume, not $260 a year.
_ADJACENT_PERIOD = re.compile(
    r"\s*(?:usd\s*)?(?:/|per\s+|a\s+|an\s+|each\s+)?(month|mo|year|yr|annum|week|wk)\b"
    r"|\s*(?:usd\s*)?(monthly|annually|yearly|weekly)\b", re.I)
_PERIOD_WORD = {"month": 12, "mo": 12, "monthly": 12, "year": 1, "yr": 1, "annum": 1,
                "annually": 1, "yearly": 1, "week": 52, "wk": 52, "weekly": 52}


# --------------------------------------------------------------------------- pure methods
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def estimate_som_share(competitors: int, stage: str, saturation: str) -> float:
    """clamp(base x 1/(1 + competitors/10) x saturation, 0.005, 0.25)."""
    if competitors < 0:
        raise ValueError("a competitor count cannot be negative")
    share = STAGE_BASE[stage] / (1 + competitors / 10) * SATURATION[saturation]
    return clamp(share, 0.005, 0.25)


def positioning_factor(price: float | None, comparable: float) -> float:
    return 1.0 if price is None else clamp(price / comparable, 0.5, 2.0)


def stage_of(text: str | None) -> str:
    for stage, pattern in STAGE_WORDS:
        if text and re.search(pattern, text, re.I):
            return stage
    return "pre_launch"


def _annual(text: str) -> int | None:
    return next((mult for mult, pattern in _PERIOD if re.search(pattern, text, re.I)), None)


# --------------------------------------------------------------------------- citations
def _parse_day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def judge(value: float | None, citations: list[dict], now: datetime) -> tuple[str | None, bool]:
    """Confidence and verified. High needs two independent fresh web domains; the founder's
    word is no domain, so alone it is low; anything else cited is medium."""
    if not citations:
        return ("low" if value is not None else None), False
    web = [c for c in citations if c["source_type"] == "web"]
    domains = {base.registered_domain(c["url"]) for c in web} - {""}
    fresh = all(_fresh(c["retrieved_at"], now) for c in web)
    if len(domains) >= 2 and fresh:
        return "high", True
    if all(c["source_type"] == "founder" for c in citations):
        return "low", False
    return "medium", bool(domains)


def _fresh(retrieved_at: str | None, now: datetime) -> bool:
    day = _parse_day(retrieved_at)
    return day is not None and -1 <= (now.date() - day).days <= STALE_DAYS


def number(value: float | None, formula: str, inputs: dict, citations: list[dict],
           now: datetime, note: str | None = None) -> dict:
    confidence, verified = judge(value, citations, now)
    return {"value": None if value is None else round(value, 2), "formula": formula,
            "inputs": inputs, "citations": citations, "confidence": confidence,
            "verified": verified, "note": note}


def _missing(formula: str, names: list[str]) -> dict:
    note = f"not computed: {', '.join(names)} not found in any source"
    return {"value": None, "formula": formula, "inputs": {}, "citations": [],
            "confidence": None, "verified": False, "note": note}


# --------------------------------------------------------------------------- inputs
def read_figure(raw: Any) -> tuple[dict | None, str | None]:
    """A usable figure with its value in USD (prices per year), or the reason it is skipped."""
    if not isinstance(raw, dict) or raw.get("kind") not in FIGURE_KINDS:
        return None, "a figure without a known kind was skipped"
    kind, unit = raw["kind"], str(raw.get("unit") or "")
    name = str(raw.get("name") or kind)
    value, url = base.finite_number(raw.get("value")), raw.get("source_url")
    if value is None or value <= 0:
        return None, f'figure "{name}" has no positive number and was skipped'
    if not isinstance(url, str) or not url.startswith(("http://", "https://")) \
            or not base.registered_domain(url):
        return None, f'figure "{name}" has no source URL and was skipped'
    if kind in ("market_value", "price") and _FOREIGN.search(unit):
        return None, f'figure "{name}" is not in USD and was not converted'
    scale = next((m for w, m in _SCALE.items() if re.search(rf"\b{w}s?\b", unit, re.I)), 1.0)
    if kind in ("market_value", "customer_count"):
        value *= scale
    elif kind == "price":
        mult = _annual(unit)
        if mult is None:
            return None, f'price figure "{name}" has no billing period and was not annualized'
        value *= mult
    elif kind == "serviceable_share":
        # The metrics contract asks for fractions; a value above 1 can only be a percent.
        value = value / 100 if value > 1 else value
        if not 0 < value <= 0.9:
            return None, f'serviceable share "{name}" is outside (0, 0.9] and was skipped'
    citation = {"url": url, "title": str(raw.get("source_title") or url),
                "retrieved_at": raw.get("retrieved_at") if _parse_day(raw.get("retrieved_at")) else None,
                "source_type": "web"}
    return {"kind": kind, "name": name, "value": value,
            "geography": str(raw.get("geography") or ""), "citation": citation}, None


def founder_price(context: dict) -> tuple[float | None, dict | None, str]:
    """The founder's own annual price per customer, when one amount and a period are stated.

    Only customer-stated answers count (an inferred attribute is not the founder's word),
    and unit_economics is never read: it mixes the price with what each sale costs.
    """
    facts = base.intake_facts(context)
    sources = []
    for name in ("pricing", "business_model"):
        record = base.attribute(context, name)
        if record.get("basis") == "customer_stated":
            sources.append((f"{name} attribute", record.get("value")))
        sources.append((f"survey {name}", facts.get(name)))
    for label, value in sources:
        annual, amount = _price_from(value)
        if annual is not None:
            cite = {"url": "", "title": f"Founder's answer ({label})", "retrieved_at": None,
                    "source_type": "founder"}
            return annual, cite, f"Founder price {money(amount)} annualized to {money(annual)}."
    return None, None, "The founder stated no single recurring price that can be annualized."


def _price_from(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, dict) and value.get("kind") == "price":
        amount, mult = base.finite_number(value.get("value")), _SLOT_PERIOD.get(value.get("period"))
        if amount and mult and value.get("currency") in (None, "USD"):
            return amount * mult, amount
        return None, None
    if not isinstance(value, str) or _FOREIGN.search(value):
        return None, None
    matches = list(_AMOUNT.finditer(value))
    if len({m.group(1) for m in matches}) != 1:
        return None, None
    period = _ADJACENT_PERIOD.match(value, matches[0].end())
    if period is None:
        return None, None
    mult = _PERIOD_WORD[(period.group(1) or period.group(2)).lower()]
    amount = float(matches[0].group(1).replace(",", ""))
    return (amount * mult, amount) if amount > 0 else (None, None)


def competitor_input(context: dict) -> tuple[int | None, str, str | None]:
    places = (base.upstream(context, "competitor_count") or {}).get("data") or {}
    if places.get("available") and isinstance(places.get("count"), int):
        return places["count"], "the Google Places count", None
    listed = ((base.upstream(context, "competitive_landscape") or {}).get("metrics") or {}).get(
        "competitors")
    if isinstance(listed, list) and listed:
        # An empty list means none were found with a source, not that none exist.
        return len(listed), "the competitive landscape's list", None
    # Assuming none would give the largest share the formula allows, so with no count
    # the obtainable share, and SOM, are left uncomputed and say why.
    return None, "no source", ("No competitor count was available, so the obtainable share "
                               "and SOM were not computed.")


def _pooled(figures: list[dict], kind: str) -> tuple[float | None, list[dict]]:
    chosen = [f for f in figures if f["kind"] == kind]
    return base.median(f["value"] for f in chosen), [f["citation"] for f in chosen]


# --------------------------------------------------------------------------- produce
def produce(context: dict, *, model=None, session=None, env=None, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    warnings: list[str] = []
    market = base.upstream(context, "market_size")
    raw = ((market or {}).get("metrics") or {}).get("figures")
    if market is None:
        warnings.append("The market_size section did not run, so no sourced figure was available.")
    figures = []
    for item in raw if isinstance(raw, list) else []:
        figure, why = read_figure(item)
        if figure:
            figures.append(figure)
        else:
            warnings.append(why)
    pricing = ((base.upstream(context, "pricing") or {}).get("data") or {})
    monthly = base.finite_number(pricing.get("category_median_monthly"))
    if monthly and monthly > 0 and pricing.get("currency") in (None, "USD"):
        figures.append({"kind": "price", "name": "pricing section category median",
                        "value": monthly * 12, "geography": "", "citation": {
                            "url": "", "title": "Pricing section category median (monthly x 12)",
                            "retrieved_at": None, "source_type": "section"}})
    geos = {f["geography"] for f in figures if f["kind"] == "market_value" and f["geography"]}
    if len(geos) > 1:
        warnings.append(f"Market-value figures cover different places ({', '.join(sorted(geos))}); "
                        "their median mixes them.")
    inputs = {kind: _pooled(figures, kind) for kind in
              ("market_value", "customer_count", "price", "serviceable_share")}
    price, price_cite, price_note = founder_price(context)
    count, count_source, count_warning = competitor_input(context)
    warnings += [count_warning] if count_warning else []
    stage = stage_of(base.stated(context, "stage"))
    landscape = ((base.upstream(context, "competitive_landscape") or {}).get("metrics") or {})
    saturation = landscape.get("saturation") if landscape.get("saturation") in SATURATION else "medium"
    if count is None:
        som_share = {"value": None, "rationale": "Not computed: no competitor count was available."}
    else:
        share = estimate_som_share(count, stage, saturation)
        som_share = {"value": round(share, 4), "rationale": (
            f"{stage} base {STAGE_BASE[stage]:.0%} x 1/(1 + {count}/10) x {saturation} saturation "
            f"{SATURATION[saturation]:g}, clamped to [0.5%, 25%]; competitor count from "
            f"{count_source}.")}
    methods = {name: _method(name, inputs, (price, price_cite, price_note), som_share, now)
               for name in METHODS}
    anchor = next((m for m in METHODS if methods[m]["tam"]["value"] is not None), None)
    if anchor and inputs["serviceable_share"][0] is None:
        warnings.append(DEFAULT_SHARE_WARNING)
    gaps = list(dict.fromkeys(methods[m]["tam"]["note"] for m in METHODS
                              if methods[m]["tam"]["value"] is None))
    data = {"methods": methods, "anchor_method": anchor, "som_share": som_share,
            "gaps": gaps, "warnings": list(dict.fromkeys(warnings))}
    cited = any(methods[m]["tam"]["value"] is not None and methods[m]["tam"]["citations"]
                for m in METHODS)
    chosen = methods[anchor] if anchor else None
    return base.envelope(
        LABEL, narrative(data), data=data, status="derived" if cited else "partial",
        findings=[f"{m}: TAM {money(methods[m]['tam']['value'])} "
                  f"({methods[m]['tam']['confidence']} confidence)" for m in METHODS
                  if methods[m]["tam"]["value"] is not None],
        assumptions=list(dict.fromkeys(a for m in METHODS for a in methods[m]["assumptions"])),
        uncertainties=gaps + data["warnings"],
        metrics={"anchor_method": anchor, "currency": "USD",
                 **{k: chosen[k]["value"] if chosen else None for k in ("tam", "sam", "som")}})


def _method(name: str, inputs: dict, founder: tuple, som_share: dict, now: datetime) -> dict:
    (market, market_c), (customers, customers_c) = inputs["market_value"], inputs["customer_count"]
    (comparable, comparable_c), (sourced_share, share_c) = inputs["price"], inputs["serviceable_share"]
    price, price_cite, price_note = founder
    assumptions = []
    if name == "top_down":
        formula = "TAM = market_value"
        need = [] if market is not None else ["market_value"]
        value, ins, cites = market, {"market_value": market}, market_c
    elif name == "bottom_up":
        formula = "TAM = customers x ARPU"
        arpu, arpu_c = (price, [price_cite]) if price is not None else (comparable, comparable_c)
        need = [n for n, v in (("customer_count", customers), ("annual price per customer", arpu))
                if v is None]
        value = customers * arpu if not need else None
        ins, cites = {"customers": customers, "arpu": arpu}, customers_c + arpu_c
        assumptions.append(price_note if price is not None else
                           "ARPU is the median public price per year; the founder's price was not usable.")
    else:
        formula = "TAM = comparable_price x positioning_factor x customers"
        need = [n for n, v in (("comparable price", comparable), ("customer_count", customers))
                if v is None]
        factor = positioning_factor(price, comparable) if comparable else None
        value = comparable * factor * customers if not need else None
        ins = {"comparable_price": comparable, "positioning_factor": factor, "customers": customers}
        cites = comparable_c + customers_c + ([price_cite] if price is not None else [])
        assumptions.append("Positioning factor is 1.0: no founder price." if price is None else
                           "Positioning factor is the founder's price over the comparable, "
                           "clamped to [0.5, 2.0].")
    if need:
        tam = _missing(formula, need)
        return {"tam": tam, "sam": _missing("SAM = TAM x serviceable_share", need),
                "som": _missing("SOM = SAM x obtainable_share", need), "assumptions": []}
    tam = number(value, formula, ins, cites, now)
    share = sourced_share if sourced_share is not None else DEFAULT_SHARE
    if sourced_share is None:
        assumptions.append("SAM uses the default serviceable share of 30%.")
    sam = number(tam["value"] * share, "SAM = TAM x serviceable_share",
                 {"tam": tam["value"], "serviceable_share": share}, cites + share_c, now)
    if som_share["value"] is None:
        som = _missing("SOM = SAM x obtainable_share", ["a competitor count"])
    else:
        som = number(sam["value"] * som_share["value"], "SOM = SAM x obtainable_share",
                     {"sam": sam["value"], "obtainable_share": som_share["value"]},
                     sam["citations"], now)
    return {"tam": tam, "sam": sam, "som": som, "assumptions": assumptions}


# --------------------------------------------------------------------------- text
def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    for floor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= floor:
            return f"${value / floor:.1f}".rstrip("0").rstrip(".") + suffix
    return f"${value:,.0f}"


def narrative(data: dict) -> str:
    anchor = data["anchor_method"]
    if anchor is None:
        why = "; ".join(data["gaps"]) or "no input was available"
        return f"No market size could be computed ({why})."
    m = data["methods"][anchor]
    share = m["sam"]["inputs"]["serviceable_share"]
    return (f"Anchored on the {anchor.replace('_', '-')} method, in USD: TAM {money(m['tam']['value'])} "
            f"({m['tam']['formula']}), SAM {money(m['sam']['value'])} (TAM x {share:.0%}), "
            + (f"SOM {money(m['som']['value'])} (SAM x {data['som_share']['value']:.1%})."
               if data["som_share"]["value"] is not None
               else "SOM not computed: no competitor count was available."))


# --------------------------------------------------------------------------- data
def _check_number(item: Any, where: str) -> None:
    if not isinstance(item, dict) or not isinstance(item.get("formula"), str):
        raise ValueError(f"{where} needs a formula")
    value = item.get("value")
    if value is not None and (base.finite_number(value) is None or value < 0):
        raise ValueError(f"{where} value must be a non-negative number or null")
    if item.get("confidence") not in CONFIDENCES or not isinstance(item.get("verified"), bool):
        raise ValueError(f"{where} needs a confidence and a verified flag")
    if not isinstance(item.get("inputs"), dict) or not isinstance(item.get("citations"), list):
        raise ValueError(f"{where} needs inputs and citations")
    if any(not isinstance(c, dict) or c.get("source_type") not in SOURCE_TYPES
           for c in item["citations"]):
        raise ValueError(f"{where} has a citation without a known source type")
    if value is None and not isinstance(item.get("note"), str):
        raise ValueError(f"{where} must say why it was not computed")


def validate_data(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("methods"), dict) \
            or set(data["methods"]) != set(METHODS):
        raise ValueError("sizing data needs the three methods")
    for name, method in data["methods"].items():
        if not isinstance(method, dict) or not isinstance(method.get("assumptions"), list):
            raise ValueError(f"{name} needs its assumptions")
        for part in ("tam", "sam", "som"):
            _check_number(method.get(part), f"{name}.{part}")
    if data.get("anchor_method") not in (*METHODS, None):
        raise ValueError("anchor_method must name a method")
    share = data.get("som_share")
    if not isinstance(share, dict) or not isinstance(share.get("rationale"), str) or (
            share.get("value") is not None and base.finite_number(share["value"]) is None):
        raise ValueError("som_share needs a value or a reason it has none")
    for field in ("gaps", "warnings"):
        if not isinstance(data.get(field), list) or not all(isinstance(x, str) for x in data[field]):
            raise ValueError(f"{field} must be a list of text")


def render_data(data: dict) -> str:
    rows = []
    for name in METHODS:
        m = data["methods"][name]
        conf = m["tam"]["confidence"] or ""
        rows.append((name.replace("_", "-"), money(m["tam"]["value"]), money(m["sam"]["value"]),
                     money(m["som"]["value"]), conf, m["tam"]["note"] or m["tam"]["formula"]))
    html = base.render_table(("Method", "TAM", "SAM", "SOM", "Confidence", "Formula"), rows)
    html += f"<p>Obtainable share: {base.esc(data['som_share']['rationale'])}</p>"
    sources = {c["title"]: c for name in METHODS for c in data["methods"][name]["tam"]["citations"]}
    if sources:
        html += "<h4>Sources</h4>" + base.render_list(
            f"{c['title']} {c['url']}".strip() for c in sources.values())
    if data["warnings"]:
        html += "<h4>Warnings</h4>" + base.render_list(data["warnings"])
    return html
