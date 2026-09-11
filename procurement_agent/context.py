"""Business projections over the model context prepared by Harness."""

from __future__ import annotations

import json
from typing import Any

from agent_harness import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


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


FIELDS = {
    "requirement_agent": "requirement",
    "inventory_agent": "inventory_analysis",
    "supplier_agent": "supplier_analysis",
    "pricing_agent": "pricing_analysis",
    "budget_agent": "budget_analysis",
    "risk_agent": "risk_analysis",
    "execution_agent": "execution",
}

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


def procurement_context(results: dict, goal: Any = None) -> dict:
    requirement = results.get("requirement", {})
    budget = results.get("budget_analysis", {})
    pricing = results.get("pricing_analysis", {})
    risk = results.get("risk_analysis", {})
    request = requirement.get("request", goal or {})
    return {
        "goal": request,
        "confirmed_facts": {key: value.get("facts", []) for key, value in results.items()},
        "conclusions": {
            key: {"status": value.get("status"), "conclusion": value.get("conclusion")}
            for key, value in results.items()
        },
        "current_plan": risk.get("recommended_plan") or pricing.get("recommended_price_plan"),
        "budget_gap": budget.get("over_budget_amount"),
        "effective_budget": budget.get("effective_available_budget"),
        "risks": risk.get("main_risks", []),
        "replan_reason": risk.get("replan_reason"),
        "evidence": {key: value.get("evidence", []) for key, value in results.items()},
        "source_ref": {key: value.get("source_ref") for key, value in results.items()},
    }


def task_view(name: str, task: dict) -> dict:
    task = business_view(task)
    if name not in DEPENDENCIES:
        return task
    results = {
        key: value
        for key, value in task.items()
        if key.endswith("_analysis") and isinstance(value, dict)
    }
    context = task.get("procurement_context") or procurement_context(results, task.get("request"))
    context = {
        **context,
        "replan_reason": task.get("replan_reason") or context.get("replan_reason"),
    }
    allowed = DEPENDENCIES[name] | {"analysis_strategy", "replan_reason", "replan_start"}
    return {
        **{key: value for key, value in task.items() if key in allowed},
        "procurement_context": context,
    }


class ProcurementContextMiddleware(AgentMiddleware):
    def before_model(self, request: ModelRequest) -> None:
        messages = []
        results = {}
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
                if item.name in FIELDS:
                    # A new analysis invalidates downstream context until revalidated.
                    downstream = {
                        "requirement_agent": list(results),
                        "inventory_agent": [
                            "supplier_analysis",
                            "pricing_analysis",
                            "budget_analysis",
                            "risk_analysis",
                        ],
                        "supplier_agent": [
                            "pricing_analysis",
                            "budget_analysis",
                            "risk_analysis",
                        ],
                        "pricing_agent": ["budget_analysis", "risk_analysis"],
                        "budget_agent": ["risk_analysis"],
                    }
                    for key in downstream.get(item.name, []):
                        results.pop(key, None)

                # request.messages already reflects Harness compaction and tool-result
                # limits. Never recover a fuller copy from request.state.
                value = business_view(decode(item.content))
                if isinstance(value, dict):
                    payload = value.get("content", value)
                    if isinstance(payload, dict):
                        payload.setdefault(
                            "source_ref",
                            {
                                "session_id": request.execution.session_id,
                                "tool_call_id": item.tool_call_id,
                                "tool": item.name,
                                "storage": "Harness checkpoint / trace",
                            },
                        )
                        if item.name in FIELDS:
                            results[FIELDS[item.name]] = payload
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
            context = procurement_context(results)
            # Derive durable business facts from retained structured results, not raw history.
            request.execution.metadata["procurement_context"] = context
            messages.insert(
                1,
                SystemMessage(
                    content="当前采购业务 Context（最新用户修改优先于历史事实）：\n"
                    + json.dumps(context, ensure_ascii=False)
                ),
            )
        request.messages = messages
