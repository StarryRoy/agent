---
name: urgent-procurement
description: 紧急采购下协调时间优先分析且不绕过预算与审批
version: "1.0.0"
tags:
  - urgent
  - 紧急
  - 加急
required_tools:
  - inventory_agent
  - supplier_agent
  - pricing_agent
  - budget_agent
  - risk_agent
dependencies: []
scripts: []
---
# 目标

在时间优先场景下快速形成有证据的方案，同时保持数量、预算、风险和审批约束。

# 适用场景

需求优先级为 `high`/`urgent`，或用户明确提出紧急、加急。

# 输入信息

产品、数量、截止日期、质量要求、预算上限和排除供应商。

# Workflow

1. 先确认关键需求字段，不完整则澄清。
2. Inventory 计算缺口；Supplier 采用 `delivery_first`；Pricing 采用 `delivery_recovery`。
3. Budget 与 Risk 重新验证所有条件性加急方案。
4. 仅把满足硬约束的方案提交审批。

# 需要查询的数据

本 Skill 不直接查询；各专业 SubAgent 按自己的正常 Skill 动态查询相关表。

# 查询结果字段契约

库存结果含缺口；供应商结果含交期窗口；定价方案含数量、最大交期、总额和条件标记；预算与风险结果含最终硬约束结论。

# 判断与异常处理

紧急不代表预算或审批豁免。任何查询失败均由对应 SubAgent 基于 Harness 错误和其 Schema/Metadata 修正。

# 输出要求

输出结构化日期、数量、金额、条件、风险、来源标识和待审批状态。
