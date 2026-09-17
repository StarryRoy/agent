---
name: supplier-analysis
description: 供应商准入、能力、交期、质量与履约表现的标准筛选流程
version: "1.0.0"
tags:
  - supplier
  - capability
  - 供应商
required_tools:
  - execute_query
  - calculate_suppliers
dependencies: []
scripts: []
---
# 目标

从内部供应商事实中筛出可参与方案组合的候选，并说明被排除原因。筛选计算必须由 `calculate_suppliers` 完成。

# 适用场景

用于正常供应商筛选、交期优先、风险优先、用户排除供应商和查询恢复。

# 输入信息

- `request.product_id`、排除供应商、最晚交付日期。
- `inventory_analysis.recommended_purchase_quantity`。
- `analysis_strategy` 与当前业务日期。
- 本角色获得的供应商、供货关系、报价、质量、交付 Schema/Metadata。

# Workflow

1. 确认目标产品、采购量、截止日期和排除条件。
2. 根据所给 Schema/Metadata 动态生成只读查询，调用 `execute_query` 获取每个有效报价供应商的一行完整事实。
3. `ok=false` 时把 Harness 错误作为修正依据，检查字段、关联和别名后重新生成查询并重试。
4. 校验字段契约后，把原始 task 与完整查询结果交给 `calculate_suppliers`。
5. 如存在供应商实时状态 Tool，再按候选编码核验；外部服务失败时保留 fallback/unknown，不删除内部证据。
6. 返回候选、排除项、交期窗口和计算器结论。

# 需要查询的数据

供应商状态/风险、产品 MOQ 与产能、当前有效报价的价格/可供量/交期，以及汇总质量与交付表现。

# 查询结果字段契约

每行必须包含且名称完全一致：`supplier_id`、`code`、`name`、`status`、`cooperation_status`、`risk_level`、`min_order_qty`、`max_capacity`、`standard_lead_days`、`unit_price`、`available_qty`、`lead_time_days`、`quality_pass_rate`、`on_time_rate`、`severe_incidents`、`average_delay_days`。比率应为 0 到 1 的数值；缺少历史时用数值零并由后续风险流程说明数据不足。

# 判断与异常处理

- 禁用供应商和用户排除供应商必须进入 rejected，不得静默丢弃。
- `delivery_first` 只改变候选排序，不可伪造更短交期。
- `risk_first` 按内部风险规则排除高风险项。
- 空结果或契约缺字段为错误；查询最多修正两次。

# 输出要求

保留 `candidate_suppliers`、`rejected_suppliers`、`required_quantity`、`delivery_window_days`、`facts`、`conclusion`、`status`、`evidence` 和策略字段。
