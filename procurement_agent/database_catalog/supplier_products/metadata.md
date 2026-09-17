# supplier_products 字段元数据

**用途**：`supplier_products` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 供货关系主键。 |
| `supplier_id` | 供应商主键。 |
| `product_id` | 可供应产品主键。 |
| `min_order_qty` | 最小起订量；正整数。 |
| `max_capacity` | 单次最大供货能力；非负整数。 |
| `standard_lead_days` | 标准交付天数；非负整数。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
