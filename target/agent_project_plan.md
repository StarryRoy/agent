# 企业采购决策与执行 Agent 项目规划

## 0. 注意事项
当前应用项目基于本地独立 Harness 开发。

Harness 项目路径：
`D:\Project\harness`

应用项目需要先在自己的虚拟环境中安装本地包（本项目已安装）：

`python -m pip install -e D:\Project\harness`

应用层通过公开包名使用：

```python
from agent_harness import create_agent, DatabaseToolkit
```

开发时把 `agent_harness` 当作普通第三方依赖使用，优先复用其现有API

不要修改 agent_harness 内部实现。


## 1. 项目目标

基于当前 `codex` 分支的 Agent Harness，开发一个完整的企业采购决策与执行应用。

用户通过自然语言提出采购需求，系统由 Main Agent 协调多个专业 SubAgent，完成需求解析、库存分析、供应商筛选、报价分析、预算校验、风险评估、方案决策、人工审批和采购执行。

应用层优先复用 Harness 已有能力，不重复实现 Runtime、Session、Persistence、Middleware、HITL、Observability 等基础设施。

## 2. 整体架构

```text
User
  ↓
Main Agent
  ├─ Requirement Agent
  ├─ Inventory Agent
  ├─ Supplier Agent
  ├─ Pricing Agent
  ├─ Budget Agent
  ├─ Risk Agent
  └─ Execution Agent
        ↓
Database Toolkit / MCP Tools
        ↓
Business Database / External Services
```

Main Agent 负责全局任务编排和最终决策，各 SubAgent 负责独立业务领域。

## 3. Main Agent

Main Agent 负责：

1. 理解用户采购目标和约束。
2. 判断当前任务需要哪些 SubAgent。
3. 拆解复杂采购任务。
4. 调度多个 SubAgent。
5. 汇总各 SubAgent 的结构化结果。
6. 判断现有信息是否足够形成采购方案。
7. 根据执行结果进行补充分析或重新规划。
8. 生成最终采购方案。
9. 推进 HITL 审批。
10. 审批通过后调用 Execution Agent 完成实际操作。

Main Agent 不直接承担专业领域的数据分析。

## 4. Requirement Agent

负责将自然语言采购需求转换为结构化需求。

至少提取：

- 产品
- 数量
- 预算
- 期望交付时间
- 最晚交付时间
- 质量要求
- 优先级
- 指定或排除供应商
- 其他约束

当关键条件缺失时，应明确返回缺失字段，由 Main Agent 决定是否继续分析或向用户补充确认。

## 5. Inventory Agent

负责分析当前库存和采购缺口。

查询内容包括：

- 当前库存
- 可用库存
- 锁定库存
- 在途库存
- 安全库存
- 历史消耗
- 预计未来消耗

输出至少包括：

- 当前可用数量
- 预计缺口
- 建议采购数量
- 库存风险
- 关键数据依据

## 6. Supplier Agent

负责供应商筛选和基本能力分析。

查询内容包括：

- 可供应产品
- 供应能力
- 最小采购量
- 交付周期
- 当前合作状态
- 历史合作记录
- 准时交付率
- 质量表现
- 历史异常

根据采购需求筛选候选供应商，并给出筛选原因。

## 7. Pricing Agent

负责价格和采购成本分析。

至少支持：

- 当前报价比较
- 历史采购价格比较
- 单价变化分析
- 总采购成本计算
- 报价异常识别
- 不同供应商组合方案
- 数量变化后的成本重算

允许根据任务需要进行多次数据库查询。

输出应包含：

- 各候选供应商报价
- 历史参考价格
- 成本差异
- 异常情况
- 推荐价格方案

## 8. Budget Agent

负责预算分析。

查询：

- 部门预算总额
- 已使用预算
- 已审批未执行预算
- 当前可用预算
- 当前采购申请占用金额

输出：

- 是否满足预算
- 可用预算
- 预计占用金额
- 超预算金额
- 可调整空间
- 预算风险

## 9. Risk Agent

负责采购方案风险评估。

分析内容至少包括：

- 供应商履约风险
- 交付风险
- 质量风险
- 报价异常风险
- 单一供应商集中风险
- 历史异常记录
- 采购规模风险

输出：

- 风险等级
- 主要风险项
- 风险依据
- 推荐方案
- 备选方案
- 不推荐方案及原因

## 10. Execution Agent

负责审批通过后的实际业务操作。

至少支持：

- 创建采购申请
- 创建采购订单
- 锁定或占用预算
- 修改采购状态
- 写入审批结果
- 写入操作日志

Execution Agent 只负责执行已确认的采购方案，不重新进行采购决策。

所有写操作通过独立 Tool 执行。

## 11. Multi-Agent 协作

SubAgent 不按照固定顺序强制执行，应由 Main Agent 根据当前任务动态决定调用关系。

典型流程：

```text
Requirement
    ↓
Inventory
    ↓
Supplier
    ↓
Pricing
    ↓
Budget
    ↓
Risk
    ↓
Main Decision
```

但应允许动态回退和重复调用，例如：

```text
Pricing → 发现全部超预算
        ↓
Main Agent
        ↓
Supplier / Inventory / Pricing
        ↓
重新生成方案
```

或者：

```text
Risk → 推荐供应商风险过高
     ↓
Main Agent
     ↓
Supplier
     ↓
Pricing
     ↓
Risk
```

避免将 Multi-Agent 实现成固定流水线。

## 12. Reflection 与 Replan

当执行结果无法满足采购目标时，Main Agent 应根据已有结果重新调整方案。

至少覆盖：

- 所有供应商报价超预算
- 推荐供应商交付时间不满足要求
- 推荐供应商风险过高
- 实际采购缺口小于原始需求
- 数据缺失
- 某个 SubAgent 返回结果不足
- 外部服务失败
- 当前方案存在明显冲突

重新规划时应保留已经确认的有效结果，只重新执行受影响部分。

## 13. 数据库设计

建立完整的模拟采购业务数据库。

至少包含：

```text
products
inventory
inventory_history

suppliers
supplier_products
quotations

purchase_history
purchase_requests
purchase_orders

departments
budgets

supplier_quality_records
supplier_delivery_records

operation_logs
```

数据中需要同时包含正常数据和异常数据，使复杂任务需要经过多次查询才能得出结论。

## 14. Database Toolkit

统一使用 Harness 提供的 Built-in Database Toolkit。

应用层不重复实现：

- 数据库连接
- SQL 执行
- 事务处理
- Schema 获取
- 底层异常转换

业务层负责：

- 查询目标
- 业务语义
- 数据解释
- 查询结果分析

查询结果应尽量保留业务上下文，例如：

```text
subtask
query
data
facts
conclusion
```

避免只向 Main Agent 返回大量无语义原始数据。

## 15. MCP

接入至少一个 MCP Server 作为外部系统能力。

可选择实现：

- 外部供应商报价服务
- ERP 服务
- 物流服务
- 供应商状态服务
- 外部采购系统

通过 Harness 现有 MCP 接入能力加载标准 Tool，不新增独立 MCP Runtime。

## 16. HITL

采购执行前必须进行人工审批。

支持：

- `approve`
- `reject`
- 修改采购数量
- 修改供应商
- 修改价格
- 修改采购方案
- 修改其他业务参数

审批后从当前 Session 恢复。

已经完成且不受修改影响的分析结果不得无意义重复执行。

## 17. Session 与状态管理

支持同一采购任务的连续修改，例如：

```text
把数量改成 400。
不要供应商 A。
预算可以增加 10 万。
允许晚一周交货。
采用第二个方案。
批准执行。
```

Session 中需要保留当前采购任务相关状态。

应用层不直接依赖 LangGraph `thread_id`。

## 18. Structured Output

各 SubAgent 尽量使用稳定结构化结果。

最终采购结果至少包含：

```text
request
inventory_analysis
candidate_suppliers
pricing_analysis
budget_analysis
risk_analysis
recommended_plan
alternative_plans
approval_status
execution_status
executed_actions
warnings
```

同时生成适合用户直接阅读的自然语言结果。

## 19. Observability

复用 Harness 已有 Observability 能力记录完整执行链路。

至少覆盖：

- Main Agent
- SubAgent
- Model
- Tool
- Database
- MCP
- Retry
- Replan
- HITL
- Execution

需要能够观察：

- 调用关系
- Trace / Span
- 执行耗时
- 调用次数
- Tool 使用情况
- 错误信息
- Retry / Fallback
- 最终任务状态

## 20. Metrics

统计至少以下指标：

- 任务成功率
- Agent 调用次数
- SubAgent 调用次数
- Tool 调用次数
- 数据库调用次数
- MCP 调用次数
- Retry 次数
- Replan 次数
- HITL 次数
- 总耗时
- 各阶段耗时
- Token 使用量
- 错误率

优先复用 Harness 当前 Metrics 能力。

## 21. Eval

建立固定业务测试集，对 Agent 系统进行回归评测。

测试场景至少覆盖：

1. 正常采购。
2. 库存充足无需采购。
3. 实际采购数量需要调整。
4. 多供应商比较。
5. 预算不足。
6. 所有供应商超预算。
7. 供应商交付时间不满足。
8. 推荐供应商风险过高。
9. 用户修改采购条件。
10. SQL 查询失败。
11. SubAgent 执行失败。
12. MCP 调用失败。
13. HITL 修改方案。
14. HITL 拒绝。
15. 最终采购执行成功。
16. 最终采购执行失败。

评估至少包括：

- 任务完成率
- SubAgent 路由正确性
- Tool 使用正确性
- 参数提取正确性
- 最终方案正确性
- 异常恢复能力
- 无效调用数量
- 平均耗时
- Token 消耗

## 22. 异常处理

系统应明确处理：

- SQL 错误
- 查询结果为空
- 数据不一致
- Model 调用失败
- Tool 调用失败
- SubAgent 调用失败
- MCP 服务失败
- Budget 不足
- 无有效供应商
- HITL 中断
- 写操作失败

优先使用 Harness 已有 Middleware、Retry、Fallback 和错误边界。

Agent 能恢复的错误应尝试自行修正；无法恢复时返回明确的失败状态和原因。

## 23. 工程边界

应用层只实现采购业务。

优先复用 Harness 已有：

```text
Agent
SubAgent
Strategy
Session
Persistence
Context
Memory
Skill
Middleware
HITL
Guardrail
MCP
Database Toolkit
Observability
Metrics
Structured Output
```

如果开发过程中发现 Harness 存在阻塞当前业务的缺陷，可以进行必要的最小修改。

不要因为应用需求继续扩展与当前业务无关的 Harness 功能。

## 24. 最终验收

项目完成后，应能够执行类似以下完整任务：

```text
用户：
下个月需要采购 500 台设备，
预算 80 万，
月底前必须到货，
帮我确定最合适的采购方案。
```

系统应能够自主完成：

```text
需求解析
→ 库存分析
→ 确认真实采购缺口
→ 筛选供应商
→ 比较报价
→ 校验预算
→ 风险分析
→ 生成采购方案
→ 必要时重新规划
→ 人工审批
→ 执行采购
→ 返回最终结果
```

整个过程中能够追踪完整执行链路，并支持 Session 恢复、异常处理和评测。
