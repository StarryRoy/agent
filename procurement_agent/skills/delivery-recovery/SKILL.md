---
name: delivery-recovery
description: 交期不满足时协调供应商和定价恢复方案
version: "1.0.0"
tags:
  - delivery_deadline_unmet
  - delivery_first
  - delivery_recovery
required_tools:
  - supplier_agent
  - pricing_agent
  - budget_agent
  - risk_agent
dependencies: []
scripts: []
---
# 目标

在保留原截止日期硬约束的情况下，寻找可验证的替代供应商、组合或条件性加急方案。

# 适用场景

全部方案交期失败，或用户缩短交期后需要重新规划。

# 输入信息

截止日期、采购缺口、当前供应商/方案及交期失败原因。

# Workflow

1. 委派 Supplier Agent 使用 `delivery_first`。
2. 委派 Pricing Agent 使用 `delivery_recovery`。
3. 让 Budget 与 Risk 重新验证加急成本、数量和风险。
4. 仍失败时要求用户明确调整交期或数量。

# 需要查询的数据

本 Skill 不直接查询；Supplier 与 Pricing 只读取其各自正常 Skill 声明的表。

# 查询结果字段契约

供应商结果包含 `candidate_suppliers` 与 `delivery_window_days`；价格方案包含 `quantity`、`max_lead_time_days`、`meets_deadline`、`meets_quantity`、`total_cost`、`conditional`。

# 判断与异常处理

不得自行缩短承诺交期或把部分交付当成全部满足。查询失败由专业 SubAgent 使用 Harness 错误修正。

# 输出要求

输出经预算和风险复核的恢复方案、条件性假设、未满足约束和下一步。
