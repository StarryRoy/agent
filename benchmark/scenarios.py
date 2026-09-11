"""Stable, version-comparable Procurement Agent benchmark scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    description: str
    input: str
    expected_statuses: tuple[str, ...]
    expect_executable_plan: bool | None = None
    expect_blocked: bool = False
    expect_hitl: bool | None = None
    expect_replan: bool = False
    expect_replan_success: bool | None = None
    expected_skills: tuple[str, ...] = ()
    expected_strategies: tuple[str, ...] = ()
    expect_mcp: bool = False
    expect_error_recovery: bool = False
    actions: tuple[tuple[str, str | None], ...] = ()
    setup_sql: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "description": self.description,
            "input": self.input,
            "expected_statuses": list(self.expected_statuses),
            "expect_executable_plan": self.expect_executable_plan,
            "expect_blocked": self.expect_blocked,
            "expect_hitl": self.expect_hitl,
            "expect_replan": self.expect_replan,
            "expect_replan_success": self.expect_replan_success,
            "expected_skills": list(self.expected_skills),
            "expected_strategies": list(self.expected_strategies),
            "expect_mcp": self.expect_mcp,
            "expect_error_recovery": self.expect_error_recovery,
            "actions": [{"type": kind, "value": value} for kind, value in self.actions],
        }


SCENARIOS = (
    Scenario(
        "normal_purchase",
        "正常采购",
        "下个月需要采购500台设备，预算80万，月底前必须到货。",
        ("approval_required",),
        expect_executable_plan=True,
        expect_hitl=True,
        expect_mcp=True,
    ),
    Scenario(
        "inventory_sufficient",
        "库存充足，无需采购",
        "下个月需要50台设备，预算20万。",
        ("completed",),
        expect_executable_plan=True,
        expect_hitl=False,
    ),
    Scenario(
        "cost_optimization",
        "超预算后使用 cost-optimization",
        "下个月需要500台设备，预算10万。",
        ("completed",),
        expect_executable_plan=False,
        expect_blocked=True,
        expect_hitl=False,
        expect_replan=True,
        expect_replan_success=False,
        expected_skills=("cost-optimization",),
        expected_strategies=("cost_reduction",),
    ),
    Scenario(
        "urgent_procurement",
        "紧急采购使用 urgent-procurement",
        "紧急采购500台设备，预算90万，要求2026-09-22前到货。",
        ("approval_required", "completed"),
        expect_hitl=True,
        expected_skills=("urgent-procurement",),
    ),
    Scenario(
        "delivery_failure",
        "交期失败后使用 delivery-recovery",
        "需要采购500台设备，预算80万，2026-09-15必须到货。",
        ("completed",),
        expect_executable_plan=False,
        expect_blocked=True,
        expect_hitl=False,
        expect_replan=True,
        expect_replan_success=False,
        expected_skills=("delivery-recovery",),
        expected_strategies=("delivery_first",),
    ),
    Scenario(
        "high_risk_supplier",
        "高风险供应商触发 supplier-risk-review",
        "下个月需要500台设备，预算80万，不要供应商A并排除供应商D。",
        ("completed",),
        expect_executable_plan=False,
        expect_blocked=True,
        expect_hitl=False,
        expect_replan=True,
        expect_replan_success=False,
        expected_skills=("supplier-risk-review",),
        expected_strategies=("risk_first",),
    ),
    Scenario(
        "replan_success",
        "交付重规划后成功",
        "需要采购500台设备，预算90万，2026-09-22必须到货。",
        ("approval_required",),
        expect_executable_plan=True,
        expect_hitl=True,
        expect_replan=True,
        expect_replan_success=True,
        expected_skills=("delivery-recovery",),
        expected_strategies=("delivery_first",),
    ),
    Scenario(
        "replan_infeasible",
        "重规划后仍不可行并正确阻断",
        "下个月需要500台设备，预算5万，且不得分批采购。",
        ("completed",),
        expect_executable_plan=False,
        expect_blocked=True,
        expect_hitl=False,
        expect_replan=True,
        expect_replan_success=False,
        expected_skills=("cost-optimization",),
        expected_strategies=("cost_reduction",),
    ),
    Scenario(
        "mcp_fallback",
        "MCP 异常后降级",
        '{"text":"下个月需要500台设备，预算80万。","simulate_mcp_failure":true}',
        ("approval_required",),
        expect_executable_plan=True,
        expect_hitl=True,
        expect_mcp=True,
        expect_error_recovery=True,
    ),
    Scenario(
        "data_recovery",
        "数据查询异常后恢复",
        '{"text":"下个月需要500台设备，预算80万。","simulate_sql_failure":true}',
        ("approval_required",),
        expect_executable_plan=True,
        expect_hitl=True,
        expect_replan=True,
        expect_replan_success=True,
        expected_strategies=("schema_recovery",),
        expect_error_recovery=True,
    ),
)


def load_scenarios(selected: tuple[str, ...] = ()) -> list[Scenario]:
    if not selected:
        return list(SCENARIOS)
    known = {scenario.id: scenario for scenario in SCENARIOS}
    unknown = sorted(set(selected) - known.keys())
    if unknown:
        raise ValueError(f"Unknown benchmark scenarios: {', '.join(unknown)}")
    return [known[identifier] for identifier in selected]
