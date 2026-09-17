# supplier_delivery_records

- 表用途：供应商汇总交付记录；用于计算准时交付率和平均延期。
- `id`：交付记录主键。
- `supplier_id`：关联 `suppliers.id`。
- `deliveries`：已统计交付次数，非负整数；为零时准时率不可判定。
- `on_time_deliveries`：准时交付次数，范围为 0 到 `deliveries`。
- `average_delay_days`：平均延期天数，非负数；越大表示历史交付风险越高。
- `recorded_at`：记录统计截止日期，ISO-8601 文本。
- `note`：补充交付说明，可为空。
