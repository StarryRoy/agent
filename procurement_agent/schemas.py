"""Stable application-level state and output contracts."""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ProcurementState(TypedDict, total=False):
    """Business fields merged by Harness with its reserved Agent state."""

    procurement_session_id: str
    workflow_version: int


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
