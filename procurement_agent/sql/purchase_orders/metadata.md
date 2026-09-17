# purchase_orders

- 表用途：审批执行后按供应商拆分的采购订单；正常分析流程不依赖该表，只用于执行结果审计。
- `id`：采购订单主键。
- `order_no`：唯一订单号。
- `request_id`：关联 `purchase_requests.id`。
- `supplier_id`：关联 `suppliers.id`。
- `quantity`：订单数量，正整数。
- `unit_price`：订单单位价格，非负金额。
- `total_amount`：订单总额，规则为 `quantity * unit_price`。
- `expected_delivery_date`：预计交付日期，ISO-8601 日期文本，可为空。
- `status`：固定值 `created`、`confirmed`、`in_transit`、`received`、`cancelled`。
- `created_at`：创建时间，ISO-8601 时间文本。
