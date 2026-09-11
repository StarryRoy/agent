"""Small application policies layered onto Harness middleware."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_harness import AgentMiddleware, EventType, ToolRequest

from .context import task_view


class ProcurementOrchestrationMiddleware(AgentMiddleware):
    """Propagate the public Session ID and observe reasoned replanning calls."""

    @staticmethod
    def _prepare(request: ToolRequest) -> None:
        if "task" not in request.arguments:
            return
        raw_task = request.arguments.get("task")
        if isinstance(raw_task, str):
            try:
                task = json.loads(raw_task)
            except json.JSONDecodeError:
                task = {"task": raw_task}
        elif isinstance(raw_task, Mapping):
            task = dict(raw_task)
        else:
            return

        context = request.execution.metadata.get("procurement_context")
        if context:
            # Carry observed facts even if the model omits them from its delegation JSON.
            task["procurement_context"] = context
        task = task_view(request.tool.name, task)

        if request.tool.name == "execution_agent":
            task["session_id"] = request.execution.session_id

        strategy = str(task.get("analysis_strategy") or "")
        if strategy and strategy not in {"standard", "balanced"} and task.get("replan_start"):
            metadata: dict[str, Any] = {
                "subagent": request.tool.name,
                "strategy": strategy,
                "reason": task.get("replan_reason"),
            }
            if request.execution.debug is not None:
                request.execution.debug.emit(
                    EventType.PLAN_REPLAN,
                    status="replanning",
                    metadata=metadata,
                )
        request.arguments = {**request.arguments, "task": json.dumps(task, ensure_ascii=False)}

    def wrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        self._prepare(request)
        return call_next(request)

    async def awrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        self._prepare(request)
        return await call_next(request)
