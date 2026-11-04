"""slots.py — a founder's answer is a typed record, not a sentence.

WHY THIS EXISTS. Intake collects facts the founder types. Until now each one was stored as
a bare string, composed into one prose paragraph by intake._synthesize_from_extracted, and
then RE-EXTRACTED out of that paragraph by regexes downstream. The round trip is where the
value loses its meaning: prose does not carry the difference between money the customer
pays and money the founder spends, so a stated "$1,000/month operating cost" came back as
the venture's PRICE and the report published a fabricated "-95%" pricing banner (audit 1,
R2). The same shape produced the 100-seats/month defect (a stock read as a flow) and a
per-visit ticket read as a subscription.

Every one of those defects is a KIND confusion, and a kind confusion is only possible
because the kind was thrown away at the door. A slot keeps it:

    {"value": 1000.0, "unit": "month", "currency": "USD", "period": "month",
     "kind": "cost", "source": "form"}

`kind` is the load-bearing field. It answers "what does this number MEAN", and it travels
with the number so no consumer has to infer it from context that prose destroyed. A price
reader that asks for kind="price" cannot be handed a cost, whatever the field is named and
whoever later adds that field to a price list. That is the difference between defending
against the defect and making it unrepresentable.

THE SIX KEYS. The design sketch said five ({value, unit, period, kind, source}); currency
is the sixth because this codebase already treats a non-USD figure as something to DISCLOSE
rather than silently redenominate (brief._CURRENCIES), and folding it into `unit` would
throw away the distinction that disclosure exists to make.

    value     float when the answer is exactly one number, str otherwise. See LOSSLESS.
    unit      the DENOMINATOR: "visit", "drink", "hour", "seat", "location". What one of
              the thing is. Taken from the founder's own phrasing when they gave one
              ("$6.50 per drink" -> "drink"), else the field's default.
    currency  ISO code, money kinds only. None elsewhere.
    period    "day" | "week" | "month" | "year" | None. None means "not recurring" for a
              price and "a stock, not a flow" for a count. The 100-seats/month defect was
              exactly a None that a regex filled in from the wrong sentence.
    kind      one of KINDS.
    source    "form" | "chat" | "extractor" | "locate" | "brief". Provenance the report's
              honesty machinery can read without guessing.

LOSSLESS BEATS CONFIDENT. A numeric answer carrying two numbers ("$150 per hour or $2,000
per project") is kept as the founder's whole string, not reduced to its first number.
number() then returns None and the consumer abstains or discloses, which is this
codebase's standing rule (a silent skip is not an answer) and is the opposite of the R2
defect, where a confident single figure was extracted from text that did not mean it.

BACKWARD COMPATIBILITY IS NOT OPTIONAL. Sessions, CLI briefs and old clients still supply
bare strings, and a slot must never be the only readable shape. Every reader here accepts
both: text() renders a slot or a string, number() parses a slot or a string, and
is_unknown's {"unknown": True} sentinel stays distinct from a slot (it has no "kind").
"""
from __future__ import annotations

import re
from typing import Any

# ------------------------------------------------------------------------------- kinds --
# The semantic role of a value. Named for what it MEASURES, never for the field it arrived
# in: "avg_ticket" and "rent_estimate" are both dollars per something, and the entire R2
# defect is that prose could not tell them apart.
PRICE = "price"      # money the CUSTOMER pays, per unit or per period
COST = "cost"        # money the FOUNDER spends. Never a price. This is R2's whole lesson.
VOLUME = "volume"    # units per period. A FLOW: meaningless without `period`.
COUNT = "count"      # a stock: seats, people, locations. A stock given a period becomes a
                     # 12x flow, which is D-ladder defect 4.
RATE = "rate"        # a percentage. The marketplace take (C10) lives here.
PLACE = "place"      # a location string, resolved by the geocoder rather than by a regex
CHOICE = "choice"    # one of a closed option set, plus a write-in escape
TEXT = "text"        # genuinely open prose

KINDS = (PRICE, COST, VOLUME, COUNT, RATE, PLACE, CHOICE, TEXT)
NUMERIC_KINDS = (PRICE, COST, VOLUME, COUNT, RATE)
MONEY_KINDS = (PRICE, COST)

# ------------------------------------------------------------- field -> kind, ONE table --
# The only place a field's meaning is declared. intake_tree._INPUT_SPECS says how to RENDER
# the question; this says what the ANSWER means. Deliberately separate: a field can be
# typed here before the form learns a widget for it.
FIELD_KINDS: dict[str, str] = {
    # what the customer pays
    "avg_ticket": PRICE, "avg_order": PRICE, "avg_transaction": PRICE,
    "rate_basis": PRICE, "pricing": PRICE,
    # what the founder spends
    "monthly_cost_estimate": COST, "rent_estimate": COST, "unit_cost": COST,
    # flows and stocks
    "expected_volume": VOLUME,
    "capacity": COUNT, "seats_per_account": COUNT, "team_size": COUNT,
    "locations_count": COUNT, "audience_threshold": COUNT,
    # proportions
    "take_rate": RATE,
    # place
    "site": PLACE, "geography": PLACE,
    # closed sets
    "kind_fork": CHOICE, "pricing_unit_scope": CHOICE, "sales_motion": CHOICE,
    "channel": CHOICE, "side_first": CHOICE, "payer": CHOICE,
}

# The denominator a field's number is measured in when the founder did not name one.
FIELD_UNITS: dict[str, str] = {
    "avg_ticket": "visit", "avg_order": "order", "avg_transaction": "transaction",
    "unit_cost": "order", "capacity": "seat", "seats_per_account": "person",
    "team_size": "person", "locations_count": "location", "audience_threshold": "user",
}

# A field whose number is inherently recurring. Declared here rather than parsed out of the
# founder's phrasing, because parsing it out of phrasing is the defect.
FIELD_PERIODS: dict[str, str] = {
    "monthly_cost_estimate": "month",
    "rent_estimate": "month",
}


def kind_of_field(field: str) -> str:
    return FIELD_KINDS.get(field, TEXT)


# --------------------------------------------------------------------------- coercion --
_PERIOD_WORDS = {
    "day": "day", "daily": "day", "week": "week", "weekly": "week",
    "month": "month", "monthly": "month", "mo": "month", "mos": "month",
    "year": "year", "yearly": "year", "annual": "year", "annually": "year",
    "annum": "year", "yr": "year",
}
_CURRENCY_SYMBOLS = (("$", "USD"), ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"))
_CURRENCY_CODES = ("USD", "EUR", "GBP", "JPY")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SUFFIX_RE = re.compile(r"\d\s*([kmb])\b", re.I)
_SUFFIXES = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
#: "per drink", "/mo", "a month", "each box" — the founder naming their own denominator.
#: The bare articles are anchored on word boundaries: unanchored, the "a" inside "seats"
#: matched and made the unit "ts", and the "a" inside "and" made "a laptop" a denominator.
_DENOM_RE = re.compile(r"(?:\bper\b|\beach\b|\ban\b|\ba\b|/)\s*([a-z]{2,15})", re.I)
#: "15 seats", "3 stations" — a denominator sitting straight after the number.
_TRAILING_NOUN_RE = re.compile(r"\d[\d,.]*\s+([a-z]{2,15})", re.I)
#: Irregular plurals worth getting right; everything else takes a bare "s".
_PLURALS = {"person": "people", "penny": "pence", "foot": "feet"}


def normalize_period(value: Any) -> str | None:
    """"per day" -> "day"; "/mo" -> "month"; "annually" -> "year"; else None."""
    s = str(value or "").strip().lower()
    if not s:
        return None
    for word in sorted(_PERIOD_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", s):
            return _PERIOD_WORDS[word]
    return None


def detect_currency(value: Any) -> str | None:
    s = str(value or "")
    for sym, code in _CURRENCY_SYMBOLS:
        if sym in s:
            return code
    for code in _CURRENCY_CODES:
        if re.search(rf"\b{code}\b", s, re.I):
            return code
    return None


def _singular(noun: str) -> str:
    n = (noun or "").strip().lower()
    for single, plural in _PLURALS.items():
        if n == plural:
            return single
    if len(n) > 3 and n.endswith("es") and n[-3] in "sxz":
        return n[:-2]
    if len(n) > 2 and n.endswith("s") and not n.endswith("ss"):
        return n[:-1]
    return n


def _plural(noun: str, n: float) -> str:
    if n == 1:
        return noun
    if noun in _PLURALS:
        return _PLURALS[noun]
    return noun if noun.endswith("s") else noun + "s"


def to_number(raw: Any) -> float | None:
    """The first number in a value, or None. Handles "$6.50", "1,000", "15%", "200k".

    Deliberately permissive: this is the LEGACY reader, for bare strings that never
    carried a kind. Slot values are parsed once at construction and never re-read here.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw or "").strip()
    if not s:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    suffix = _SUFFIX_RE.search(s[m.start():m.end() + 3])
    if suffix:
        value *= _SUFFIXES[suffix.group(1).lower()]
    return value


def _denominators(raw: str, kind: str) -> tuple[str | None, str | None]:
    """The unit and period the FOUNDER named, read off their own phrasing.

    Returns (unit, period). "$499 per seat per month" -> ("seat", "month");
    "$150 per hour" -> ("hour", None), because an hour is a unit of work, not a
    reporting period; "40 per day" -> (None, "day"); "15 seats" -> ("seat", None).
    """
    unit = period = None
    named = False
    for match in _DENOM_RE.finditer(raw):
        named = True
        word = match.group(1).lower()
        as_period = _PERIOD_WORDS.get(word)
        if as_period and period is None:
            period = as_period
        elif not as_period and unit is None:
            unit = _singular(word)
    # The trailing noun is only a denominator when the founder named none: in "40 per day"
    # the word after the number is "per", and reading it as a unit produced "40 pers".
    if unit is None and not named and kind in (COUNT, VOLUME):
        trailing = _TRAILING_NOUN_RE.search(raw)
        if trailing and not _PERIOD_WORDS.get(trailing.group(1).lower()):
            unit = _singular(trailing.group(1))
    return unit, period


# ------------------------------------------------------------------------ construction --
def make(field: str, raw: Any, *, unit: Any = None, period: Any = None,
         source: str = "form", kind: str | None = None) -> dict:
    """Build the typed record for one answer.

    `raw` is what the founder typed. `unit` and `period` are what the FORM told us — the
    period selector beside a volume box, the unit hint beside a price box. The form's
    answer wins over the founder's phrasing, and the founder's phrasing wins over the
    field's default, because inferring either from prose when we were told is the defect
    this module removes.
    """
    k = kind or kind_of_field(field)
    raw_text = "" if raw is None else str(raw).strip()
    form_unit = str(unit).strip() if unit not in (None, "") else None
    form_period = normalize_period(period)
    currency = None

    if k in NUMERIC_KINDS:
        found = _NUM_RE.findall(raw_text) if not isinstance(raw, (int, float)) else ["1"]
        said_unit, said_period = _denominators(raw_text, k)
        if len(found) == 1 or isinstance(raw, (int, float)):
            value: Any = to_number(raw)
        else:
            # Zero numbers ("just me and a laptop") or several ("$150/hour or $2,000/
            # project"): keep the founder's whole answer. Reducing it would be the R2
            # move in miniature, and number() abstains rather than guess which one.
            value = raw_text
        unit_out = form_unit or said_unit or FIELD_UNITS.get(field)
        period_out = form_period or said_period or FIELD_PERIODS.get(field)
        if k in MONEY_KINDS:
            currency = detect_currency(raw_text) or detect_currency(form_unit) or "USD"
        if k == COUNT:
            period_out = None          # a stock has no period. Ever. See D-ladder 4.
    else:
        value = raw if isinstance(raw, (list, dict)) else raw_text
        unit_out = form_unit
        period_out = form_period
        if k == PLACE:
            unit_out = period_out = None

    return {"value": value, "unit": unit_out, "currency": currency,
            "period": period_out, "kind": k, "source": source}


def is_slot(value: Any) -> bool:
    """A typed record, as distinct from the {"unknown": True} sentinel and from a bare
    string. Both of those stay legal everywhere a slot is legal."""
    return isinstance(value, dict) and "kind" in value and "value" in value


# ----------------------------------------------------------------------------- reading --
def number(value: Any, *, expect: str | tuple[str, ...] | None = None) -> float | None:
    """The number in a value, or None.

    `expect` is the guard. A caller that wants a PRICE passes expect="price" and gets None
    from a cost slot, whatever the field was called and whoever later adds that field to
    the caller's price list. That refusal is the entire point: R2 was a cost sitting in a
    price list, and a bare string cannot refuse.

    A bare string has no kind to check, so `expect` cannot be enforced on one and it is
    parsed as before. That is the honest state for CLI briefs and old sessions, not a
    hole: those values never carried a kind to lose.
    """
    if is_slot(value):
        if expect is not None:
            wanted = (expect,) if isinstance(expect, str) else tuple(expect)
            if value.get("kind") not in wanted:
                return None
        inner = value.get("value")
        return float(inner) if isinstance(inner, (int, float)) and not isinstance(inner, bool) \
            else None
    if isinstance(value, dict):
        return None                      # the {"unknown": True} sentinel, or a foreign dict
    return to_number(value)


def _fmt_money(n: float) -> str:
    return f"{n:,.2f}" if n % 1 else f"{n:,.0f}"


def _fmt_plain(n: float) -> str:
    return f"{n:,.10g}" if n % 1 else f"{n:,.0f}"


def text(value: Any) -> str:
    """The canonical rendering: what a slot looks like written down.

    _synthesize_from_extracted composes the brief out of these, so this function decides
    what the prose says. It renders the slot's OWN kind, unit and period, which is why the
    prose can no longer disagree with the record: one value, one renderer.
    """
    if not is_slot(value):
        if isinstance(value, dict):
            return ""
        if isinstance(value, (list, tuple)):
            return ", ".join(str(v) for v in value)
        return str(value or "").strip()

    kind, raw = value.get("kind"), value.get("value")
    unit, period = value.get("unit"), value.get("period")
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return str(raw or "").strip()    # the lossless path: the founder's own words
    n = float(raw)

    if kind in MONEY_KINDS:
        cur = value.get("currency") or "USD"
        sym = next((s for s, c in _CURRENCY_SYMBOLS if c == cur), "")
        out = f"{sym}{_fmt_money(n)}" if sym else f"{_fmt_money(n)} {cur}"
        # A price's denominator IS its meaning: "$6.50" alone cannot tell a per-drink
        # ticket from a monthly fee, and that ambiguity is what shipped a seat-priced
        # report for a cafe. Period first when both exist ("$499 per seat per month").
        if unit and period:
            return f"{out} per {unit} per {period}"
        if period:
            return f"{out} per {period}"
        if unit:
            return f"{out} per {unit}"
        return out
    if kind == RATE:
        return f"{_fmt_plain(n)}%"
    if kind == VOLUME:
        base = f"{_fmt_plain(n)} {_plural(unit, n)}" if unit else _fmt_plain(n)
        return f"{base} per {period}" if period else base
    # COUNT: a stock, rendered with its noun and never with a period.
    return f"{_fmt_plain(n)} {_plural(unit, n)}" if unit else _fmt_plain(n)


#: A word sitting after the number — the founder's own noun, whatever it is.
_HAS_NOUN_RE = re.compile(r"\d[\d,.]*\s*[a-z/]", re.I)


def phrase(field: str, value: Any) -> str:
    """The value as the BRIEF should say it.

    Identical to text() for a typed slot. A legacy bare string is APPENDED TO, never
    rewritten: an old session's "$6.50" becomes "$6.50 per visit" (a price with no noun
    falls off brief.extract_price's per-unit patterns entirely), while "5 locations across
    Portland" is left exactly as the founder wrote it. Re-rendering it as "5 locations"
    would be lossless-looking and quietly drop the place, which is the failure mode this
    whole module exists to stop.
    """
    rendered = text(value)
    if not rendered or is_slot(value):
        return rendered
    kind = kind_of_field(field)
    if kind not in NUMERIC_KINDS or not _NUM_RE.search(rendered):
        return rendered
    if _HAS_NOUN_RE.search(rendered):
        return rendered                  # the founder already named their own denominator
    unit, period = FIELD_UNITS.get(field), FIELD_PERIODS.get(field)
    if kind in MONEY_KINDS and (period or unit):
        return f"{rendered} per {period or unit}"
    if kind == COUNT and unit:
        return f"{rendered} {_plural(unit, to_number(rendered) or 0)}"
    return rendered


def slot_in(extracted: dict | None, field: str) -> dict | None:
    """The typed slot for a field, or None when the value is a bare string, an unknown,
    or absent."""
    v = (extracted or {}).get(field)
    return v if is_slot(v) else None


def number_in(extracted: dict | None, field: str,
              *, expect: str | tuple[str, ...] | None = None) -> float | None:
    """The number a field holds, refusing a kind the caller did not ask for."""
    return number((extracted or {}).get(field), expect=expect)


def text_in(extracted: dict | None, field: str) -> str:
    return text((extracted or {}).get(field))


def period_in(extracted: dict | None, field: str) -> str | None:
    s = slot_in(extracted, field)
    return s.get("period") if s else None


def as_facts(extracted: dict | None) -> dict:
    """Every answered field rendered as its canonical string.

    intake_record's `facts` used to be built with an `isinstance(v, str)` filter, which a
    typed record silently fails — the fact would vanish from the run rather than arrive
    typed. This is the shape that filter was reaching for."""
    out: dict[str, str] = {}
    for field, value in (extracted or {}).items():
        if isinstance(value, dict) and value.get("unknown") is True:
            continue
        rendered = phrase(field, value)
        if rendered:
            out[field] = rendered
    return out


def as_slots(extracted: dict | None) -> dict:
    """Only the typed records, for consumers that want the kind guard. Bare strings are
    deliberately absent: a consumer reading this dict knows every value carries a kind."""
    return {f: v for f, v in (extracted or {}).items() if is_slot(v)}
