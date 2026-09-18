#!/usr/bin/env bash
# Run before pushing: fails if an OpenAI-style key or a tracked .env file would be committed.
set -euo pipefail
cd "$(dirname "$0")/.."
status=0
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "ERROR: .env is tracked by git. Run: git rm --cached .env"; status=1
fi
if git ls-files -z | xargs -0 grep -nIE 'sk-(proj-)?[A-Za-z0-9_-]{20,}' -- 2>/dev/null; then
  echo "ERROR: possible API key found in tracked files (above)."; status=1
fi
[ $status -eq 0 ] && echo "OK: no .env tracked and no API-key patterns in tracked files."
exit $status
