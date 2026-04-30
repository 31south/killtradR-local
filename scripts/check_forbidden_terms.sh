#!/usr/bin/env bash
set -euo pipefail

status=0

for file in "$@"; do
  [[ -f "$file" ]] || continue

  if grep -IniE 'anthropic|claude|\bmock\b|\bfake\b|\bsimulated\b|\bsynthetic\b|\bdummy\b' "$file"; then
    cat <<'MSG' >&2

Forbidden provider/data term found in source.
Review carefully before bypassing. If this is a false positive, use --no-verify only after confirming it does not introduce non-real market data or a proprietary provider dependency.
MSG
    status=1
  fi
done

exit "$status"
