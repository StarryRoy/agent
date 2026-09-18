---
name: cost-optimization
description: 超预算时帮助 Pricing Agent 形成满足硬约束的低成本方案
version: "1.0.0"
tags:
  - cost_reduction
  - all_suppliers_over_budget
  - 超预算
required_tools:
  - execute_query
  - calculate_pricing
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

1. 从上游 State 确认采购缺口、当前方案、预算缺口和不可放宽的数量/交期约束。
2. 根据本角色可用 Tool Schema、Schema 和 Metadata，自主选择查询与定价计算路径。
3. 比较单一供应商、组合、谈判价或条件性方案；不隐式减少数量，不伪造折扣。
4. 保留完整价格、数量、交期和条件性证据，供 Budget/Risk Agent 后续独立复核。

# 需要查询的数据

仅查询本 Skill 声明范围内的报价和历史价格数据，不委派其他 Agent，也不生成其他 Agent 的 Tool 参数。

# 查询结果字段契约

返回完整 `plans`（含 `quantity`、`total_cost`、`meets_quantity`、`meets_deadline`、`conditional`）以及成本降低依据和不可行原因。

# 判断与异常处理

谈判价、分批或加急均必须标记为条件性。查询失败时由当前 SubAgent 根据 Harness 错误自主修正或安全降级；不得输出 SQL。

# 输出要求

输出候选方案、条件、剩余缺口与不可行原因；不声称预算或风险已经复核。
