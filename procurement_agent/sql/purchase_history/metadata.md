# purchase_history

- 表用途：已完成采购历史；用于价格基准、实际交期与历史质量表现分析。
- `id`：历史采购主键。
- `supplier_id`：关联 `suppliers.id`。
- `product_id`：关联 `products.id`。
- `quantity`：历史订单数量，正整数。
- `unit_price`：历史单位价格，非负金额；仅在币种口径一致时与当前报价比较。
- `ordered_at`：历史下单日期，ISO-8601 文本。
- `promised_delivery_days`：供应商承诺交期天数。
- `actual_delivery_days`：实际交期天数；与承诺值比较履约偏差。
- `quality_result`：固定值 `passed`、`passed_with_minor_issues`、`passed_with_rework`、`rejected`，质量风险依次上升。
