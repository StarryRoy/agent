# suppliers 字段元数据

**用途**：`suppliers` 业务表，查询时必须遵守 `schema.sql` 中的键、约束和关联。

| 字段 | 业务含义与取值规则 |
|---|---|
| `id` | 供应商内部主键。 |
| `code` | 供应商唯一编码。 |
| `name` | 供应商法定/业务名称。 |
| `status` | 启用状态；固定值：active、suspended。 |
| `cooperation_status` | 合作阶段；固定值：strategic、approved、probation、blocked。 |
| `risk_level` | 风险等级；固定值：low、medium、high、critical。 |

## 查询约定

- 只读取完成当前业务判断所需字段；关联时使用 `schema.sql` 中声明的主外键。
- 日期按 ISO-8601 文本比较；比率应使用浮点除法并防止分母为零。
- 不推断未列出的固定值；遇到未知值时原样返回并标记待核验。
