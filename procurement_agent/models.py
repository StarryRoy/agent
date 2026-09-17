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

# Offline test-double output. Production models generate these statements at
# runtime from the catalog embedded in their instructions.
DETERMINISTIC_READ_SQL = {
    "inventory": """SELECT p.id AS product_id, p.sku, p.name, i.current_qty,
        i.locked_qty, i.in_transit_qty, i.safety_stock, i.updated_at,
        COALESCE(AVG(h.consumed_qty), 0) AS average_monthly_consumption
        FROM products p JOIN inventory i ON i.product_id = p.id
        LEFT JOIN inventory_history h ON h.product_id = p.id WHERE p.id = :product_id
        GROUP BY p.id, p.sku, p.name, i.current_qty, i.locked_qty,
        i.in_transit_qty, i.safety_stock, i.updated_at""",
    "supplier": """SELECT s.id AS supplier_id, s.code, s.name, s.status,
        s.cooperation_status, s.risk_level, sp.min_order_qty, sp.max_capacity,
        sp.standard_lead_days, q.unit_price, q.available_qty, q.lead_time_days,
        COALESCE(1.0 * qr.passed_lots / NULLIF(qr.inspected_lots, 0), 0) AS quality_pass_rate,
        COALESCE(1.0 * dr.on_time_deliveries / NULLIF(dr.deliveries, 0), 0) AS on_time_rate,
        COALESCE(qr.severe_incidents, 0) AS severe_incidents,
        COALESCE(dr.average_delay_days, 0) AS average_delay_days
        FROM suppliers s JOIN supplier_products sp ON sp.supplier_id = s.id
        JOIN quotations q ON q.supplier_id = s.id AND q.product_id = sp.product_id
        LEFT JOIN supplier_quality_records qr ON qr.supplier_id = s.id
        LEFT JOIN supplier_delivery_records dr ON dr.supplier_id = s.id
        WHERE sp.product_id = :product_id AND q.status = 'valid' ORDER BY q.unit_price, s.id""",
    "pricing": """SELECT s.id AS supplier_id, s.code, s.name, q.unit_price, q.min_qty,
        q.available_qty, q.lead_time_days, q.quoted_at, q.valid_until,
        AVG(ph.unit_price) AS historical_average_price, MIN(ph.unit_price) AS historical_min_price,
        MAX(ph.unit_price) AS historical_max_price FROM quotations q
        JOIN suppliers s ON s.id = q.supplier_id
        LEFT JOIN purchase_history ph ON ph.supplier_id = s.id AND ph.product_id = q.product_id
        WHERE q.product_id = :product_id AND q.status = 'valid'
        GROUP BY s.id, s.code, s.name, q.unit_price, q.min_qty, q.available_qty,
        q.lead_time_days, q.quoted_at, q.valid_until ORDER BY q.unit_price""",
    "budget": """SELECT d.id AS department_id, d.code, d.name, b.fiscal_year,
        b.total_amount, b.used_amount, b.approved_pending_amount,
        b.total_amount - b.used_amount - b.approved_pending_amount AS available_amount
        FROM departments d JOIN budgets b ON b.department_id = d.id
        WHERE d.code = :code AND b.fiscal_year = :year""",
    "risk": """SELECT s.code, s.risk_level, q.inspected_lots, q.passed_lots,
        q.severe_incidents, q.note AS quality_note, d.deliveries, d.on_time_deliveries,
        d.average_delay_days, d.note AS delivery_note FROM suppliers s
        LEFT JOIN supplier_quality_records q ON q.supplier_id = s.id
        LEFT JOIN supplier_delivery_records d ON d.supplier_id = s.id
        WHERE s.id IN (SELECT supplier_id FROM supplier_products WHERE product_id = :product_id)""",
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
            return {
                "status": "error",
                "error": parsed.get("error"),
                "content": parsed.get("content"),
            }
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


class DeterministicProcurementModel(BaseChatModel):
    """Explicit test double; production composition never selects it implicitly."""

    role: str
    profile: dict[str, Any] = Field(
        default_factory=lambda: {"max_input_tokens": 128_000}, exclude=True
    )
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
    ) -> DeterministicProcurementModel:
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
            message
            for message in messages[human_index + 1 :]
            if isinstance(message, ToolMessage)
            and message.name in {ROLE_TO_TOOL[self.role], "execute_query", "supplier_status"}
        ]
        primary = ROLE_TO_TOOL[self.role]
        analysis_messages = [item for item in tool_messages if item.name == primary]
        if self.role in DETERMINISTIC_READ_SQL:
            parsed_task = _parse(task)
            envelope = parsed_task if isinstance(parsed_task, dict) else {}
            request = dict(envelope.get("request") or envelope)
            query_messages = [item for item in tool_messages if item.name == "execute_query"]
            if not query_messages:
                sql = DETERMINISTIC_READ_SQL[self.role]
                if request.get("simulate_sql_failure") and self.role == "inventory":
                    sql = "SELECT * FROM missing_inventory_table WHERE product_id = :product_id"
                parameters = {"product_id": request.get("product_id")}
                if self.role == "budget":
                    parameters = {
                        "code": request.get("department_code", "IT"),
                        "year": 2026,
                    }
                return _call("execute_query", {"sql": sql, "parameters": parameters})
            query_result = _tool_value(query_messages[-1])
            if isinstance(query_result, dict) and not query_result.get("ok", True):
                # Model the documented schema-guided correction loop in offline tests.
                return _call(
                    "execute_query",
                    {
                        "sql": DETERMINISTIC_READ_SQL[self.role],
                        "parameters": {"product_id": request.get("product_id")},
                    },
                )
            if not analysis_messages:
                envelope["database_result"] = query_result
                envelope["source_ref"] = "DatabaseToolkit.execute_query"
                return _call(primary, {"task": _json(envelope)})

        if not tool_messages:
            argument_name = "plan_json" if self.role == "execution" else "task"
            return _call(primary, {argument_name: task})

        if (
            self.role == "supplier"
            and analysis_messages
            and not any(item.name == "supplier_status" for item in tool_messages)
            and "supplier_status" in self.bound_tool_names
        ):
            analysis = _tool_value(analysis_messages[-1])
            candidates = (
                analysis.get("candidate_suppliers", []) if isinstance(analysis, dict) else []
            )
            force_failure = False
            try:
                force_failure = bool(
                    json.loads(task).get("request", {}).get("simulate_mcp_failure")
                )
            except (json.JSONDecodeError, AttributeError):
                pass
            return _call(
                "supplier_status",
                {
                    "supplier_codes": [item["code"] for item in candidates],
                    "force_failure": force_failure,
                },
            )

        if self.role == "supplier" and any(
            item.name == "supplier_status" for item in tool_messages
        ):
            analysis = _tool_value(analysis_messages[-1])
            external = _tool_value(
                next(item for item in reversed(tool_messages) if item.name == "supplier_status")
            )
            if not isinstance(analysis, dict):
                analysis = {"status": "error", "error": str(analysis)}
            if isinstance(external, str) and "error" in external.casefold():
                analysis.setdefault("warnings", []).append(
                    "MCP供应商状态服务失败，使用数据库状态降级"
                )
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
                if role == "pricing_analysis" and "交期" in str(
                    current[role].get("conclusion", "")
                ):
                    if combined.get("supplier_analysis", {}).get("analysis_strategy") != (
                        "delivery_first"
                    ):
                        return self._delegate(
                            "supplier_analysis",
                            combined,
                            analysis_strategy="delivery_first",
                            replan_reason="delivery_deadline_unmet",
                            replan_start=True,
                        )
                    return self._delegate(
                        "pricing_analysis",
                        combined,
                        analysis_strategy="delivery_recovery",
                        replan_reason="delivery_deadline_unmet",
                    )
                if current_counts.get(agent_name, 0) < 2:
                    error = current[role].get("error")
                    database_failure = (
                        "database" in str(error).casefold() or "table" in str(error).casefold()
                    )
                    strategy = "schema_recovery" if database_failure else "fallback_recovery"
                    return self._delegate(
                        role,
                        combined,
                        analysis_strategy=strategy,
                        replan_reason="data_or_subagent_failure",
                        replan_start=True,
                    )
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
            supplier_strategy = str(
                combined.get("supplier_analysis", {}).get("analysis_strategy") or "standard"
            )
            pricing_strategy = str(
                combined.get("pricing_analysis", {}).get("analysis_strategy") or "standard"
            )
            budget_strategy = str(
                combined.get("budget_analysis", {}).get("analysis_strategy") or "standard"
            )
            risk_strategy = str(
                combined.get("risk_analysis", {}).get("analysis_strategy") or "standard"
            )
            supplier_replan_in_progress = supplier_strategy not in {"standard", "balanced"}
            replan_in_progress = pricing_strategy not in {"standard", "balanced"}
            if supplier_replan_in_progress and not replan_in_progress:
                return self._delegate("pricing_analysis", combined)
            if replan_in_progress:
                downstream_strategy = f"revalidate_{pricing_strategy}"
                if budget_strategy != downstream_strategy:
                    return self._delegate("budget_analysis", combined)
                if risk_strategy != downstream_strategy:
                    return self._delegate("risk_analysis", combined)
            elif risk_count == 1:
                reason = risk.get("replan_reason")
                replan_role = (
                    "supplier_analysis"
                    if reason
                    in {
                        "delivery_deadline_unmet",
                        "supplier_risk_too_high",
                    }
                    else "pricing_analysis"
                )
                strategy = {
                    "all_suppliers_over_budget": "cost_reduction",
                    "delivery_deadline_unmet": "delivery_first",
                    "supplier_risk_too_high": "risk_first",
                }.get(str(reason), "fallback_recovery")
                return self._delegate(
                    replan_role,
                    {**combined, "replan_reason": reason},
                    analysis_strategy=strategy,
                    replan_reason=reason,
                    replan_start=True,
                )

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
        if re.search(r"\d+\s*(?:台|件|个|套)", text):
            return full
        if "方案" in text and any(
            word in text for word in ("采用", "选择", "第二", "第一", "第三")
        ):
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
    def _delegate(
        role: str,
        combined: dict[str, Any],
        *,
        analysis_strategy: str | None = None,
        replan_reason: str | None = None,
        replan_start: bool = False,
    ) -> AIMessage:
        names = {
            "inventory_analysis": "inventory_agent",
            "supplier_analysis": "supplier_agent",
            "pricing_analysis": "pricing_agent",
            "budget_analysis": "budget_agent",
            "risk_analysis": "risk_agent",
        }
        if analysis_strategy is None and role == "pricing_analysis":
            supplier_strategy = combined.get("supplier_analysis", {}).get("analysis_strategy")
            analysis_strategy = {
                "delivery_first": "delivery_recovery",
                "risk_first": "risk_diversification",
            }.get(supplier_strategy)
        if analysis_strategy is None and role in {"budget_analysis", "risk_analysis"}:
            upstream_strategy = combined.get("pricing_analysis", {}).get("analysis_strategy")
            if upstream_strategy not in {None, "balanced", "standard"}:
                analysis_strategy = f"revalidate_{upstream_strategy}"
        task = {
            "request": combined.get("requirement", {}).get("request", {}),
            "inventory_analysis": combined.get("inventory_analysis", {}),
            "supplier_analysis": combined.get("supplier_analysis", {}),
            "pricing_analysis": combined.get("pricing_analysis", {}),
            "budget_analysis": combined.get("budget_analysis", {}),
            "risk_analysis": combined.get("risk_analysis", {}),
            "replan_reason": replan_reason
            or combined.get("replan_reason")
            or combined.get("pricing_analysis", {}).get("replan_reason")
            or combined.get("supplier_analysis", {}).get("replan_reason"),
            "analysis_strategy": analysis_strategy or "standard",
            "replan_start": replan_start,
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
