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

# The decision tree's fields (intake_tree.py). Which of these a session is actually asked
# depends on what KIND of business it is — a cafe gets capacity/site/rent, a SaaS gets the
# per-seat question, a marketplace gets its take — plus founder-only core facts the pipeline
# used to guess (status quo, costs, customer evidence, competitors, success target).
TREE_FIELDS = (
    "status_quo", "monthly_cost_estimate", "customer_evidence", "named_competitors",
    "success_target",
    "capacity", "avg_ticket", "rent_estimate",
    "pricing_unit_scope", "seats_per_account", "sales_motion",
    "avg_order", "unit_cost", "channel",
    "team_size", "rate_basis",
    "take_rate", "side_first", "avg_transaction",
    "payer", "audience_threshold", "hybrid_legs",
    "site", "locations_count", "local_anchor", "real_traction", "regulatory",
    "kind_fork",
)
ALL_FIELDS = REQUIRED_FIELDS + NICE_TO_HAVE_FIELDS + TREE_FIELDS

# The escape hatch: a founder who answers vaguely forever must not be trapped in the
# interview. After this many user turns, every still-open tree question is marked as an
# assumption and the session goes ready — the report then discloses what was assumed.
MAX_TREE_TURNS = 14


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

PENDING FIELD: {pending_field}
OTHER FIELDS THIS VENTURE'S INTERVIEW USES (store any answer that fits one, whatever was
asked): {active_fields}
The interviewer's LAST question asked about this specific field. If the user's message
answers it — even partially, even just a number — store the answer under exactly this key in
`extracted`. If the user clearly says they don't know, leave it null (the system records the
"don't know" separately). Any OTHER facts in the message still go to their own fields.

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
    "key_features": ["..."] or null,
    "<the pending field, when one is named above>": "..." or null
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
        "pending_field": None,   # which tree field the last question asked about
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
        pending_field=session.get("pending_field") or "(none)",
        active_fields=", ".join(session.get("active_fields") or []) or "(none yet)",
        transcript=_format_transcript(session["messages"]),
        extracted=json.dumps({k: v for k, v in session["extracted"].items()
                              if v not in (None, "", [])}, indent=2),
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

    # Merge extracted state — only overwrite if new value is non-null, and never
    # overwrite an explicit "not sure" marker with a model hallucination.
    from intake_tree import is_unknown as _tree_unknown
    new_extracted = resp.get("extracted") or {}
    for k in ALL_FIELDS:
        v = new_extracted.get(k)
        if v not in (None, "", []) and not _tree_unknown(session["extracted"].get(k)):
            session["extracted"][k] = v

    # THE TREE. The LLM above only extracts; which question comes next is decided here,
    # deterministically, from what kind of business this is — the pipeline's own
    # classifiers, run during the conversation instead of after the founder is gone.
    from intake_tree import (classify_turn, mark_unknown, next_question, tree_fields,
                             utterance_is_not_sure)

    # "Not sure" is an ANSWER: the pending field becomes a disclosed assumption instead of
    # being re-asked forever or force-filled with fake precision.
    pending = session.get("pending_field")
    if pending and utterance_is_not_sure(user_message) and             not session["extracted"].get(pending):
        mark_unknown(session["extracted"], pending)

    cls = classify_turn(session["extracted"])
    tree_q = next_question(session["extracted"], cls)
    from intake_tree import plan_questions as _plan_qs
    session["active_fields"] = [q["field"] for q in _plan_qs(session["extracted"], cls)]

    # The escape hatch: after MAX_TREE_TURNS user messages, every still-open tree question
    # becomes an assumption and the interview ends — vagueness must not trap anyone.
    if tree_q and user_msg_count >= MAX_TREE_TURNS:
        from intake_tree import plan_questions
        for q in plan_questions(session["extracted"], cls):
            if not session["extracted"].get(q["field"]):
                mark_unknown(session["extracted"], q["field"])
        tree_q = None

    next_action = resp.get("next_action") or "ask"
    if tree_q is not None:
        next_action = "ask"          # the venture's own pack still has open questions
    session["classification"] = {k: cls.get(k) for k in
                                 ("kind", "explicit", "needs_fork", "is_physical",
                                  "multi_location", "non_us", "launched", "regulated")}

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
    if (next_action != "ready" and not missing_required and user_msg_count >= 2
            and tree_q is None):
        log.info("intake force-ready (session=%s): all required filled but model kept asking",
                 session_id[:8])
        next_action = "ready"
    # The mirror guard: the LLM may declare ready while the venture's own pack still has
    # open questions. The tree outranks it — that is the whole point of the tree.
    if next_action == "ready" and tree_q is not None:
        next_action = "ask"

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
            # The final chip state too — without it the progress chips freeze one turn
            # stale (measured: "success target" showed open on a ready session).
            "tree_fields": tree_fields(session["extracted"], cls),
            "classification": session.get("classification"),
            "user_msg_count": user_msg_count,
        }

    # Otherwise — ask. The tree's question wins; the LLM's own suggestion is only used
    # when the pack is exhausted but required fields are still missing (early turns).
    asked_field = asked_why = None
    if tree_q is not None:
        next_q = tree_q["question"]
        asked_field, asked_why = tree_q["field"], tree_q["drives"]
    else:
        next_q = (resp.get("next_question") or "").strip() or             _fallback_question(session["extracted"])
    session["pending_field"] = asked_field
    session["messages"].append({"role": "assistant", "content": next_q})
    return {
        "session_id": session_id,
        "assistant_message": next_q,
        "asked_field": asked_field,
        "asked_why": asked_why,
        "classification": session.get("classification"),
        "tree_fields": tree_fields(session["extracted"], cls),
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
        # "Located in X", NOT "Geography: X". MEASURED: plan.extract_location requires a
        # prepositional phrase, and the label form returned None on every description this
        # builder has ever produced. The consequence was silent and total —
        # size_by_scale returns None without a location (no trade-area sizing at all) and
        # geo_competitor_opps returns [] (no local competitor census) — so a neighbourhood
        # cafe fell back to national sizing and the report said "needs an address" rather
        # than "I could not read the address you gave me".
        parts.append(f"Located in {ex['geography']}.")
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

    # THE TREE'S FACTS. Each rides the brief in a phrasing a downstream consumer already
    # parses — "Named competitors:" seeds discover._union_named_competitors via the profile
    # extractor, price figures are read by brief.extract_price, location counts by
    # plan.extract_location_count. A fact phrased unreadably is a fact not collected.
    from intake_tree import is_unknown as _unk
    def _val(k):
        v = ex.get(k)
        return None if (v in (None, "", []) or _unk(v)) else v
    _tree_lines = (
        ("site", "The exact site: {}."),
        ("locations_count", "{}."),
        ("capacity", "Capacity: {}."),
        ("avg_ticket", "Typical price: {} per visit."),
        ("avg_order", "Typical order value: {}."),
        ("avg_transaction", "Typical transaction: {}."),
        ("rate_basis", "Charges {}."),
        ("pricing_unit_scope", "The fee is charged {}."),
        ("seats_per_account", "Typically {} users per customer."),
        ("take_rate", "The platform keeps {} of each transaction."),
        ("side_first", "Supply/demand priority: {}."),
        ("team_size", "Team who can deliver the work: {}."),
        ("sales_motion", "Sales motion: {}."),
        ("channel", "Sales channel: {}."),
        ("payer", "Revenue comes from: {}."),
        ("audience_threshold", "Audience needed before revenue: {}."),
        ("hybrid_legs", "Revenue legs: {}."),
        ("named_competitors", "Named competitors: {}."),
        ("status_quo", "What customers do today instead: {}."),
        ("monthly_cost_estimate", "Founder's estimated monthly operating cost: {}."),
        ("customer_evidence", "Customer conversations so far: {}."),
        ("success_target", "The founder's year-one goal: {}."),
        ("real_traction", "Traction to date: {}."),
        ("regulatory", "Known regulatory requirements: {}."),
        ("local_anchor", "Founder-supplied local figure: {}."),
    )
    for field, tpl in _tree_lines:
        v = _val(field)
        if v is not None:
            parts.append(tpl.format(v))

    # "Not sure" answers become DISCLOSED assumptions, not silence. The report's own
    # honesty machinery (data_origin, UNSOURCED labels) keys off knowing a figure was
    # never given — a dropped unknown reads downstream as "nothing to say" instead of
    # "asked, and the founder does not know yet".
    assumed = [f.replace("_", " ") for f in ALL_FIELDS
               if _unk(ex.get(f)) and f != "kind_fork"]
    if assumed:
        parts.append("The founder does not know yet (treat as assumptions and label them): "
                     + ", ".join(assumed) + ".")
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


# The taxonomy in the founder's words. The card must never say "subscription" or
# "transactional" — the orbital founder answered our vocabulary with "Undetermined".
_KIND_IN_FOUNDER_WORDS = {
    "transactional": "customers pay per visit or per item, like a shop",
    "subscription": "customers pay a recurring fee, like Netflix",
    "ecommerce": "customers buy products you ship, like an online store",
    "services": "customers pay for your team's time, like a contractor",
    "marketplace": "you keep a cut of sales between other people, like Uber",
    "ad_supported": "free for users — advertisers or sponsors pay",
    "hybrid": "customers pay in more than one way (an up-front part and an ongoing part)",
}


def confirmation_items(extracted: dict | None) -> list[dict]:
    """The few answers whose value changes a published number, with what each one drives.

    DELIBERATELY SHORT. Confirming eight fields teaches people to click through; the two
    that move the arithmetic get a card, and stage/key_features/differentiation do not.

    Each item carries `drives` — what breaks if it is wrong — because "confirm your
    location" is a chore and "this sets the 1.5 km ring we count competitors in" is a
    reason to actually read it. `precise` is False when the value fills the field but not
    the need, which is the failure a required-field check cannot see.
    """
    from intake_tree import classify_turn, is_unknown as _unk
    ex = extracted or {}
    cls = classify_turn(ex)
    physical = _is_physical(ex.get("business_model")) or cls.get("is_physical")
    items: list[dict] = []

    # THE KIND DECISION — the line job d62bc04f never got. The classifier's pick is shown
    # in the founder's words, labelled stated when their brief named a revenue shape and
    # inferred when the classifier derived it. An inference the founder never sees is a
    # silent pick, and the last silent pick shipped a seat-priced report for a venture
    # whose brief said "Undetermined".
    kind = cls.get("kind") or "transactional"
    items.append({
        "field": "kind",
        "label": "How the money works",
        "value": _KIND_IN_FOUNDER_WORDS.get(kind, kind),
        "provenance": "stated" if cls.get("explicit") else "inferred",
        "precise": bool(cls.get("explicit")),
        "drives": "which financial tables get built — every projection takes this shape",
        "warning": (None if cls.get("explicit") else
                    "You didn't say this directly — I worked it out from your description. "
                    "If it's wrong, every number will be."),
        "ask": "How will customers pay you?",
    })

    # THE COMPETITOR SEED — always on the card, even (especially) when empty. One real
    # name anchors discovery; the last run without one fabricated three competitors that
    # were all the same website.
    comp = ex.get("named_competitors")
    comp = None if (_unk(comp) or not comp) else str(comp)
    items.append({
        "field": "named_competitors",
        "label": "Competitors you know of",
        "value": comp,
        "provenance": "stated" if comp else ("assumed" if _unk(ex.get("named_competitors"))
                                             else None),
        "precise": bool(comp),
        "drives": "the competitor research starts from real names instead of guesses",
        "warning": (None if comp else
                    "None named. If you know even one company doing something close, it "
                    "anchors the whole competitive section."),
        "ask": "Any company doing something close? One name is enough.",
    })

    # ASSUMED LINES — every "not sure" the founder gave, so the card is the last place to
    # change their mind before those become labeled assumptions in the report.
    for f in ALL_FIELDS:
        if f in ("kind_fork", "named_competitors"):
            continue
        if _unk(ex.get(f)):
            items.append({
                "field": f,
                "label": f.replace("_", " ").capitalize(),
                "value": None,
                "provenance": "assumed",
                "precise": False,
                "drives": "the report will estimate this and label everything built on it",
                "warning": None,
                "ask": "Know it now? Type it — otherwise I'll estimate and say so.",
            })

    geo = (ex.get("site") if ex.get("site") and not _unk(ex.get("site"))
           else ex.get("geography"))
    geo = ("" if _unk(geo) else (geo or "")).strip()
    geo_precise = bool(geo) and (not physical or bool(_SITE_MARKERS.search(geo)))
    items.append({
        "field": "geography",
        "label": "Location",
        "value": geo or None,
        "provenance": "stated" if geo else "assumed",
        "precise": geo_precise,
        "drives": ("the 1.5 km trade area — the households, local spending and competitor "
                   "census every market-size figure is built from"),
        "warning": (None if geo_precise else
                    "This is a city, not a site. The trade area is a 1.5 km ring, so two "
                    "addresses in the same city can produce completely different households "
                    "and competitor counts — and reports without a specific site are "
                    "routinely WITHHELD by the verifier. Which neighbourhood or "
                    "cross-streets?"),
        "ask": "Which neighbourhood, or the nearest cross-streets?",
    })

    _praw = ex.get("pricing") or ex.get("avg_ticket") or ex.get("avg_order") \
        or ex.get("rate_basis") or ex.get("avg_transaction")
    price = ("" if _unk(_praw) else str(_praw or "")).strip()
    price_precise = bool(_PRICE_FIGURE.search(price))
    items.append({
        "field": "pricing",
        "label": "Price per unit",
        "value": price or None,
        "provenance": "stated" if price else "assumed",
        "precise": price_precise,
        "drives": ("break-even volume, the daily planning target and the obtainable "
                   "ceiling — without a figure the report cannot state any of them"),
        "warning": (None if price_precise else
                    ("No number captured — and without a figure the verifier often WITHHOLDS "
                     "the report, because break-even and the daily target cannot be stated. "
                     "A rough number beats none; it will be labeled as yours.")),
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
    # REBUILD. final_description is synthesised when the session goes ready, which is
    # BEFORE the operator sees the card — so a correction made on the card would never
    # reach the run, and the card would be theatre for the one field it exists to fix.
    session["final_description"] = _synthesize_from_extracted(ex)
    return session
