# operation_logs 字段元数据

**用途**：`operation_logs` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 操作日志自增主键。 |
| `session_id` | 关联会话标识；可为空。 |
| `entity_type` | 业务实体类型；如 purchase_request。 |
| `entity_id` | 业务实体主键；可为空。 |
| `action` | 动作名称；如 approve_and_execute。 |
| `result` | 结果；固定值：success、failure。 |
| `details_json` | 结构化操作详情 JSON；可为空。 |
| `created_at` | 操作时间；ISO-8601 文本。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
