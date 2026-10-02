#!/bin/sh
# All runtime mutations are confined to fresh, labelled local acceptance resources.
set -eu
exec python3 "$(dirname "$0")/run_acceptance.py" "$@"
