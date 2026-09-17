# budgets

- 表用途：部门年度预算余额；用于核验采购方案是否可占用预算。
- `id`：预算记录主键。
- `department_id`：关联 `departments.id`；与请求部门对应。
- `fiscal_year`：预算所属四位财年。
- `total_amount`：年度预算总额，非负金额。
- `used_amount`：已实际使用金额，非负金额。
- `approved_pending_amount`：已审批但尚未执行/结算的占用金额，非负金额。
- 可用预算规则：`total_amount - used_amount - approved_pending_amount`，不得仅用总额判断。
