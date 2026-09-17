# purchase_orders 字段元数据

**用途**：`purchase_orders` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 采购订单自增主键。 |
| `order_no` | 采购订单唯一编号。 |
| `request_id` | 来源采购申请主键。 |
| `supplier_id` | 中标供应商主键。 |
| `quantity` | 订单数量；正整数。 |
| `unit_price` | 订单单价。 |
| `total_amount` | 订单总金额。 |
| `expected_delivery_date` | 预计到货日期；ISO-8601，可为空。 |
| `status` | 订单状态；固定值：created。 |
| `created_at` | 创建时间；ISO-8601 文本。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
