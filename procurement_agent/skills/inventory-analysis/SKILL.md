---
name: inventory-analysis
description: 库存、历史消耗、预计可用量和采购缺口的标准分析流程
version: "1.0.0"
tags:
  - inventory
  - shortage
  - 库存
required_tools:
  - execute_query
  - calculate_inventory
dependencies: []
scripts: []
---
# 目标

基于产品库存与历史消耗，形成可由后续供应商流程直接使用的采购缺口。查询只负责取数，数量计算必须交给 `calculate_inventory`。

# 适用场景

用于正常采购、数量变更、产品变更，以及库存查询失败后的恢复。若 `analysis_strategy=fallback_recovery`，仍先查询相同业务字段，计算器会采用保守预测规则。

# 输入信息

- `request.product_id`、`request.quantity`。
- `analysis_strategy` 与 `replan_reason`。
- Schema/Metadata 中仅提供的 `products`、`inventory`、`inventory_history`。

# Workflow

1. 从输入确认产品主键和需求量；缺失时返回明确错误，不猜测。
2. 根据当前 Schema/Metadata 动态生成一条只读查询，并调用 `execute_query`。
3. 若返回 `ok=false`，读取 Harness 的错误类型和消息，对照 Schema/Metadata 修正表名、列名、关联、聚合或参数后重试；不得把失败结果交给计算器。
4. 若查询成功但字段契约不完整，修正查询并重试。
5. 将完整 Harness 查询结果和原始 task 原样传给 `calculate_inventory`。
6. 输出计算器结果，不自行改写缺口、预测或风险值。

# 需要查询的数据

产品标识、库存快照，以及该产品的历史期间消耗汇总。不得查询本 Skill 未提供的表。

# 查询结果字段契约

每行必须包含且名称完全一致：`product_id`、`sku`、`name`、`current_qty`、`locked_qty`、`in_transit_qty`、`safety_stock`、`updated_at`、`average_monthly_consumption`。结果应只包含目标产品的一行；没有历史记录时平均消耗返回数值零而不是省略字段。

# 判断与异常处理

- 当前可用量、预测消耗、预计可用量和缺口均由计算器判断。
- 空结果不是库存为零，应返回数据不可用。
- 查询错误最多修正两次；仍失败则返回 Harness 原始错误摘要并标记 `status=error`。
- 禁止在输出中泄露生成的 SQL。

# 输出要求

保留 `status`、`facts`、`conclusion`、`evidence`、库存数量、预测消耗、预计可用量、采购缺口、建议采购量和库存风险等计算器字段。
