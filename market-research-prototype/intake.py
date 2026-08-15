"""
Iter 37: Conversational intake — chat with the founder until we have enough
to fire the 14-step pipeline. Don't stop until critical fields are filled.

The pipeline needs (at minimum) a clear paragraph that names:
  - product (what it does, 1-2 sentences)
  - target customer (who buys/uses)
  - business model (DTC, B2B SaaS, marketplace, ...)
  - geography (default US)

Nice-to-have (will ask once if missing):
  - pricing (if known)
  - differentiation thesis
  - stage (pre-launch, launched, scaling)
  - key features

Each turn, the LLM:
  1. Looks at the running transcript
  2. Decides what's still unknown
  3. Either asks ONE focused next question, or signals "ready" and assembles
     the final paragraph that downstream `run_plan` will consume.

Sessions are stored in-memory (these are 1-3 minute conversations); SQLite
persistence is a follow-up if needed.
"""
from __future__ import annotations
import json
import re
import time
import uuid
from threading import Lock
from typing import Any

from capabilities.effort import STANDARD, resolve_effort
from llm import call_json
from logger import get

log = get("intake")


REQUIRED_FIELDS = ("product", "target_customer", "business_model", "geography")
NICE_TO_HAVE_FIELDS = ("pricing", "differentiation", "stage", "key_features")
ALL_FIELDS = REQUIRED_FIELDS + NICE_TO_HAVE_FIELDS


INTAKE_PROMPT = """You are an analyst interviewing a founder to gather just enough information to run a market-research pipeline. Be conversational, warm, and concise — never interrogative.

Your job each turn:
1. Read the conversation so far.
2. Update what you know in `extracted` (8 possible fields below).
3. Decide:
   - If at least the 4 REQUIRED fields are filled with usable answers, set `next_action="ready"` and write a clean `final_description` paragraph.
   - Otherwise, set `next_action="ask"` and write ONE focused next question. Cover multiple gaps in one question if natural.

REQUIRED fields:
  - product: 1-2 sentences on what the product does
  - target_customer: who buys/uses (specific is better than generic)
  - business_model: DTC, B2B SaaS, marketplace, retail, ad-supported, …
  - geography: country/region (default "US" if unstated after asking once)

NICE-TO-HAVE (ask only if there's space; don't over-ask):
  - pricing: any pricing info or stage if not set
  - differentiation: what makes them different from incumbents
  - stage: idea / pre-launch / launched / scaling
  - key_features: 2-4 standout capabilities

Rules:
- NEVER ask more than ONE question per turn.
- Acknowledge what you just learned in 1 short clause before asking the next thing.
- If a user gives a great paragraph dump, fill multiple fields at once.
- After 6 user messages with critical gaps still open, lower the bar — go ready with what you have and note the gaps in the final paragraph.
- The `final_description` (when ready) must be a single coherent paragraph (~80-150 words) suitable as input to a market-research pipeline. Don't pad, don't add fictional detail.

CONVERSATION SO FAR:
{transcript}

CURRENT EXTRACTED STATE (may be empty on first turn):
{extracted}

USER MESSAGE COUNT: {user_msg_count}

Return JSON:
{{
  "extracted": {{
    "product": "..." or null,
    "target_customer": "..." or null,
    "business_model": "..." or null,
    "geography": "..." or null,
    "pricing": "..." or null,
    "differentiation": "..." or null,
    "stage": "..." or null,
    "key_features": ["..."] or null
  }},
  "next_action": "ask" or "ready",
  "next_question": "your next question (when next_action=ask)" or null,
  "final_description": "single-paragraph description for the pipeline (when next_action=ready)" or null,
  "reasoning": "1 short sentence on why you chose ask vs ready"
}}"""


_sessions: dict[str, dict] = {}
_lock = Lock()


def _format_transcript(messages: list[dict]) -> str:
    if not messages:
        return "(no messages yet)"
    return "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)


def start_session(initial_message: str | None = None) -> dict:
    """
    Start a new intake conversation. Returns the opening assistant question.
    `initial_message` lets the caller pre-seed the first user message
    (so a single text submission triggers the chat).
    """
    sid = str(uuid.uuid4())
    session = {
        "id": sid,
        "created_at": int(time.time()),
        "messages": [],   # [{role, content}]
        "extracted": {f: None for f in ALL_FIELDS},
        "ready": False,
        "final_description": None,
        # W6-3: how much depth this report deserves. Set at intake because that is
        # where the operator describes what the report is FOR — before this, the only
        # way to ask for a deep run was to know to pass `effort` to POST /plan by hand.
        "effort": STANDARD,
    }
    with _lock:
        _sessions[sid] = session

    if initial_message:
        return process_message(sid, initial_message)

    # First-turn opening question — fixed, no LLM needed
    opener = (
        "Hi! I'll help you put together a market-research report. "
        "To start: in a sentence or two, what does your product do and who is it for?"
    )
    session["messages"].append({"role": "assistant", "content": opener})
    return {
        "session_id": sid,
        "assistant_message": opener,
        "extracted": session["extracted"],
        "ready": False,
        "user_msg_count": 0,
        "effort": session["effort"],
    }


def set_effort(session_id: str, effort: str) -> dict:
    """Set how much depth this report deserves: quick | standard | deep.

    Resolution goes through capabilities.effort, so an unrecognised value lands on
    STANDARD and never on QUICK — the same rule the rest of the pipeline holds. A
    typo must not quietly thin a report the operator meant to pay more for.
    """
    with _lock:
        session = _sessions.get(session_id)
    if not session:
        return {"error": "session not found"}
    session["effort"] = resolve_effort(effort)
    return {"session_id": session_id, "effort": session["effort"]}


def process_message(session_id: str, user_message: str) -> dict:
    """
    Append the user message, run the LLM, append the assistant reply.
    Returns the new state. When ready=True, frontend can fire POST /plan
    with `final_description`.
    """
    with _lock:
        session = _sessions.get(session_id)
    if not session:
        return {"error": "session not found"}

    user_message = (user_message or "").strip()
    if not user_message:
        return {"error": "empty message"}

    session["messages"].append({"role": "user", "content": user_message})
    user_msg_count = sum(1 for m in session["messages"] if m["role"] == "user")

    # Call LLM to update extracted state + decide next action.
    # Retry on transient LLM hiccups (empty / _parse_error) before salvaging — a
    # single flaky response must NOT dead-end the chat into re-asking given info.
    prompt = INTAKE_PROMPT.format(
        transcript=_format_transcript(session["messages"]),
        extracted=json.dumps(session["extracted"], indent=2),
        user_msg_count=user_msg_count,
    )
    resp = {}
    for attempt in range(3):
        try:
            resp = call_json(
                system="You interview founders for market research intake. Be concise, warm, never interrogative. Return only JSON.",
                user=prompt, max_tokens=1200,
            )
        except Exception as e:
            log.warning("intake LLM failed (attempt %d): %s", attempt + 1, e)
            resp = {}
        if resp and "_parse_error" not in resp:
            break  # got a usable response
        log.info("intake retry %d (empty/parse_error)", attempt + 1)

    # Salvage: if LLM hard-failed, give a generic prompt
    if not resp or "_parse_error" in resp:
        assistant_text = (
            "Got it. Could you tell me a bit more about who your target customer is "
            "and how you plan to charge for the product?"
        )
        session["messages"].append({"role": "assistant", "content": assistant_text})
        return {
            "session_id": session_id,
            "assistant_message": assistant_text,
            "extracted": session["extracted"],
            "ready": False,
            "user_msg_count": user_msg_count,
        }

    # Merge extracted state — only overwrite if new value is non-null
    new_extracted = resp.get("extracted") or {}
    for k in ALL_FIELDS:
        v = new_extracted.get(k)
        if v not in (None, "", []):
            session["extracted"][k] = v

    next_action = resp.get("next_action") or "ask"

    # Safety: don't end session before user has spoken at least 2 times
    # (otherwise a verbose first message can shortcut critical clarifications)
    missing_required = [f for f in REQUIRED_FIELDS if not session["extracted"].get(f)]
    if next_action == "ready" and missing_required and user_msg_count < 6:
        next_action = "ask"

    # cycle33 (browser-test fix): the model sometimes loops on "ask" forever even
    # when all required fields are already filled — observed re-asking the SAME
    # question verbatim, so the session never reached ready and report generation
    # was blocked. Once the 4 required fields are present and the user has spoken
    # at least twice, force ready rather than waiting for the model to volunteer it.
    if next_action != "ready" and not missing_required and user_msg_count >= 2:
        log.info("intake force-ready (session=%s): all required filled but model kept asking",
                 session_id[:8])
        next_action = "ready"

    if next_action == "ready":
        final = resp.get("final_description") or ""
        if len(final) < 30:
            # Synthesize a fallback paragraph from extracted state
            final = _synthesize_from_extracted(session["extracted"])
        # Force minimum length so /plan validation passes (>=30 chars)
        if len(final) < 30:
            final = (final + ". " + (session["extracted"].get("product") or "") + " " +
                     (session["extracted"].get("target_customer") or "")).strip()
        session["final_description"] = final
        session["ready"] = True
        # NOT "Generating your report now" — nothing is generating. The run does not start
        # until the operator confirms the load-bearing answers and presses the button, and
        # the button is disabled while this message is on screen. A UI that narrates an
        # action it is not taking is the same defect class as a report asserting a number
        # it did not compute, and it trains people to distrust the parts that are true.
        assistant_text = ("That's enough to work with. Check the two answers below — they "
                          "decide the numbers — then generate whenever you're ready.")
        session["messages"].append({"role": "assistant", "content": assistant_text})
        log.info("intake ready (session=%s, %d turns, %d/%d required filled)",
                 session_id[:8], user_msg_count,
                 sum(1 for f in REQUIRED_FIELDS if session["extracted"].get(f)),
                 len(REQUIRED_FIELDS))
        return {
            "session_id": session_id,
            "assistant_message": assistant_text,
            "extracted": session["extracted"],
            "ready": True,
            "final_description": final,
            "user_msg_count": user_msg_count,
        }

    # Otherwise — ask next question
    next_q = (resp.get("next_question") or "").strip()
    if not next_q:
        # Fallback question targeting the first missing required field
        next_q = _fallback_question(session["extracted"])
    session["messages"].append({"role": "assistant", "content": next_q})
    return {
        "session_id": session_id,
        "assistant_message": next_q,
        "extracted": session["extracted"],
        "ready": False,
        "user_msg_count": user_msg_count,
    }


def get_session(session_id: str) -> dict | None:
    with _lock:
        s = _sessions.get(session_id)
    return dict(s) if s else None


def venture_memory(ex: dict):
    """W5-4: turn the intake's extracted fields into a venture-scoped Memory.

    These are facts the operator STATED. Downstream steps currently re-derive them
    from the prose description on every LLM call — and sometimes derive them
    differently (an operator who said "marketplace" gets subscription financials).
    Carrying them as standing context makes the operator's own words the anchor.

    Only fields the operator actually filled become facts; a None is not a fact.
    """
    from context.memory import Memory, Scope
    m = Memory()
    for field in ALL_FIELDS:
        v = (ex or {}).get(field)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v if x)
        if v:
            m.remember(Scope.VENTURE, field, str(v))
    return m


def _synthesize_from_extracted(ex: dict) -> str:
    parts = []
    if ex.get("product"):
        parts.append(ex["product"])
    if ex.get("target_customer"):
        parts.append(f"Target customer: {ex['target_customer']}.")
    if ex.get("business_model"):
        parts.append(f"Business model: {ex['business_model']}.")
    if ex.get("geography"):
        parts.append(f"Geography: {ex['geography']}.")
    if ex.get("pricing"):
        parts.append(f"Pricing: {ex['pricing']}.")
    if ex.get("differentiation"):
        parts.append(f"Differentiation: {ex['differentiation']}.")
    if ex.get("stage"):
        parts.append(f"Stage: {ex['stage']}.")
    if ex.get("key_features"):
        feats = ex["key_features"]
        if isinstance(feats, list):
            parts.append("Key features: " + ", ".join(feats) + ".")
    return " ".join(parts)


def _fallback_question(ex: dict) -> str:
    if not ex.get("product"):
        return "Could you describe what your product does in one or two sentences?"
    if not ex.get("target_customer"):
        return "Who's the target customer? Be specific if you can — industry, role, company size."
    if not ex.get("business_model"):
        return "How do you plan to make money — DTC, B2B SaaS, marketplace, something else?"
    if not ex.get("geography"):
        return "What geography are you targeting first? US, UK, EU, global?"
    return "Anything else important about the product or market that I should know?"


# ---------------------------------------------------------------------------------------
# Confirmation — the one stop before six minutes of research and a report full of numbers.
# ---------------------------------------------------------------------------------------

# A geography precise enough to draw a trade-area ring around. size_hyperlocal uses a 1.5 km
# radius for a walk-in venue, so "San Francisco" is not a location, it is a list of them.
# MEASURED: "San Francisco, California" geocodes to tract 011700 (lat 37.7879) while
# "Mission District of San Francisco" lands on tract 017700 (lat 37.7675) — 2.3 km apart,
# so the two 1.5 km catchments barely intersect and every household, income and competitor
# figure would belong to a neighbourhood the operator is not opening in.
_SITE_MARKERS = re.compile(
    r"\d|\bdistrict\b|\bneighbou?rhood\b|\bnear\b|\bcorner\b|\band\b.*\bst\b|"
    r"\bstreet\b|\bave\b|\bavenue\b|\brd\b|\broad\b|\bblvd\b|\bmission\b|\bdowntown\b|"
    r"\buptown\b|\bsoma\b|\bwest\b|\beast\b|\bnorth\b|\bsouth\b", re.I)

_PHYSICAL_HINTS = ("brick", "mortar", "retail", "store", "shop", "cafe", "restaurant",
                   "storefront", "walk-in", "salon", "studio", "gym", "clinic")

# A price is a FIGURE. MEASURED: the intake once put "Pay per drink" in this field — it
# fills the slot, carries no number, and every downstream volume figure still vanishes.
_PRICE_FIGURE = re.compile(r"\d")


def _is_physical(business_model: str | None) -> bool:
    low = (business_model or "").lower()
    return any(h in low for h in _PHYSICAL_HINTS)


def confirmation_items(extracted: dict | None) -> list[dict]:
    """The few answers whose value changes a published number, with what each one drives.

    DELIBERATELY SHORT. Confirming eight fields teaches people to click through; the two
    that move the arithmetic get a card, and stage/key_features/differentiation do not.

    Each item carries `drives` — what breaks if it is wrong — because "confirm your
    location" is a chore and "this sets the 1.5 km ring we count competitors in" is a
    reason to actually read it. `precise` is False when the value fills the field but not
    the need, which is the failure a required-field check cannot see.
    """
    ex = extracted or {}
    physical = _is_physical(ex.get("business_model"))
    items: list[dict] = []

    geo = (ex.get("geography") or "").strip()
    geo_precise = bool(geo) and (not physical or bool(_SITE_MARKERS.search(geo)))
    items.append({
        "field": "geography",
        "label": "Location",
        "value": geo or None,
        "precise": geo_precise,
        "drives": ("the 1.5 km trade area — the households, local spending and competitor "
                   "census every market-size figure is built from"),
        "warning": (None if geo_precise else
                    "This is a city, not a site. The trade area is a 1.5 km ring, so two "
                    "addresses in the same city can produce completely different "
                    "households and competitor counts. Which neighbourhood or cross-streets?"),
        "ask": "Which neighbourhood, or the nearest cross-streets?",
    })

    price = (ex.get("pricing") or "").strip()
    price_precise = bool(_PRICE_FIGURE.search(price))
    items.append({
        "field": "pricing",
        "label": "Price per unit",
        "value": price or None,
        "precise": price_precise,
        "drives": ("break-even volume, the daily planning target and the obtainable "
                   "ceiling — without a figure the report cannot state any of them"),
        "warning": (None if price_precise else
                    ("No number captured. A description of how you charge is not a price: "
                     "without a figure there is no break-even line and no daily target.")),
        "ask": "Roughly what will one unit cost a customer?",
    })
    return items


def confirmation_payload(session: dict | None) -> dict:
    """What the UI renders before the Generate button becomes real."""
    ex = (session or {}).get("extracted") or {}
    items = confirmation_items(ex)
    return {"items": items,
            "all_precise": all(i["precise"] for i in items),
            "confirmed": is_confirmed(session or {})}


def is_confirmed(session: dict) -> bool:
    return bool((session or {}).get("confirmed"))


def mark_confirmed(session: dict) -> dict:
    """Record the confirmation AND the values it was given for.

    Snapshotting matters: if a report's trade area later disagrees with what the operator
    believes they asked for, the artifact has to be able to say which location was on the
    screen when they pressed the button.
    """
    ex = (session or {}).get("extracted") or {}
    session["confirmed"] = True
    session["confirmed_facts"] = {i["field"]: ex.get(i["field"])
                                  for i in confirmation_items(ex)}
    return session
