# quotations

- 表用途：供应商产品报价；用于当前价格、可供量、MOQ、报价交期和有效期比较。
- `id`：报价主键。
- `supplier_id`：关联 `suppliers.id`。
- `product_id`：关联 `products.id`。
- `unit_price`：未税/含税口径由业务数据统一维护的单位价格，非负金额；与 `currency` 一起解释。
- `currency`：ISO 货币代码，非固定值，默认 `CNY`；不同币种不可直接相加。
- `min_qty`：该报价最小订购量。
- `available_qty`：该报价当前可供应数量。
- `lead_time_days`：报价承诺交期天数，应优先于标准交期。
- `quoted_at`：报价日期，ISO-8601 文本。
- `valid_until`：报价有效截止日期，ISO-8601 文本。
- `status`：固定值 `valid`（有效）、`expired`（过期）、`revoked`（撤回）；正常采购只使用 `valid` 且未过有效期的报价。
