# operation_logs

- 表用途：采购写操作审计日志；用于按会话和业务实体追溯执行结果。
- `id`：日志主键。
- `session_id`：Harness 会话标识，可为空。
- `entity_type`：被操作实体类型，非固定值，例如 `purchase_request`。
- `entity_id`：被操作实体主键，可为空。
- `action`：业务动作名，非固定值，例如 `approve_and_execute`。
- `result`：固定值 `success`、`failed`、`rejected`。
- `details_json`：动作详情 JSON 文本，可为空；读取前需按 JSON 解析。
- `created_at`：记录时间，ISO-8601 时间文本。
