---
name: cost-optimization
description: 超预算时协调定价、预算与风险重新规划
version: "1.0.0"
tags:
  - cost_reduction
  - all_suppliers_over_budget
  - 超预算
required_tools:
  - pricing_agent
  - budget_agent
  - risk_agent
dependencies: []
scripts: []
---
# 目标

在不伪造折扣、不隐式减少硬性数量的前提下，寻找成本更低或明确条件性的备选方案。

# 适用场景

`replan_reason=all_suppliers_over_budget`、存在正 `budget_gap`，或用户明确要求压降成本。

# 输入信息

采购需求、库存缺口、候选供应商、当前方案、有效预算与预算缺口。

# Workflow

1. 委派 Pricing Agent，设置 `analysis_strategy=cost_reduction` 并保留重规划原因。
2. 要求 Pricing 按其正常 Skill 动态查询并计算谈判目标、组合或分阶段条件方案。
3. 将新方案依次交给 Budget 与 Risk 重新验证。
4. 仍违反硬约束时阻断执行并请求用户调整。

# 需要查询的数据

本 Skill 不直接查询数据库；各专业 SubAgent 仅查询其正常 Skill 声明的相关表。

# 查询结果字段契约

Pricing 必须返回完整 `plans`（含 `quantity`、`total_cost`、`meets_quantity`、`meets_deadline`、`conditional`）；Budget 必须返回 `effective_available_budget` 和 `over_budget_amount`；Risk 必须返回 `recommended_plan` 或 `replan_reason`。

# 判断与异常处理

谈判价、分批或加急均必须标记为条件性。查询失败由对应 SubAgent 根据 Harness 错误修正；不得在 Main Agent 中生成 SQL。

# 输出要求

输出经预算和风险复核的新方案、条件、剩余缺口与不可行原因。
