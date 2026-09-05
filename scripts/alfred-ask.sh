#!/usr/bin/env bash
# Alfred Run Script — first word is tutorial, rest is the question (feature 42).
set -euo pipefail
BASE="${QUICK_STUDY_URL:-http://127.0.0.1:8000}"
INPUT="${1:-}"
TUTORIAL="${INPUT%% *}"
QUESTION="${INPUT#* }"
if [ -z "$TUTORIAL" ] || [ "$TUTORIAL" = "$QUESTION" ]; then
  echo "usage: <tutorial> <question>" >&2
  exit 2
fi
curl -sS -X POST "$BASE/v1/tutorials/${TUTORIAL}/ask" \
  -H "Content-Type: application/json" \
  ${QUICK_STUDY_TOKEN:+-H "Authorization: Bearer $QUICK_STUDY_TOKEN"} \
  -d "{\"question\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$QUESTION")}"
