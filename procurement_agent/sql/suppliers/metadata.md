# suppliers

- 表用途：供应商主数据及内部准入/风险状态；用于候选筛选和风险基线。
- `id`：供应商内部主键。
- `code`：唯一供应商编码；跨结果契约和外部状态服务的稳定标识。
- `name`：供应商名称。
- `status`：当前启用状态；固定值 `active`（可参与业务）、`suspended`（暂停，不可选）。
- `cooperation_status`：合作等级；固定值 `strategic`（战略）、`approved`（已准入）、`probation`（观察期）、`blocked`（禁止合作）。
- `risk_level`：内部风险等级；固定值 `low`、`medium`、`high`、`critical`，风险依次升高。
