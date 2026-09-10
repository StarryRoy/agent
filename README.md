# 企业采购决策与执行 Agent

这是一个基于本地 `Agent Harness` 公开 API 的完整采购应用。Main Agent 动态协调需求、库存、
供应商、价格、预算、风险与执行七个 SubAgent；采购写操作由 Execution Agent 发起，并由
Harness 原生 HITL 在持久化 Checkpoint 上暂停和恢复。

应用默认使用确定性、可离线运行的 `BaseChatModel` 来驱动真实的 Harness Agent/Tool 循环，
因此演示、异常注入和回归评测不依赖外部模型密钥。业务数据访问统一经过 Harness 的
`DatabaseToolkit`，外部供应商状态通过 Harness `load_mcp_tools()` 加载的本地标准 MCP Server
获取。项目没有修改 `agent_harness`。

## 架构

```text
Natural-language user / CLI
            │
   Procurement Main Agent
            │ dynamic routing / reflection / selective replan
  ┌─────────┼──────────┬─────────┬────────┬────────┬─────────┐
Requirement Inventory Supplier  Pricing  Budget   Risk   Execution
    Agent      Agent     Agent    Agent    Agent   Agent     Agent
                         │                                  │
                 Supplier-status MCP                 Harness HITL
                         │                                  │
                         └──── DatabaseToolkit ─────────────┘
                                      │
                         SQLite business database

All Agents ── persistent SQLite Checkpointer
All spans  ── MetricsEventSink + data/traces.jsonl
```

路由不是固定流水线：库存满足时在 Inventory Agent 后直接结束；会话修改预算时只重跑预算和
风险，排除供应商或放宽交期时从供应商分析开始重跑；无可行方案会按原因回到 Supplier 或
Pricing，再校验 Budget/Risk。已确认且不受修改影响的结果从同一 Session Checkpoint 复用。

## 安装

项目虚拟环境已经以 editable 方式引用本地 Harness。重新安装可执行：

```powershell
cd D:\Project\agent
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 快速运行

完整验收示例（分析、暂停、批准、执行）：

```powershell
.\.venv\Scripts\python.exe main.py --data-dir .\data demo --approve --session demo-001
```

分步操作：

```powershell
.\.venv\Scripts\python.exe main.py request "下个月需要采购500台设备，预算80万，月底前必须到货。" --session buy-001
.\.venv\Scripts\python.exe main.py approve buy-001
```

也可以在审批前修改；应用会拒绝旧的待执行 Tool Call，保留先前证据，再只重跑受影响部分：

```powershell
.\.venv\Scripts\python.exe main.py modify buy-001 "把数量改成400台，不要供应商A。"
.\.venv\Scripts\python.exe main.py approve buy-001
```

拒绝方案：

```powershell
.\.venv\Scripts\python.exe main.py reject buy-001
```

Python API：

```python
from procurement_agent import create_procurement_app

with create_procurement_app(data_dir="data") as app:
    proposal = app.submit(
        "下个月需要采购500台设备，预算80万，月底前必须到货。",
        session_id="buy-001",
    )
    print(proposal.status)  # approval_required
    print(proposal.data["recommended_plan"])

    completed = app.approve("buy-001")
    print(completed.data["execution_status"])  # success
```

异步服务应使用 `create_procurement_app_async()`、`asubmit()`、`aapprove()`、`areject()` 和
`amodify()`。`astream()` 暴露 Harness 的稳定 StreamEvent，包括 Tool、SubAgent、审批与最终
事件。

## 业务数据库

`procurement_agent/database.py` 创建并填充以下表：

- `products`, `inventory`, `inventory_history`
- `suppliers`, `supplier_products`, `quotations`
- `purchase_history`, `purchase_requests`, `purchase_orders`
- `departments`, `budgets`
- `supplier_quality_records`, `supplier_delivery_records`
- `operation_logs`

种子数据包含暂停合作的超低价供应商、低质量/延迟供应商、价格与交付历史，以及正常的低风险
供应商。示例需求的原始数量是 500 台；库存、在途、安全库存及预测消耗分析后，真实采购缺口
是 445 台。默认推荐由 SUP-B 与 SUP-A 分摊的风险均衡组合，总额 640,600 元。

## HITL 和执行语义

`execute_procurement_plan` 是 Execution Agent 的独立敏感 Tool，带 Harness
`harness_approval` 元数据。执行前 AgentResult 为 `paused`，公开应用状态为
`approval_required`；`approve`、`reject` 或业务修改均从同一个公开 Session ID 恢复。

批准后，Execution Agent 使用 `DatabaseToolkit.execute_write()` 完成：

1. 创建采购申请并记录批准状态；
2. 按供应商分配创建采购订单；
3. 原子条件更新预算占用，避免并发超占；
4. 修改采购状态；
5. 写入审批及操作日志。

执行前会再次读取预算，发现预算已变化时安全失败。所有结果都返回明确的已执行动作和失败原因。

## Observability 与 Metrics

每次 Main/SubAgent、Model、Tool、MCP、HITL、Retry 和执行调用都由 Harness Observer 记录。
脱敏后的完整事件追加到 `data/traces.jsonl`。`app.metrics()` 合并 Harness
`MetricsEventSink` 与业务分类，返回任务成功率、Agent/SubAgent/Tool/Database/MCP 调用数、
Retry、Replan、HITL、总耗时、阶段耗时、Token 使用量和错误率。

## Eval 与测试

固定评测集 `evals/scenarios.json` 覆盖规划要求中的 16 个场景：正常采购、无需采购、数量调整、
多供应商、预算不足、全部超预算、交期不满足、高风险供应商、条件修改、SQL/SubAgent/MCP
失败、HITL 修改/拒绝以及执行成功/失败。

```powershell
# 完整测试
.\.venv\Scripts\python.exe -m pytest

# Ruff
.\.venv\Scripts\ruff.exe check .

# 运行固定评测（可先限制场景数）
.\.venv\Scripts\python.exe main.py eval --limit 3
```

异常场景使用 JSON 包装的确定性注入字段：`simulate_sql_failure`、
`simulate_subagent_failure`、`simulate_mcp_failure` 和 `simulate_execution_failure`。这些字段只为
离线回归测试服务，正常自然语言入口无需使用。
