---
name: pricing-analysis
description: 当前报价、历史价格和单一或组合供应方案的标准定价流程
version: "1.0.0"
tags:
  - pricing
  - quotation
  - 价格
required_tools:
  - execute_query
  - calculate_pricing
dependencies: []
scripts: []
---
# 目标

使用有效报价和历史价格基准生成满足数量、MOQ、产能与交期约束的单一/组合方案，所有金额和分配由 `calculate_pricing` 计算。

# 适用场景

用于标准比价、成本压降、交期恢复、风险分散和重新比价。

# 输入信息

- 需求、库存建议采购量。
- `supplier_analysis.candidate_suppliers`、交期窗口和外部状态。
- `analysis_strategy`、预算上限及当前业务日期。
- 本角色获得的供应商、报价、采购历史 Schema/Metadata。

# Workflow

1. 确认可选供应商编码、目标数量和硬约束。
2. 动态生成只读查询并调用 `execute_query`，只取目标产品报价与同供应商/产品历史价格聚合。
3. 查询失败时根据 Harness 错误与 Schema/Metadata 修正后重试；不得臆造价格或历史基准。
4. 校验字段契约，将完整查询结果和 task 交给 `calculate_pricing`。
5. 直接使用计算器产生的方案、异常和排序；条件性降价或加急必须保留 `conditional=true`。

# 需要查询的数据

当前单位价格、MOQ、可供量、报价交期、报价日期/有效期，以及同供应商同产品的历史均价、最低价和最高价。

# 查询结果字段契约

每行必须包含且名称完全一致：`supplier_id`、`code`、`name`、`unit_price`、`min_qty`、`available_qty`、`lead_time_days`、`quoted_at`、`valid_until`、`historical_average_price`、`historical_min_price`、`historical_max_price`。无历史时三个历史字段保留并返回 null。

# 判断与异常处理

- 仅计算候选供应商；暂停/阻断的外部状态不得进入方案。
- 当前价格相对历史异常、MOQ、产能、数量和交期由计算器判定。
- 空结果、过期/无效报价或字段不完整不能被解释为零价格。
- 查询最多修正两次；仍失败返回错误并停止方案计算。

# 输出要求

保留 `quotations`、`historical_reference`、`cost_differences`、`anomalies`、`plans`、`recommended_price_plan`、`facts`、`conclusion`、`status`、`evidence` 和策略字段。
