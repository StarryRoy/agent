# quotations 字段元数据

**用途**：`quotations` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 报价主键。 |
| `supplier_id` | 报价供应商主键。 |
| `product_id` | 报价产品主键。 |
| `unit_price` | 未税/约定口径单价；正数。 |
| `currency` | ISO 4217 币种；默认 CNY。 |
| `min_qty` | 报价最小适用数量；正整数。 |
| `available_qty` | 报价期内可供应量；非负整数。 |
| `lead_time_days` | 承诺交期天数；非负整数。 |
| `quoted_at` | 报价日期；ISO-8601 文本。 |
| `valid_until` | 报价有效截止日；ISO-8601 文本。 |
| `status` | 报价状态；固定值：valid、expired、withdrawn。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
