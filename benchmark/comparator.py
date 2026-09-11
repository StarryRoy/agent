"""Before/after comparison for eligible real-LLM benchmark runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

COMPARISON_METRICS = (
    ("平均 Input Token", "average_input_tokens", "number"),
    ("平均总 Token", "average_total_tokens", "number"),
    ("平均耗时", "average_duration_ms", "number"),
    ("任务成功率", "task_success_rate", "rate"),
    ("Replan 成功率", "replan_success_rate", "rate"),
    ("平均 Tool 调用", "average_tool_calls", "number"),
)


def _change(before: Any, after: Any, kind: str) -> str:
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return "N/A"
    if kind == "rate":
        return f"{(after - before) * 100:+.2f} pp"
    if before == 0:
        return "N/A"
    return f"{(after - before) / before * 100:+.2f}%"


def compare_runs(before_path: Path, after_path: Path) -> dict[str, Any]:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    if not before.get("metadata", {}).get("real_llm") or not after.get("metadata", {}).get(
        "real_llm"
    ):
        raise ValueError("Before/after comparison only accepts real-LLM benchmark data")
    before_summary = before.get("summary", {})
    after_summary = after.get("summary", {})
    rows = []
    for label, key, kind in COMPARISON_METRICS:
        before_value = before_summary.get(key)
        after_value = after_summary.get(key)
        rows.append(
            {
                "metric": label,
                "before": before_value,
                "after": after_value,
                "change": _change(before_value, after_value, kind),
            }
        )
    return {"before": str(before_path), "after": str(after_path), "rows": rows}


def discover_comparison(
    results_dir: Path, explicit_before: Path | None, explicit_after: Path | None
) -> dict[str, Any] | None:
    before = explicit_before or results_dir / "before.json"
    after = explicit_after or results_dir / "after.json"
    if not before.exists() or not after.exists():
        return None
    return compare_runs(before.resolve(), after.resolve())
