---
name: risk-analysis
description: 对采购方案执行供应商、质量、交付、预算和集中度风险评分
version: "1.0.0"
tags:
  - risk
  - quality
  - delivery
  - 风险
required_tools:
  - execute_query
  - calculate_risk
dependencies: []
scripts: []
---
# 目标

使用供应商风险记录和上游确定性方案，对每个采购方案评分、执行硬约束过滤并产生推荐方案。评分必须由 `calculate_risk` 完成。

# 适用场景

用于首次方案评审，以及成本、交期、供应商或预算重规划后的重新评审。

# 输入信息

- 需求与目标产品。
- 候选供应商、定价方案、有效预算。
- `analysis_strategy` 和本角色获得的供应商、供货关系、质量、交付 Schema/Metadata。

# Workflow

1. 确认待评估方案及涉及的产品/供应商。
2. 动态生成只读查询，调用 `execute_query` 获取目标产品可供供应商的风险、质量和交付记录。
3. `ok=false` 时把 Harness 错误交回推理，对照 Schema/Metadata 修正查询并重试。
4. 校验字段契约后，把完整查询结果和 task 交给 `calculate_risk`。
5. 使用计算器的风险评分、硬约束结果和重规划原因，不通过删除不利记录降低风险。

# 需要查询的数据

供应商编码/内部风险、受检与合格批次、严重质量事件、质量备注、交付与准时次数、平均延期、交付备注。

# 查询结果字段契约

每行必须包含且名称完全一致：`code`、`risk_level`、`inspected_lots`、`passed_lots`、`severe_incidents`、`quality_note`、`deliveries`、`on_time_deliveries`、`average_delay_days`、`delivery_note`。没有对应汇总记录时仍保留字段并返回数值零/null；不得省略别名。

# 判断与异常处理

- 零样本应视为证据不足，不能推断完美履约。
- 数量、交期、预算和 critical 风险是方案硬约束。
- 无推荐方案时保留 `replan_reason`，由 Main Agent 决定下一轮角色和策略。
- 查询最多修正两次；仍失败则停止评分并返回错误。

# 输出要求

保留 `risk_level`、`main_risks`、`risk_basis`、`recommended_plan`、`alternative_plans`、`not_recommended_plans`、`replan_reason`、`facts`、`conclusion`、`status` 和 `evidence`。
