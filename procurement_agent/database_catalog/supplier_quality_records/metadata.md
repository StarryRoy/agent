# supplier_quality_records 字段元数据

**用途**：`supplier_quality_records` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 质量汇总记录主键。 |
| `supplier_id` | 供应商主键。 |
| `inspected_lots` | 已检批次数；非负整数。 |
| `passed_lots` | 合格批次数；0 至 inspected_lots。 |
| `severe_incidents` | 严重质量事件数；非负整数。 |
| `recorded_at` | 统计截止日期；ISO-8601 文本。 |
| `note` | 质量说明；可为空自由文本。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
