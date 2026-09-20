"""Small application policies layered onto Harness middleware."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_harness import AgentMiddleware, EventType, ToolRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from pydantic import ValidationError

from .business_validation import (
    DOWNSTREAM_FIELDS,
    FIELD_BY_AGENT,
    STAGE_DEPENDENCIES,
    BusinessConsistencyError,
    build_execution_arguments,
    missing_dependencies,
    validate_business_result,
)
from .context import DEPENDENCIES, SIMULATION_FIELDS, decode, task_view
from .test_runtime import ProcurementTestConfig, use_test_config


class ExecutionToolInputMiddleware(AgentMiddleware):
    """Build execution arguments from the confirmed State before approval.

    Execution is the one sensitive Tool whose arguments must never be accepted
    from the model's copy of upstream data.  This hook runs after the model
    response but before Harness validates the Tool call and creates HITL, so a
    malformed or incomplete State becomes a preflight result rather than an
    approval followed by a schema failure.
    """

    @staticmethod
    def _task(request: Any) -> Mapping[str, Any]:
        messages = getattr(request, "messages", None)
        if messages is None:
            messages = request.state.get("messages", [])
        for message in reversed(messages):
            if not isinstance(message, HumanMessage):
                continue
            value = decode(message.content)
            if isinstance(value, Mapping):
                return value
            break
        return {}

    @classmethod
    def _confirmed_state(cls, request: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        task = cls._task(request)
        results = request.state.get("procurement_results")
        if isinstance(results, Mapping) and results:
            return results, task
        upstream = task.get("upstream_state")
        return (upstream if isinstance(upstream, Mapping) else {}), task

    @staticmethod
    def _preflight_failure(error: Exception) -> AIMessage:
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "agent_harness_structured_response",
                    "args": {
                        "status": "failed",
                        "error": {
                            "type": "execution_preflight_invalid_state",
                            "message": f"执行前 State 未通过校验: {error}",
                            "retryable": False,
                        },
                        "executed_actions": [],
                    },
                    "id": "execution-preflight-failure",
                    "type": "tool_call",
                }
            ],
        )

    @staticmethod
    def _preflight_result(error: Exception) -> dict[str, Any]:
        return {
            "status": "failed",
            "error": {
                "type": "execution_preflight_invalid_state",
                "message": f"执行前 State 未通过校验: {error}",
                "retryable": False,
            },
            "executed_actions": [],
        }

    def after_model(self, request: Any, response: Any) -> Any:
        if request.execution.agent_name != "execution_agent" or not isinstance(response, AIMessage):
            return response
        calls = list(response.tool_calls)
        execution_calls = [call for call in calls if call.get("name") == "execute_procurement_plan"]
        if not execution_calls:
            return response

        confirmed_state, task = self._confirmed_state(request)
        session_id = task.get("session_id") or request.execution.session_id
        try:
            arguments = build_execution_arguments(confirmed_state, session_id)
        except (BusinessConsistencyError, KeyError, TypeError, ValidationError) as exc:
            return self._preflight_failure(exc)

        updated_calls = [
            {**call, "args": arguments} if call.get("name") == "execute_procurement_plan" else call
            for call in calls
        ]
        return response.model_copy(update={"tool_calls": updated_calls})

    def wrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        if (
            request.execution.agent_name != "execution_agent"
            or request.tool.name != "execute_procurement_plan"
        ):
            return call_next(request)
        confirmed_state, task = self._confirmed_state(request)
        session_id = task.get("session_id") or request.execution.session_id
        try:
            # This second guard also covers a resumed HITL edit: edited
            # arguments are never allowed to replace confirmed State.
            request.arguments = build_execution_arguments(confirmed_state, session_id)
        except (BusinessConsistencyError, KeyError, TypeError, ValidationError) as exc:
            return self._preflight_result(exc)
        return call_next(request)

    async def awrap_tool_call(self, request: ToolRequest, call_next: Any) -> Any:
        if (
            request.execution.agent_name != "execution_agent"
            or request.tool.name != "execute_procurement_plan"
        ):
            return await call_next(request)
        confirmed_state, task = self._confirmed_state(request)
        session_id = task.get("session_id") or request.execution.session_id
        try:
            request.arguments = build_execution_arguments(confirmed_state, session_id)
        except (BusinessConsistencyError, KeyError, TypeError, ValidationError) as exc:
            return self._preflight_result(exc)
        return await call_next(request)


class ProcurementOrchestrationMiddleware(AgentMiddleware):
    """Enforce SubAgent batches and commit validated results to business State."""

    def __init__(self, *, enable_test_config: bool = False) -> None:
        self.enable_test_config = enable_test_config
        self._fault_states: dict[str, dict[str, bool]] = {}

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
        """Prepare only the child task's formal upstream State.

        The request is a SubAgent boundary, so application code may add the
        canonical State projection here.  It must not rewrite arguments for any
        Tool called inside the child; those arguments are generated and owned by
        the child agent.
        """

        if request.tool.name not in FIELD_BY_AGENT:
            return
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
        source_text = request.execution.metadata.get("procurement_source_text")
        goal = task.get("goal") or task.get("text")
        if request.tool.name == "requirement_agent" and isinstance(source_text, str) and source_text.strip():
            goal = source_text

        if request.tool.name == "requirement_agent":
            upstream = {"requirement": requirement} if requirement else {}
        else:
            raw_state = task.get("upstream_state")
            upstream = dict(raw_state) if isinstance(raw_state, Mapping) else {}
            # The formal State is authoritative and is the only source from
            # which the application may prepare child business context.
            upstream.update(
                {
                    field: results[field]
                    for field in DEPENDENCIES[request.tool.name]
                    if field in results
                }
            )

        task = task_view(
            request.tool.name,
            {"goal": goal, "upstream_state": upstream},
        )

        if request.tool.name == "execution_agent":
            task["session_id"] = request.execution.session_id
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
                    raw = {key: candidate.get(key, False) for key in SIMULATION_FIELDS}
                break
        if not isinstance(raw, Mapping):
            return None
        session_id = str(request.execution.session_id or "")
        fault_state = self._fault_states.setdefault(session_id, {})
        config = ProcurementTestConfig.model_validate(raw)
        # Pydantic may copy mutable input values during validation.  Assign the
        # application-owned state after validation so the one-shot real-tool
        # fault survives separate SubAgent tool invocations in one session.
        config.fault_state = fault_state
        return config

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
        strategy = str(normalized.get("analysis_strategy") or "")
        if strategy and strategy not in {"standard", "balanced"}:
            task = decode(request.arguments.get("task"))
            goal = task.get("goal") if isinstance(task, Mapping) else None
            emitted = request.execution.metadata.setdefault("procurement_replan_goals", set())
            if isinstance(emitted, set) and goal and goal not in emitted:
                emitted.add(goal)
                if request.execution.debug is not None:
                    request.execution.debug.emit(
                        EventType.PLAN_REPLAN,
                        status="replanning",
                        metadata={
                            "subagent": request.tool.name,
                            "strategy": strategy,
                            "reason": normalized.get("replan_reason"),
                            "goal": goal,
                        },
                    )
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
    """Compatibility shim; internal Tool arguments belong to the SubAgent.

    The application intentionally performs no argument pinning or rewriting.
    New composition does not install this middleware, but keeping a no-op
    implementation avoids breaking callers that imported the old extension.
    """
