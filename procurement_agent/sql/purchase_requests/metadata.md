# purchase_requests

- 表用途：已提交或已执行的采购申请；写入时由数据库触发器校验方案、预算并原子创建订单和日志。
- `id`：采购申请主键。
- `request_no`：唯一申请号。
- `session_id`：发起执行的 Harness 会话标识。
- `department_id`：关联 `departments.id`。
- `product_id`：关联 `products.id`。
- `requested_qty`：原始需求数量，正整数。
- `approved_qty`：审批通过数量，正整数，必须等于方案分配数量之和。
- `required_date`：要求交付日期，ISO-8601 日期文本，可为空。
- `supplier_plan_json`：完整供应商分配 JSON；必须含非空 `allocations`。
- `unit_price`：方案加权平均单价。
- `total_amount`：方案总额，必须等于各分配数量乘单价之和。
- `status`：固定值 `draft`、`approved`、`ordered`、`cancelled`。
- `approval_status`：固定值 `pending`、`approved`、`rejected`。
- `created_at`：创建时间，ISO-8601 时间文本；其年份用于预算年度校验。
