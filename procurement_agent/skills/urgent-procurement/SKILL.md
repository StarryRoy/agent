---
name: urgent-procurement
description: 紧急、加急采购和时间优先分析 urgent
tags: [紧急, 加急, urgent]
---
查询数据库时仅描述取数目标、业务判断和查询顺序，不写具体 SQL。查询类 SubAgent 必须结合对应表的 schema.sql 与 metadata.md 动态生成 SQL，并直接调用 Harness DatabaseToolkit.execute_query。若执行失败，读取错误并结合相同 schema/metadata 修正 SQL 后重试；成功后再交给确定性分析 Tool。
先明确截止日期、数量、质量和预算上限，缺失条件交由 Requirement 澄清。
Inventory 核验可用、在途、安全库存和缺口，禁止将全部库存等同可用库存。
Main 委派 Supplier 使用 analysis_strategy=delivery_first，Pricing 使用 delivery_recovery，
比较可核实的加急和分批供应；不把“紧急”视为预算或审批豁免。
交期、产能、加急成本均由已有 Tool 计算。返回结构化日期、数量、金额、条件与来源标识。
Budget 和 Risk 重新校验；仅向 HITL 提交满足硬约束的方案。
