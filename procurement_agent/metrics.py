"""Application event logging and procurement-specific metric aggregation."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness import EventType, MetricsEventSink, RuntimeEvent


class JsonLinesEventSink:
    """Append redacted Harness runtime events to a local JSONL audit trail."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event: RuntimeEvent) -> None:
        line = json.dumps(event.as_dict(), ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


@dataclass(slots=True)
class ProcurementMetricSink:
    """Count domain categories that complement Harness' built-in metrics."""

    harness: MetricsEventSink = field(default_factory=MetricsEventSink)
    database_calls: int = 0
    mcp_calls: int = 0
    errors: int = 0
    stage_duration_ms: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: RuntimeEvent) -> None:
        self.harness.emit(event)
        with self._lock:
            if event.event_type == EventType.TOOL_START.value:
                name = str(event.metadata.get("name", ""))
                if name.startswith(("analyze_", "execute_procurement_")):
                    self.database_calls += 1
            elif event.event_type == EventType.MCP_TOOL.value:
                self.mcp_calls += 1
            elif event.event_type in {
                EventType.AGENT_ERROR.value,
                EventType.MODEL_ERROR.value,
                EventType.TOOL_ERROR.value,
                EventType.SUBAGENT_ERROR.value,
                EventType.PLAN_FAIL.value,
            }:
                self.errors += 1
            if event.event_type == EventType.AGENT_END.value and event.duration_ms is not None:
                self.stage_duration_ms[event.agent_name] = (
                    self.stage_duration_ms.get(event.agent_name, 0.0) + event.duration_ms
                )

    def snapshot(self, *, tasks: int, successful_tasks: int, replans: int) -> dict[str, Any]:
        base = self.harness.snapshot().as_dict()
        with self._lock:
            return {
                "tasks": tasks,
                "successful_tasks": successful_tasks,
                "task_success_rate": successful_tasks / tasks if tasks else 0.0,
                **base,
                "database_calls": self.database_calls,
                "mcp_calls": self.mcp_calls,
                "replan_count": replans,
                "errors": self.errors,
                "error_rate": self.errors / max(base["agent_calls"], 1),
                "total_duration_ms": base["agent_duration_ms"],
                "stage_duration_ms": dict(self.stage_duration_ms),
            }
