"""
Multi-backend LLM wrapper. Supports Anthropic, Groq, and Gemini.
Picks backend via LLM_BACKEND env var (default: auto-detect from available keys).

Call interface is identical regardless of backend: call_json() and call_text()
return the same types. Usage tracking works for all backends.

Supported backends + free tier:
  groq    → Llama 3.3 70B, 30 RPM free, set GROQ_API_KEY
  gemini  → Gemini 2.0 Flash, 15 RPM free, set GEMINI_API_KEY
  anthropic → Claude Haiku, paid, set ANTHROPIC_API_KEY
"""
from __future__ import annotations
import json
import re
import os
import threading
import time
from dataclasses import dataclass, field

from logger import get

log = get("llm")


# ---------------------------------------------------------------------------
# Pricing ($ per 1M tokens). Free-tier backends are priced at $0.
# ---------------------------------------------------------------------------
PRICING = {
    # Anthropic
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    # Groq free tier
    "llama-3.3-70b-versatile": {"input": 0.0, "output": 0.0},
    "llama-3.1-8b-instant": {"input": 0.0, "output": 0.0},
    "mixtral-8x7b-32768": {"input": 0.0, "output": 0.0},
    # Gemini free tier
    "gemini-2.0-flash": {"input": 0.0, "output": 0.0},
    "gemini-1.5-flash": {"input": 0.0, "output": 0.0},
}
DEFAULT_PRICING = {"input": 0.0, "output": 0.0}


# ---------------------------------------------------------------------------
# Usage tracker (unchanged — works for all backends)
# ---------------------------------------------------------------------------
@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, in_tok: int, out_tok: int) -> None:
        price = PRICING.get(model, DEFAULT_PRICING)
        cost = (in_tok / 1_000_000) * price["input"] + (out_tok / 1_000_000) * price["output"]
        self.calls += 1
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.usd += cost
        slot = self.by_model.setdefault(
            model, {"calls": 0, "in": 0, "out": 0, "usd": 0.0}
        )
        slot["calls"] += 1
        slot["in"] += in_tok
        slot["out"] += out_tok
        slot["usd"] += cost

    def summary(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": round(self.usd, 4),
            "by_model": {
                m: {**v, "usd": round(v["usd"], 4)} for m, v in self.by_model.items()
            },
        }

    def log_summary(self) -> None:
        s = self.summary()
        log.info(
            "usage: %d calls, %d in + %d out tokens, $%.4f",
            s["calls"], s["input_tokens"], s["output_tokens"], s["usd"],
        )


usage = Usage()
def reset_usage() -> None:
    global usage
    usage = Usage()
def get_usage() -> Usage:
    return usage


# ---------------------------------------------------------------------------
# Backend auto-detection
# ---------------------------------------------------------------------------
BACKEND_DEFAULTS = {
    "groq": {"model": "llama-3.3-70b-versatile", "key_env": "GROQ_API_KEY"},
    # cycle36: gemini-2.0-flash now 404s for this key tier. gemini-flash-latest is a
    # live, non-deprecating alias (verified generateContent OK). See _call_gemini fallbacks.
    "gemini": {"model": "gemini-flash-latest", "key_env": "GEMINI_API_KEY"},
    "anthropic": {"model": "claude-haiku-4-5", "key_env": "ANTHROPIC_API_KEY"},
}


# Which backends cost money. A fallback that silently reaches one of these turns a
# throttle into a bill — see fallback_chain().
PAID_BACKENDS = frozenset({"anthropic"})


def _configured(backend: str) -> bool:
    key = os.environ.get(BACKEND_DEFAULTS[backend]["key_env"], "").strip()
    return bool(key) and not key.endswith("...")


def fallback_chain() -> list:
    """The backends _chain_text may try, in order.

    MEASURED PROBLEM: the chain used to be `[primary] + every other backend`. With GROQ,
    GEMINI and ANTHROPIC keys all present that is groq -> gemini -> anthropic, so the
    moment both free tiers throttle — Groq 30 RPM, Gemini 15 RPM, and a full run makes
    LLM calls in bursts (four 4Ps sections in parallel, multi-perspective consumer
    research) — the run quietly continues on the PAID key and bills for it. Spending
    someone's money as a side effect of throttling is a surprise, not a fallback.

    So a paid backend is only in the chain when the operator has said so: explicitly via
    LLM_BACKEND, via LLM_ALLOW_PAID=1, or by configuring no free backend at all (where
    excluding it would turn a working setup into no LLM).

    Backends with no key are dropped: trying one burns an attempt and a whole-chain
    backoff for a call that cannot succeed.
    """
    primary, _ = _backend_and_model()
    allow_paid = (os.environ.get("LLM_ALLOW_PAID", "").strip().lower()
                  in ("1", "true", "yes"))
    explicit = os.environ.get("LLM_BACKEND", "").strip().lower()
    chain = [primary]
    for name in BACKEND_DEFAULTS:
        if name == primary or not _configured(name):
            continue
        if name in PAID_BACKENDS and not (allow_paid or explicit == name):
            continue
        chain.append(name)
    return chain


_EXHAUSTED: dict = {"count": 0, "reason": ""}


def reset_exhaustion() -> None:
    _EXHAUSTED["count"] = 0
    _EXHAUSTED["reason"] = ""


def note_exhaustion(reason: str) -> None:
    """Record that every backend refused one call.

    Callers turn an exhausted chain into a failed step, and a failed step degrades the
    report — a missing market scale, no customer voice. Without this the artifact cannot
    tell "we could not look" from "we looked and found nothing", which is the distinction
    this pipeline keeps having to relearn.
    """
    _EXHAUSTED["count"] += 1
    _EXHAUSTED["reason"] = reason or _EXHAUSTED["reason"]


def exhaustion_summary() -> dict:
    if not _EXHAUSTED["count"]:
        return {}
    return {"count": _EXHAUSTED["count"], "reason": _EXHAUSTED["reason"],
            "note": ("one or more steps failed because every configured LLM backend "
                     "refused the call (free-tier rate limits). Sections derived from "
                     "those steps are absent because they could not be COMPUTED, not "
                     "because the venture lacks signal.")}


def _detect_backend() -> str:
    """Auto-detect from available API keys. Priority: groq > gemini > anthropic."""
    explicit = os.environ.get("LLM_BACKEND", "").lower()
    if explicit and explicit in BACKEND_DEFAULTS:
        return explicit
    for name, cfg in BACKEND_DEFAULTS.items():
        key = os.environ.get(cfg["key_env"], "").strip()
        if key and not key.endswith("..."):
            return name
    from errors import AuthError
    raise AuthError(
        "No LLM API key found. Set one of:\n"
        "  GROQ_API_KEY      (free at https://console.groq.com)\n"
        "  GEMINI_API_KEY    (free at https://aistudio.google.com)\n"
        "  ANTHROPIC_API_KEY (paid at https://console.anthropic.com)"
    )


def _backend_and_model() -> tuple[str, str]:
    backend = _detect_backend()
    model_override = os.environ.get("CLAUDE_MODEL") or os.environ.get("LLM_MODEL")
    model = model_override or BACKEND_DEFAULTS[backend]["model"]
    return backend, model


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------
def _call_anthropic(system: str, user: str, max_tokens: int, model: str,
                    json_mode: bool = True) -> tuple[str, int, int]:
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model, max_tokens=max_tokens,
        temperature=0,  # F2: deterministic — same input → same number
        system=system, messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text, msg.usage.input_tokens, msg.usage.output_tokens


def _call_groq(system: str, user: str, max_tokens: int, model: str,
               json_mode: bool = True) -> tuple[str, int, int]:
    from groq import Groq
    key = os.environ.get("GROQ_API_KEY", "")
    client = Groq(api_key=key)
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        temperature=0, seed=42,  # F2: deterministic (Groq supports a seed)
        # Only when the caller wants JSON. call_text shares these backends, so a hardcoded
        # response_format meant the prose path was constrained to emit an object and
        # returned that raw text to be printed.
        **({"response_format": {"type": "json_object"}} if json_mode else {}),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    text = resp.choices[0].message.content or ""
    in_tok = getattr(resp.usage, "prompt_tokens", 0) or 0
    out_tok = getattr(resp.usage, "completion_tokens", 0) or 0
    return text, in_tok, out_tok


# Free-tier pacing. MEASURED on this machine: GROQ_API_KEY is empty, so the chain is
# Gemini ALONE at 15 RPM with no second free provider to absorb a throttle — the interval
# below is the entire rate budget, and the pipeline calls from ~8 ThreadPoolExecutor
# fan-outs (4Ps sections, evidence phase, place, discover, differentiators,
# competitor_pricing, run_labeled). A bare global read by N threads let every one of them
# decide "4s have passed" at the same instant and fire together, which is how a 15 RPM
# tier is exhausted in one breath — and why the LLM-dependent steps (market scale
# classification, customer voice) degraded while deterministic ones came through.
_GEMINI_MIN_INTERVAL = 4.0          # seconds between call STARTS (15 RPM free tier)
_gemini_last_call = 0.0             # guarded by _gemini_rate_lock
_gemini_rate_lock = threading.Lock()


def _gemini_reset_rate_state() -> None:
    """Test seam: forget the last call so a case starts from a clean budget."""
    global _gemini_last_call
    with _gemini_rate_lock:
        _gemini_last_call = 0.0


def _gemini_rate_gate() -> None:
    """Block until this thread may START a Gemini call, then claim the slot.

    The lock is held across BOTH the wait and the stamp, deliberately:
      - without it, concurrent threads read one timestamp and all pass the check;
      - stamping AFTER the API call (the previous behaviour) let every caller that
        arrived mid-flight measure its wait from the last call's END, so spacing
        collapsed exactly when the pipeline was busiest.
    Holding it serializes Gemini calls, which is correct — with one free provider at
    15 RPM there is no parallelism available, and pretending otherwise loses sections.
    """
    global _gemini_last_call
    with _gemini_rate_lock:
        elapsed = time.time() - _gemini_last_call
        if _gemini_last_call and elapsed < _GEMINI_MIN_INTERVAL:
            time.sleep(_GEMINI_MIN_INTERVAL - elapsed)
        _gemini_last_call = time.time()

# Recent non-transient backend failures, so an exhausted chain can say WHY (see
# _record_backend_failure). Bounded — this is a diagnostic, not a log.
_LAST_CHAIN_ERRORS: list[str] = []

def _gemini_client():
    """The genai client. Separated so the request-shaping logic below is testable without
    a live key or a network round trip."""
    from google import genai
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))


# Which models accept `thinking_config`, learned at runtime. Support is SPLIT across the
# models a free key can reach, and the two requirements conflict — measured live:
#   gemini-flash-latest / gemini-flash-lite-latest  -> 400 INVALID_ARGUMENT WITH it
#   gemini-3.5-flash                                -> emits ZERO characters WITHOUT it
# so no single static config works, and this is a per-model fact rather than a global one.
# Before this, `thinking_config` was sent unconditionally and the primary model rejected
# every request: the whole LLM layer returned _parse_error, reported to the user as
# "the model is busy (rate limit)".
_GEMINI_THINKING_OK: dict[str, bool] = {}


def _call_gemini(system: str, user: str, max_tokens: int, model: str,
                 json_mode: bool = True) -> tuple[str, int, int]:
    # (no `global _gemini_last_call` — the timestamp is owned by _gemini_rate_gate now,
    # which claims the slot under a lock BEFORE the call rather than after it.)
    client = _gemini_client()
    full_prompt = f"{system}\n\n{user}"

    _gemini_rate_gate()

    # Try multiple models on 429 — different models have separate quota pools.
    # cycle36: these are the models VERIFIED available for this key tier (gemini-2.0-flash
    # and gemini-2.5-flash now 404). Ordered live → fast-lite → quality backup.
    models_to_try = [model]
    fallbacks = ["gemini-flash-latest", "gemini-flash-lite-latest",
                 "gemini-2.5-flash-lite", "gemini-3.5-flash"]
    for fb in fallbacks:
        if fb != model and fb not in models_to_try:
            models_to_try.append(fb)

    # Iter 39: kill the 5s+15s retry on 429. Per-minute quota doesn't reset
    # in seconds — falling through to the next model in the chain is always
    # faster. Was burning ~20s × N-calls per plan.
    last_err = None
    for m in models_to_try:
      # Try the thinking-disabled form first unless this model has already rejected it.
      # `thinking_budget: 0` is load-bearing where supported (see cycle36 note below), so
      # it is dropped per model on evidence, never pre-emptively.
      for _use_thinking in ([True, False] if _GEMINI_THINKING_OK.get(m, True) else [False]):
        try:
            response = client.models.generate_content(
                model=m,
                contents=full_prompt,
                # cycle36 CRITICAL FIX: thinking_budget=0 disables internal "thinking".
                # The newer Gemini models (2.5+/3.x) spend the ENTIRE max_output_tokens
                # budget on hidden thinking tokens, leaving nothing for the JSON →
                # truncated/empty output → parse_error. This was the real root cause of
                # TAM $0 and intermittent dropped sections (any tight-budget call —
                # spend/households use max_tokens=60 — got eaten by thinking). Verified:
                # with thinking off, 60 tokens emits clean JSON; with it on, even 512
                # tokens truncates mid-object. Also faster + cheaper.
                config={**({"response_mime_type": "application/json"}
                           if json_mode else {}),
                        "max_output_tokens": max_tokens,
                        "temperature": 0,  # F2: deterministic — same input → same number
                        "seed": 42,
                        **({"thinking_config": {"thinking_budget": 0}}
                           if _use_thinking else {})},
            )
            text = response.text or ""
            usage_meta = getattr(response, "usage_metadata", None)
            in_tok = getattr(usage_meta, "prompt_token_count", 0) or 0
            out_tok = getattr(usage_meta, "candidates_token_count", 0) or 0
            _GEMINI_THINKING_OK[m] = _use_thinking
            return text, in_tok, out_tok
        except Exception as e:
            last_err = e
            err_str = str(e)
            if _use_thinking and ("INVALID_ARGUMENT" in err_str or "400" in err_str):
                # This model does not accept thinking_config. Retry it once WITHOUT, and
                # remember, so the wasted attempt is paid once rather than per call.
                _GEMINI_THINKING_OK[m] = False
                log.info("gemini %s rejects thinking_config — retrying without it", m)
                continue
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                log.info("gemini %s on %s — falling through to next model", "429", m)
                break
            if "503" in err_str or "UNAVAILABLE" in err_str:
                log.info("gemini %s on %s — falling through", "503", m)
                break
            if "404" in err_str or "NOT_FOUND" in err_str:
                log.warning("gemini model %s not available, skipping", m)
                break
            # Real unexpected error: bubble immediately
            raise

    raise last_err or RuntimeError("gemini: all models exhausted")


_BACKENDS = {
    "anthropic": _call_anthropic,
    "groq": _call_groq,
    "gemini": _call_gemini,
}


# ---------------------------------------------------------------------------
# Public API (unchanged interface)
# ---------------------------------------------------------------------------
def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return text


def _try_one_backend(backend: str, system: str, user: str, max_tokens: int,
                     tier: Optional[str] = None) -> tuple[str, int, int, str] | None:
    """Try a single backend. Return (text, in_tok, out_tok, model) on success, None on transient failure."""
    cfg = BACKEND_DEFAULTS.get(backend) or {}
    key = os.environ.get(cfg.get("key_env", ""), "").strip()
    if not key or key.endswith("..."):
        return None
    fn = _BACKENDS.get(backend)
    if not fn:
        return None
    # W5-3: tier routing. model_for() honours CLAUDE_MODEL/LLM_MODEL itself, and
    # resolves anything it doesn't recognise to the default — never downward.
    from model.tiering import model_for
    model = model_for(tier, backend, cfg.get("model", ""))
    try:
        text, in_tok, out_tok = fn(
            system + "\n\nCRITICAL: Return valid JSON only. No markdown fences, no commentary.",
            user, max_tokens, model, json_mode=True,   # explicit: this chain is the JSON path
        )
        return text, in_tok, out_tok, model
    except Exception as e:
        es = str(e).lower()
        # Treat as transient: rate-limit, server error, timeout, AND transient network
        # faults. cycle36: the free Gemini tier frequently drops the TLS connection
        # ("SSL: UNEXPECTED_EOF_WHILE_READING", "server disconnected", "connection reset"),
        # which were NOT matched here → treated as a HARD failure with no retry → with no
        # second provider key set, the whole call returned parse_error (TAM $0, dropped
        # report sections). Classifying them transient lets the outer retry recover them.
        if any(t in es for t in ("429", "503", "500", "502", "504", "timeout", "timed out",
                                  "rate limit", "resource_exhausted", "unavailable",
                                  "overloaded", "unexpected_eof", "ssl", "eof occurred",
                                  "server disconnected", "connection reset",
                                  "connection aborted", "remotedisconnected",
                                  "connection error", "broken pipe", "deadline")):
            log.info("[llm] %s transient failure (%s) — falling through", backend, str(e)[:120])
            return None
        # Non-transient: re-raise so caller can decide
        log.warning("[llm] %s hard failure: %s", backend, e)
        # Keep the reason so the caller's user-facing message can name the real cause.
        # A 400 is OUR malformed request; reporting it as "rate-limited" sends the reader
        # off to wait for a quota that was never the problem — which is exactly how a dead
        # LLM layer stayed hidden behind "the model is busy".
        _LAST_CHAIN_ERRORS.append(f"{backend}: {e}")
        del _LAST_CHAIN_ERRORS[:-8]
        return None


def _chain_text(system: str, user: str, max_tokens: int,
                tier: Optional[str] = None) -> Optional[str]:
    """Run the cross-provider chain (primary → others) with whole-chain backoff.
    Returns raw text, or None when every backend is exhausted.

    cycle31: cross-provider fallback on transient failures (429s, timeouts, 5xxs);
    each provider has its own keys so saturating one doesn't block the call.
    cycle36: retry the WHOLE chain with backoff. With a single provider configured,
    one transient SSL/network/429 used to return parse_error immediately → TAM $0,
    dropped 4Ps sections, thin signals. A few backoff'd attempts recover the vast
    majority of these transient free-tier failures.
    """
    primary, _ = _backend_and_model()
    chain = fallback_chain()

    _CHAIN_BACKOFF = (0.0, 3.0, 8.0, 15.0)  # backoff before attempts 1-4; free Gemini tier
    # can be unreachable for ~10s stretches, so a 4th attempt past that window matters
    for attempt, delay in enumerate(_CHAIN_BACKOFF):
        if delay:
            time.sleep(delay)
        for backend in chain:
            t0 = time.time()
            out = _try_one_backend(backend, system, user, max_tokens, tier)
            if out is None:
                continue
            text, in_tok, out_tok, model_used = out
            usage.add(model_used, in_tok, out_tok)
            try:
                import provenance as _trace
                _trace.record_llm(model_used, cached=False, in_tok=in_tok, out_tok=out_tok)
            except Exception:
                pass
            log.debug("call_json [%s/%s] %d→%d tok, %.1fs",
                      backend, model_used, in_tok, out_tok, time.time() - t0)
            if backend != primary:
                log.info("[llm] cross-provider fallback succeeded on %s (primary %s exhausted)",
                         backend, primary)
            return text
        if attempt < len(_CHAIN_BACKOFF) - 1:
            log.info("[llm] chain exhausted (attempt %d/%d) — backing off %.0fs and retrying",
                     attempt + 1, len(_CHAIN_BACKOFF), _CHAIN_BACKOFF[attempt + 1])
    log.warning("[llm] ALL backends exhausted after %d attempts (chain: %s)",
                len(_CHAIN_BACKOFF), ", ".join(chain))
    return None


def _cache_key(system: str, user: str, response_model: Optional[type] = None,
               tier: Optional[str] = None) -> str:
    """Cache key over the full prompt — and the schema fingerprint when validating,
    so a schema change never serves a stale shape from cache.

    W5-3: the tier is part of the key. The same prompt at two tiers is answered by two
    different models, so a shared slot would let a UTILITY-model answer be served as a
    REASONING one (or vice versa) depending only on which ran first.
    """
    import hashlib
    schema_part = ""
    if response_model is not None:
        schema_part = response_model.__name__ + json.dumps(
            response_model.model_json_schema(), sort_keys=True)
    from model.tiering import resolve_tier
    parts = (system, user, schema_part, resolve_tier(tier))
    digest = hashlib.sha256("|||".join(parts).encode()).hexdigest()[:16]
    return f"llm_json:{digest}"


def _json_tail_expectation(text: str) -> str | None:
    """Walk the JSON and report what it is still waiting for at the end.

    Returns "string" (inside an unterminated string), "value" (a value is required and absent),
    "colon" (a key was written with no separator), "key", "comma", or None when the text is
    structurally complete at the top level.

    WHY A SCANNER RATHER THAN MORE PATTERNS. _truncated_value below used to work purely by
    enumerating the shapes a cut VALUE can take, and a cut landing at a value POSITION was not
    in the list -- so `{"id": 2, "source":` was declared clean and json_repair fabricated
    `{"id": 2, "source": ""}`, dropping every field after the cut. That cost three consecutive
    unpublishable reports. Enumerating known-bad endings will keep having holes; asking the
    structure what it expects cannot.
    """
    in_str = esc = False
    stack: list[dict] = []          # {"kind": "{"|"[", "expect": key|colon|value|comma}
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                if stack:
                    top = stack[-1]
                    if top["kind"] == "{" and top["expect"] == "key":
                        top["expect"] = "colon"      # a key just closed; ':' must follow
                    elif top["expect"] == "value":
                        top["expect"] = "comma"      # a string value satisfied the slot
            continue
        if ch == '"':
            in_str = True
            continue
        if ch.isspace():
            continue
        if ch in "{[":
            if stack and stack[-1]["expect"] == "value":
                stack[-1]["expect"] = "comma"        # this container IS the awaited value
            stack.append({"kind": ch, "expect": "key" if ch == "{" else "value"})
            continue
        if ch in "}]":
            if stack:
                stack.pop()
            if stack and stack[-1]["expect"] == "value":
                stack[-1]["expect"] = "comma"
            continue
        if ch == ":":
            if stack and stack[-1]["kind"] == "{":
                stack[-1]["expect"] = "value"
            continue
        if ch == ",":
            if stack:
                stack[-1]["expect"] = "key" if stack[-1]["kind"] == "{" else "value"
            continue
        # Any other character belongs to a number or a bare keyword, which fills a value slot.
        if stack and stack[-1]["expect"] == "value":
            stack[-1]["expect"] = "comma"
    if in_str:
        return "string"
    return stack[-1]["expect"] if stack else None


def _truncated_value(text: str) -> str | None:
    """Name the in-progress value this text ends inside, or None if it ends cleanly.

    json_repair happily completes a value that was cut off mid-write, and the result is
    valid JSON carrying a number the model never wrote. Measured on the installed version:

        '{"households": 8872, "radius_m": 30'  -> radius_m = 30      (was writing 3000)
        '{"tam_usd": 1234567, "sam_usd": 98'   -> sam_usd  = 98      (was writing 98000000)
        '{"price": 12.'                        -> price    = 12.0
        '{"tam": 2.5e'                         -> tam      = 2.5

    Each returns with error=None and flows into the report as a figure. A 30 m radius in
    place of 3,000 m is the 372x county-scale class of error by another route.

    A bare trailing number CANNOT be recovered: '{"tam_usd": 1234567' is indistinguishable
    from a complete value that lost its brace and a truncated 12345670. The text does not
    carry the information needed to tell them apart, so the only honest move is to decline
    and let call_json's existing retry re-ask. One extra call is a trivial price.

    Structural damage AFTER a provably complete value — a missing brace following a closed
    string, `true`/`false`/`null`, or a closed bracket, plus trailing commas — stays
    salvageable, because nothing there is ambiguous."""
    t = text.rstrip()
    if not t:
        return None
    # An odd number of unescaped quotes means a string is still open.
    unescaped = len(re.findall(r'(?<!\\)"', t))
    if unescaped % 2:
        return "an unterminated string"
    # These must be NUMBER-aware, not character-aware. A bare trailing `e` is the last
    # letter of `true`, and a bare trailing `.` is the end of an English sentence — treating
    # either as a cut number reports prose and booleans as truncated.
    if re.search(r'\d$', t):
        # A digit at the very end: either a finished number missing its delimiter, or a
        # number cut mid-write. Indistinguishable, therefore untrusted.
        return "a number with no closing delimiter"
    if re.search(r'\d\.$', t):
        return "a number cut after its decimal point"
    if re.search(r'\d(?:\.\d*)?[eE][-+]?$', t):
        return "a number cut inside its exponent"
    if re.search(r'[:\[,]\s*[-+]$', t):
        return "a number that is only a sign"
    if re.search(r'[:\[,]\s*(?:tru|fals|nul)$', t):
        return "a truncated keyword"
    # A cut at a value POSITION: the key exists and its value does not. MEASURED — this is the
    # hole that let run4's product payload through. It ended at `{"id": 2, "source":`, the guard
    # said clean, and json_repair invented `"source": ""` while silently dropping `narrative`
    # and everything after the cut. Two of the four citations the model was writing survived,
    # orphaning two superscript markers in the prose, and THAT was the dangling_citations BLOCK
    # on run5, run6 and run7. A trailing comma is deliberately NOT included: a comma means
    # another pair was coming and none was written, so dropping it fabricates nothing, whereas
    # a colon leaves a pair that exists missing its value.
    expectation = _json_tail_expectation(t)
    if expectation == "value":
        return "a key with no value"
    if expectation == "colon":
        return "a key with no value separator"
    return None


def _parse_payload_ex(text: str) -> tuple[Optional[object], Optional[str], bool]:
    """Fences → json.loads → json_repair salvage. Returns (obj, error, repaired).

    `repaired` is True when the object only exists because json_repair patched the text. Callers
    must NOT persist a repaired result: MEASURED, call_json cached one and cache.py's 7-day TTL
    replayed it into three consecutive runs, turning a single truncated response into three
    unpublishable reports whose product section was byte-identical.
    """
    text = _strip_fences(text)
    try:
        return json.loads(text), None, False
    except json.JSONDecodeError as e:
        cut = _truncated_value(text)
        if cut:
            # Refuse rather than commit a fabricated value. The caller retries.
            log.warning("call_json: output ends in %s — refusing the json_repair salvage "
                        "rather than publish a value the model did not write (likely a "
                        "max_tokens cutoff)", cut)
            return None, f"response truncated: ends in {cut} ({e})", False
        try:
            import json_repair
            result = json_repair.loads(text)
            if result and isinstance(result, (dict, list)):
                log.warning("call_json: json_repair repaired structure after a complete "
                            "final value — usable for this call, NOT cached")
                return result, None, True
        except ImportError:
            log.debug("json_repair not installed, parse error will propagate")
        except Exception:
            pass
        return None, str(e), False


def _parse_payload(text: str) -> tuple[Optional[object], Optional[str]]:
    """Two-tuple shim over _parse_payload_ex, kept for existing callers and tests."""
    obj, error, _repaired = _parse_payload_ex(text)
    return obj, error


def call_json(system: str, user: str, max_tokens: int = 2000,
              response_model: Optional[type] = None, max_retries: int = 2,
              tier: Optional[str] = None, memory=None) -> dict:
    """
    Call the configured LLM backend with JSON mode, through the cross-provider chain.

    W1 (H-plan D2-3): structured output with auto re-ask. With `response_model` (a
    Pydantic BaseModel class), the schema is shown to the model, the response is
    validated, and a malformed/invalid response triggers a corrective RE-ASK carrying
    the exact validation error — up to `max_retries` times — before the last-resort
    `_parse_error` dict. Schemaless calls keep their old shape but also get one
    corrective re-ask on unparseable output instead of failing immediately.
    Validated results are returned as plain dicts (model_dump), so callers keep the
    dict interface, and `.get("_parse_error")` checks continue to work.
    """
    import os
    from cache import get as cache_get, put as cache_put
    # cycle31-r3: LLM_CACHE_BYPASS=1 disables read+write of cache, used for
    # statistical sampling so each --samples N run gets fresh LLM responses
    # and we measure real variance instead of cache hits.
    bypass = os.environ.get("LLM_CACHE_BYPASS", "").strip() in ("1", "true", "yes")
    # W5-4: standing context goes in FRONT of the system prompt, and therefore into
    # the cache key — two runs with different established facts are different calls.
    # Passed explicitly rather than read from a global: the pipeline runs several
    # ventures concurrently in one process, and a shared global would leak one
    # venture's established facts into another's prompts.
    if memory is not None:
        system = memory.apply(system)
    cache_key = _cache_key(system, user, response_model, tier)
    if not bypass:
        cached = cache_get(cache_key)
        if cached is not None:
            log.debug("call_json cache HIT %s", cache_key)
            try:
                import provenance as _trace
                _trace.record_llm("cache", cached=True)
            except Exception:
                pass
            return cached

    system_full = system
    if response_model is not None:
        system_full = (
            system + "\n\nYour JSON response MUST match exactly this JSON Schema:\n"
            + json.dumps(response_model.model_json_schema(), sort_keys=True)
        )

    attempts = 1 + (max_retries if response_model is not None else 1)
    error: Optional[str] = None
    text: str = ""
    for attempt in range(attempts):
        if attempt == 0:
            user_msg = user
        else:
            log.info("[llm] corrective re-ask %d/%d: %s", attempt, attempts - 1,
                     (error or "")[:160])
            user_msg = (
                f"{user}\n\nYour previous response was invalid — {error}\n"
                f"Previous response:\n{text[:1500]}\n\n"
                "Return ONLY the corrected JSON. No prose, no fences."
            )
        raw = _chain_text(system_full, user_msg, max_tokens, tier)
        if raw is None:
            _why = ("invalid request rejected by every backend (a 400 — this is a bug in "
                    "the request, not throttling; see the llm log)"
                    if any("INVALID_ARGUMENT" in str(x) or "400" in str(x)
                           for x in _LAST_CHAIN_ERRORS)
                    else "rate-limited or unavailable")
            note_exhaustion(_why)
            return {"_parse_error": f"all backends exhausted ({_why})",
                    "_raw": "", "_chain_tried": fallback_chain()}
        text = raw
        obj, error, repaired = _parse_payload_ex(text)
        if obj is None:
            continue
        if response_model is not None:
            try:
                from pydantic import ValidationError
                result: dict = response_model.model_validate(obj).model_dump()
            except Exception as e:
                error = str(e)
                continue
        else:
            result = obj
        # A REPAIRED PARSE IS USABLE ONCE, NEVER PERSISTED. Caching one is how a single
        # truncated run4 response became the dangling_citations BLOCK on run5, run6 AND run7 —
        # the salvaged dict (missing `narrative`, carrying a json_repair-invented empty
        # citation source) sat in .cache.sqlite under a 7-day TTL and every later run replayed
        # it. Returning it to THIS caller is fine; freezing it for a week is not.
        if not bypass and not repaired:
            cache_put(cache_key, result)
        elif repaired:
            log.warning("call_json: not caching a json_repair-salvaged result — a repaired "
                        "parse must not be replayed by later runs")
        return result

    log.warning("call_json: invalid after %d attempt(s): %s", attempts, (error or "")[:200])
    return {"_parse_error": f"invalid after {attempts} attempt(s): {error}",
            "_raw": _strip_fences(text)[:2000]}


def call_text(system: str, user: str, max_tokens: int = 2000,
              tier: Optional[str] = None, memory=None) -> str:
    if memory is not None:
        system = memory.apply(system)
    backend, model = _backend_and_model()
    from model.tiering import model_for
    model = model_for(tier, backend, model)
    fn = _BACKENDS[backend]
    # json_mode=False: this is the prose path. Groq and Gemini both hardcoded structured
    # output, so every call_text caller -- agents/synthesis.py's research_brief, which the
    # report renders verbatim -- was asking a model constrained to emit a JSON object for a
    # paragraph. Invisible on Anthropic, which never set a response format, which is how it
    # went unnoticed.
    text, in_tok, out_tok = fn(system, user, max_tokens, model, json_mode=False)
    usage.add(model, in_tok, out_tok)
    return text
