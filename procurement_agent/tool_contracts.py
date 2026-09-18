"""Strict input and output contracts for procurement business tools."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

NonEmpty = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
Rate = Annotated[float, Field(ge=0, le=1)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisStrategy(str, Enum):
    STANDARD = "standard"
    BALANCED = "balanced"
    SCHEMA_RECOVERY = "schema_recovery"
    FALLBACK_RECOVERY = "fallback_recovery"
    DELIVERY_FIRST = "delivery_first"
    RISK_FIRST = "risk_first"
    COST_REDUCTION = "cost_reduction"
    DELIVERY_RECOVERY = "delivery_recovery"
    RISK_DIVERSIFICATION = "risk_diversification"
    REVALIDATE_COST_REDUCTION = "revalidate_cost_reduction"
    REVALIDATE_DELIVERY_RECOVERY = "revalidate_delivery_recovery"
    REVALIDATE_RISK_DIVERSIFICATION = "revalidate_risk_diversification"


class ProcurementRequest(ContractModel):
    product: str | None = Field(..., description="标准产品名称；尚未识别时为 null。")
    product_id: int | None = Field(..., ge=1, description="产品数据库 ID；尚未识别时为 null。")
    sku: str | None = Field(..., description="产品 SKU；尚未识别时为 null。")
    quantity: int | None = Field(..., gt=0, description="需求数量；尚未识别时为 null。")
    budget: float | None = Field(..., gt=0, description="用户预算上限；未指定时为 null。")
    expected_delivery_date: str | None = Field(..., description="期望交付日期 ISO-8601。")
    latest_delivery_date: str | None = Field(..., description="最晚交付日期 ISO-8601。")
    quality_requirements: list[str] = Field(..., description="明确的质量要求列表。")
    priority: Literal["normal", "high"] = Field(..., description="采购优先级。")
    preferred_suppliers: list[str] = Field(..., description="优先供应商编码。")
    excluded_suppliers: list[str] = Field(..., description="排除供应商编码。")
    department_code: NonEmpty = Field(..., description="预算所属部门编码。")
    other_constraints: list[str] = Field(..., description="其他采购约束。")
    selected_plan_index: int | None = Field(..., ge=1, description="用户选择的方案序号。")
    simulate_sql_failure: bool = Field(..., description="测试用 SQL 失败开关。")
    simulate_subagent_failure: bool | Literal["supplier"] = Field(
        ..., description="测试用 SubAgent 失败开关。"
    )
    simulate_mcp_failure: bool = Field(..., description="测试用 MCP 失败开关。")
    simulate_execution_failure: bool = Field(..., description="测试用执行失败开关。")
    simulate_atomic_failure: bool = Field(..., description="测试用事务失败开关。")


class ActionableProcurementRequest(ProcurementRequest):
    """A requirement complete enough for downstream analysis and execution."""

    @model_validator(mode="after")
    def require_product_and_quantity(self) -> ActionableProcurementRequest:
        missing = [
            name
            for name in ("product", "product_id", "sku", "quantity")
            if getattr(self, name) is None
        ]
        if missing:
            raise ValueError("downstream request is missing: " + ", ".join(missing))
        return self


class QueryError(ContractModel):
    type: NonEmpty = Field(..., description="稳定错误类型。")
    message: NonEmpty = Field(..., description="可读错误说明。")
    retryable: bool | None = Field(None, description="查询是否可重试。")


class QueryFailure(ContractModel):
    ok: Literal[False] = Field(..., description="查询失败标志。")
    operation: Literal["execute_query"] = Field(..., description="数据库操作名称。")
    error: QueryError = Field(..., description="结构化查询错误。")


class QuerySuccessBase(ContractModel):
    ok: Literal[True] = Field(..., description="查询成功标志。")
    operation: Literal["execute_query"] = Field(..., description="数据库操作名称。")
    columns: list[str] = Field(..., description="返回列名。")
    row_count: NonNegativeInt = Field(..., description="返回行数。")
    truncated: bool = Field(..., description="结果是否被截断。")


class InventoryRow(ContractModel):
    product_id: PositiveInt = Field(..., description="产品 ID。")
    sku: NonEmpty = Field(..., description="产品 SKU。")
    name: NonEmpty = Field(..., description="产品名称。")
    current_qty: NonNegativeInt = Field(..., description="当前库存。")
    locked_qty: NonNegativeInt = Field(..., description="锁定库存。")
    in_transit_qty: NonNegativeInt = Field(..., description="在途数量。")
    safety_stock: NonNegativeInt = Field(..., description="安全库存。")
    updated_at: NonEmpty = Field(..., description="库存更新时间。")
    average_monthly_consumption: NonNegativeFloat = Field(..., description="月均消耗。")


class SupplierRow(ContractModel):
    supplier_id: PositiveInt = Field(..., description="供应商 ID。")
    code: NonEmpty = Field(..., description="供应商编码。")
    name: NonEmpty = Field(..., description="供应商名称。")
    status: NonEmpty = Field(..., description="供应商状态。")
    cooperation_status: NonEmpty = Field(..., description="合作状态。")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(..., description="风险等级。")
    min_order_qty: PositiveInt = Field(..., description="最小起订量。")
    max_capacity: NonNegativeInt = Field(..., description="最大供货能力。")
    standard_lead_days: NonNegativeInt = Field(..., description="标准交期天数。")
    unit_price: NonNegativeFloat = Field(..., description="当前单价。")
    available_qty: NonNegativeInt = Field(..., description="报价可供数量。")
    lead_time_days: NonNegativeInt = Field(..., description="报价交期天数。")
    quality_pass_rate: Rate = Field(..., description="历史质量合格率。")
    on_time_rate: Rate = Field(..., description="历史准时率。")
    severe_incidents: NonNegativeInt = Field(..., description="严重事件数量。")
    average_delay_days: NonNegativeFloat = Field(..., description="平均延期天数。")


class PricingRow(ContractModel):
    supplier_id: PositiveInt = Field(..., description="供应商 ID。")
    code: NonEmpty = Field(..., description="供应商编码。")
    name: NonEmpty = Field(..., description="供应商名称。")
    unit_price: NonNegativeFloat = Field(..., description="报价单价。")
    min_qty: PositiveInt = Field(..., description="报价最小数量。")
    available_qty: NonNegativeInt = Field(..., description="报价可供数量。")
    lead_time_days: NonNegativeInt = Field(..., description="报价交期。")
    quoted_at: NonEmpty = Field(..., description="报价时间。")
    valid_until: NonEmpty = Field(..., description="报价有效期。")
    historical_average_price: float | None = Field(..., ge=0, description="历史均价。")
    historical_min_price: float | None = Field(..., ge=0, description="历史最低价。")
    historical_max_price: float | None = Field(..., ge=0, description="历史最高价。")


class BudgetRow(ContractModel):
    department_id: PositiveInt = Field(..., description="部门 ID。")
    code: NonEmpty = Field(..., description="部门编码。")
    name: NonEmpty = Field(..., description="部门名称。")
    fiscal_year: int = Field(..., ge=2000, le=2200, description="财年。")
    total_amount: NonNegativeFloat = Field(..., description="预算总额。")
    used_amount: NonNegativeFloat = Field(..., description="已使用预算。")
    approved_pending_amount: NonNegativeFloat = Field(..., description="已批未执行金额。")
    available_amount: NonNegativeFloat = Field(..., description="可用预算。")


class RiskRow(ContractModel):
    code: NonEmpty = Field(..., description="供应商编码。")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        ..., description="供应商风险等级。"
    )
    inspected_lots: NonNegativeInt = Field(..., description="质检批次数。")
    passed_lots: NonNegativeInt = Field(..., description="质检通过批次数。")
    severe_incidents: NonNegativeInt = Field(..., description="严重质量事件数。")
    quality_note: str | None = Field(..., description="质量备注。")
    deliveries: NonNegativeInt = Field(..., description="历史交付次数。")
    on_time_deliveries: NonNegativeInt = Field(..., description="准时交付次数。")
    average_delay_days: NonNegativeFloat = Field(..., description="平均延期天数。")
    delivery_note: str | None = Field(..., description="交付备注。")


class InventoryQuerySuccess(QuerySuccessBase):
    rows: list[InventoryRow] = Field(..., description="目标产品库存行。")


class SupplierQuerySuccess(QuerySuccessBase):
    rows: list[SupplierRow] = Field(..., description="供应商能力与历史行。")


class PricingQuerySuccess(QuerySuccessBase):
    rows: list[PricingRow] = Field(..., description="当前与历史报价行。")


class BudgetQuerySuccess(QuerySuccessBase):
    rows: list[BudgetRow] = Field(..., description="部门财年预算行。")


class RiskQuerySuccess(QuerySuccessBase):
    rows: list[RiskRow] = Field(..., description="供应商风险记录行。")


class Evidence(ContractModel):
    source: Literal["DatabaseToolkit.execute_query"] = Field(..., description="证据来源。")
    operation: Literal["execute_query"] = Field(..., description="数据库操作。")
    row_count: NonNegativeInt = Field(..., description="证据行数。")
    truncated: bool = Field(..., description="证据是否截断。")
    subtask: NonEmpty = Field(..., description="证据对应子任务。")


class ParseRequirementArgs(ContractModel):
    text: NonEmpty = Field(..., description="用户原始采购消息，必须逐字传递。")
    previous_request: ProcurementRequest | None = Field(
        ..., description="上一轮结构化需求；首次请求为 null。"
    )


class RequirementOutput(ContractModel):
    subtask: Literal["requirement_extraction"] = Field(..., description="子任务名称。")
    query: None = Field(..., description="需求解析不执行数据库查询。")
    data: dict[Literal["source_text"], str] = Field(..., description="原始需求文本证据。")
    facts: list[str] = Field(..., description="解析事实。")
    conclusion: NonEmpty = Field(..., description="解析结论。")
    request: ProcurementRequest = Field(..., description="结构化采购需求。")
    missing_fields: list[Literal["product", "quantity"]] = Field(
        ..., description="缺失的关键字段。"
    )


class InventoryAnalysis(ContractModel):
    subtask: Literal["inventory_analysis"] = Field(..., description="子任务名称。")
    evidence: list[Evidence] = Field(..., min_length=1, description="查询证据。")
    current_available_quantity: NonNegativeInt = Field(..., description="当前可用数量。")
    in_transit_quantity: NonNegativeInt = Field(..., description="在途数量。")
    safety_stock: NonNegativeInt = Field(..., description="安全库存。")
    forecast_consumption: NonNegativeInt = Field(..., description="预测消耗。")
    projected_usable_quantity: NonNegativeInt = Field(..., description="预计可用数量。")
    estimated_shortfall: NonNegativeInt = Field(..., description="预计缺口。")
    recommended_purchase_quantity: NonNegativeInt = Field(..., description="建议采购数量。")
    inventory_risk: Literal["low", "medium", "high"] = Field(..., description="库存风险。")
    facts: list[str] = Field(..., description="库存事实。")
    conclusion: NonEmpty = Field(..., description="库存结论。")
    status: Literal["success"] = Field(..., description="分析状态。")
    analysis_strategy: AnalysisStrategy = Field(..., description="分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")
    fallback_basis: str | None = Field(..., description="降级估算依据。")


class SupplierCandidate(SupplierRow):
    can_fulfill_alone: bool = Field(..., description="能否独立满足数量。")
    meets_deadline: bool = Field(..., description="是否满足交期。")
    selection_reasons: list[str] = Field(..., description="候选依据。")
    rejection_reasons: list[str] | None = Field(None, description="排除依据。")
    external_status: str | None = Field(None, description="外部实时状态。")


class SupplierAnalysis(ContractModel):
    subtask: Literal["supplier_analysis"] = Field(..., description="子任务名称。")
    evidence: list[Evidence] = Field(..., min_length=1, description="查询证据。")
    candidate_suppliers: list[SupplierCandidate] = Field(..., description="候选供应商。")
    rejected_suppliers: list[SupplierCandidate] = Field(..., description="被排除供应商。")
    required_quantity: NonNegativeInt = Field(..., description="所需采购数量。")
    delivery_window_days: int | None = Field(..., ge=0, description="可用交付窗口。")
    facts: list[str] = Field(..., description="供应商事实。")
    conclusion: NonEmpty = Field(..., description="供应商结论。")
    status: Literal["success", "error"] = Field(..., description="分析状态。")
    analysis_strategy: AnalysisStrategy = Field(..., description="分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")
    external_status: dict[str, str] = Field(..., description="供应商实时状态映射。")
    mcp_status: Literal["not_checked", "success", "fallback"] = Field(
        ..., description="实时状态检查状态。"
    )
    warnings: list[str] = Field(..., description="降级警告。")


class Allocation(ContractModel):
    supplier_id: PositiveInt = Field(..., description="供应商 ID。")
    supplier_code: NonEmpty = Field(..., description="供应商编码。")
    supplier_name: NonEmpty = Field(..., description="供应商名称。")
    quantity: PositiveInt = Field(..., description="分配数量。")
    unit_price: NonNegativeFloat = Field(..., description="成交单价。")
    lead_time_days: NonNegativeInt = Field(..., description="交期天数。")
    conditional: bool = Field(..., description="是否为条件性报价。")


class PricingPlan(ContractModel):
    plan_id: NonEmpty = Field(..., description="方案 ID。")
    name: NonEmpty = Field(..., description="方案名称。")
    allocations: list[Allocation] = Field(..., min_length=1, description="供应商分配。")
    quantity: PositiveInt = Field(..., description="方案总数量。")
    average_unit_price: NonNegativeFloat = Field(..., description="平均单价。")
    total_cost: NonNegativeFloat = Field(..., description="方案总价。")
    max_lead_time_days: NonNegativeInt = Field(..., description="最长交期。")
    meets_quantity: bool = Field(..., description="是否满足数量。")
    meets_deadline: bool = Field(..., description="是否满足交期。")
    within_user_budget: bool = Field(..., description="是否满足用户预算。")
    analysis_strategy: AnalysisStrategy = Field(..., description="生成策略。")
    conditional: bool = Field(..., description="是否包含条件性条款。")


class PricingAnalysis(ContractModel):
    subtask: Literal["pricing_analysis"] = Field(..., description="子任务名称。")
    evidence: list[Evidence] = Field(..., min_length=1, description="查询证据。")
    quotations: list[EvaluatedQuote] = Field(..., description="经验证的报价记录。")
    historical_reference: list[HistoricalPriceReference] = Field(..., description="历史价格对比。")
    cost_differences: list[CostDifference] = Field(..., description="方案成本对比。")
    anomalies: list[PriceAnomaly] = Field(..., description="价格异常。")
    plans: list[PricingPlan] = Field(..., description="候选价格方案。")
    recommended_price_plan: PricingPlan | None = Field(..., description="推荐价格方案。")
    facts: list[str] = Field(..., description="价格事实。")
    conclusion: NonEmpty = Field(..., description="价格结论。")
    status: Literal["success", "error"] = Field(..., description="分析状态。")
    analysis_strategy: AnalysisStrategy = Field(..., description="分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")


class Department(ContractModel):
    id: PositiveInt = Field(..., description="部门 ID。")
    code: NonEmpty = Field(..., description="部门编码。")
    name: NonEmpty = Field(..., description="部门名称。")


class EvaluatedQuote(PricingRow):
    price_change_rate: float | None = Field(..., description="相对历史均价变动率。")
    external_status: NonEmpty = Field(..., description="供应商实时状态。")


class HistoricalPriceReference(ContractModel):
    supplier: NonEmpty = Field(..., description="供应商编码。")
    average: float | None = Field(..., ge=0, description="历史均价。")
    current: NonNegativeFloat = Field(..., description="当前报价。")
    change_rate: float | None = Field(..., description="价格变动率。")


class CostDifference(ContractModel):
    plan_id: NonEmpty = Field(..., description="方案 ID。")
    total_cost: NonNegativeFloat = Field(..., description="方案总成本。")


class PriceAnomaly(ContractModel):
    supplier: NonEmpty = Field(..., description="供应商编码。")
    type: Literal["price_variance"] = Field(..., description="异常类型。")
    rate: float = Field(..., description="异常变动率。")


class BudgetAnalysis(ContractModel):
    subtask: Literal["budget_analysis"] = Field(..., description="子任务名称。")
    evidence: list[Evidence] = Field(..., min_length=1, description="查询证据。")
    department: Department = Field(..., description="预算部门。")
    budget_total: NonNegativeFloat = Field(..., description="预算总额。")
    used_budget: NonNegativeFloat = Field(..., description="已用预算。")
    approved_not_executed: NonNegativeFloat = Field(..., description="已批未执行金额。")
    available_budget: NonNegativeFloat = Field(..., description="部门可用预算。")
    user_budget: float | None = Field(..., ge=0, description="用户预算上限。")
    effective_available_budget: NonNegativeFloat = Field(..., description="有效可用预算。")
    estimated_occupation: NonNegativeFloat = Field(..., description="预计占用。")
    within_budget: bool = Field(..., description="是否在预算内。")
    over_budget_amount: NonNegativeFloat = Field(..., description="超预算金额。")
    adjustment_room: NonNegativeFloat = Field(..., description="预算余量。")
    budget_risk: Literal["low", "medium", "high"] = Field(..., description="预算风险。")
    facts: list[str] = Field(..., description="预算事实。")
    conclusion: NonEmpty = Field(..., description="预算结论。")
    status: Literal["success"] = Field(..., description="分析状态。")
    analysis_strategy: AnalysisStrategy = Field(..., description="分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")


class AssessedPlan(PricingPlan):
    risk_score: int = Field(..., ge=0, le=100, description="风险评分。")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(..., description="风险等级。")
    risk_items: list[str] = Field(..., description="风险项目。")


class RiskAnalysis(ContractModel):
    subtask: Literal["risk_analysis"] = Field(..., description="子任务名称。")
    evidence: list[Evidence] = Field(..., min_length=1, description="查询证据。")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(..., description="总体风险。")
    main_risks: list[str] = Field(..., description="主要风险。")
    risk_basis: dict[str, RiskRow] = Field(..., description="供应商风险依据。")
    recommended_plan: AssessedPlan | None = Field(..., description="推荐方案。")
    alternative_plans: list[AssessedPlan] = Field(..., description="备选方案。")
    not_recommended_plans: list[AssessedPlan] = Field(..., description="不推荐方案。")
    replan_reason: str | None = Field(..., description="重规划原因。")
    facts: list[str] = Field(..., description="风险事实。")
    conclusion: NonEmpty = Field(..., description="风险结论。")
    status: Literal["success", "needs_replan"] = Field(..., description="分析状态。")
    analysis_strategy: AnalysisStrategy = Field(..., description="分析策略。")


class InventoryArgs(ContractModel):
    request: ActionableProcurementRequest = Field(..., description="完整结构化采购需求。")
    query_result: InventoryQuerySuccess | QueryFailure = Field(..., description="库存查询结果。")
    analysis_strategy: AnalysisStrategy = Field(..., description="库存分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")


class SupplierArgs(ContractModel):
    request: ActionableProcurementRequest = Field(..., description="完整结构化采购需求。")
    inventory_analysis: InventoryAnalysis = Field(..., description="成功的库存分析。")
    query_result: SupplierQuerySuccess | QueryFailure = Field(..., description="供应商查询结果。")
    analysis_strategy: AnalysisStrategy = Field(..., description="供应商分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")


class PricingArgs(ContractModel):
    request: ActionableProcurementRequest = Field(..., description="完整结构化采购需求。")
    inventory_analysis: InventoryAnalysis = Field(..., description="成功的库存分析。")
    supplier_analysis: SupplierAnalysis = Field(..., description="成功的供应商分析。")
    query_result: PricingQuerySuccess | QueryFailure = Field(..., description="报价查询结果。")
    analysis_strategy: AnalysisStrategy = Field(..., description="价格分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")


class BudgetArgs(ContractModel):
    request: ActionableProcurementRequest = Field(..., description="完整结构化采购需求。")
    pricing_analysis: PricingAnalysis = Field(..., description="成功的价格分析。")
    query_result: BudgetQuerySuccess | QueryFailure = Field(..., description="预算查询结果。")
    analysis_strategy: AnalysisStrategy = Field(..., description="预算分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")


class RiskArgs(ContractModel):
    request: ActionableProcurementRequest = Field(..., description="完整结构化采购需求。")
    supplier_analysis: SupplierAnalysis = Field(..., description="成功的供应商分析。")
    pricing_analysis: PricingAnalysis = Field(..., description="成功的价格分析。")
    budget_analysis: BudgetAnalysis = Field(..., description="成功的预算分析。")
    query_result: RiskQuerySuccess | QueryFailure = Field(..., description="风险查询结果。")
    analysis_strategy: AnalysisStrategy = Field(..., description="风险分析策略。")
    replan_reason: str | None = Field(..., description="重规划原因。")


class ExecutionArgs(ContractModel):
    request: ActionableProcurementRequest = Field(..., description="获批采购需求。")
    recommended_plan: AssessedPlan = Field(..., description="获批推荐方案。")
    budget_analysis: BudgetAnalysis = Field(..., description="已验证预算分析。")
    session_id: NonEmpty = Field(..., description="公开采购会话 ID。")


class AnalysisError(ContractModel):
    subtask: NonEmpty = Field(..., description="失败子任务。")
    evidence: list[Evidence] = Field(..., min_length=1, description="查询证据。")
    facts: list[str] = Field(..., description="已确认事实。")
    conclusion: NonEmpty = Field(..., description="失败结论。")
    status: Literal["error"] = Field(..., description="失败状态。")
    analysis_strategy: AnalysisStrategy = Field(..., description="分析策略。")
    replan_reason: str | None = Field(None, description="重规划原因。")
    error: QueryError = Field(..., description="结构化错误。")


class InventoryOutput(RootModel[InventoryAnalysis | AnalysisError]):
    pass


class SupplierOutput(RootModel[SupplierAnalysis | AnalysisError]):
    pass


class PricingOutput(RootModel[PricingAnalysis | AnalysisError]):
    pass


class BudgetOutput(RootModel[BudgetAnalysis | AnalysisError]):
    pass


class RiskOutput(RootModel[RiskAnalysis | AnalysisError]):
    pass


class CreatePurchaseRequestAction(ContractModel):
    action: Literal["create_purchase_request"] = Field(..., description="创建采购申请动作。")
    id: PositiveInt = Field(..., description="新建采购申请 ID。")
    request_no: NonEmpty = Field(..., description="采购申请编号。")


class CreatePurchaseOrdersAction(ContractModel):
    action: Literal["create_purchase_orders"] = Field(..., description="创建采购订单动作。")
    count: PositiveInt = Field(..., description="新建采购订单数量。")


class ReserveBudgetAction(ContractModel):
    action: Literal["reserve_budget"] = Field(..., description="占用预算动作。")
    amount: NonNegativeFloat = Field(..., description="预算占用金额。")


class UpdatePurchaseStatusAction(ContractModel):
    action: Literal["update_purchase_status"] = Field(..., description="更新采购状态动作。")
    status: Literal["ordered"] = Field(..., description="更新后的采购状态。")


class RecordApprovalAction(ContractModel):
    action: Literal["record_approval"] = Field(..., description="记录审批动作。")
    status: Literal["approved"] = Field(..., description="记录的审批状态。")


class WriteOperationLogAction(ContractModel):
    action: Literal["write_operation_log"] = Field(..., description="写入操作日志动作。")
    session_id: NonEmpty = Field(..., description="公开采购会话 ID。")


ExecutionAction = Annotated[
    CreatePurchaseRequestAction
    | CreatePurchaseOrdersAction
    | ReserveBudgetAction
    | UpdatePurchaseStatusAction
    | RecordApprovalAction
    | WriteOperationLogAction,
    Field(discriminator="action"),
]


class CommittedTransaction(ContractModel):
    committed: Literal[True] = Field(..., description="事务已提交。")
    rolled_back: Literal[False] = Field(..., description="事务未回滚。")
    verified_by: Literal["database_constraints_and_triggers"] = Field(
        ..., description="事务校验机制。"
    )


class RolledBackTransaction(ContractModel):
    committed: Literal[False] = Field(..., description="事务未提交。")
    rolled_back: Literal[True] = Field(..., description="事务已整体回滚。")


class ExecutionSuccess(ContractModel):
    status: Literal["success"] = Field(..., description="执行成功状态。")
    purchase_request_id: PositiveInt = Field(..., description="采购申请 ID。")
    request_no: NonEmpty = Field(..., description="采购申请编号。")
    purchase_order_count: PositiveInt = Field(..., description="采购订单数量。")
    budget_reserved: NonNegativeFloat = Field(..., description="预算占用金额。")
    executed_actions: list[ExecutionAction] = Field(..., description="已执行动作。")
    transaction: CommittedTransaction = Field(..., description="已提交事务结果。")


class ExecutionPreflightFailure(ContractModel):
    status: Literal["failed"] = Field(..., description="执行失败状态。")
    error: QueryError = Field(..., description="执行前错误。")
    executed_actions: list[ExecutionAction] = Field(
        ..., max_length=0, description="失败前未执行任何动作。"
    )


class TransactionExecutionError(QueryError):
    database_error: QueryError = Field(..., description="导致事务失败的底层数据库错误。")


class ExecutionTransactionFailure(ContractModel):
    status: Literal["failed"] = Field(..., description="事务执行失败状态。")
    error: TransactionExecutionError = Field(..., description="结构化事务执行错误。")
    executed_actions: list[ExecutionAction] = Field(
        ..., max_length=0, description="回滚后不存在已执行动作。"
    )
    transaction: RolledBackTransaction = Field(..., description="已回滚事务结果。")


class ExecutionOutput(
    RootModel[ExecutionSuccess | ExecutionTransactionFailure | ExecutionPreflightFailure]
):
    pass


TOOL_CONTRACTS = {
    "parse_requirement": (ParseRequirementArgs, RequirementOutput),
    "calculate_inventory": (InventoryArgs, InventoryOutput),
    "calculate_suppliers": (SupplierArgs, SupplierOutput),
    "calculate_pricing": (PricingArgs, PricingOutput),
    "calculate_budget": (BudgetArgs, BudgetOutput),
    "calculate_risk": (RiskArgs, RiskOutput),
    "execute_procurement_plan": (ExecutionArgs, ExecutionOutput),
}
