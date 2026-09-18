"""Small application policies layered onto Harness middleware."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

from agent_harness import AgentMiddleware, EventType, ToolRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from pydantic import ValidationError

from .business_validation import (
    DOWNSTREAM_FIELDS,
    FIELD_BY_AGENT,
    STAGE_DEPENDENCIES,
    BusinessConsistencyError,
    missing_dependencies,
    validate_business_result,
)
from .context import decode, task_view
from .test_runtime import ProcurementTestConfig, use_test_config


def _tool_contract_value(value: Any) -> Any:
    """Remove SubAgent-only annotations before strict calculator validation."""

    if isinstance(value, Mapping):
        return {key: _tool_contract_value(item) for key, item in value.items() if key != "remarks"}
    if isinstance(value, list):
        return [_tool_contract_value(item) for item in value]
    return value


class ProcurementOrchestrationMiddleware(AgentMiddleware):
    """Enforce SubAgent batches and commit validated results to business State."""

    def __init__(self, *, enable_test_config: bool = False) -> None:
        self.enable_test_config = enable_test_config

    def before_agent(self, execution: Any) -> None:
        # ``execution.input`` contains only this invoke's input before
        # LangGraph restores the checkpoint.  In particular, do not inject an
        # empty value here: that would overwrite a same-session business State.
        # Validated writes are returned from tool execution as Command(update=)
        # and are committed by the StateGraph/checkpointer.
        return None

    @staticmethod
    def _batch_dependency_failures(request: Any, response: Any) -> dict[str, dict[str, Any]]:
        if request.purpose != "agent" or not isinstance(response, AIMessage):
            return {}
        calls = [call for call in response.tool_calls if call.get("name") in FIELD_BY_AGENT]
        if not calls:
            return {}
        snapshot = request.execution.metadata.get("procurement_round_state", {})
        snapshot = snapshot if isinstance(snapshot, Mapping) else {}
        produced_in_batch = {FIELD_BY_AGENT[str(call["name"])] for call in calls}
        failures = {}
        for call in calls:
            name = str(call["name"])
            missing = set(missing_dependencies(name, snapshot))
            # If this batch refreshes an upstream result, a dependent call must
            # wait for the next Main round even when an older value exists.
            missing.update(
                field
                for field in produced_in_batch
                if field in STAGE_DEPENDENCIES[name]
            )
            if missing:
                dependencies = sorted(missing)
                failures[str(call["id"])] = {
                    "status": "error",
                    "error_type": "dependency_conflict",
                    "subagent": name,
                    "missing_dependencies": dependencies,
                    "observation": (
                        f"{name} 本轮不可执行；本轮开始时缺少或正在刷新前置结果: "
                        f"{', '.join(dependencies)}。请等待前置 SubAgent 完成后重新规划。"
                    ),
                }
        return failures

    def after_model(self, request: Any, response: Any) -> Any:
        request.execution.metadata["procurement_blocked_calls"] = (
            self._batch_dependency_failures(request, response)
        )
        return response

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

        results = request.state.get("procurement_results")
        results = dict(results) if isinstance(results, Mapping) else {}
        requirement = results.get("requirement", {})
        if request.tool.name == "requirement_agent":
            source_text = request.execution.metadata.get("procurement_source_text")
            if isinstance(source_text, str) and source_text.strip():
                task["text"] = source_text
            task["previous_request"] = requirement.get("request") or None
        else:
            task["request"] = requirement.get("request")
            task.setdefault("analysis_strategy", "standard")
            task.setdefault("replan_reason", None)
        for field in (
            "inventory_analysis",
            "supplier_analysis",
            "pricing_analysis",
            "budget_analysis",
        ):
            if field in results:
                task[field] = results[field]
        if request.tool.name == "execution_agent":
            task["recommended_plan"] = results["risk_analysis"]["recommended_plan"]
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

    def _test_config(self, request: ToolRequest) -> ProcurementTestConfig | None:
        if not self.enable_test_config:
            return None
        raw = request.execution.metadata.get("procurement_test_config")
        if not isinstance(raw, Mapping):
            # A HITL resume can continue directly inside a tool node, without a
            # fresh model pass to repopulate per-invocation metadata.  Test
            # controls are runtime-only and may be recovered from the original
            # user input; they are never business facts.
            for message in reversed(request.state.get("messages", [])):
                if not isinstance(message, HumanMessage):
                    continue
                candidate = decode(message.content)
                if isinstance(candidate, Mapping):
                    simulation_fields = (
                        "simulate_sql_failure",
                        "simulate_subagent_failure",
                        "simulate_mcp_failure",
                        "simulate_execution_failure",
                        "simulate_atomic_failure",
                    )
                    raw = {key: candidate.get(key, False) for key in simulation_fields}
                break
        return ProcurementTestConfig.model_validate(raw) if isinstance(raw, Mapping) else None

    def wrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        blocked = request.execution.metadata.get("procurement_blocked_calls", {}).get(
            request.tool_call_id
        )
        if blocked is not None:
            return blocked
        self._prepare(request)
        with use_test_config(self._test_config(request)):
            value = call_next(request)
        return self._confirm(request, value)

    async def awrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        blocked = request.execution.metadata.get("procurement_blocked_calls", {}).get(
            request.tool_call_id
        )
        if blocked is not None:
            return blocked
        self._prepare(request)
        with use_test_config(self._test_config(request)):
            value = await call_next(request)
        return self._confirm(request, value)

    def after_agent(self, execution: Any, result: Any) -> Any:
        return result

    @staticmethod
    def _confirm(request: ToolRequest, value: Any) -> Any:
        if request.tool.name not in FIELD_BY_AGENT:
            return value
        if not isinstance(value, Mapping) or value.get("status") != "success":
            return value
        content = decode(value.get("content"))
        results = request.state.get("procurement_results")
        confirmed = dict(results) if isinstance(results, Mapping) else {}
        try:
            normalized = validate_business_result(request.tool.name, content, confirmed)
        except (BusinessConsistencyError, ValidationError) as exc:
            return {
                **value,
                "content": None,
                "status": "error",
                "error": f"SubAgent 输出未通过业务确认: {exc}",
            }
        updated_results = dict(confirmed)
        for field in DOWNSTREAM_FIELDS[request.tool.name]:
            updated_results.pop(field, None)
        updated_results[FIELD_BY_AGENT[request.tool.name]] = normalized
        confirmed_value = {**value, "content": normalized}
        # A fresh top-level value is essential: LangGraph receives a formal
        # State update and the checkpointer writes it, rather than relying on a
        # mutation through a shared nested-dict reference.
        return Command(
            update={
                "procurement_results": updated_results,
                "messages": [
                    ToolMessage(
                        content=json.dumps(confirmed_value, ensure_ascii=False, default=str),
                        tool_call_id=request.tool_call_id,
                        name=request.tool.name,
                    )
                ],
            }
        )


class ProcurementBusinessToolMiddleware(AgentMiddleware):
    """Pin calculator inputs to the exact structured task received by a SubAgent."""

    TOOL_FIELDS: ClassVar[dict[str, tuple[str, ...]]] = {
        "parse_requirement": ("text", "previous_request"),
        "calculate_inventory": ("request", "analysis_strategy", "replan_reason"),
        "calculate_suppliers": (
            "request",
            "inventory_analysis",
            "analysis_strategy",
            "replan_reason",
        ),
        "calculate_pricing": (
            "request",
            "inventory_analysis",
            "supplier_analysis",
            "analysis_strategy",
            "replan_reason",
        ),
        "calculate_budget": (
            "request",
            "pricing_analysis",
            "analysis_strategy",
            "replan_reason",
        ),
        "calculate_risk": (
            "request",
            "supplier_analysis",
            "pricing_analysis",
            "budget_analysis",
            "analysis_strategy",
            "replan_reason",
        ),
        "execute_procurement_plan": (
            "request",
            "recommended_plan",
            "budget_analysis",
            "session_id",
        ),
    }

    @staticmethod
    def _task(request: ToolRequest) -> dict[str, Any]:
        messages = request.execution.input.get("messages", [])
        for message in messages:
            value = decode(getattr(message, "content", None))
            if isinstance(value, Mapping):
                return dict(value)
        return {}

    def wrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        fields = self.TOOL_FIELDS.get(request.tool.name)
        if fields:
            task = self._task(request)
            missing = [field for field in fields if field not in task]
            if missing:
                raise BusinessConsistencyError(
                    f"{request.tool.name} 缺少确认任务字段: {', '.join(missing)}"
                )
            request.arguments = {
                **request.arguments,
                **{field: _tool_contract_value(task[field]) for field in fields},
            }
        return call_next(request)

    async def awrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        fields = self.TOOL_FIELDS.get(request.tool.name)
        if fields:
            task = self._task(request)
            missing = [field for field in fields if field not in task]
            if missing:
                raise BusinessConsistencyError(
                    f"{request.tool.name} 缺少确认任务字段: {', '.join(missing)}"
                )
            request.arguments = {
                **request.arguments,
                **{field: _tool_contract_value(task[field]) for field in fields},
            }
        return await call_next(request)
