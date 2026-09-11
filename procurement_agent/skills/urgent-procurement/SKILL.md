---
name: urgent-procurement
description: 紧急、加急采购和时间优先分析 urgent
tags: [紧急, 加急, urgent]
---
先明确截止日期、数量、质量和预算上限，缺失条件交由 Requirement 澄清。
Inventory 核验可用、在途、安全库存和缺口，禁止将全部库存等同可用库存。
Main 委派 Supplier 使用 analysis_strategy=delivery_first，Pricing 使用 delivery_recovery，
比较可核实的加急和分批供应；不把“紧急”视为预算或审批豁免。
交期、产能、加急成本均由已有 Tool 计算。返回结构化日期、数量、金额、条件与来源标识。
Budget 和 Risk 重新校验；仅向 HITL 提交满足硬约束的方案。
