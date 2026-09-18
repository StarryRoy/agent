"""Business projections over the model context prepared by Harness."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_harness import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from .business_validation import FIELD_BY_AGENT


def decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return decode(json.loads(value))
        except json.JSONDecodeError:
            return value
    if (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], dict)
        and value[0].get("type") == "text"
    ):
        return decode(value[0]["text"])
    return value


def business_view(value: Any) -> Any:
    """Remove query payloads, retaining their evidence and all business values."""
    if isinstance(value, list):
        return [business_view(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "queries":
            result["evidence"] = [
                {
                    "source": query.get("subtask"),
                    "ok": query.get("data", {}).get("ok"),
                    "facts": query.get("facts", []),
                    "conclusion": query.get("conclusion"),
                }
                for query in item
            ]
        elif key == "schema_evidence":
            result["schema_checked"] = bool(item)
        elif key == "content":
            result[key] = business_view(decode(item))
        else:
            result[key] = business_view(item)
    return result


FIELDS = FIELD_BY_AGENT

# Only pass each Tool's actual dependencies, preserving exact plans and constraints.
DEPENDENCIES = {
    "requirement_agent": {"text", "previous_request"},
    "inventory_agent": {"request"},
    "supplier_agent": {"request", "inventory_analysis"},
    "pricing_agent": {"request", "inventory_analysis", "supplier_analysis"},
    "budget_agent": {"request", "pricing_analysis"},
    "risk_agent": {"request", "supplier_analysis", "pricing_analysis", "budget_analysis"},
    "execution_agent": {"request", "recommended_plan", "budget_analysis", "session_id"},
}


def task_view(name: str, task: dict) -> dict:
    task = business_view(task)
    if name not in DEPENDENCIES:
        return task
    allowed = DEPENDENCIES[name] | {
        "analysis_strategy",
        "replan_reason",
        "replan_start",
    }
    return {key: value for key, value in task.items() if key in allowed}


class ProcurementContextMiddleware(AgentMiddleware):
    def __init__(self, *, enable_test_config: bool = False) -> None:
        self.enable_test_config = enable_test_config

    def before_model(self, request: ModelRequest) -> None:
        # Keep the latest original user turn available to the parent tool-call
        # middleware. Real models can otherwise delegate an empty context to the
        # requirement agent and lose the source procurement request entirely.
        for item in reversed(request.messages):
            if not isinstance(item, HumanMessage):
                continue
            value = decode(item.content)
            source_text = value if isinstance(value, str) else None
            if isinstance(value, dict) and isinstance(value.get("text"), str):
                source_text = value["text"]
                simulation_fields = (
                    "simulate_sql_failure",
                    "simulate_subagent_failure",
                    "simulate_mcp_failure",
                    "simulate_execution_failure",
                    "simulate_atomic_failure",
                )
                if self.enable_test_config and any(key in value for key in simulation_fields):
                    request.execution.metadata["procurement_test_config"] = {
                        key: value.get(key, False) for key in simulation_fields
                    }
            if source_text and source_text.strip():
                request.execution.metadata["procurement_source_text"] = source_text
            break

        messages = []
        state_results = request.state.get("procurement_results")
        results = state_results if isinstance(state_results, Mapping) else {}
        # Freeze the facts visible before this model call. Batch dependency
        # validation must not be affected by earlier calls from the same response.
        request.execution.metadata["procurement_round_state"] = dict(results)
        # Preserve call/response pairs and counters, but omit replaced results within a turn.
        latest = {}
        superseded = set()
        for item in request.messages:
            if isinstance(item, HumanMessage):
                latest = {}
            elif isinstance(item, ToolMessage) and item.name in FIELDS:
                if item.name in latest:
                    superseded.add(latest[item.name])
                latest[item.name] = item.tool_call_id
        for item in request.messages:
            if isinstance(item, ToolMessage) and item.name not in {"load_skill", "unload_skill"}:
                # Tool messages are model context only. Formal business State is
                # never reconstructed from chat history.
                value = business_view(decode(item.content))
                if isinstance(value, dict):
                    if item.tool_call_id in superseded:
                        value = {"superseded": True, "tool_call_id": item.tool_call_id}
                    item = item.model_copy(
                        update={"content": json.dumps(value, ensure_ascii=False)}
                    )
            elif isinstance(item, AIMessage) and item.tool_calls:
                calls = []
                for call in item.tool_calls:
                    args = dict(call["args"])
                    for key in ("task", "plan_json"):
                        if key in args:
                            task = decode(args[key])
                            args[key] = json.dumps(
                                task_view(call["name"], task) if isinstance(task, dict) else task,
                                ensure_ascii=False,
                            )
                    calls.append({**call, "args": args})
                item = item.model_copy(update={"tool_calls": calls})
            elif isinstance(item, HumanMessage):
                value = decode(item.content)
                if isinstance(value, dict):
                    item = item.model_copy(
                        update={"content": json.dumps(business_view(value), ensure_ascii=False)}
                    )
            messages.append(item)
        if results:
            messages.insert(
                1 if messages and isinstance(messages[0], SystemMessage) else 0,
                SystemMessage(
                    content=(
                        "以下是应用层持久化业务 State，也是当前采购业务唯一正式事实源。"
                        "历史 ToolMessage 如与其冲突，以此 State 为准：\n"
                        + json.dumps(results, ensure_ascii=False)
                    )
                ),
            )
        # Gemini 3.x rejects model prefilling: a structured-output request may
        # not end with the Agent's AIMessage. Harness already binds the native
        # JSON schema formatter; give that format-only call a final user turn
        # without changing the normal Agent/Tool conversation.
        if request.purpose == "format" and (
            not messages or not isinstance(messages[-1], (HumanMessage, ToolMessage))
        ):
            messages.append(
                HumanMessage(
                    content=(
                        "请将上面的最终结果转换为要求的结构化输出。"
                        "只使用已有业务事实，不要补充或修改结论。"
                    )
                )
            )
        request.messages = messages
