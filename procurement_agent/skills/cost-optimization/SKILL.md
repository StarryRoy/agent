---
name: cost-optimization
description: 超预算、预算缺口和低成本方案重规划；all_suppliers_over_budget cost_reduction
tags: [cost_reduction, all_suppliers_over_budget, 超预算]
---
读取当前目标、procurement_context.budget_gap、effective_budget、current_plan 和已确认供应能力。
Main 将 request、inventory_analysis、supplier_analysis 与 Context 委派 Pricing，设置
analysis_strategy=cost_reduction；保留 replan_reason=all_suppliers_over_budget。
Pricing 调用 analyze_pricing，比较有报价依据的谈判价格、供应商组合和分阶段方案。
禁止为满足预算编造折扣、减少硬性数量或隐式放宽交期。谈判/分批条件必须明确标记。
金额、数量、缺口和预算占用只采用 Tool 计算值。返回完整 plans、价格差异、条件和 evidence。
Main 用新方案重新调用 Budget 与 Risk；仍不可行则请求用户调整，不得绕过 HITL。
