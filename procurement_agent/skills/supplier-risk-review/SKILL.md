---
name: supplier-risk-review
description: 高供应商风险时协调候选重筛、分散采购和复核
version: "1.0.0"
tags:
  - supplier_risk_too_high
  - risk_first
  - risk_diversification
required_tools:
  - supplier_agent
  - pricing_agent
  - budget_agent
  - risk_agent
dependencies: []
scripts: []
---
# 目标

在保留质量、交付和外部状态证据的前提下降低供应商与集中度风险。

# 适用场景

推荐方案为高/严重风险、存在严重质量事件，或单一供应商集中度不可接受。

# 输入信息

候选与排除供应商、当前分配、内部风险证据、外部状态和风险结论。

# Workflow

1. 委派 Supplier Agent 使用 `risk_first`。
2. 委派 Pricing Agent 使用 `risk_diversification` 生成分散方案。
3. 让 Budget 和 Risk 对新方案重新核验。
4. MCP 不可用时保留 unknown/fallback 标记，不宣称已验证。

# 需要查询的数据

本 Skill 不直接查询；专业 SubAgent 仅查询各自正常 Skill 允许的相关表。

# 查询结果字段契约

供应商结果保留 `risk_level`、`quality_pass_rate`、`on_time_rate`、`severe_incidents`；Risk 结果保留分数、风险条目、推荐/不推荐方案和 `replan_reason`。

# 判断与异常处理

不得删除不利证据来降低评分。数据库错误由专业 SubAgent 根据 Harness 错误修正；外部服务错误按降级路径处理。

# 输出要求

输出分散后的完整分配、成本/交期变化、风险证据、复核结论和阻断原因。
