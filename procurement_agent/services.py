"""Business semantics layered on Harness' built-in Database Toolkit."""

from __future__ import annotations

import calendar
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from agent_harness import DatabaseToolkit
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, TypeAdapter

from .database import read_sql_asset
from .test_runtime import current_test_config
from .tool_contracts import (
    TOOL_CONTRACTS,
    AnalysisStrategy,
    AssessedPlan,
    BudgetAnalysis,
    BudgetQuerySuccess,
    InventoryAnalysis,
    InventoryQuerySuccess,
    PricingAnalysis,
    PricingQuerySuccess,
    ProcurementRequest,
    QueryFailure,
    RiskQuerySuccess,
    SupplierAnalysis,
    SupplierQuerySuccess,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _consume_query_result(
    subtask: str,
    query_result: BaseModel,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any] | None]:
    result = query_result.model_dump(mode="json")
    evidence = {
        "source": "DatabaseToolkit.execute_query",
        "operation": result.get("operation", "execute_query"),
        "row_count": int(result.get("row_count") or len(result.get("rows") or [])),
        "truncated": bool(result.get("truncated", False)),
        "subtask": subtask,
    }
    if isinstance(query_result, QueryFailure):
        return (
            [],
            evidence,
            result.get("error")
            or {
                "type": "query_failed",
                "message": "查询失败",
            },
        )
    if result.get("truncated"):
        return (
            [],
            evidence,
            {
                "type": "result_truncated",
                "message": "查询结果被截断；请缩小过滤范围后重试",
            },
        )
    rows = list(result.get("rows") or [])
    if not rows:
        return [], evidence, {"type": "empty_result", "message": "查询未返回业务记录"}
    return rows, evidence, None


def _last_day_next_month(today: date) -> date:
    year = today.year + (1 if today.month == 12 else 0)
    month = 1 if today.month == 12 else today.month + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _days_until(raw: str | None, today: date) -> int | None:
    if not raw:
        return None
    try:
        return max((date.fromisoformat(raw) - today).days, 0)
    except ValueError:
        return None


@dataclass(slots=True)
class ProcurementServices:
    """Deterministic calculators plus the separately approved execution write."""

    database: DatabaseToolkit
    today: date

    def parse_requirement(
        self,
        text: str,
        previous_request: ProcurementRequest | None,
    ) -> dict[str, Any]:
        envelope: dict[str, Any] = {"text": text}
        text = text.strip()
        if text.startswith("{"):
            try:
                embedded = json.loads(text)
            except json.JSONDecodeError:
                embedded = None
            if isinstance(embedded, dict):
                envelope = {**envelope, **embedded}
                text = str(embedded.get("text", text)).strip()
        previous = previous_request.model_dump(mode="json") if previous_request else {}
        request = {
            "product": previous.get("product"),
            "product_id": previous.get("product_id"),
            "sku": previous.get("sku"),
            "quantity": previous.get("quantity"),
            "budget": previous.get("budget"),
            "expected_delivery_date": previous.get("expected_delivery_date"),
            "latest_delivery_date": previous.get("latest_delivery_date"),
            "quality_requirements": list(previous.get("quality_requirements") or []),
            "priority": previous.get("priority", "normal"),
            "preferred_suppliers": list(previous.get("preferred_suppliers") or []),
            "excluded_suppliers": list(previous.get("excluded_suppliers") or []),
            "department_code": previous.get("department_code", "IT"),
            "other_constraints": list(previous.get("other_constraints") or []),
            "selected_plan_index": previous.get("selected_plan_index"),
        }

        product_aliases = {
            1: ("工业平板", "平板设备", "设备", "DEV-TAB-STD"),
            2: ("笔记本", "电脑", "LAP-BIZ-14"),
            3: ("显示器", "MON-27-4K"),
        }
        product_names = {
            1: ("标准工业平板设备", "DEV-TAB-STD"),
            2: ("商务笔记本电脑", "LAP-BIZ-14"),
            3: ("27英寸4K显示器", "MON-27-4K"),
        }
        for product_id, aliases in product_aliases.items():
            if any(alias.casefold() in text.casefold() for alias in aliases):
                request["product_id"] = product_id
                request["product"], request["sku"] = product_names[product_id]
                break

        quantity_matches = list(re.finditer(r"(\d[\d,]*)\s*(?:台|件|个|套)", text))
        if quantity_matches:
            request["quantity"] = int(quantity_matches[-1].group(1).replace(",", ""))

        budget_match = re.search(
            r"预算(?:可以|可)?\s*(上限|限额|增加|提高|追加|改为|调整为|是|为|到)?\s*"
            r"(\d+(?:\.\d+)?)\s*(万|万元|元)?",
            text,
        )
        if budget_match:
            action, amount_text, unit = budget_match.groups()
            amount = float(amount_text) * (10000 if unit and unit.startswith("万") else 1)
            if action in {"增加", "提高", "追加"} and request.get("budget") is not None:
                request["budget"] = float(request["budget"]) + amount
            else:
                request["budget"] = amount

        department_aliases = {
            "IT": ("信息技术部", "IT"),
            "OPS": ("运营部", "OPS"),
            "RND": ("研发部", "RND"),
        }
        for code, aliases in department_aliases.items():
            if any(alias.casefold() in text.casefold() for alias in aliases):
                request["department_code"] = code
                break

        absolute_date = re.search(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?", text)
        if absolute_date:
            due = date(*map(int, absolute_date.groups()))
            request["expected_delivery_date"] = due.isoformat()
            request["latest_delivery_date"] = due.isoformat()
        elif "下个月" in text or "下月" in text:
            due = _last_day_next_month(self.today)
            request["expected_delivery_date"] = due.isoformat()
            request["latest_delivery_date"] = due.isoformat()
        elif "月底" in text:
            due = date(
                self.today.year,
                self.today.month,
                calendar.monthrange(self.today.year, self.today.month)[1],
            )
            request["expected_delivery_date"] = due.isoformat()
            request["latest_delivery_date"] = due.isoformat()

        late_match = re.search(r"(?:允许|可以).*?晚\s*(\d+)\s*(天|周)", text)
        if late_match and request.get("latest_delivery_date"):
            delta = int(late_match.group(1)) * (7 if late_match.group(2) == "周" else 1)
            shifted = date.fromisoformat(str(request["latest_delivery_date"])) + timedelta(
                days=delta
            )
            request["latest_delivery_date"] = shifted.isoformat()

        if any(word in text for word in ("必须", "紧急", "优先")):
            request["priority"] = "high"
        if "企业级" in text and "企业级" not in request["quality_requirements"]:
            request["quality_requirements"].append("企业级")
        if "三年质保" in text and "三年质保" not in request["quality_requirements"]:
            request["quality_requirements"].append("三年质保")
        if "合格率" in text:
            quality = re.search(r"合格率.*?(\d+(?:\.\d+)?)\s*%", text)
            if quality:
                requirement = f"批次合格率不低于{quality.group(1)}%"
                if requirement not in request["quality_requirements"]:
                    request["quality_requirements"].append(requirement)
        if "优先保证按期交付" in text and "优先保证按期交付" not in request["other_constraints"]:
            request["other_constraints"].append("优先保证按期交付")
        if "满足数量、质量和预算约束" in text and "满足数量、质量和预算约束" not in request[
            "other_constraints"
        ]:
            request["other_constraints"].append("满足数量、质量和预算约束")
        if "尽量降低总成本" in text and "尽量降低总成本" not in request["other_constraints"]:
            request["other_constraints"].append("尽量降低总成本")

        supplier_aliases = {
            "SUP-A": ("供应商A", "SUP-A", "华东智造"),
            "SUP-B": ("供应商B", "SUP-B", "新锐科技"),
            "SUP-C": ("供应商C", "SUP-C", "远航设备"),
            "SUP-D": ("供应商D", "SUP-D", "稳达工业"),
        }
        if any(word in text for word in ("不要", "排除", "禁用")):
            for code, aliases in supplier_aliases.items():
                if (
                    any(alias.casefold() in text.casefold() for alias in aliases)
                    and code not in request["excluded_suppliers"]
                ):
                    request["excluded_suppliers"].append(code)
        if any(word in text for word in ("指定", "优先用", "首选")):
            for code, aliases in supplier_aliases.items():
                if (
                    any(alias.casefold() in text.casefold() for alias in aliases)
                    and code not in request["preferred_suppliers"]
                ):
                    request["preferred_suppliers"].append(code)

        plan_words = {"第一个": 1, "第一": 1, "第二个": 2, "第二": 2, "第三个": 3, "第三": 3}
        if "方案" in text:
            for word, index in plan_words.items():
                if word in text:
                    request["selected_plan_index"] = index
                    break

        missing = [field for field in ("product", "quantity") if not request.get(field)]
        return {
            "subtask": "requirement_extraction",
            "query": None,
            "data": {"source_text": text},
            "facts": [f"识别到{len(request['excluded_suppliers'])}个排除供应商"],
            "conclusion": "关键字段完整" if not missing else "需要补充关键信息",
            "request": request,
            "missing_fields": missing,
        }

    def calculate_inventory(
        self,
        request: ProcurementRequest,
        query_result: InventoryQuerySuccess | QueryFailure,
        analysis_strategy: AnalysisStrategy,
        replan_reason: str | None,
    ) -> dict[str, Any]:
        """Calculate inventory shortage from LLM-provided query rows only."""

        request = request.model_dump(mode="json")
        strategy = str(analysis_strategy.value)
        test_config = current_test_config()
        if (
            test_config
            and test_config.simulate_subagent_failure
            and strategy != "fallback_recovery"
        ):
            raise RuntimeError("scripted inventory SubAgent failure")
        records, evidence, error = _consume_query_result("inventory_and_consumption", query_result)
        if error:
            return {
                "subtask": "inventory_analysis",
                "evidence": [evidence],
                "facts": [],
                "conclusion": "库存数据不可用",
                "status": "error",
                "analysis_strategy": strategy,
                "replan_reason": replan_reason,
                "error": error,
            }
        if len(records) != 1:
            return {
                "subtask": "inventory_analysis",
                "evidence": [evidence],
                "facts": [],
                "conclusion": "库存查询必须且只能返回目标产品一行",
                "status": "error",
                "analysis_strategy": strategy,
                "replan_reason": replan_reason,
                "error": {
                    "type": "result_contract_violation",
                    "message": f"库存查询返回 {len(records)} 行",
                },
            }
        row = records[0]
        available = int(row["current_qty"]) - int(row["locked_qty"])
        fallback_basis = None
        if strategy == "fallback_recovery":
            forecast = max(math.ceil(int(request.get("quantity") or 0) * 0.15), 1)
            fallback_basis = "历史消耗不可用，按需求量15%保守估算期间消耗"
        else:
            forecast = math.ceil(float(row["average_monthly_consumption"]))
        projected_usable = max(
            0,
            available + int(row["in_transit_qty"]) - forecast - int(row["safety_stock"]),
        )
        demand = int(request.get("quantity") or 0)
        gap = max(demand - projected_usable, 0)
        risk = "high" if available < int(row["safety_stock"]) else "medium" if gap else "low"
        facts = [
            f"当前可用{available}",
            f"在途{row['in_transit_qty']}",
            f"安全库存{row['safety_stock']}",
            f"月均消耗约{forecast}",
        ]
        conclusion = f"预计可用于本次需求{projected_usable}，采购缺口{gap}"
        return {
            "subtask": "inventory_analysis",
            "evidence": [evidence],
            "current_available_quantity": available,
            "in_transit_quantity": int(row["in_transit_qty"]),
            "safety_stock": int(row["safety_stock"]),
            "forecast_consumption": forecast,
            "projected_usable_quantity": projected_usable,
            "estimated_shortfall": gap,
            "recommended_purchase_quantity": gap,
            "inventory_risk": risk,
            "facts": facts,
            "conclusion": conclusion,
            "status": "success",
            "analysis_strategy": strategy,
            "replan_reason": replan_reason,
            "fallback_basis": fallback_basis,
        }

    def calculate_suppliers(
        self,
        request: ProcurementRequest,
        inventory_analysis: InventoryAnalysis,
        query_result: SupplierQuerySuccess | QueryFailure,
        analysis_strategy: AnalysisStrategy,
        replan_reason: str | None,
    ) -> dict[str, Any]:
        """Screen supplier rows without generating or executing SQL."""

        request = request.model_dump(mode="json")
        inventory = inventory_analysis.model_dump(mode="json")
        strategy = str(analysis_strategy.value)
        test_config = current_test_config()
        if (
            test_config
            and test_config.simulate_subagent_failure == "supplier"
            and strategy != "fallback_recovery"
        ):
            raise RuntimeError("scripted supplier SubAgent failure")
        records, evidence, error = _consume_query_result(
            "supplier_capability_and_history",
            query_result,
        )
        if error:
            return {
                "subtask": "supplier_analysis",
                "evidence": [evidence],
                "facts": [],
                "conclusion": "供应商数据不可用",
                "status": "error",
                "analysis_strategy": strategy,
                "replan_reason": replan_reason,
                "error": error,
            }
        excluded = set(request.get("excluded_suppliers") or [])
        qty = int(inventory.get("recommended_purchase_quantity") or request.get("quantity") or 0)
        days = _days_until(request.get("latest_delivery_date"), self.today)
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for row in records:
            reasons: list[str] = []
            if row["status"] != "active":
                reasons.append("供应商当前非启用状态")
            if row["code"] in excluded:
                reasons.append("用户明确排除")
            if qty and int(row["max_capacity"]) < min(qty, int(row["min_order_qty"])):
                reasons.append("供货能力不足")
            item = dict(row)
            item["can_fulfill_alone"] = int(row["max_capacity"]) >= qty
            item["meets_deadline"] = days is None or int(row["lead_time_days"]) <= days
            selection_reasons = [
                f"最大供应能力{row['max_capacity']}",
                f"交付周期{row['lead_time_days']}天",
                f"历史准时率{float(row['on_time_rate']):.1%}",
                f"历史质量合格率{float(row['quality_pass_rate']):.1%}",
            ]
            if strategy == "risk_first" and row["risk_level"] in {"high", "critical"}:
                reasons.append("风险优先重规划排除高风险供应商")
            if reasons:
                item["rejection_reasons"] = reasons
                rejected.append(item)
            else:
                item["selection_reasons"] = selection_reasons
                candidates.append(item)
        if strategy == "delivery_first":
            candidates.sort(key=lambda item: (not item["meets_deadline"], item["lead_time_days"]))
        elif strategy == "risk_first":
            order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            candidates.sort(key=lambda item: (order.get(item["risk_level"], 9), item["unit_price"]))
        facts = [f"查询到{len(candidates)}家可用候选、{len(rejected)}家被排除"]
        conclusion = "存在有效供应商" if candidates else "无有效供应商"
        return {
            "subtask": "supplier_analysis",
            "evidence": [evidence],
            "candidate_suppliers": candidates,
            "rejected_suppliers": rejected,
            "required_quantity": qty,
            "delivery_window_days": days,
            "facts": facts,
            "conclusion": conclusion,
            "status": "success" if candidates else "error",
            "analysis_strategy": strategy,
            "replan_reason": replan_reason,
            "external_status": {},
            "mcp_status": "not_checked",
            "warnings": [],
        }

    def calculate_pricing(
        self,
        request: ProcurementRequest,
        inventory_analysis: InventoryAnalysis,
        supplier_analysis: SupplierAnalysis,
        query_result: PricingQuerySuccess | QueryFailure,
        analysis_strategy: AnalysisStrategy,
        replan_reason: str | None,
    ) -> dict[str, Any]:
        """Build price plans from contract-shaped query rows."""

        request = request.model_dump(mode="json")
        inventory = inventory_analysis.model_dump(mode="json")
        supplier_analysis = supplier_analysis.model_dump(mode="json")
        strategy = str(analysis_strategy.value)
        candidates = list(supplier_analysis.get("candidate_suppliers") or [])
        qty = int(inventory.get("recommended_purchase_quantity") or request.get("quantity") or 0)
        records, evidence, error = _consume_query_result(
            "current_and_historical_prices",
            query_result,
        )
        if error:
            return {
                "subtask": "pricing_analysis",
                "evidence": [evidence],
                "facts": [],
                "conclusion": "报价数据不可用",
                "status": "error",
                "analysis_strategy": strategy,
                "replan_reason": replan_reason,
                "error": error,
            }
        candidate_codes = {item.get("code") for item in candidates}
        external = supplier_analysis.get("external_status") or {}
        quotes: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []
        for row in records:
            if row["code"] not in candidate_codes:
                continue
            historical = row.get("historical_average_price")
            variance = (
                (float(row["unit_price"]) - float(historical)) / float(historical)
                if historical
                else None
            )
            quote = dict(row)
            quote["price_change_rate"] = variance
            quote["external_status"] = external.get(row["code"], "unknown")
            quotes.append(quote)
            if variance is not None and abs(variance) > 0.15:
                anomalies.append(
                    {"supplier": row["code"], "type": "price_variance", "rate": variance}
                )

        quote_by_code = {item["code"]: item for item in quotes}
        days = supplier_analysis.get("delivery_window_days")
        eligible = [
            item
            for item in candidates
            if item["code"] in quote_by_code
            and (
                strategy == "delivery_recovery"
                or days is None
                or int(quote_by_code[item["code"]]["lead_time_days"]) <= int(days)
            )
            and external.get(item["code"], "operational") not in {"suspended", "blocked"}
        ]
        plans: list[dict[str, Any]] = []

        def terms(quote: dict[str, Any]) -> tuple[float, int, bool]:
            price = float(quote["unit_price"])
            lead = int(quote["lead_time_days"])
            conditional = False
            if strategy == "cost_reduction":
                historical = float(quote.get("historical_average_price") or price)
                price = round(min(price, historical) * 0.98, 2)
                conditional = True
            elif strategy == "delivery_recovery":
                price = round(price * 1.08, 2)
                lead = max(math.ceil(lead * 0.7), 1)
                conditional = True
            return price, lead, conditional

        def make_plan(name: str, allocations: list[dict[str, Any]]) -> dict[str, Any]:
            total_qty = sum(int(item["quantity"]) for item in allocations)
            total = round(
                sum(float(item["unit_price"]) * int(item["quantity"]) for item in allocations), 2
            )
            max_lead = max((int(item["lead_time_days"]) for item in allocations), default=0)
            budget = request.get("budget")
            return {
                "plan_id": f"PLAN-{len(plans) + 1}",
                "name": name,
                "allocations": allocations,
                "quantity": total_qty,
                "average_unit_price": round(total / total_qty, 2) if total_qty else 0,
                "total_cost": total,
                "max_lead_time_days": max_lead,
                "meets_quantity": total_qty == qty,
                "meets_deadline": days is None or max_lead <= int(days),
                "within_user_budget": budget is None or total <= float(budget),
                "analysis_strategy": strategy,
                "conditional": any(item.get("conditional") for item in allocations),
            }

        for candidate in eligible:
            quote = quote_by_code[candidate["code"]]
            capacity = min(int(candidate["max_capacity"]), int(quote["available_qty"]))
            if qty >= int(quote["min_qty"]) and capacity >= qty:
                unit_price, lead_time, conditional = terms(quote)
                plans.append(
                    make_plan(
                        f"{candidate['code']}单一供应商方案",
                        [
                            {
                                "supplier_id": candidate["supplier_id"],
                                "supplier_code": candidate["code"],
                                "supplier_name": candidate["name"],
                                "quantity": qty,
                                "unit_price": unit_price,
                                "lead_time_days": lead_time,
                                "conditional": conditional,
                            }
                        ],
                    )
                )

        allocations: list[dict[str, Any]] = []
        remaining = qty
        for candidate in sorted(
            eligible, key=lambda item: float(quote_by_code[item["code"]]["unit_price"])
        ):
            if remaining <= 0:
                break
            quote = quote_by_code[candidate["code"]]
            capacity = min(int(candidate["max_capacity"]), int(quote["available_qty"]))
            take = min(remaining, capacity)
            minimum = int(quote["min_qty"])
            if take < minimum:
                continue
            unit_price, lead_time, conditional = terms(quote)
            allocations.append(
                {
                    "supplier_id": candidate["supplier_id"],
                    "supplier_code": candidate["code"],
                    "supplier_name": candidate["name"],
                    "quantity": take,
                    "unit_price": unit_price,
                    "lead_time_days": lead_time,
                    "conditional": conditional,
                }
            )
            remaining -= take
        if allocations:
            plans.append(make_plan("成本优先多供应商组合", allocations))

        balanced_allocations: list[dict[str, Any]] = []
        remaining = qty
        balanced_pool = [
            item
            for item in eligible
            if item.get("risk_level") in {"low", "medium"}
            and external.get(item["code"], "operational") == "operational"
        ]
        for candidate in sorted(
            balanced_pool, key=lambda item: float(quote_by_code[item["code"]]["unit_price"])
        ):
            if remaining <= 0:
                break
            quote = quote_by_code[candidate["code"]]
            capacity = min(int(candidate["max_capacity"]), int(quote["available_qty"]))
            take = min(remaining, capacity)
            if take < int(quote["min_qty"]):
                continue
            unit_price, lead_time, conditional = terms(quote)
            balanced_allocations.append(
                {
                    "supplier_id": candidate["supplier_id"],
                    "supplier_code": candidate["code"],
                    "supplier_name": candidate["name"],
                    "quantity": take,
                    "unit_price": unit_price,
                    "lead_time_days": lead_time,
                    "conditional": conditional,
                }
            )
            remaining -= take
        if balanced_allocations:
            plans.append(make_plan("风险均衡多供应商组合", balanced_allocations))

        if strategy == "cost_reduction" and request.get("budget") and eligible:
            cheapest = min(eligible, key=lambda item: terms(quote_by_code[item["code"]])[0])
            quote = quote_by_code[cheapest["code"]]
            unit_price, lead_time, _ = terms(quote)
            affordable = min(
                int(float(request["budget"]) // unit_price),
                int(cheapest["max_capacity"]),
                int(quote["available_qty"]),
                qty,
            )
            if affordable >= int(quote["min_qty"]):
                plans.append(
                    make_plan(
                        "预算内分阶段采购备选",
                        [
                            {
                                "supplier_id": cheapest["supplier_id"],
                                "supplier_code": cheapest["code"],
                                "supplier_name": cheapest["name"],
                                "quantity": affordable,
                                "unit_price": unit_price,
                                "lead_time_days": lead_time,
                                "conditional": True,
                            }
                        ],
                    )
                )

        unique: dict[str, dict[str, Any]] = {}
        for plan in plans:
            key = _json(plan["allocations"])
            unique.setdefault(key, plan)
        plans = sorted(
            unique.values(), key=lambda item: (not item["meets_quantity"], item["total_cost"])
        )
        for index, plan in enumerate(plans, start=1):
            plan["plan_id"] = f"PLAN-{index}"
        facts = [f"比较{len(quotes)}份有效报价和{len(plans)}个可行组合"]
        conclusion = "已生成价格方案" if plans else "无法形成满足数量和交期的组合"
        return {
            "subtask": "pricing_analysis",
            "evidence": [evidence],
            "quotations": quotes,
            "historical_reference": [
                {
                    "supplier": item["code"],
                    "average": item.get("historical_average_price"),
                    "current": item["unit_price"],
                    "change_rate": item.get("price_change_rate"),
                }
                for item in quotes
            ],
            "cost_differences": [
                {"plan_id": plan["plan_id"], "total_cost": plan["total_cost"]} for plan in plans
            ],
            "anomalies": anomalies,
            "plans": plans,
            "recommended_price_plan": plans[0] if plans else None,
            "facts": facts,
            "conclusion": conclusion,
            "status": "success" if plans else "error",
            "analysis_strategy": strategy,
            "replan_reason": replan_reason,
        }

    def calculate_budget(
        self,
        request: ProcurementRequest,
        pricing_analysis: PricingAnalysis,
        query_result: BudgetQuerySuccess | QueryFailure,
        analysis_strategy: AnalysisStrategy,
        replan_reason: str | None,
    ) -> dict[str, Any]:
        """Calculate budget availability from LLM-provided rows."""

        strategy = str(analysis_strategy.value)
        request = request.model_dump(mode="json")
        pricing = pricing_analysis.model_dump(mode="json")
        records, evidence, error = _consume_query_result(
            "department_budget",
            query_result,
        )
        if error:
            return {
                "subtask": "budget_analysis",
                "evidence": [evidence],
                "facts": [],
                "status": "error",
                "conclusion": "预算数据不可用",
                "analysis_strategy": strategy,
                "replan_reason": replan_reason,
                "error": error,
            }
        if len(records) != 1:
            return {
                "subtask": "budget_analysis",
                "evidence": [evidence],
                "facts": [],
                "status": "error",
                "conclusion": "预算查询必须且只能返回部门财年一行",
                "analysis_strategy": strategy,
                "replan_reason": replan_reason,
                "error": {
                    "type": "result_contract_violation",
                    "message": f"预算查询返回 {len(records)} 行",
                },
            }
        row = records[0]
        plans = list(pricing.get("plans") or [])
        estimated = min((float(plan["total_cost"]) for plan in plans), default=0.0)
        available = float(row["available_amount"])
        user_budget = request.get("budget")
        effective = min(available, float(user_budget)) if user_budget is not None else available
        over = max(estimated - effective, 0)
        facts = [
            f"部门可用预算{available:.2f}",
            f"用户预算上限{float(user_budget):.2f}"
            if user_budget is not None
            else "用户未设置单独上限",
            f"最低可行方案预计占用{estimated:.2f}",
        ]
        conclusion = "预算满足" if over == 0 else f"超出有效预算{over:.2f}"
        return {
            "subtask": "budget_analysis",
            "evidence": [evidence],
            "department": {"id": row["department_id"], "code": row["code"], "name": row["name"]},
            "budget_total": float(row["total_amount"]),
            "used_budget": float(row["used_amount"]),
            "approved_not_executed": float(row["approved_pending_amount"]),
            "available_budget": available,
            "user_budget": float(user_budget) if user_budget is not None else None,
            "effective_available_budget": effective,
            "estimated_occupation": estimated,
            "within_budget": over == 0,
            "over_budget_amount": over,
            "adjustment_room": max(effective - estimated, 0),
            "budget_risk": "high" if over else "medium" if estimated > effective * 0.9 else "low",
            "facts": facts,
            "conclusion": conclusion,
            "status": "success",
            "analysis_strategy": strategy,
            "replan_reason": replan_reason,
        }

    def calculate_risk(
        self,
        request: ProcurementRequest,
        supplier_analysis: SupplierAnalysis,
        pricing_analysis: PricingAnalysis,
        budget_analysis: BudgetAnalysis,
        query_result: RiskQuerySuccess | QueryFailure,
        analysis_strategy: AnalysisStrategy,
        replan_reason: str | None,
    ) -> dict[str, Any]:
        """Score plans from query rows and upstream deterministic results."""

        strategy = str(analysis_strategy.value)
        request = request.model_dump(mode="json")
        supplier = supplier_analysis.model_dump(mode="json")
        pricing = pricing_analysis.model_dump(mode="json")
        budget = budget_analysis.model_dump(mode="json")
        candidates = {item["code"]: item for item in supplier.get("candidate_suppliers") or []}
        rows, evidence, error = _consume_query_result(
            "supplier_risk_records",
            query_result,
        )
        if error:
            return {
                "subtask": "risk_analysis",
                "evidence": [evidence],
                "facts": [],
                "conclusion": "风险数据不可用",
                "status": "error",
                "analysis_strategy": strategy,
                "error": error,
            }
        records = {row["code"]: row for row in rows}
        effective_budget = float(budget.get("effective_available_budget") or 0)
        assessed: list[dict[str, Any]] = []
        for plan in pricing.get("plans") or []:
            score = 0
            items: list[str] = []
            allocations = list(plan.get("allocations") or [])
            for allocation in allocations:
                code = allocation["supplier_code"]
                candidate = candidates.get(code, {})
                record = records.get(code, {})
                base = {"low": 4, "medium": 12, "high": 28, "critical": 50}.get(
                    str(candidate.get("risk_level", record.get("risk_level", "medium"))), 12
                )
                score += base
                inspected = float(record.get("inspected_lots") or 0)
                quality_rate = float(record.get("passed_lots") or 0) / inspected if inspected else 0
                deliveries = float(record.get("deliveries") or 0)
                on_time_rate = (
                    float(record.get("on_time_deliveries") or 0) / deliveries if deliveries else 0
                )
                if quality_rate < 0.95:
                    score += 18
                    items.append(f"{code}质量合格率{quality_rate:.1%}")
                if on_time_rate < 0.85:
                    score += 15
                    items.append(f"{code}准时交付率{on_time_rate:.1%}")
                if int(record.get("severe_incidents") or 0) > 0:
                    score += 15
                    items.append(f"{code}存在严重质量异常")
            if len(allocations) == 1 and int(plan.get("quantity") or 0) >= 300:
                score += 15
                items.append("单一供应商集中度高")
            if not plan.get("meets_deadline", False):
                score += 35
                items.append("交期不满足")
            if not plan.get("meets_quantity", False):
                score += 45
                items.append("供货数量不足")
            if effective_budget and float(plan["total_cost"]) > effective_budget:
                score += 40
                items.append("超出有效预算")
            score = min(score, 100)
            level = (
                "low"
                if score <= 20
                else "medium"
                if score <= 40
                else "high"
                if score <= 70
                else "critical"
            )
            assessed_plan = dict(plan)
            assessed_plan.update(risk_score=score, risk_level=level, risk_items=items)
            assessed.append(assessed_plan)

        feasible = [
            item
            for item in assessed
            if item.get("meets_quantity")
            and item.get("meets_deadline")
            and (not effective_budget or float(item["total_cost"]) <= effective_budget)
            and item["risk_level"] not in {"critical"}
        ]
        selected_index = request.get("selected_plan_index")
        recommended = None
        if selected_index and 1 <= int(selected_index) <= len(assessed):
            selected = assessed[int(selected_index) - 1]
            if selected in feasible:
                recommended = selected
        if recommended is None and feasible:
            recommended = min(feasible, key=lambda item: (item["risk_score"], item["total_cost"]))
        alternatives = [item for item in assessed if item is not recommended and item in feasible]
        not_recommended = [item for item in assessed if item not in feasible]
        if not assessed:
            main_risks = ["无可评估采购方案"]
        elif recommended is None:
            main_risks = sorted({risk for item in assessed for risk in item["risk_items"]})
        else:
            main_risks = list(recommended["risk_items"])
        facts = [f"评估{len(assessed)}个方案，{len(feasible)}个通过硬约束"]
        conclusion = "形成推荐方案" if recommended else "需要重新规划或用户调整约束"
        return {
            "subtask": "risk_analysis",
            "evidence": [evidence],
            "risk_level": recommended.get("risk_level") if recommended else "high",
            "main_risks": main_risks,
            "risk_basis": records,
            "recommended_plan": recommended,
            "alternative_plans": alternatives,
            "not_recommended_plans": not_recommended,
            "replan_reason": None
            if recommended
            else self._replan_reason(assessed, effective_budget),
            "facts": facts,
            "conclusion": conclusion,
            "status": "success" if recommended else "needs_replan",
            "analysis_strategy": strategy,
        }

    @staticmethod
    def _replan_reason(plans: list[dict[str, Any]], effective_budget: float) -> str:
        if (
            plans
            and effective_budget
            and all(float(item["total_cost"]) > effective_budget for item in plans)
        ):
            return "all_suppliers_over_budget"
        if plans and all(not item.get("meets_deadline") for item in plans):
            return "delivery_deadline_unmet"
        if plans and all(item.get("risk_level") in {"high", "critical"} for item in plans):
            return "supplier_risk_too_high"
        return "insufficient_or_conflicting_data"

    def execute_plan(
        self,
        request: ProcurementRequest,
        recommended_plan: AssessedPlan,
        budget_analysis: BudgetAnalysis,
        session_id: str,
    ) -> dict[str, Any]:
        request = request.model_dump(mode="json")
        plan = recommended_plan.model_dump(mode="json")
        budget = budget_analysis.model_dump(mode="json")
        test_config = current_test_config()
        if test_config and test_config.simulate_execution_failure:
            return {
                "status": "failed",
                "error": {
                    "type": "scripted_execution_failure",
                    "message": "执行前外部采购系统失败",
                },
                "executed_actions": [],
            }
        total_cost = float(plan["total_cost"])
        now = datetime.now(UTC).isoformat()
        nonce = uuid.uuid4().hex[:10].upper()
        request_no = f"PR-{self.today:%Y%m%d}-{nonce}"
        average_price = total_cost / int(plan["quantity"])
        transactional_plan = dict(plan)
        if test_config and test_config.simulate_atomic_failure:
            transactional_plan["__force_failure"] = True

        # One DatabaseToolkit write is the transaction boundary. SQLite executes all
        # trigger steps in the same statement and rolls everything back on any RAISE,
        # foreign-key, budget, status, or log failure.
        request_write = self.database.execute_write(
            read_sql_asset("purchase_requests", "execute.sql"),
            {
                "request_no": request_no,
                "session_id": session_id,
                "department_id": budget["department"]["id"],
                "product_id": request.get("product_id"),
                "requested_qty": request.get("quantity"),
                "approved_qty": plan.get("quantity"),
                "required_date": request.get("latest_delivery_date"),
                "plan_json": _json(transactional_plan),
                "unit_price": average_price,
                "total_amount": total_cost,
                "created_at": now,
            },
        )
        if not request_write.get("ok"):
            return {
                "status": "failed",
                "error": {
                    "type": "atomic_execution_failed",
                    "message": "采购事务已整体回滚",
                    "database_error": request_write.get("error"),
                },
                "executed_actions": [],
                "transaction": {"committed": False, "rolled_back": True},
            }
        request_id = request_write["last_insert_id"]
        actions = [
            {"action": "create_purchase_request", "id": request_id, "request_no": request_no},
            {"action": "create_purchase_orders", "count": len(plan["allocations"])},
            {"action": "reserve_budget", "amount": total_cost},
            {"action": "update_purchase_status", "status": "ordered"},
            {"action": "record_approval", "status": "approved"},
            {"action": "write_operation_log", "session_id": session_id},
        ]
        return {
            "status": "success",
            "purchase_request_id": request_id,
            "request_no": request_no,
            "purchase_order_count": len(plan["allocations"]),
            "budget_reserved": total_cost,
            "executed_actions": actions,
            "transaction": {
                "committed": True,
                "rolled_back": False,
                "verified_by": "database_constraints_and_triggers",
            },
        }

    def tools(self) -> dict[str, BaseTool]:
        """Build explicit business tools used by the corresponding SubAgents."""

        definitions = {
            "parse_requirement": (
                self.parse_requirement,
                "Parse and merge a procurement requirement.",
            ),
            "calculate_inventory": (
                self.calculate_inventory,
                "Calculate inventory shortage from a successful execute_query result.",
            ),
            "calculate_suppliers": (
                self.calculate_suppliers,
                "Screen suppliers from a successful execute_query result.",
            ),
            "calculate_pricing": (
                self.calculate_pricing,
                "Build price combinations from a successful execute_query result.",
            ),
            "calculate_budget": (
                self.calculate_budget,
                "Calculate budget fit from a successful execute_query result.",
            ),
            "calculate_risk": (
                self.calculate_risk,
                "Score plans from a successful execute_query result.",
            ),
            "execute_procurement_plan": (
                self.execute_plan,
                "Execute one approved procurement plan.",
            ),
        }
        result: dict[str, BaseTool] = {}
        for name, (function, description) in definitions.items():
            args_schema, output_schema = TOOL_CONTRACTS[name]
            output_adapter = TypeAdapter(output_schema)

            def invoke(
                _function: Any = function,
                _output_adapter: TypeAdapter[Any] = output_adapter,
                **arguments: Any,
            ) -> dict[str, Any]:
                validated = _output_adapter.validate_python(_function(**arguments))
                return validated.model_dump(mode="json")

            metadata = {
                "database_toolkit": name == "execute_procurement_plan",
                "business_domain": "procurement",
                "deterministic_calculator": name.startswith("calculate_"),
                "output_schema": output_schema.model_json_schema(),
            }
            if name == "execute_procurement_plan":
                metadata["database_operation"] = "write"
            result[name] = StructuredTool(
                func=invoke,
                name=name,
                description=description,
                args_schema=args_schema,
                metadata=metadata,
            )
        result["execute_procurement_plan"].metadata.update(
            {
                "harness_approval": True,
                "harness_approval_message": "批准后将创建采购申请/订单并占用预算，是否继续？",
            }
        )
        return result
