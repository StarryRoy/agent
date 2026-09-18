"""Stable application-level state and output contracts."""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from . import tool_contracts as _tool_contracts
from .tool_contracts import (
    AnalysisError,
    BudgetAnalysis,
    ExecutionPreflightFailure,
    ExecutionSuccess,
    ExecutionTransactionFailure,
    InventoryAnalysis,
    PricingAnalysis,
    RequirementOutput,
    RiskAnalysis,
    SupplierAnalysis,
)


class ProcurementState(TypedDict, total=False):
    """Business fields merged by Harness with its reserved Agent state."""

    procurement_session_id: str
    workflow_version: int
    procurement_results: dict[str, dict[str, Any]]


class RequirementAgentOutput(RequirementOutput):
    remarks: list[str] | None = Field(None, description="无法由固定字段表达的补充说明。")


class InventoryAgentOutput(InventoryAnalysis):
    remarks: list[str] | None = Field(None, description="库存场景的补充说明。")


class SupplierAgentOutput(SupplierAnalysis):
    remarks: list[str] | None = Field(None, description="供应商场景的补充说明。")


class PricingAgentOutput(PricingAnalysis):
    remarks: list[str] | None = Field(None, description="价格场景的补充说明。")


class BudgetAgentOutput(BudgetAnalysis):
    remarks: list[str] | None = Field(None, description="预算场景的补充说明。")


class RiskAgentOutput(RiskAnalysis):
    remarks: list[str] | None = Field(None, description="风险场景的补充说明。")


class AgentAnalysisError(AnalysisError):
    remarks: list[str] | None = Field(None, description="分析失败场景的补充说明。")


class ExecutionAgentSuccess(ExecutionSuccess):
    remarks: list[str] | None = Field(None, description="执行场景的补充说明。")


class ExecutionAgentTransactionFailure(ExecutionTransactionFailure):
    remarks: list[str] | None = Field(None, description="执行失败的补充说明。")


class ExecutionAgentPreflightFailure(ExecutionPreflightFailure):
    remarks: list[str] | None = Field(None, description="执行前失败的补充说明。")


for _model in (
    RequirementAgentOutput,
    InventoryAgentOutput,
    SupplierAgentOutput,
    PricingAgentOutput,
    BudgetAgentOutput,
    RiskAgentOutput,
    AgentAnalysisError,
    ExecutionAgentSuccess,
    ExecutionAgentTransactionFailure,
    ExecutionAgentPreflightFailure,
):
    _model.model_rebuild(_types_namespace=vars(_tool_contracts))


SUBAGENT_OUTPUT_TYPES = {
    "requirement_agent": RequirementAgentOutput,
    "inventory_agent": InventoryAgentOutput | AgentAnalysisError,
    "supplier_agent": SupplierAgentOutput | AgentAnalysisError,
    "pricing_agent": PricingAgentOutput | AgentAnalysisError,
    "budget_agent": BudgetAgentOutput | AgentAnalysisError,
    "risk_agent": RiskAgentOutput | AgentAnalysisError,
    "execution_agent": (
        ExecutionAgentSuccess | ExecutionAgentTransactionFailure | ExecutionAgentPreflightFailure
    ),
}


class ProcurementDecision(BaseModel):
    """Structured result returned by Main Agent."""

    model_config = ConfigDict(extra="allow")

    request: dict[str, Any] = Field(default_factory=dict)
    inventory_analysis: dict[str, Any] = Field(default_factory=dict)
    candidate_suppliers: list[dict[str, Any]] = Field(default_factory=list)
    pricing_analysis: dict[str, Any] = Field(default_factory=dict)
    budget_analysis: dict[str, Any] = Field(default_factory=dict)
    risk_analysis: dict[str, Any] = Field(default_factory=dict)
    recommended_plan: dict[str, Any] | None = None
    alternative_plans: list[dict[str, Any]] = Field(default_factory=list)
    approval_status: str = "not_required"
    execution_status: str = "not_started"
    executed_actions: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    replan_count: int = 0
    summary: str = ""
