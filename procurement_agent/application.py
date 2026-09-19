"""Application facade for natural-language requests and resumable HITL."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Self

from agent_harness import Agent, AgentResult, DatabaseToolkit, StreamEvent
from pydantic import BaseModel

from .metrics import ProcurementMetricSink


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ProcurementResponse:
    """Stable user-facing response independent of LangGraph internals."""

    status: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    interrupts: tuple[Any, ...] = ()
    trace_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "data": self.data,
            "message": self.message,
            "interrupts": _plain(self.interrupts),
            "trace_id": self.trace_id,
        }


class ProcurementApplication:
    """Own resources and expose submit/approve/reject/modify operations."""

    def __init__(
        self,
        *,
        agent: Agent,
        database: DatabaseToolkit,
        checkpointer_connection: Any,
        metrics: ProcurementMetricSink,
    ) -> None:
        self.agent = agent
        self.database = database
        self.checkpointer_connection = checkpointer_connection
        self.metric_sink = metrics
        self._tasks = 0
        self._successful_tasks = 0
        self._replans = 0

    async def asubmit(self, text: str, *, session_id: str | None = None) -> ProcurementResponse:
        """Submit a new turn; approval words automatically resume a paused task."""

        if session_id and await self.agent.runtime.ais_paused(session_id=session_id):
            normalized = text.strip().casefold()
            if normalized in {"approve", "批准", "批准执行", "同意", "确认执行"}:
                return await self.aapprove(session_id)
            if normalized in {"reject", "拒绝", "驳回", "不批准"}:
                return await self.areject(session_id)
            return await self.amodify(session_id, text)
        result = await self.agent.ainvoke(text, session_id=session_id)
        return self._convert(result, count_task=True)

    async def aapprove(self, session_id: str) -> ProcurementResponse:
        result = await self.agent.aresume(session_id=session_id, decision="approve")
        return self._convert(result, count_task=True)

    async def areject(self, session_id: str) -> ProcurementResponse:
        result = await self.agent.aresume(session_id=session_id, decision="reject")
        return self._convert(result, count_task=True)

    async def amodify(self, session_id: str, change: str) -> ProcurementResponse:
        """Reject the pending write, retain prior evidence, then replan affected roles."""

        if await self.agent.runtime.ais_paused(session_id=session_id):
            await self.agent.aresume(session_id=session_id, decision="reject")
        result = await self.agent.ainvoke(change, session_id=session_id)
        return self._convert(result, count_task=True)

    def submit(self, text: str, *, session_id: str | None = None) -> ProcurementResponse:
        return self._sync(self.asubmit(text, session_id=session_id))

    def approve(self, session_id: str) -> ProcurementResponse:
        return self._sync(self.aapprove(session_id))

    def reject(self, session_id: str) -> ProcurementResponse:
        return self._sync(self.areject(session_id))

    def modify(self, session_id: str, change: str) -> ProcurementResponse:
        return self._sync(self.amodify(session_id, change))

    async def astream(self, text: str, *, session_id: str) -> AsyncIterator[StreamEvent]:
        async for event in self.agent.astream(text, session_id=session_id):
            yield event

    def clear_session(self, session_id: str) -> None:
        self.agent.clear_session(session_id)

    def metrics(self) -> dict[str, Any]:
        return self.metric_sink.snapshot(
            tasks=self._tasks,
            successful_tasks=self._successful_tasks,
            replans=self._replans,
        )

    def close(self) -> None:
        self.database.close()
        self.checkpointer_connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @staticmethod
    def _sync(coroutine: Any) -> ProcurementResponse:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        coroutine.close()
        raise RuntimeError("An event loop is already running; use the async application method")

    def _convert(
        self,
        result: AgentResult,
        *,
        count_task: bool,
        prefer_state: bool = False,
    ) -> ProcurementResponse:
        trace_id = str((result.metadata or {}).get("trace_id") or "") or None
        if result.status == "paused":
            data = self._with_budget_display(self._proposal_from_state(result.state or {}))
            data["replan_count"] = max(
                int(data.get("replan_count") or 0),
                self.metric_sink.replan_count_for_trace(trace_id),
            )
            data["approval_status"] = "pending"
            data["execution_status"] = "awaiting_approval"
            plan = data.get("recommended_plan") or {}
            message = (
                f"建议方案 {plan.get('name', '')}：采购{plan.get('quantity', 0)}台，"
                f"总额{float(plan.get('total_cost', 0)):.2f}元。执行前等待人工审批。"
            )
            return ProcurementResponse(
                status="approval_required",
                session_id=result.session_id,
                data=data,
                message=message,
                interrupts=result.interrupts,
                trace_id=trace_id,
            )

        if prefer_state:
            # A running checkpoint may still contain the previous turn's final
            # structured_response.  The persisted procurement State is the
            # only authoritative source for an in-progress snapshot.
            data = _plain(self._running_from_state(result.state or {}))
        else:
            data = _plain(
                result.structured_output
                if result.structured_output is not None
                else result.output
            )
        if not isinstance(data, dict):
            data = {"summary": str(data), "warnings": []}
        data = self._with_budget_display(data)
        data["replan_count"] = max(
            int(data.get("replan_count") or 0),
            self.metric_sink.replan_count_for_trace(trace_id),
        )
        message = str(data.get("summary") or "采购任务处理完成。")
        if count_task:
            self._tasks += 1
            self._replans += int(data.get("replan_count") or 0)
            if data.get("execution_status") in {"success", "not_required"}:
                self._successful_tasks += 1
        status = "completed" if result.status == "completed" else "error"
        return ProcurementResponse(status, result.session_id, data, message, (), trace_id)

    @staticmethod
    def _with_budget_display(data: Mapping[str, Any]) -> dict[str, Any]:
        """Add final-response budget amounts without changing formal business State."""

        projected = dict(data)
        budget = projected.get("budget_analysis")
        if not isinstance(budget, Mapping):
            return projected
        minimum_cost = budget.get("estimated_occupation")
        if minimum_cost is None:
            return projected

        recommended = projected.get("recommended_plan")
        if not isinstance(recommended, Mapping):
            risk = projected.get("risk_analysis")
            recommended = risk.get("recommended_plan") if isinstance(risk, Mapping) else None
        recommended_cost = (
            float(recommended["total_cost"])
            if isinstance(recommended, Mapping) and recommended.get("total_cost") is not None
            else None
        )
        minimum_cost = float(minimum_cost)
        display_fields = {
            "display_occupation": recommended_cost
            if recommended_cost is not None
            else minimum_cost,
            "recommended_plan_occupation": recommended_cost,
            "minimum_cost_plan_occupation": minimum_cost,
        }
        projected["budget_analysis"] = {**display_fields, **dict(budget)}
        return projected

    @classmethod
    def _running_from_state(cls, state: Mapping[str, Any]) -> dict[str, Any]:
        """Project the latest formal business State for live UI snapshots."""

        data = cls._proposal_from_state(state)
        business_state = state.get("procurement_results")
        business_state = business_state if isinstance(business_state, Mapping) else {}
        stage_titles = (
            ("requirement", "需求分析"),
            ("inventory_analysis", "库存分析"),
            ("supplier_analysis", "供应商分析"),
            ("pricing_analysis", "价格分析"),
            ("budget_analysis", "预算分析"),
            ("risk_analysis", "风险分析"),
            ("execution", "采购执行"),
        )
        completed = [title for field, title in stage_titles if field in business_state]
        if completed:
            data["summary"] = "已完成：" + "、".join(completed) + "。其余分析正在进行。"
        else:
            data["summary"] = "采购分析正在开始，已完成的阶段结果会实时显示在这里。"
        data["approval_status"] = "not_required"
        data["execution_status"] = "not_started"

        execution = business_state.get("execution")
        if isinstance(execution, Mapping):
            data["executed_actions"] = list(execution.get("executed_actions") or [])
            data["execution_status"] = str(execution.get("status") or "not_started")

        warnings: list[str] = []
        for value in business_state.values():
            if isinstance(value, Mapping):
                stage_warnings = value.get("warnings")
                if isinstance(stage_warnings, list):
                    warnings.extend(str(item) for item in stage_warnings)
        data["warnings"] = warnings
        return data

    @staticmethod
    def _proposal_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
        business_state = state.get("procurement_results")
        business_state = business_state if isinstance(business_state, Mapping) else {}
        values = {
            field: value
            for field in (
                "requirement",
                "inventory_analysis",
                "supplier_analysis",
                "pricing_analysis",
                "budget_analysis",
                "risk_analysis",
                "execution",
            )
            if isinstance((value := business_state.get(field)), Mapping)
        }
        requirement = values.get("requirement", {})
        supplier = values.get("supplier_analysis", {})
        risk = values.get("risk_analysis", {})
        return {
            "request": requirement.get("request", {}),
            "inventory_analysis": values.get("inventory_analysis", {}),
            "candidate_suppliers": supplier.get("candidate_suppliers", []),
            "pricing_analysis": values.get("pricing_analysis", {}),
            "budget_analysis": values.get("budget_analysis", {}),
            "risk_analysis": risk,
            "recommended_plan": risk.get("recommended_plan"),
            "alternative_plans": risk.get("alternative_plans", []),
            "executed_actions": [],
            "warnings": supplier.get("warnings", []),
            "missing_fields": requirement.get("missing_fields", []),
            "replan_count": 0,
            "summary": "采购分析已完成，等待审批。",
        }
