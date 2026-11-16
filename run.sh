#!/usr/bin/env bash
# Starts gazehop from its virtual environment. Arguments are passed through, e.g. ./run.sh doctor
cd "$(dirname "$0")"
[[ -x .venv/bin/python ]] || { echo "Run ./install.sh first."; exit 1; }
exec .venv/bin/python -m gazehop "$@"
