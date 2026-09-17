---
name: supplier-risk-review
description: 供应商风险、质量异常与集中度复核 supplier_risk_too_high risk_first risk_diversification
tags: [supplier_risk_too_high, risk_first, risk_diversification]
---
查询数据库时仅描述取数目标、业务判断和查询顺序，不写具体 SQL。查询类 SubAgent 必须结合对应表的 schema.sql 与 metadata.md 动态生成 SQL，并直接调用 Harness DatabaseToolkit.execute_query。若执行失败，读取错误并结合相同 schema/metadata 修正 SQL 后重试；成功后再交给确定性分析 Tool。
读取当前供应商、用户排除条件、方案分配和风险结论，区分数据库历史证据与 MCP 当前状态。
Main 委派 Supplier 使用 risk_first，随后 Pricing 使用 risk_diversification。
Supplier 先依据相关 schema.sql、metadata.md 生成只读查询并调用 execute_query，再用 analyze_suppliers 与 MCP 核验状态、质量合格率、严重事件和准时率，
MCP 不可用必须保留 fallback/unknown 标记，不能宣称已验证。
Pricing 比较分散供应方案的成本和交期；评分、产能与分配计算继续由 Tool 完成。
Risk 返回风险级别、条目、数值依据和 source_ref；Budget 与 Risk 必须验证新方案。
禁止仅通过删除不利证据降低风险，仍不可行则阻断执行并明确待调整约束。
