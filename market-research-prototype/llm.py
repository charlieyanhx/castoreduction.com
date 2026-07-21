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
import os
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
def _call_anthropic(system: str, user: str, max_tokens: int, model: str) -> tuple[str, int, int]:
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model, max_tokens=max_tokens,
        temperature=0,  # F2: deterministic — same input → same number
        system=system, messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text, msg.usage.input_tokens, msg.usage.output_tokens


def _call_groq(system: str, user: str, max_tokens: int, model: str) -> tuple[str, int, int]:
    from groq import Groq
    key = os.environ.get("GROQ_API_KEY", "")
    client = Groq(api_key=key)
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        temperature=0, seed=42,  # F2: deterministic (Groq supports a seed)
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    text = resp.choices[0].message.content or ""
    in_tok = getattr(resp.usage, "prompt_tokens", 0) or 0
    out_tok = getattr(resp.usage, "completion_tokens", 0) or 0
    return text, in_tok, out_tok


_gemini_last_call = 0  # rate limiter timestamp

def _call_gemini(system: str, user: str, max_tokens: int, model: str) -> tuple[str, int, int]:
    global _gemini_last_call
    from google import genai
    key = os.environ.get("GEMINI_API_KEY", "")
    client = genai.Client(api_key=key)
    full_prompt = f"{system}\n\n{user}"

    # Rate limiter — at least 4s between calls (free tier = 15 RPM)
    elapsed = time.time() - _gemini_last_call
    if elapsed < 4:
        time.sleep(4 - elapsed)

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
        try:
            _gemini_last_call = time.time()
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
                config={"response_mime_type": "application/json",
                        "max_output_tokens": max_tokens,
                        "temperature": 0,  # F2: deterministic — same input → same number
                        "seed": 42,
                        "thinking_config": {"thinking_budget": 0}},
            )
            text = response.text or ""
            usage_meta = getattr(response, "usage_metadata", None)
            in_tok = getattr(usage_meta, "prompt_token_count", 0) or 0
            out_tok = getattr(usage_meta, "candidates_token_count", 0) or 0
            return text, in_tok, out_tok
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                log.info("gemini %s on %s — falling through to next model", "429", m)
                continue
            if "503" in err_str or "UNAVAILABLE" in err_str:
                log.info("gemini %s on %s — falling through", "503", m)
                continue
            if "404" in err_str or "NOT_FOUND" in err_str:
                log.warning("gemini model %s not available, skipping", m)
                continue
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
            user, max_tokens, model,
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
    chain = [primary] + [b for b in BACKEND_DEFAULTS.keys() if b != primary]

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
    log.warning("[llm] ALL backends exhausted after %d attempts", len(_CHAIN_BACKOFF))
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


def _parse_payload(text: str) -> tuple[Optional[object], Optional[str]]:
    """Fences → json.loads → json_repair salvage. Returns (obj, None) or (None, error)."""
    text = _strip_fences(text)
    try:
        return json.loads(text), None
    except json.JSONDecodeError as e:
        try:
            import json_repair
            result = json_repair.loads(text)
            if result and isinstance(result, (dict, list)):
                log.warning("call_json: json_repair salvaged malformed output")
                return result, None
        except ImportError:
            log.debug("json_repair not installed, parse error will propagate")
        except Exception:
            pass
        return None, str(e)


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
            return {"_parse_error": "all backends exhausted (rate-limited or unavailable)",
                    "_raw": "", "_chain_tried": list(BACKEND_DEFAULTS)}
        text = raw
        obj, error = _parse_payload(text)
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
        if not bypass:
            cache_put(cache_key, result)
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
    text, in_tok, out_tok = fn(system, user, max_tokens, model)
    usage.add(model, in_tok, out_tok)
    return text
