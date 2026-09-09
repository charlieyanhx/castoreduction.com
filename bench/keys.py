"""bench/keys.py -- run this one call against a key you supply, not the one in .env.

WHY IT IS NOT JUST `GEMINI_API_KEY=x ./bench.sh ...`. That works, and it is what you
should do in a script. Two things it does not do, and both matter when you are testing
one agent by hand:

  IT DOES NOT PIN THE BACKEND. `.env` here configures Gemini, so exporting an Anthropic
  key gets you a chain of [gemini, ...] and your key is never reached. You would read a
  Gemini answer and believe it came from the key you supplied. Supplying a key sets
  LLM_BACKEND to that provider, so the answer comes from the key you named.

  IT PUTS THE SECRET IN YOUR SHELL HISTORY. `--key gemini` with no value asks for it on
  the terminal without echoing, which leaves nothing in history and nothing in `ps` for
  anyone else on the machine to read. `--key gemini=AIza...` is still accepted, because
  sometimes you are in a scratch shell and do not care, and it warns once.

A PAID BACKEND NEEDS A SECOND YES. llm.fallback_chain refuses to reach a paid provider
without an explicit opt-in, on the grounds that spending money as a side effect of a
throttle is a surprise rather than a fallback. Naming a paid key is one deliberate act,
so this asks for --allow-paid as the second: the difference between "I have this key"
and "bill it".

The key is never logged, never printed, and never written to out/bench/last.json. What
is printed is the provider and the last four characters, which is enough to tell two of
your own keys apart and not enough to be one.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional


class KeyRefused(ValueError):
    """The supplied key cannot be used as asked. Carries the reason, for the CLI."""


def _providers() -> dict:
    import llm
    return llm.BACKEND_DEFAULTS


def parse(spec: str) -> tuple[str, Optional[str]]:
    """'gemini' -> ('gemini', None); 'gemini=AIza...' -> ('gemini', 'AIza...')."""
    provider, _, value = spec.partition("=")
    provider = provider.strip().lower()
    known = _providers()
    if provider not in known:
        raise KeyRefused(f"{provider!r} is not a provider; try one of "
                         f"{', '.join(sorted(known))}")
    return provider, (value.strip() or None)


def fingerprint(value: str) -> str:
    """Enough to tell two keys apart, not enough to be one."""
    return f"...{value[-4:]}" if len(value) > 8 else "...(short)"


@contextmanager
def supplied_key(spec: Optional[str], allow_paid: bool = False) -> Iterator[Optional[str]]:
    """Install a key for the duration of the block; restore the environment exactly.

    Yields a printable description of what was installed, or None when no key was given
    and the environment is untouched.
    """
    if not spec:
        yield None
        return

    import llm

    provider, value = parse(spec)
    if provider in llm.PAID_BACKENDS and not allow_paid:
        raise KeyRefused(
            f"{provider} bills per call. Add --allow-paid to say so deliberately.")

    if value is None:
        import getpass
        value = getpass.getpass(f"{provider} API key (not echoed): ").strip()
    else:
        print("note: a key on the command line lands in your shell history. "
              f"`--key {provider}` on its own asks for it instead.")
    if not value:
        raise KeyRefused("no key given")

    key_env = _providers()[provider]["key_env"]
    # Restore EXACTLY: a variable that was absent must go back to absent, not to "".
    # Anything else leaves the process believing it has a key it does not have.
    previous = {name: os.environ.get(name) for name in (key_env, "LLM_BACKEND")}
    os.environ[key_env] = value
    os.environ["LLM_BACKEND"] = provider
    try:
        yield f"{provider} {fingerprint(value)}"
    finally:
        for name, was in previous.items():
            if was is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = was
