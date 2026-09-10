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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _payload(task: str) -> dict[str, Any]:
    try:
        parsed = json.loads(task)
    except (TypeError, json.JSONDecodeError):
        return {"text": str(task)}
    return parsed if isinstance(parsed, dict) else {"text": str(task)}


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return list(result.get("rows", [])) if result.get("ok") else []


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
    """Domain operations; every database call goes through ``DatabaseToolkit``."""

    database: DatabaseToolkit
    today: date

    def _query(
        self, subtask: str, sql: str, parameters: dict[str, Any] | list[Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        result = self.database.execute_query(sql, parameters)
        context = {
            "subtask": subtask,
            "query": " ".join(sql.split()),
            "data": result,
            "facts": [],
            "conclusion": "查询成功" if result.get("ok") else "查询失败",
        }
        return result, context

    def parse_requirement(self, task: str) -> dict[str, Any]:
        envelope = _payload(task)
        text = str(envelope.get("text", "")).strip()
        if text.startswith("{"):
            try:
                embedded = json.loads(text)
            except json.JSONDecodeError:
                embedded = None
            if isinstance(embedded, dict):
                envelope = {**envelope, **embedded}
                text = str(embedded.get("text", text)).strip()
        previous = dict(envelope.get("previous_request") or {})
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
            r"预算(?:可以|可)?\s*(增加|提高|追加|改为|调整为|是|为|到)?\s*"
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
        if "三年质保" in text and "三年质保" not in request["quality_requirements"]:
            request["quality_requirements"].append("三年质保")
        if "合格率" in text:
            quality = re.search(r"合格率.*?(\d+(?:\.\d+)?)%", text)
            if quality:
                request["quality_requirements"].append(f"批次合格率不低于{quality.group(1)}%")

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

        simulations = {
            key: envelope.get(key, False)
            for key in (
                "simulate_sql_failure",
                "simulate_subagent_failure",
                "simulate_mcp_failure",
                "simulate_execution_failure",
                "simulate_atomic_failure",
            )
        }
        request.update(simulations)
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

    def analyze_inventory(self, task: str) -> dict[str, Any]:
        envelope = _payload(task)
        request = dict(envelope.get("request") or envelope)
        strategy = str(envelope.get("analysis_strategy") or "standard")
        if request.get("simulate_subagent_failure") and strategy != "fallback_recovery":
            raise RuntimeError("scripted inventory SubAgent failure")
        schema_evidence: dict[str, Any] | None = None
        if strategy == "schema_recovery":
            schema_evidence = self.database.get_schema("inventory")
            if not schema_evidence.get("ok"):
                return {
                    "subtask": "inventory_analysis",
                    "queries": [],
                    "facts": [],
                    "conclusion": "库存 Schema 恢复失败",
                    "status": "error",
                    "analysis_strategy": strategy,
                    "replan_reason": envelope.get("replan_reason"),
                    "error": schema_evidence.get("error"),
                }
        if strategy == "fallback_recovery":
            sql = """
                SELECT p.id AS product_id, p.sku, p.name, i.current_qty, i.locked_qty,
                       i.in_transit_qty, i.safety_stock, i.updated_at
                FROM products p JOIN inventory i ON i.product_id = p.id
                WHERE p.id = :product_id
            """
        else:
            sql = """
                SELECT p.id AS product_id, p.sku, p.name, i.current_qty, i.locked_qty,
                       i.in_transit_qty, i.safety_stock, i.updated_at,
                       COALESCE(AVG(h.consumed_qty), 0) AS average_monthly_consumption
                FROM products p
                JOIN inventory i ON i.product_id = p.id
                LEFT JOIN inventory_history h ON h.product_id = p.id
                WHERE p.id = :product_id
                GROUP BY p.id, p.sku, p.name, i.current_qty, i.locked_qty,
                         i.in_transit_qty, i.safety_stock, i.updated_at
            """
        if request.get("simulate_sql_failure") and strategy != "schema_recovery":
            sql = "SELECT * FROM missing_inventory_table WHERE product_id = :product_id"
        result, query = self._query(
            "inventory_and_consumption", sql, {"product_id": request.get("product_id")}
        )
        records = _rows(result)
        if not records:
            return {
                "subtask": "inventory_analysis",
                "queries": [query],
                "facts": [],
                "conclusion": "库存数据不可用",
                "status": "error",
                "analysis_strategy": strategy,
                "replan_reason": envelope.get("replan_reason"),
                "error": result.get("error", {"type": "empty_result", "message": "无库存记录"}),
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
        query["facts"] = [
            f"当前可用{available}",
            f"在途{row['in_transit_qty']}",
            f"安全库存{row['safety_stock']}",
            f"月均消耗约{forecast}",
        ]
        query["conclusion"] = f"预计可用于本次需求{projected_usable}，采购缺口{gap}"
        return {
            "subtask": "inventory_analysis",
            "queries": [query],
            "current_available_quantity": available,
            "in_transit_quantity": int(row["in_transit_qty"]),
            "safety_stock": int(row["safety_stock"]),
            "forecast_consumption": forecast,
            "projected_usable_quantity": projected_usable,
            "estimated_shortfall": gap,
            "recommended_purchase_quantity": gap,
            "inventory_risk": risk,
            "facts": query["facts"],
            "conclusion": query["conclusion"],
            "status": "success",
            "analysis_strategy": strategy,
            "replan_reason": envelope.get("replan_reason"),
            "schema_evidence": schema_evidence,
            "fallback_basis": fallback_basis,
        }

    def analyze_suppliers(self, task: str) -> dict[str, Any]:
        envelope = _payload(task)
        request = dict(envelope.get("request") or {})
        inventory = dict(envelope.get("inventory_analysis") or {})
        strategy = str(envelope.get("analysis_strategy") or "balanced")
        if (
            request.get("simulate_subagent_failure") == "supplier"
            and strategy != "fallback_recovery"
        ):
            raise RuntimeError("scripted supplier SubAgent failure")
        sql = """
            SELECT s.id AS supplier_id, s.code, s.name, s.status, s.cooperation_status,
                   s.risk_level, sp.min_order_qty, sp.max_capacity, sp.standard_lead_days,
                   q.unit_price, q.available_qty, q.lead_time_days,
                   COALESCE(1.0 * qr.passed_lots / NULLIF(qr.inspected_lots, 0), 0) AS quality_pass_rate,
                   COALESCE(1.0 * dr.on_time_deliveries / NULLIF(dr.deliveries, 0), 0) AS on_time_rate,
                   COALESCE(qr.severe_incidents, 0) AS severe_incidents,
                   COALESCE(dr.average_delay_days, 0) AS average_delay_days
            FROM suppliers s
            JOIN supplier_products sp ON sp.supplier_id = s.id
            JOIN quotations q ON q.supplier_id = s.id AND q.product_id = sp.product_id
            LEFT JOIN supplier_quality_records qr ON qr.supplier_id = s.id
            LEFT JOIN supplier_delivery_records dr ON dr.supplier_id = s.id
            WHERE sp.product_id = :product_id AND q.status = 'valid'
            ORDER BY q.unit_price, s.id
        """
        result, query = self._query(
            "supplier_capability_and_history", sql, {"product_id": request.get("product_id")}
        )
        excluded = set(request.get("excluded_suppliers") or [])
        qty = int(inventory.get("recommended_purchase_quantity") or request.get("quantity") or 0)
        days = _days_until(request.get("latest_delivery_date"), self.today)
        candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for row in _rows(result):
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
            item["selection_reasons"] = [
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
                candidates.append(item)
        if strategy == "delivery_first":
            candidates.sort(key=lambda item: (not item["meets_deadline"], item["lead_time_days"]))
        elif strategy == "risk_first":
            order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            candidates.sort(key=lambda item: (order.get(item["risk_level"], 9), item["unit_price"]))
        query["facts"] = [f"查询到{len(candidates)}家可用候选、{len(rejected)}家被排除"]
        query["conclusion"] = "存在有效供应商" if candidates else "无有效供应商"
        return {
            "subtask": "supplier_analysis",
            "queries": [query],
            "candidate_suppliers": candidates,
            "rejected_suppliers": rejected,
            "required_quantity": qty,
            "delivery_window_days": days,
            "facts": query["facts"],
            "conclusion": query["conclusion"],
            "status": "success" if candidates else "error",
            "analysis_strategy": strategy,
            "replan_reason": envelope.get("replan_reason"),
        }

    def analyze_pricing(self, task: str) -> dict[str, Any]:
        envelope = _payload(task)
        request = dict(envelope.get("request") or {})
        inventory = dict(envelope.get("inventory_analysis") or {})
        supplier_analysis = dict(envelope.get("supplier_analysis") or {})
        strategy = str(envelope.get("analysis_strategy") or "balanced")
        candidates = list(supplier_analysis.get("candidate_suppliers") or [])
        qty = int(inventory.get("recommended_purchase_quantity") or request.get("quantity") or 0)
        sql = """
            SELECT s.id AS supplier_id, s.code, s.name, q.unit_price, q.min_qty,
                   q.available_qty, q.lead_time_days, q.quoted_at, q.valid_until,
                   AVG(ph.unit_price) AS historical_average_price,
                   MIN(ph.unit_price) AS historical_min_price,
                   MAX(ph.unit_price) AS historical_max_price
            FROM quotations q
            JOIN suppliers s ON s.id = q.supplier_id
            LEFT JOIN purchase_history ph ON ph.supplier_id = s.id AND ph.product_id = q.product_id
            WHERE q.product_id = :product_id AND q.status = 'valid'
            GROUP BY s.id, s.code, s.name, q.unit_price, q.min_qty, q.available_qty,
                     q.lead_time_days, q.quoted_at, q.valid_until
            ORDER BY q.unit_price
        """
        result, query = self._query(
            "current_and_historical_prices", sql, {"product_id": request.get("product_id")}
        )
        candidate_codes = {item.get("code") for item in candidates}
        external = supplier_analysis.get("external_status") or {}
        quotes: list[dict[str, Any]] = []
        anomalies: list[dict[str, Any]] = []
        for row in _rows(result):
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
        query["facts"] = [f"比较{len(quotes)}份有效报价和{len(plans)}个可行组合"]
        query["conclusion"] = "已生成价格方案" if plans else "无法形成满足数量和交期的组合"
        return {
            "subtask": "pricing_analysis",
            "queries": [query],
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
            "facts": query["facts"],
            "conclusion": query["conclusion"],
            "status": "success" if plans else "error",
            "analysis_strategy": strategy,
            "replan_reason": envelope.get("replan_reason"),
        }

    def analyze_budget(self, task: str) -> dict[str, Any]:
        envelope = _payload(task)
        strategy = str(envelope.get("analysis_strategy") or "standard")
        request = dict(envelope.get("request") or {})
        pricing = dict(envelope.get("pricing_analysis") or {})
        sql = """
            SELECT d.id AS department_id, d.code, d.name, b.fiscal_year,
                   b.total_amount, b.used_amount, b.approved_pending_amount,
                   b.total_amount - b.used_amount - b.approved_pending_amount AS available_amount
            FROM departments d JOIN budgets b ON b.department_id = d.id
            WHERE d.code = :code AND b.fiscal_year = :year
        """
        result, query = self._query(
            "department_budget",
            sql,
            {"code": request.get("department_code", "IT"), "year": self.today.year},
        )
        records = _rows(result)
        if not records:
            return {
                "subtask": "budget_analysis",
                "queries": [query],
                "status": "error",
                "conclusion": "预算数据不可用",
            }
        row = records[0]
        plans = list(pricing.get("plans") or [])
        estimated = min((float(plan["total_cost"]) for plan in plans), default=0.0)
        available = float(row["available_amount"])
        user_budget = request.get("budget")
        effective = min(available, float(user_budget)) if user_budget is not None else available
        over = max(estimated - effective, 0)
        query["facts"] = [
            f"部门可用预算{available:.2f}",
            f"用户预算上限{float(user_budget):.2f}"
            if user_budget is not None
            else "用户未设置单独上限",
            f"最低可行方案预计占用{estimated:.2f}",
        ]
        query["conclusion"] = "预算满足" if over == 0 else f"超出有效预算{over:.2f}"
        return {
            "subtask": "budget_analysis",
            "queries": [query],
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
            "facts": query["facts"],
            "conclusion": query["conclusion"],
            "status": "success",
            "analysis_strategy": strategy,
            "replan_reason": envelope.get("replan_reason"),
        }

    def analyze_risk(self, task: str) -> dict[str, Any]:
        envelope = _payload(task)
        strategy = str(envelope.get("analysis_strategy") or "standard")
        request = dict(envelope.get("request") or {})
        supplier = dict(envelope.get("supplier_analysis") or {})
        pricing = dict(envelope.get("pricing_analysis") or {})
        budget = dict(envelope.get("budget_analysis") or {})
        candidates = {item["code"]: item for item in supplier.get("candidate_suppliers") or []}
        sql = """
            SELECT s.code, s.risk_level, q.inspected_lots, q.passed_lots,
                   q.severe_incidents, q.note AS quality_note, d.deliveries,
                   d.on_time_deliveries, d.average_delay_days, d.note AS delivery_note
            FROM suppliers s
            LEFT JOIN supplier_quality_records q ON q.supplier_id = s.id
            LEFT JOIN supplier_delivery_records d ON d.supplier_id = s.id
            WHERE s.id IN (SELECT supplier_id FROM supplier_products WHERE product_id = :product_id)
        """
        result, query = self._query(
            "supplier_risk_records", sql, {"product_id": request.get("product_id")}
        )
        records = {row["code"]: row for row in _rows(result)}
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
        query["facts"] = [f"评估{len(assessed)}个方案，{len(feasible)}个通过硬约束"]
        query["conclusion"] = "形成推荐方案" if recommended else "需要重新规划或用户调整约束"
        return {
            "subtask": "risk_analysis",
            "queries": [query],
            "risk_level": recommended.get("risk_level") if recommended else "high",
            "main_risks": main_risks,
            "risk_basis": records,
            "recommended_plan": recommended,
            "alternative_plans": alternatives,
            "not_recommended_plans": not_recommended,
            "replan_reason": None
            if recommended
            else self._replan_reason(assessed, effective_budget),
            "facts": query["facts"],
            "conclusion": query["conclusion"],
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

    def execute_plan(self, plan_json: str) -> dict[str, Any]:
        envelope = _payload(plan_json)
        request = dict(envelope.get("request") or {})
        plan = dict(envelope.get("recommended_plan") or {})
        session_id = str(envelope.get("session_id") or "")
        if request.get("simulate_execution_failure"):
            return {
                "status": "failed",
                "error": {
                    "type": "scripted_execution_failure",
                    "message": "执行前外部采购系统失败",
                },
                "executed_actions": [],
            }
        if not session_id:
            return {
                "status": "failed",
                "error": {"type": "missing_session", "message": "执行日志缺少公开 Session ID"},
                "executed_actions": [],
            }
        if not plan or not plan.get("allocations"):
            return {
                "status": "failed",
                "error": {"type": "invalid_plan", "message": "缺少可执行供应商分配"},
                "executed_actions": [],
            }
        total_cost = float(plan["total_cost"])
        now = datetime.now(UTC).isoformat()
        nonce = uuid.uuid4().hex[:10].upper()
        request_no = f"PR-{self.today:%Y%m%d}-{nonce}"
        average_price = total_cost / int(plan["quantity"])
        transactional_plan = dict(plan)
        if request.get("simulate_atomic_failure"):
            transactional_plan["__force_failure"] = True

        # One DatabaseToolkit write is the transaction boundary. SQLite executes all
        # trigger steps in the same statement and rolls everything back on any RAISE,
        # foreign-key, budget, status, or log failure.
        request_write = self.database.execute_write(
            """
            INSERT INTO purchase_requests(
                request_no, session_id, department_id, product_id, requested_qty, approved_qty,
                required_date, supplier_plan_json, unit_price, total_amount, status,
                approval_status, created_at
            ) VALUES(
                :request_no, :session_id, :department_id, :product_id, :requested_qty, :approved_qty,
                :required_date, :plan_json, :unit_price, :total_amount, 'approved',
                'approved', :created_at
            )
            """,
            {
                "request_no": request_no,
                "session_id": session_id,
                "department_id": envelope.get("budget_analysis", {})
                .get("department", {})
                .get("id", 1),
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
        verification = self.database.execute_query(
            """
            SELECT pr.status, pr.approval_status, pr.session_id,
                   COUNT(DISTINCT po.id) AS order_count,
                   COUNT(DISTINCT ol.id) AS log_count
            FROM purchase_requests pr
            LEFT JOIN purchase_orders po ON po.request_id = pr.id
            LEFT JOIN operation_logs ol ON ol.entity_type = 'purchase_request'
                AND ol.entity_id = pr.id AND ol.action = 'approve_and_execute'
            WHERE pr.id = :request_id
            GROUP BY pr.id, pr.status, pr.approval_status, pr.session_id
            """,
            {"request_id": request_id},
        )
        verification_rows = _rows(verification)
        verified = bool(
            verification_rows
            and verification_rows[0]["status"] == "ordered"
            and verification_rows[0]["approval_status"] == "approved"
            and verification_rows[0]["session_id"] == session_id
            and int(verification_rows[0]["order_count"]) == len(plan["allocations"])
            and int(verification_rows[0]["log_count"]) == 1
        )
        if not verified:
            raise RuntimeError("Committed procurement transaction failed invariant verification")
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
            "transaction": {"committed": True, "rolled_back": False, "verified": True},
        }

    def tools(self) -> dict[str, BaseTool]:
        """Build explicit business tools used by the corresponding SubAgents."""

        definitions = {
            "parse_requirement": (
                self.parse_requirement,
                "Parse and merge a procurement requirement.",
            ),
            "analyze_inventory": (
                self.analyze_inventory,
                "Analyze inventory, forecast use, and purchase gap.",
            ),
            "analyze_suppliers": (
                self.analyze_suppliers,
                "Screen suppliers using database evidence.",
            ),
            "analyze_pricing": (
                self.analyze_pricing,
                "Compare quotations and build cost combinations.",
            ),
            "analyze_budget": (
                self.analyze_budget,
                "Validate the proposal against department and user budgets.",
            ),
            "analyze_risk": (
                self.analyze_risk,
                "Assess supplier, delivery, quality, price, and concentration risk.",
            ),
            "execute_procurement_plan": (
                self.execute_plan,
                "Execute one approved procurement plan.",
            ),
        }
        result: dict[str, BaseTool] = {}
        for name, (function, description) in definitions.items():
            metadata = {
                "database_toolkit": name != "parse_requirement",
                "database_operation": "write" if name == "execute_procurement_plan" else "read",
                "business_domain": "procurement",
            }
            result[name] = StructuredTool.from_function(
                function,
                name=name,
                description=description,
                metadata=metadata,
            )
        result["execute_procurement_plan"].metadata.update(
            {
                "harness_approval": True,
                "harness_approval_message": "批准后将创建采购申请/订单并占用预算，是否继续？",
            }
        )
        return result
