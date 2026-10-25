#!/usr/bin/env bash
# Fails if personal data from the author's own machines is in a tracked file.
#
# Two kinds of patterns:
#  1. Generic, built in below: a tailnet-style IPv4 address in source or docs. Test files may use the documented
#     100.64.0.x example range.
#  2. Personal literals (real names, host names, device serials, real addresses, the drive id). These must NOT be
#     written into the repository, so they live in a local file with one grep -E pattern per line:
#         ${ANDROSYNC_PERSONAL_PATTERNS:-$HOME/.config/androsync/personal-patterns.txt}
#     They are matched case-insensitively in every tracked file, tests included. Without the file (for example in CI)
#     only the generic patterns run and a note says so.
#
# Usage: scripts/check_no_personal_data.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

SELF=':!scripts/check_no_personal_data.sh'
# Tailscale's own public CGNAT range is documentation, not a real address.
SAFE_CONSTANTS='100\.64\.0\.0/10'
hits=0

# 1. generic
generic_matches=$(git grep -nIE '\b100\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b' -- . "$SELF" ':!laptop/tests' ':!android/app/src/test' 2>/dev/null | grep -Ev "$SAFE_CONSTANTS" || true)
if [ -n "$generic_matches" ]; then echo "$generic_matches"; hits=1; fi

# 2. personal literals, kept outside the repository
patterns_file="${ANDROSYNC_PERSONAL_PATTERNS:-$HOME/.config/androsync/personal-patterns.txt}"
if [ -f "$patterns_file" ]; then
  while IFS= read -r pattern; do
    case "$pattern" in ''|'#'*) continue ;; esac
    found=$(git grep -nIiE "$pattern" -- . "$SELF" 2>/dev/null || true)
    if [ -n "$found" ]; then echo "$found"; hits=1; fi
  done < "$patterns_file"
else
  echo "check_no_personal_data.sh: note: $patterns_file not found, only the generic patterns were checked" >&2
fi

if [ "$hits" -ne 0 ]; then
  echo >&2
  echo "check_no_personal_data.sh: found personal data above. Replace it with a placeholder." >&2
  exit 1
fi
echo "check_no_personal_data.sh: clean"
