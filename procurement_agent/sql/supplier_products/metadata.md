# supplier_products

- 表用途：供应商对产品的供货能力；用于判断 MOQ、最大供货量和标准交期。
- `id`：关系记录主键。
- `supplier_id`：关联 `suppliers.id`。
- `product_id`：关联 `products.id`。
- `min_order_qty`：最小订购量，非负整数；任何单笔分配不得低于该值。
- `max_capacity`：当前业务周期最大供应能力，非负整数。
- `standard_lead_days`：标准交货周期天数，非负整数；没有有效报价交期时才可作为参考。
