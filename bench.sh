#!/usr/bin/env bash
# Exercise one registered capability, or all 63, without building a report.
#
# The venv is not optional: bare `python` on this machine is the anaconda base, which is
# missing this project's dependencies and turns a working bench into fifty import errors.
# Same reason test_all.sh pins it.
#
#   ./bench.sh                              what you can run
#   ./bench.sh hackernews_mentions          one capability, on its own
#   ./bench.sh reddit_mentions query=coffee  ... with your own arguments
#   ./bench.sh smoke                        all of them, ~80s, no tokens
#   ./bench.sh smoke --kind tool            just the tool surface
#
# Unlike test_all.sh this DOES reach the network and, with --llm real, a model. That is
# the point: the offline suite proves the wiring, this proves the sources still answer.
set -e
cd "$(dirname "$0")"
exec .venv/bin/python -m bench "$@"
