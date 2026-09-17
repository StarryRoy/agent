# supplier_delivery_records 字段元数据

**用途**：`supplier_delivery_records` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 交付汇总记录主键。 |
| `supplier_id` | 供应商主键。 |
| `deliveries` | 交付总次数；非负整数。 |
| `on_time_deliveries` | 准时次数；0 至 deliveries。 |
| `average_delay_days` | 平均延期天数；非负数。 |
| `recorded_at` | 统计截止日期；ISO-8601 文本。 |
| `note` | 交付说明；可为空自由文本。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
