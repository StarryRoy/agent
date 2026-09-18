---
name: supplier-risk-review
description: 高供应商风险时帮助 Supplier 或 Risk Agent 重筛和评估方案
version: "1.0.0"
tags:
  - supplier_risk_too_high
  - risk_first
  - risk_diversification
required_tools:
  - execute_query
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

1. 从上游 State 确认候选/排除供应商、当前分配、内部风险证据和外部状态。
2. Supplier Agent 侧优先重筛供应商能力、履约和集中度；Risk Agent 侧优先评估分散方案、质量、交付与硬约束。
3. 根据当前角色可用 Tool Schema、Schema 和 Metadata，自主选择查询、计算和重试路径。
4. 保留不利证据、分配变化和风险结论；无法满足硬约束时明确阻断。

# 需要查询的数据

Supplier 与 Risk 仅查询各自角色允许的供应商、履约和风险数据，不委派其他 Agent。

# 查询结果字段契约

按当前角色保留完整风险证据：供应商结果包括 `risk_level`、`quality_pass_rate`、`on_time_rate`、`severe_incidents`；Risk 结果包括分数、风险条目、推荐/不推荐方案和 `replan_reason`。

# 判断与异常处理

不得删除不利证据来降低评分。数据库错误由当前 SubAgent 根据 Harness 错误自主修正；外部服务错误按当前角色的安全降级路径处理。

# 输出要求

输出当前角色负责的完整分配或风险评估、成本/交期变化、风险证据、结论和阻断原因；不声称其他 Agent 已完成复核。
