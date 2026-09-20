"""Trace-derived metrics. Missing provider data remains unavailable, never estimated."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ERROR_EVENTS = {"agent.error", "model.error", "tool.error", "subagent.error", "plan.fail"}


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            events.append(
                {"event_type": "trace.parse_error", "error": str(exc), "line": line_number}
            )
    return events


def _sum_or_none(values: list[int | float]) -> int | float | None:
    return sum(values) if values else None


def collect_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    model_starts = [event for event in events if event.get("event_type") == "model.start"]
    model_ends = [
        event for event in events if event.get("event_type") in {"model.end", "model.error"}
    ]
    contexts = []
    input_tokens: list[int] = []
    output_tokens: list[int] = []
    total_tokens: list[int] = []
    for index, event in enumerate(model_ends, 1):
        usage = event.get("metadata", {}).get("token_usage")
        usage = usage if isinstance(usage, dict) else {}
        input_value = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_value = usage.get("output_tokens", usage.get("completion_tokens"))
        total_value = usage.get("total_tokens")
        if isinstance(input_value, (int, float)):
            input_tokens.append(int(input_value))
        else:
            input_value = None
        if isinstance(output_value, (int, float)):
            output_tokens.append(int(output_value))
        else:
            output_value = None
        if isinstance(total_value, (int, float)):
            total_tokens.append(int(total_value))
        else:
            # Do not manufacture a total from input/output counts.  A
            # benchmark report may aggregate provider-reported totals only;
            # missing usage remains unavailable.
            total_value = None
        contexts.append(
            {
                "call": index,
                "agent": event.get("agent_name"),
                "purpose": event.get("metadata", {}).get("purpose"),
                "input_tokens": input_value,
                "output_tokens": output_value,
                "total_tokens": total_value,
                "duration_ms": event.get("duration_ms"),
                "status": event.get("status"),
            }
        )

    tools = [event for event in events if event.get("event_type") == "tool.start"]
    tool_ends = [event for event in events if event.get("event_type") in {"tool.end", "tool.error"}]
    database_ends = [event for event in events if event.get("event_type") == "database.end"]
    mcp_names = {
        str(event.get("metadata", {}).get("name"))
        for event in events
        if event.get("event_type") == "mcp.tool"
    }
    skill_events = [event for event in events if event.get("event_type") == "skill.load"]
    skill_loads = [
        str(skill)
        for event in skill_events
        for skill in event.get("metadata", {}).get("skills", [])
    ]
    replans = [event for event in events if event.get("event_type") == "plan.replan"]
    errors = [
        {
            "event_type": event.get("event_type"),
            "agent": event.get("agent_name"),
            "error": event.get("error"),
        }
        for event in events
        if event.get("event_type") in ERROR_EVENTS or event.get("event_type") == "trace.parse_error"
    ]
    input_total = _sum_or_none(input_tokens)
    return {
        "llm": {
            "input_tokens": input_total,
            "output_tokens": _sum_or_none(output_tokens),
            "total_tokens": _sum_or_none(total_tokens),
            "calls": len(model_starts),
            "calls_with_token_usage": sum(item["total_tokens"] is not None for item in contexts),
        },
        "agent": {
            "calls": sum(event.get("event_type") == "agent.start" for event in events),
            "rounds": sum(
                event.get("metadata", {}).get("purpose") == "agent" for event in model_starts
            ),
            "path": [
                str(event.get("agent_name"))
                for event in events
                if event.get("event_type") == "agent.start"
            ],
            "subagent_calls": sum(event.get("event_type") == "subagent.start" for event in events),
            "subagent_path": [
                str(event.get("metadata", {}).get("name", event.get("agent_name")))
                for event in events
                if event.get("event_type") == "subagent.start"
            ],
            "tool_calls": len(tools),
            "tool_path": [str(event.get("metadata", {}).get("name", "")) for event in tools],
        },
        "context": {
            "calls": contexts,
            "average_tokens": (sum(input_tokens) / len(input_tokens)) if input_tokens else None,
            "max_tokens": max(input_tokens) if input_tokens else None,
            "task_cumulative_input_tokens": input_total,
            "unavailable_calls": len(contexts) - len(input_tokens),
        },
        "performance": {
            "llm_duration_ms": _sum_or_none(
                [
                    float(event["duration_ms"])
                    for event in model_ends
                    if event.get("duration_ms") is not None
                ]
            ),
            "tool_duration_ms": _sum_or_none(
                [
                    float(event["duration_ms"])
                    for event in tool_ends
                    if event.get("duration_ms") is not None
                ]
            ),
            "database_duration_ms": _sum_or_none(
                [
                    float(event["duration_ms"])
                    for event in database_ends
                    if event.get("duration_ms") is not None
                ]
            ),
            "mcp_duration_ms": _sum_or_none(
                [
                    float(event["duration_ms"])
                    for event in tool_ends
                    if event.get("duration_ms") is not None
                    and str(event.get("metadata", {}).get("name")) in mcp_names
                ]
            ),
        },
        "replan": {
            "count": len(replans),
            "strategies": [event.get("metadata", {}).get("strategy") for event in replans],
            "events": [event.get("metadata", {}) for event in replans],
        },
        "skills": {"loads": skill_loads, "unique": list(dict.fromkeys(skill_loads))},
        "hitl": {"count": sum(event.get("event_type") == "hitl.pause" for event in events)},
        "mcp": {"calls": sum(event.get("event_type") == "mcp.tool" for event in events)},
        "database": {"calls": sum(event.get("event_type") == "database.start" for event in events)},
        "errors": errors,
    }


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(math.ceil(percentile_value * len(ordered)) - 1, 0)
    return ordered[index]
