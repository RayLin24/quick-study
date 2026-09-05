#!/usr/bin/env bash
# Raycast Script Command — Ask a local Quick Study tutorial (feature 42).
# @raycast.schemaVersion 1
# @raycast.title Quick Study Ask
# @raycast.mode fullOutput
# @raycast.argument1 { "type": "text", "placeholder": "tutorial name" }
# @raycast.argument2 { "type": "text", "placeholder": "question" }
set -euo pipefail
BASE="${QUICK_STUDY_URL:-http://127.0.0.1:8000}"
TUTORIAL="${1:?tutorial}"
QUESTION="${2:?question}"
curl -sS -X POST "$BASE/v1/tutorials/${TUTORIAL}/ask" \
  -H "Content-Type: application/json" \
  ${QUICK_STUDY_TOKEN:+-H "Authorization: Bearer $QUICK_STUDY_TOKEN"} \
  -d "{\"question\": $(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$QUESTION")}"
