#!/usr/bin/env bash
# Run every Go fuzz target for a duration or execution count (default 2s), one at a time.
# Usage: bash scripts/ci/fuzz.sh [fuzztime]
set -euo pipefail

fuzztime="${1:-2s}"
found=0
for pkg in $(go list ./...); do
  for target in $(go test "$pkg" -list '^Fuzz' | grep '^Fuzz' || true); do
    found=1
    echo "== $pkg $target ($fuzztime)"
    go test "$pkg" -run='^$' -fuzz="^${target}\$" -fuzztime="$fuzztime" -parallel=2
  done
done
if [ "$found" -eq 0 ]; then
  echo "no fuzz targets found" >&2
  exit 1
fi
