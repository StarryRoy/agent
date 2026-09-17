# purchase_history 字段元数据

**用途**：`purchase_history` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 历史采购记录主键。 |
| `supplier_id` | 历史供应商主键。 |
| `product_id` | 历史产品主键。 |
| `quantity` | 采购数量；正整数。 |
| `unit_price` | 成交单价；正数。 |
| `ordered_at` | 下单日期；ISO-8601 文本。 |
| `promised_delivery_days` | 承诺交付天数；非负整数。 |
| `actual_delivery_days` | 实际交付天数；非负整数。 |
| `quality_result` | 质检结论；固定值：passed、passed_with_minor_issues、passed_with_rework、rejected。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
