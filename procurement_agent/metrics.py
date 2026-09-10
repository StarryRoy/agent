"""Application event logging and procurement-specific metric aggregation."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness import (
    DatabaseToolkit,
    EventSink,
    EventType,
    MetricsEventSink,
    RuntimeEvent,
    RuntimeObserver,
)

LOGGER = logging.getLogger(__name__)


class ObservedDatabaseToolkit(DatabaseToolkit):
    """DatabaseToolkit with events for every actual toolkit operation."""

    def __init__(self, *args: Any, event_sinks: Sequence[EventSink] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._database_event_sinks = tuple(event_sinks)

    def _event(
        self,
        phase: str,
        operation: str,
        *,
        duration_ms: float | None = None,
        status: str = "started",
        error: str | None = None,
    ) -> None:
        context = RuntimeObserver.current_context()
        event = RuntimeEvent(
            event_type=f"database.{phase}",
            agent_name=context.agent_name if context else "application",
            session_id=context.session_id if context else None,
            trace_id=context.trace_id if context else "",
            span_id=context.span_id if context else "",
            parent_span_id=context.parent_span_id if context else None,
            status=status,
            duration_ms=duration_ms,
            metadata={"operation": operation, "database_toolkit": True},
            error=error,
        )
        for sink in self._database_event_sinks:
            try:
                sink.emit(event)
            except Exception as exc:
                LOGGER.debug("Database event sink failed", exc_info=exc)

    def _observe(self, operation: str, function: Any, *args: Any, **kwargs: Any) -> Any:
        self._event("start", operation)
        started = time.perf_counter()
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            self._event(
                "end",
                operation,
                duration_ms=(time.perf_counter() - started) * 1000,
                status="error",
                error=str(exc),
            )
            raise
        duration = (time.perf_counter() - started) * 1000
        success = not isinstance(result, dict) or bool(result.get("ok", True))
        error = None if success else str(result.get("error"))
        self._event(
            "end",
            operation,
            duration_ms=duration,
            status="success" if success else "error",
            error=error,
        )
        return result

    def list_tables(self) -> dict[str, Any]:
        return self._observe("list_tables", super().list_tables)

    def get_schema(self, table: str) -> dict[str, Any]:
        return self._observe("get_schema", super().get_schema, table)

    def execute_query(
        self, sql: str, parameters: dict[str, Any] | list[Any] | None = None
    ) -> dict[str, Any]:
        return self._observe("execute_query", super().execute_query, sql, parameters)

    def execute_write(
        self, sql: str, parameters: dict[str, Any] | list[Any] | None = None
    ) -> dict[str, Any]:
        return self._observe("execute_write", super().execute_write, sql, parameters)


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
    database_errors: int = 0
    database_duration_ms: float = 0.0
    mcp_calls: int = 0
    replan_events: int = 0
    errors: int = 0
    stage_duration_ms: dict[str, float] = field(default_factory=dict)
    agent_route: list[str] = field(default_factory=list)
    subagent_route: list[str] = field(default_factory=list)
    tool_route: list[str] = field(default_factory=list)
    database_route: list[str] = field(default_factory=list)
    replan_strategies: list[dict[str, Any]] = field(default_factory=list)
    replans_by_trace: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: RuntimeEvent) -> None:
        self.harness.emit(event)
        with self._lock:
            if event.event_type == EventType.AGENT_START.value:
                self.agent_route.append(event.agent_name)
            elif event.event_type == EventType.SUBAGENT_START.value:
                self.subagent_route.append(str(event.metadata.get("name", event.agent_name)))
            elif event.event_type == EventType.TOOL_START.value:
                self.tool_route.append(str(event.metadata.get("name", "")))
            elif event.event_type == EventType.MCP_TOOL.value:
                self.mcp_calls += 1
            elif event.event_type == EventType.PLAN_REPLAN.value:
                self.replan_events += 1
                self.replan_strategies.append(dict(event.metadata))
                self.replans_by_trace[event.trace_id] = (
                    self.replans_by_trace.get(event.trace_id, 0) + 1
                )
            elif event.event_type == "database.start":
                self.database_calls += 1
                self.database_route.append(str(event.metadata.get("operation", "")))
            elif event.event_type == "database.end":
                self.database_duration_ms += float(event.duration_ms or 0)
                if event.status == "error":
                    self.database_errors += 1
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
                "database_errors": self.database_errors,
                "database_duration_ms": self.database_duration_ms,
                "mcp_calls": self.mcp_calls,
                "replan_count": max(replans, self.replan_events),
                "errors": self.errors,
                "error_rate": self.errors / max(base["agent_calls"], 1),
                "total_duration_ms": base["agent_duration_ms"],
                "stage_duration_ms": dict(self.stage_duration_ms),
                "agent_route": list(self.agent_route),
                "subagent_route": list(self.subagent_route),
                "tool_route": list(self.tool_route),
                "database_route": list(self.database_route),
                "replan_strategies": list(self.replan_strategies),
            }

    def replan_count_for_trace(self, trace_id: str | None) -> int:
        if not trace_id:
            return 0
        with self._lock:
            return self.replans_by_trace.get(trace_id, 0)
