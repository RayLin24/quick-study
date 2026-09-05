from __future__ import annotations

import json
import re

import yaml

_JSON_FENCE = re.compile(r"```(?:json|JSON)[^\n]*\n(.*?)```", re.DOTALL)
_YAML_FENCE = re.compile(r"```(?:yaml|yml)[^\n]*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def parse_llm_structured(response: str):
    """Prefer JSON (fenced or bare list/object), then YAML."""
    text = (response or "").strip()
    json_match = _JSON_FENCE.search(text)
    candidates = []
    if json_match:
        candidates.append(json_match.group(1).strip())
    stripped = text
    if stripped.startswith("[") or stripped.startswith("{"):
        candidates.append(stripped)
    for blob in candidates:
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            continue
    yaml_match = _YAML_FENCE.search(text)
    yaml_str = yaml_match.group(1).strip() if yaml_match else text
    return yaml.safe_load(yaml_str)
