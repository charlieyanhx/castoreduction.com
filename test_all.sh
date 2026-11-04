#!/usr/bin/env bash
# Run the whole test suite. Exit non-zero if anything fails.
#
# This used to invoke three files by hand (test_infra, test_integration, test_api) and
# print "all tests passed" on the way out. The suite is 251 files, so the green tick
# covered a little over 1% of it while reading as a full pass. Run pytest, which collects
# everything, and let its exit code be the answer.
#
# The suite is OFFLINE BY DEFAULT: conftest.py scrubs provider credentials, neuters
# load_dotenv, refuses non-loopback sockets and swaps the LLM backends for a deterministic
# stub. Nothing here bills an API or depends on a network. See conftest.py for why, and
# for the CASTOR_TEST_ALLOW_LIVE=1 escape hatch a deliberate live run needs.
set -e
cd "$(dirname "$0")"
exec .venv/bin/python -m pytest "$@"
