---
name: budget-analysis
description: 部门年度可用预算、用户预算上限和方案占用的标准核验流程
version: "1.0.0"
tags:
  - budget
  - department
  - 预算
required_tools:
  - execute_query
  - calculate_budget
dependencies: []
scripts: []
---
# 目标

确定部门真实可用预算和用户上限，并核验最低可行采购方案的预计占用。所有预算运算由 `calculate_budget` 完成。

# 适用场景

用于首次预算核验、用户修改预算、定价方案更新和重规划后的重新验证。

# 输入信息

- `request.department_code`、用户预算上限。
- `pricing_analysis.plans`。
- 当前业务日期/财年和本角色的部门、预算 Schema/Metadata。

# Workflow

1. 确认部门编码和当前财年。
2. 动态生成只读查询，调用 `execute_query` 获取该部门该财年的唯一预算记录。
3. Harness 返回错误时按 Schema/Metadata 修正查询后重试。
4. 校验字段契约，将完整查询结果和 task 交给 `calculate_budget`。
5. 输出部门余额、有效预算、方案占用、超额和风险，不自行替换计算器数值。

# 需要查询的数据

部门主键/编码/名称、财年、预算总额、已用金额、已审批待执行金额，以及由这三项得到的可用金额。

# 查询结果字段契约

唯一结果行必须包含且名称完全一致：`department_id`、`code`、`name`、`fiscal_year`、`total_amount`、`used_amount`、`approved_pending_amount`、`available_amount`。`available_amount` 必须表示总额减已用减待执行占用。

# 判断与异常处理

- 无预算记录不等于预算为零，应返回数据不可用。
- 多行结果表示部门/财年过滤不精确，应修正查询。
- 用户预算与部门可用预算取更严格者，由计算器判断。
- 查询最多修正两次，仍失败则返回 Harness 错误。

# 输出要求

保留部门信息、预算总额/已用/待执行/可用、用户预算、有效预算、预计占用、是否超额、超额金额、调整空间、预算风险、`facts`、`conclusion`、`status` 和 `evidence`。
