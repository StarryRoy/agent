---
name: delivery-recovery
description: 交期失败、延期恢复和加急组合 delivery_deadline_unmet delivery_first delivery_recovery
tags: [delivery_deadline_unmet, delivery_first, delivery_recovery]
---
查询数据库时仅描述取数目标、业务判断和查询顺序，不写具体 SQL。查询类 SubAgent 必须结合对应表的 schema.sql 与 metadata.md 动态生成 SQL，并直接调用 Harness DatabaseToolkit.execute_query。若执行失败，读取错误并结合相同 schema/metadata 修正 SQL 后重试；成功后再交给确定性分析 Tool。
读取目标截止日期、缺口、当前方案及不满足交期的结论。保留原截止日期作为硬约束。
Main 先委派 Supplier 使用 delivery_first，再委派 Pricing 使用 delivery_recovery。
通过现有 Tool 比较可验证的加急、替代供应商和分批交付，保留每项数量、交期和加急成本。
不得自行缩短供应商承诺交期或把部分到货当作全部满足。条件性方案必须明确说明。
返回新 plans、meets_deadline、meets_quantity、total_cost、evidence 和 replan_reason。
Main 重新核验预算与风险；仍失败则要求用户明确调整交期或需求，不得跳过审批。
