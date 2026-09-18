---
name: delivery-recovery
description: 交期不满足时帮助 Supplier 或 Pricing Agent 恢复可验证方案
version: "1.0.0"
tags:
  - delivery_deadline_unmet
  - delivery_first
  - delivery_recovery
required_tools:
  - execute_query
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

1. 从上游 State 确认原始截止日期、采购缺口、当前方案和交期失败原因。
2. Supplier Agent 侧优先核验可按时交付的供应商与分配；Pricing Agent 侧优先比较满足交期的价格、组合或条件性加急方案。
3. 根据当前角色可用 Tool Schema、Schema 和 Metadata，自主选择查询、计算和重试路径。
4. 保留交期、数量、成本和条件性证据；仍不满足硬约束时明确阻断，不擅自修改截止日期或数量。

# 需要查询的数据

Supplier 与 Pricing 只读取其各自角色允许的交付、供应商和报价数据，不委派其他 Agent。

# 查询结果字段契约

按当前角色返回相应完整结构：供应商结果包含 `candidate_suppliers` 与 `delivery_window_days`；价格方案包含 `quantity`、`max_lead_time_days`、`meets_deadline`、`meets_quantity`、`total_cost`、`conditional`。

# 判断与异常处理

不得自行缩短承诺交期或把部分交付当成全部满足。查询失败由当前 SubAgent 根据 Harness 错误自主修正或安全降级。

# 输出要求

输出恢复方案、条件性假设、未满足约束和下一步；不声称预算或风险已经由其他 Agent 复核。
