# supplier_quality_records

- 表用途：供应商汇总质量记录；用于计算批次合格率和识别严重质量事故。
- `id`：质量记录主键。
- `supplier_id`：关联 `suppliers.id`。
- `inspected_lots`：受检批次数，非负整数；为零时合格率不可判定，不能当作 0% 或 100%。
- `passed_lots`：合格批次数，范围为 0 到 `inspected_lots`。
- `severe_incidents`：严重质量事件数，非负整数；大于零应进入风险项。
- `recorded_at`：记录统计截止日期，ISO-8601 文本。
- `note`：补充质量说明，可为空。
