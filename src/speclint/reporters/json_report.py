from __future__ import annotations

import json
from typing import Any

from ..runner import RunResult


def render_json(result: RunResult) -> str:
    payload: dict[str, Any] = {
        "schema": "speclint/v1",
        "summary": {
            "specs_checked": result.specs_checked,
            "rules_evaluated": result.rules_evaluated,
            "findings": _summarize_by_severity(result),
            "transport_chosen": result.transport_chosen,
            "rules_skipped_llm": result.rules_skipped_llm,
            "package_order": result.package_order,
            "overrides": result.override_log,
        },
        "findings": [f.to_dict() for f in result.findings],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def _summarize_by_severity(result: RunResult) -> dict[str, int]:
    counts = {"error": 0, "warn": 0, "info": 0}
    for f in result.findings:
        if f.severity in counts:
            counts[f.severity] += 1
    return counts
