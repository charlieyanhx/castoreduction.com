"""Session-wide pytest isolation: point the jobs DB at a fresh temp file so no
test run can ever read or write the production .jobs.sqlite. jobs._db_path()
resolves JOBS_DB_PATH per connection, so this works regardless of import order.
Individual files may override with their own temp path (test_api.py does)."""
import os
import tempfile

_tmp = tempfile.NamedTemporaryFile(prefix="castor-test-jobs-", suffix=".sqlite",
                                   delete=False)
_tmp.close()
os.environ["JOBS_DB_PATH"] = _tmp.name


# Geocode-cache isolation (P5 follow-through): geocode_address caches to disk, and a
# suite full of MOCKED geocodes was writing fake coordinates into the production cache
# — seven poisoned entries measured on 2026-08-20 — which live runs would then serve.
# Same contract as the jobs DB above: tests get a throwaway dir, always.
_geo_tmp = tempfile.mkdtemp(prefix="castor-test-geocache-")
os.environ["CASTOR_GEO_CACHE_DIR"] = _geo_tmp


# ------------------------------------------------------- no real provider credentials
# THE ROOT CAUSE of "fast alone, slow in the full suite", and it is one line of import
# side effect: api.py:71 calls load_dotenv() when it is imported. Any test that touches
# api (test_api, test_jobs_are_owned, and a dozen others) therefore loads the REAL
# GEMINI/GROQ/ANTHROPIC keys into os.environ for the rest of the session, and from that
# moment every later test that reaches llm.call_json without a mock makes a live, billable
# call, pays llm._gemini_rate_gate's 4s slot and then up to 26s of whole-chain backoff.
#
# Run that same file on its own and nothing imported api, so there are no keys,
# fallback_chain() refuses immediately and the file looks perfectly healthy. That is why
# the defect survived: the per-file workflow every developer actually uses cannot see it.
#
# So the keys are removed from the environment for the whole session, and load_dotenv is
# neutered so importing api cannot put them back. A test needing a live provider must be
# explicit about it, and none currently is.
# CASTOR_TEST_ALLOW_LIVE=1 keeps the credentials, for the rare deliberate live run
# (paired with @pytest.mark.allow_network). Without it there is nothing to spend.
_LLM_KEY_VARS = ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
_ALLOW_LIVE = os.environ.get("CASTOR_TEST_ALLOW_LIVE") == "1"
if not _ALLOW_LIVE:
    for _k in _LLM_KEY_VARS:
        os.environ.pop(_k, None)

try:
    import dotenv as _dotenv

    def _no_dotenv(*a, **k):
        """A test process does not read .env. See the note above."""
        return False

    if not _ALLOW_LIVE:
        _dotenv.load_dotenv = _no_dotenv
        if hasattr(_dotenv, "main") and hasattr(_dotenv.main, "load_dotenv"):
            _dotenv.main.load_dotenv = _no_dotenv
except ImportError:
    pass


# ---------------------------------------------------------------- no live network
# MEASURED: an unguarded full run held open sockets to yandex.ru, wikimedia, yahoo and
# cloudfront, and a faulthandler dump caught the real cost, four_ps._run_section calling
# the LIVE Gemini backend and every worker thread parked in llm._gemini_rate_gate, which
# sleeps 4s per call to respect the 15 RPM free tier. .env holds real keys, so the suite
# was spending quota and, on a paid chain, money.
#
# The reason it stayed invisible: the offenders pass in isolation. A file that makes live
# calls inside the full suite is fast on its own, because the llm cache answers whatever
# an earlier test already asked. So "run the one file you touched" cannot find this class
# of defect, and only a guard that is always on can.
#
# Loopback stays open: TestClient and any local fixture server are not the thing being
# prevented. Mark a test @pytest.mark.allow_network if it genuinely must reach out.
import socket as _socket

import pytest as _pytest

_REAL_CONNECT = _socket.socket.connect


class NetworkUseInTest(OSError):
    """A test tried to open a socket to the outside world.

    An OSError, which is what an unplugged network actually raises: requests wraps it into
    requests.ConnectionError and scrape.http returns None, so every caller takes the
    offline branch it already has. That is the behaviour being asserted by the tests that
    reach out, and it is now deterministic instead of dependent on somebody's wifi.

    It was briefly a BaseException so no broad handler could swallow it. That made the
    guard loud but made ~20 tests fail for the wrong reason: they HANDLE being offline and
    were simply never given the chance to prove it. Retries are disabled below so taking
    the offline branch is instant rather than 14s of tenacity backoff.

    Inherits BaseException, NOT Exception, and that is the whole point. Every layer
    between the socket and the test catches broad Exception and converts it to None or a
    skeleton (scrape.http returns None, llm._try_one_backend returns None and the chain
    then sleeps through 0+3+8+15s of backoff). A guard those handlers can swallow does not
    fail the test, it just makes it slow and silent. This one cannot be caught by accident.

    The fix is almost never to allow it. It is to patch the seam the test meant to
    exercise, which is usually llm.call_json or the tool/source function, not the socket.
    """


def _is_local(addr) -> bool:
    """Is this address on this machine? Loopback is allowed through: TestClient and a
    local fixture server are not what the guard exists to prevent."""
    try:
        host = addr[0] if isinstance(addr, tuple) else str(addr)
    except Exception:
        return False
    return isinstance(host, str) and (
        host.startswith("127.") or host in ("::1", "localhost", "0.0.0.0"))


def _blocked_connect(self, addr, *a, **k):
    """socket.connect, minus the outside world."""
    if _is_local(addr):
        return _REAL_CONNECT(self, addr, *a, **k)
    raise NetworkUseInTest(
        f"live network connect to {addr!r} during a test. Patch the seam the test is "
        f"about (the tool or source function) rather than letting the suite reach the "
        f"internet. If this test truly needs the network, mark it "
        f"@pytest.mark.allow_network.")


@_pytest.fixture(autouse=True)
def _no_live_network(request):
    """Refuse non-loopback sockets, and stop tenacity retrying the refusal."""
    if request.node.get_closest_marker("allow_network"):
        yield
        return
    import net
    _prev_retries = net.DEFAULT_MAX_RETRIES
    # Retrying a network that is deliberately absent only buys 2+4+8s of tenacity sleep
    # per call for an answer that cannot change.
    net.DEFAULT_MAX_RETRIES = 0
    _socket.socket.connect = _blocked_connect
    try:
        yield
    finally:
        _socket.socket.connect = _REAL_CONNECT
        net.DEFAULT_MAX_RETRIES = _prev_retries


def pytest_configure(config):
    """Register the opt-out marker so `-W error::pytest.PytestUnknownMarkWarning` stays
    usable and `--strict-markers` does not reject it."""
    config.addinivalue_line(
        "markers", "allow_network: test may open sockets to the outside world")


# -------------------------------------------------------------- no live LLM backends
# Blocking sockets is NOT enough on its own, and the reason is worth writing down:
# llm._gemini_rate_gate sleeps BEFORE it calls out, to respect the 15 RPM free tier. So a
# test whose socket is refused still pays the 4 second wait, and the suite stays slow
# while looking like it is offline.
#
# The seam is the backend callable itself. Anything that mocks llm.call_json, replaces
# llm._BACKENDS, or patches _try_one_backend is untouched by this, because it never
# reaches the real provider function. What DOES reach it is a test that meant to exercise
# a pure code path and pulled the whole LLM chain in behind it, which is the defect.
_REAL_BACKENDS: dict = {}


def _offline_backend(system, user, max_tokens, model=None, json_mode=True, *a, **k):
    """Stand in for a provider, deterministically and instantly.

    NOT a refusal. These tests are about plumbing (does the prompt carry the right
    directive, does the figure keep its provenance, does the section degrade honestly),
    and they were reaching a real model for an answer they never assert on. A test that
    DID depend on specific live model text would be flaky by construction, so substituting
    a fixed empty payload cannot weaken a correct assertion, and it removes the 4s rate
    gate and the 26s whole-chain backoff from every one of them.

    Returns the (text, in_tok, out_tok) shape the real backends return. "{}" is a valid
    JSON object, so call_json parses it and the caller takes its own not-enough-data
    path, which is the branch the offline suite should be exercising anyway.
    """
    return ("{}", 0, 0)


@_pytest.fixture(autouse=True)
def _no_live_llm(request):
    """Swap every real provider for the offline stub, for the duration of one test.

    Restores the originals afterwards so a test that deliberately inspects llm._BACKENDS
    still sees the real table outside its own run.
    """
    if request.node.get_closest_marker("allow_network"):
        yield
        return
    import llm
    if not _REAL_BACKENDS:
        _REAL_BACKENDS.update(llm._BACKENDS)
    llm._BACKENDS.update({k: _offline_backend for k in _REAL_BACKENDS})
    try:
        yield
    finally:
        llm._BACKENDS.update(_REAL_BACKENDS)
