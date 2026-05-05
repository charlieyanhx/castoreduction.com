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
    "gemini": {"model": "gemini-2.0-flash", "key_env": "GEMINI_API_KEY"},
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
        system=system, messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text, msg.usage.input_tokens, msg.usage.output_tokens


def _call_groq(system: str, user: str, max_tokens: int, model: str) -> tuple[str, int, int]:
    from groq import Groq
    key = os.environ.get("GROQ_API_KEY", "")
    client = Groq(api_key=key)
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
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

    # Try multiple models on 429 — different models have separate quota pools
    models_to_try = [model]
    fallbacks = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-flash-lite"]
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
                config={"response_mime_type": "application/json", "max_output_tokens": max_tokens},
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


def _try_one_backend(backend: str, system: str, user: str, max_tokens: int) -> tuple[str, int, int, str] | None:
    """Try a single backend. Return (text, in_tok, out_tok, model) on success, None on transient failure."""
    cfg = BACKEND_DEFAULTS.get(backend) or {}
    key = os.environ.get(cfg.get("key_env", ""), "").strip()
    if not key or key.endswith("..."):
        return None
    fn = _BACKENDS.get(backend)
    if not fn:
        return None
    model_override = os.environ.get("CLAUDE_MODEL") or os.environ.get("LLM_MODEL")
    model = model_override or cfg.get("model", "")
    try:
        text, in_tok, out_tok = fn(
            system + "\n\nCRITICAL: Return valid JSON only. No markdown fences, no commentary.",
            user, max_tokens, model,
        )
        return text, in_tok, out_tok, model
    except Exception as e:
        es = str(e).lower()
        # Treat as transient: rate-limit, server error, timeout
        if any(t in es for t in ("429", "503", "timeout", "timed out", "rate limit",
                                  "resource_exhausted", "unavailable", "overloaded")):
            log.info("[llm] %s transient failure (%s) — falling through", backend, str(e)[:120])
            return None
        # Non-transient: re-raise so caller can decide
        log.warning("[llm] %s hard failure: %s", backend, e)
        return None


def call_json(system: str, user: str, max_tokens: int = 2000) -> dict:
    """
    Call the configured LLM backend with JSON mode. Strips markdown fences.
    cycle31: cross-provider fallback — primary backend → Groq → Gemini → Anthropic
    on transient failures (429s, timeouts, 5xxs). Each provider has its own keys
    so saturating one doesn't block the call.
    """
    import hashlib, os
    from cache import get as cache_get, put as cache_put
    # cycle31-r3: LLM_CACHE_BYPASS=1 disables read+write of cache, used for
    # statistical sampling so each --samples N run gets fresh LLM responses
    # and we measure real variance instead of cache hits.
    bypass = os.environ.get("LLM_CACHE_BYPASS", "").strip() in ("1", "true", "yes")
    prompt_hash = hashlib.sha256((system + "|||" + user).encode()).hexdigest()[:16]
    cache_key = f"llm_json:{prompt_hash}"
    if not bypass:
        cached = cache_get(cache_key)
        if cached is not None:
            log.debug("call_json cache HIT %s", cache_key)
            return cached

    primary, _ = _backend_and_model()
    # Build fallback chain: primary first, then the others in default priority order
    chain = [primary] + [b for b in BACKEND_DEFAULTS.keys() if b != primary]

    text = None; in_tok = 0; out_tok = 0; model_used = ""
    for backend in chain:
        t0 = time.time()
        out = _try_one_backend(backend, system, user, max_tokens)
        if out is None:
            continue
        text, in_tok, out_tok, model_used = out
        dur = time.time() - t0
        usage.add(model_used, in_tok, out_tok)
        log.debug("call_json [%s/%s] %d→%d tok, %.1fs", backend, model_used, in_tok, out_tok, dur)
        if backend != primary:
            log.info("[llm] cross-provider fallback succeeded on %s (primary %s exhausted)", backend, primary)
        break

    if text is None:
        log.warning("[llm] ALL backends exhausted — returning parse_error")
        return {"_parse_error": "all backends exhausted (rate-limited or unavailable)",
                "_raw": "", "_chain_tried": chain}

    text = _strip_fences(text)
    try:
        result = json.loads(text)
        if not bypass:
            cache_put(cache_key, result)
        return result
    except json.JSONDecodeError as e:
        try:
            import json_repair
            result = json_repair.loads(text)
            if result and isinstance(result, (dict, list)):
                log.warning("call_json: json_repair salvaged malformed output")
                if not bypass:
                    cache_put(cache_key, result)
                return result
        except ImportError:
            log.debug("json_repair not installed, parse error will propagate")
        except Exception:
            pass
        log.warning("call_json: JSON parse failed: %s", e)
        return {"_parse_error": str(e), "_raw": text[:2000]}


def call_text(system: str, user: str, max_tokens: int = 2000) -> str:
    backend, model = _backend_and_model()
    fn = _BACKENDS[backend]
    text, in_tok, out_tok = fn(system, user, max_tokens, model)
    usage.add(model, in_tok, out_tok)
    return text
