"""Deterministic chat models that drive real Harness Agent/Tool loops offline."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from .schemas import ProcurementDecision

ROLE_TO_TOOL = {
    "requirement": "parse_requirement",
    "inventory": "analyze_inventory",
    "supplier": "analyze_suppliers",
    "pricing": "analyze_pricing",
    "budget": "analyze_budget",
    "risk": "analyze_risk",
    "execution": "execute_procurement_plan",
}

SUBAGENT_TO_FIELD = {
    "requirement_agent": "requirement",
    "inventory_agent": "inventory_analysis",
    "supplier_agent": "supplier_analysis",
    "pricing_agent": "pricing_analysis",
    "budget_agent": "budget_analysis",
    "risk_agent": "risk_analysis",
    "execution_agent": "execution",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _parse(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return _parse(json.loads(value))
        except json.JSONDecodeError:
            return value
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], dict) and "text" in value[0]:
            return _parse(value[0]["text"])
        return [_parse(item) for item in value]
    if isinstance(value, dict):
        return {key: _parse(item) for key, item in value.items()}
    return value


def _tool_value(message: ToolMessage) -> Any:
    parsed = _parse(message.content)
    if isinstance(parsed, dict) and {"content", "status"}.issubset(parsed):
        if parsed.get("status") == "error":
            return {"status": "error", "error": parsed.get("error"), "content": parsed.get("content")}
        return _parse(parsed.get("content"))
    return parsed


def _latest_human_index(messages: Sequence[BaseMessage]) -> int:
    return max(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=-1,
    )


def _human_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else _json(content)


def _call(name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": args,
                "id": f"{name}-{uuid.uuid4().hex}",
                "type": "tool_call",
            }
        ],
    )


class ProcurementChatModel(BaseChatModel):
    """Role-aware model used to make the sample fully deterministic and testable."""

    role: str
    bound_tool_names: list[str] = Field(default_factory=list, exclude=True)

    @property
    def _llm_type(self) -> str:
        return f"deterministic-procurement-{self.role}"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"role": self.role}

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> ProcurementChatModel:
        self.bound_tool_names = [getattr(tool, "name", str(tool)) for tool in tools]
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> RunnableLambda:
        def format_result(messages: Sequence[BaseMessage], config: Any = None) -> Any:
            raw = next(
                (
                    message.content
                    for message in reversed(messages)
                    if isinstance(message, AIMessage) and message.content
                ),
                "{}",
            )
            value = _parse(raw)
            if not isinstance(value, dict):
                value = {"warnings": [str(value)], "summary": str(value)}
            if isinstance(schema, type) and issubclass(schema, ProcurementDecision):
                return schema.model_validate(value)
            return value

        return RunnableLambda(format_result)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._main(messages) if self.role == "main" else self._subagent(messages)
        message.response_metadata = {
            "token_usage": {
                "input_tokens": sum(max(len(_human_text(item)) // 4, 1) for item in messages),
                "output_tokens": max(len(str(message.content)) // 4, 1),
            }
        }
        usage = message.response_metadata["token_usage"]
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _subagent(self, messages: list[BaseMessage]) -> AIMessage:
        human_index = _latest_human_index(messages)
        if human_index < 0:
            return AIMessage(content=_json({"status": "error", "error": "missing task"}))
        task = _human_text(messages[human_index])
        tool_messages = [
            message for message in messages[human_index + 1 :] if isinstance(message, ToolMessage)
        ]
        primary = ROLE_TO_TOOL[self.role]
        if not tool_messages:
            argument_name = "plan_json" if self.role == "execution" else "task"
            return _call(primary, {argument_name: task})

        if self.role == "supplier" and len(tool_messages) == 1 and "supplier_status" in self.bound_tool_names:
            analysis = _tool_value(tool_messages[0])
            candidates = analysis.get("candidate_suppliers", []) if isinstance(analysis, dict) else []
            force_failure = False
            try:
                force_failure = bool(json.loads(task).get("request", {}).get("simulate_mcp_failure"))
            except (json.JSONDecodeError, AttributeError):
                pass
            return _call(
                "supplier_status",
                {
                    "supplier_codes": [item["code"] for item in candidates],
                    "force_failure": force_failure,
                },
            )

        if self.role == "supplier" and len(tool_messages) >= 2:
            analysis = _tool_value(tool_messages[0])
            external = _tool_value(tool_messages[-1])
            if not isinstance(analysis, dict):
                analysis = {"status": "error", "error": str(analysis)}
            if isinstance(external, str) and "error" in external.casefold():
                analysis.setdefault("warnings", []).append("MCP供应商状态服务失败，使用数据库状态降级")
                analysis["external_status"] = {}
                analysis["mcp_status"] = "fallback"
            else:
                if isinstance(external, dict) and "statuses" in external:
                    statuses = external["statuses"]
                elif isinstance(external, dict):
                    statuses = external
                else:
                    statuses = {}
                analysis["external_status"] = statuses
                analysis["mcp_status"] = "success"
                for candidate in analysis.get("candidate_suppliers", []):
                    candidate["external_status"] = statuses.get(candidate["code"], "unknown")
            return AIMessage(content=_json(analysis))

        result = _tool_value(tool_messages[-1])
        if isinstance(result, str) and result.startswith("Error:"):
            result = {"status": "error", "error": result}
        return AIMessage(content=_json(result))

    def _main(self, messages: list[BaseMessage]) -> AIMessage:
        human_index = _latest_human_index(messages)
        if human_index < 0:
            return AIMessage(content=_json(self._empty_result("未收到采购需求")))
        text = _human_text(messages[human_index])
        previous = self._results(messages[:human_index])
        current_messages = messages[human_index + 1 :]
        current = self._results(current_messages)
        current_counts = self._counts(current_messages)

        if "requirement" not in current:
            previous_request = previous.get("requirement", {}).get("request", {})
            return _call(
                "requirement_agent",
                {"task": _json({"text": text, "previous_request": previous_request})},
            )

        request_result = current["requirement"]
        request = dict(request_result.get("request") or {})
        if request_result.get("missing_fields"):
            result = self._empty_result(
                "需要补充关键信息：" + "、".join(request_result["missing_fields"])
            )
            result.update(request=request, missing_fields=request_result["missing_fields"])
            return AIMessage(content=_json(result))

        route = self._route(text, bool(previous.get("requirement")))
        combined = {**previous, **current, "requirement": request_result}
        inventory = combined.get("inventory_analysis", {})
        if inventory and inventory.get("recommended_purchase_quantity") == 0:
            result = self._assemble(combined, approval="not_required", execution="not_required")
            result["recommended_plan"] = {
                "plan_id": "NO-PURCHASE",
                "name": "库存满足，无需采购",
                "allocations": [],
                "quantity": 0,
                "total_cost": 0,
            }
            result["summary"] = "现有及在途库存扣除预测消耗和安全库存后已满足需求，无需采购。"
            return AIMessage(content=_json(result))

        for role in route:
            if role not in current:
                return self._delegate(role, combined)
            if current[role].get("status") == "error":
                agent_name = {
                    "inventory_analysis": "inventory_agent",
                    "supplier_analysis": "supplier_agent",
                    "pricing_analysis": "pricing_agent",
                    "budget_analysis": "budget_agent",
                    "risk_analysis": "risk_agent",
                }[role]
                if current_counts.get(agent_name, 0) < 2:
                    return self._delegate(role, combined)
                result = self._assemble(combined, approval="not_required", execution="blocked")
                result["replan_count"] = 1
                result["warnings"].append(f"{agent_name}重试后仍失败")
                result["summary"] = "专业分析失败且重试未恢复，采购执行已安全阻断。"
                return AIMessage(content=_json(result))

        combined = {**previous, **current, "requirement": request_result}
        risk = combined.get("risk_analysis", {})
        initial_full_route = route == [
            "inventory_analysis",
            "supplier_analysis",
            "pricing_analysis",
            "budget_analysis",
            "risk_analysis",
        ]
        if not risk.get("recommended_plan") and initial_full_route:
            risk_count = current_counts.get("risk_agent", 0)
            if current_counts.get("supplier_agent", 0) > current_counts.get("pricing_agent", 0):
                return self._delegate("pricing_analysis", combined)
            if current_counts.get("pricing_agent", 0) > current_counts.get("budget_agent", 0):
                return self._delegate("budget_analysis", combined)
            if current_counts.get("budget_agent", 0) > current_counts.get("risk_agent", 0):
                return self._delegate("risk_analysis", combined)
            if risk_count == 1:
                reason = risk.get("replan_reason")
                replan_role = "supplier_analysis" if reason in {
                    "delivery_deadline_unmet",
                    "supplier_risk_too_high",
                } else "pricing_analysis"
                return self._delegate(replan_role, {**combined, "replan_reason": reason})

        if not risk.get("recommended_plan"):
            result = self._assemble(combined, approval="not_required", execution="blocked")
            reason = risk.get("replan_reason", "no_feasible_plan")
            result["replan_count"] = max(current_counts.get("risk_agent", 1) - 1, 0)
            result["warnings"].append(f"重新规划后仍无可执行方案：{reason}")
            result["summary"] = "当前约束下没有通过预算、交付和风险硬约束的方案，请调整条件。"
            return AIMessage(content=_json(result))

        if "execution" not in current:
            execution_task = {
                "request": request,
                "recommended_plan": risk["recommended_plan"],
                "budget_analysis": combined.get("budget_analysis", {}),
            }
            return _call("execution_agent", {"task": _json(execution_task)})

        execution = current["execution"]
        execution_status = str(execution.get("status", "failed"))
        rejected = "rejected" in str(execution.get("error", "")).lower()
        if rejected:
            execution_status = "failed"
        approval = "rejected" if rejected else "approved"
        result = self._assemble(combined, approval=approval, execution=execution_status)
        result["executed_actions"] = list(execution.get("executed_actions") or [])
        if execution_status != "success":
            result["warnings"].append(str(execution.get("error") or "采购执行失败"))
        result["summary"] = (
            f"方案已审批并执行，采购申请号{execution.get('request_no')}。"
            if execution_status == "success"
            else "方案未执行成功，未完成采购闭环。"
        )
        return AIMessage(content=_json(result))

    @staticmethod
    def _route(text: str, has_previous: bool) -> list[str]:
        full = [
            "inventory_analysis",
            "supplier_analysis",
            "pricing_analysis",
            "budget_analysis",
            "risk_analysis",
        ]
        if not has_previous:
            return full
        if "方案" in text and any(word in text for word in ("采用", "选择", "第二", "第一", "第三")):
            return ["risk_analysis"]
        if "预算" in text and not re.search(r"\d+\s*(?:台|件|个|套)", text):
            return ["budget_analysis", "risk_analysis"]
        if any(word in text for word in ("不要供应商", "排除", "晚一", "交货", "交付")):
            return ["supplier_analysis", "pricing_analysis", "budget_analysis", "risk_analysis"]
        return full

    @staticmethod
    def _results(messages: Sequence[BaseMessage]) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for message in messages:
            if not isinstance(message, ToolMessage) or message.name not in SUBAGENT_TO_FIELD:
                continue
            value = _tool_value(message)
            if isinstance(value, dict):
                results[SUBAGENT_TO_FIELD[message.name]] = value
        return results

    @staticmethod
    def _counts(messages: Sequence[BaseMessage]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for message in messages:
            if isinstance(message, ToolMessage):
                counts[message.name or ""] = counts.get(message.name or "", 0) + 1
        return counts

    @staticmethod
    def _delegate(role: str, combined: dict[str, Any]) -> AIMessage:
        names = {
            "inventory_analysis": "inventory_agent",
            "supplier_analysis": "supplier_agent",
            "pricing_analysis": "pricing_agent",
            "budget_analysis": "budget_agent",
            "risk_analysis": "risk_agent",
        }
        task = {
            "request": combined.get("requirement", {}).get("request", {}),
            "inventory_analysis": combined.get("inventory_analysis", {}),
            "supplier_analysis": combined.get("supplier_analysis", {}),
            "pricing_analysis": combined.get("pricing_analysis", {}),
            "budget_analysis": combined.get("budget_analysis", {}),
            "risk_analysis": combined.get("risk_analysis", {}),
            "replan_reason": combined.get("replan_reason"),
        }
        return _call(names[role], {"task": _json(task)})

    @staticmethod
    def _empty_result(summary: str) -> dict[str, Any]:
        return {
            "request": {},
            "inventory_analysis": {},
            "candidate_suppliers": [],
            "pricing_analysis": {},
            "budget_analysis": {},
            "risk_analysis": {},
            "recommended_plan": None,
            "alternative_plans": [],
            "approval_status": "not_required",
            "execution_status": "not_started",
            "executed_actions": [],
            "warnings": [summary],
            "missing_fields": [],
            "replan_count": 0,
            "summary": summary,
        }

    @classmethod
    def _assemble(
        cls, combined: dict[str, Any], *, approval: str, execution: str
    ) -> dict[str, Any]:
        requirement = combined.get("requirement", {})
        supplier = combined.get("supplier_analysis", {})
        risk = combined.get("risk_analysis", {})
        warnings: list[str] = []
        for value in combined.values():
            if isinstance(value, dict):
                warnings.extend(str(item) for item in value.get("warnings", []))
                if value.get("status") == "error":
                    warnings.append(str(value.get("error") or value.get("conclusion")))
        return {
            "request": requirement.get("request", {}),
            "inventory_analysis": combined.get("inventory_analysis", {}),
            "candidate_suppliers": supplier.get("candidate_suppliers", []),
            "pricing_analysis": combined.get("pricing_analysis", {}),
            "budget_analysis": combined.get("budget_analysis", {}),
            "risk_analysis": risk,
            "recommended_plan": risk.get("recommended_plan"),
            "alternative_plans": risk.get("alternative_plans", []),
            "approval_status": approval,
            "execution_status": execution,
            "executed_actions": [],
            "warnings": list(dict.fromkeys(warnings)),
            "missing_fields": requirement.get("missing_fields", []),
            "replan_count": 0,
            "summary": "采购分析完成。",
        }
