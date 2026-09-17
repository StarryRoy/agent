# purchase_requests 字段元数据

**用途**：`purchase_requests` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 采购申请自增主键。 |
| `request_no` | 采购申请唯一编号。 |
| `session_id` | 发起会话标识。 |
| `department_id` | 申请部门主键。 |
| `product_id` | 采购产品主键。 |
| `requested_qty` | 原始申请数量；正整数。 |
| `approved_qty` | 审批数量；正整数。 |
| `required_date` | 要求到货日期；ISO-8601，可为空。 |
| `supplier_plan_json` | 已确认供应商分配 JSON。 |
| `unit_price` | 方案平均单价。 |
| `total_amount` | 申请总金额。 |
| `status` | 申请状态；固定值：approved、ordered。 |
| `approval_status` | 审批状态；固定值：approved。 |
| `created_at` | 创建时间；ISO-8601 文本。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
