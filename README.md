# 企业采购决策与执行 Agent

企业采购决策与执行平台，覆盖从需求分析、库存核验、供应商筛选、报价评估、预算与风险审查，
到人工审批、采购执行和过程审计的完整流程。

系统基于业务数据和采购约束生成结构化方案，并提供以下能力：

- 按需调度需求、库存、供应商、价格、预算、风险和执行分析；
- 根据库存、交付、质量、预算和供应商风险等条件进行综合评估；
- 在执行前进行人工审批，支持修改、拒绝和从原会话恢复；
- 持久化会话状态、执行事件、指标和审计轨迹；
- 通过数据库事务保证采购申请、订单、预算占用及操作日志的一致性。

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

### 场景 Skill 与业务 Context

`procurement_agent/skills/` 为 Inventory、Supplier、Pricing、Budget、Risk 各提供一个正常业务
Skill，并保留 `cost-optimization`、`urgent-procurement`、`supplier-risk-review`、
`delivery-recovery` 四个重规划策略。每个目录都使用 Harness `SkillLoader` 的原生 `SKILL.md`
格式和受支持 frontmatter。查询 SubAgent 只注册自己的领域 Skill；Main Agent 只注册重规划
Skill。加载事件记录在现有 Trace 的 `skill.load` 中。

每个查询 Skill 定义业务 Workflow、所需数据和稳定字段契约，不包含具体 SQL。对应 SubAgent
只得到相关表的 `schema.sql`/`metadata.md`，调用原生 `execute_query` 后再把成功结果交给
`calculate_inventory`、`calculate_suppliers`、`calculate_pricing`、`calculate_budget` 或
`calculate_risk`。Harness 返回查询错误时，LLM 根据错误、Schema、Metadata 和 Skill 修正 SQL
重试；Python 计算器不生成也不执行查询。

模型输入中的查询结果会转换成业务字段和 `evidence`，保留精确数值、约束、方案、状态及
`source_ref`，最终业务结果不回传 SQL。原始 Tool 消息仍由 Harness Checkpoint 保存；需要更多
事实时重新委派对应分析 Tool 查询。同轮被新分析替代的旧结果不再重复进入模型输入，委派只传
所需依赖。
结构化 `procurement_context` 随子任务进入既有 Session，包含目标、事实、结论、当前方案、
`budget_gap`、有效预算、风险、Replan 原因与来源；新分析使下游旧结论失效，等待重新核验。

例如预算不足时，Main 加载 `cost-optimization` 并保留预算缺口和当前方案，再委派 Pricing 以
`cost_reduction` 执行其领域 Skill，最后由 Budget/Risk 校验新方案。交期和供应商风险采用对应
策略，继续沿用原有 Replan、MCP 和 HITL 审批。

### 一键启动

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe run.py
```

启动器会弹出模式选择窗口。Demo 模式使用确定性测试模型，无需外部模型服务；Real 模式使用
部署环境中配置的模型服务。正式运行前，请按所接入的模型服务完成凭据和模型参数配置。
选择后会自动启动 FastAPI 和前端静态服务，并打开浏览器；在启动窗口按 `Ctrl+C` 会尽量正常
停止两个子进程。可用
`PROCUREMENT_BACKEND_PORT` 和 `PROCUREMENT_FRONTEND_PORT` 调整端口。

### Web 演示（FastAPI + 独立前端）

安装上述 `requirements.txt` 后，在项目根目录打开两个 PowerShell 终端。
以下命令使用明确启用的离线确定性演示模型，无需 API Key：

```powershell
# 终端 1：后端，沿用已有数据库与 Harness Checkpoint，不重置数据
cd D:\Project\agent
$env:PROCUREMENT_DEMO = "1"
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

# 终端 2：前端，无需 Node.js 或构建步骤
cd D:\Project\agent
.\.venv\Scripts\python.exe frontend\static_server.py 5173 --bind 127.0.0.1 --directory frontend
```

打开 [采购工作台](http://127.0.0.1:5173)，点击“填入演示需求”，再点击“开始分析”。
页面显示真实 Harness 事件、库存/供应商/价格/预算/风险分析，以及推荐和备选方案。
待状态变为“等待审批”，可选择批准执行、拒绝，或输入“把数量改成400台，不要供应商A。”
后修改并继续。修改复用同一 Session，再次审批才会执行新方案。
浏览器保存最近的 Session ID；刷新或重启后端后也可输入该 ID 恢复查看和审批。

正式模型模式须移除演示标志，并确保运行环境已经完成模型服务配置：

```powershell
Remove-Item Env:PROCUREMENT_DEMO -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

配置项：`PROCUREMENT_DATA_DIR` 指定数据目录（默认 `data`）；`PROCUREMENT_MCP=0`
仅用于关闭外部供应商 MCP 的离线测试（默认启用）；`PROCUREMENT_CORS_ORIGINS` 为逗号分隔的
前端来源，默认允许 `http://127.0.0.1:5173` 和 `http://localhost:5173`。
前端默认连接 `http://127.0.0.1:8000`；连接其他本地端口可使用
`http://127.0.0.1:5173/?api=http://127.0.0.1:8001`。

此入口用于本地演示，绑定回环地址，无登录与租户权限层。后端使用单个 worker，HTTP 操作在
共享应用实例上串行执行；不要使用 `--workers` 多进程共享该实例的数据目录。浏览器断开 SSE
不会取消采购，正常关闭服务会等待已接受操作完成。进程被强行终止时，Checkpoint 和 Trace
仍保留；未完成且未处于 HITL 暂停的任务显示“执行中断”，不会被当作采购成功。

### Web 工程结构与 API

`backend/app.py` 定义生命周期、REST 路由和 SSE；`backend/schemas.py` 定义 HTTP 契约；
`backend/adapter.py` 调用现有应用并转换传输数据；`frontend/` 是原生 HTML/CSS/JavaScript
页面和 API client。`procurement_agent` 核心未修改。接口只以 `session_id` 标识会话，
不会返回 `thread_id`、Checkpoint 配置或原始 HITL interrupt。

REST 前缀为 `/api/v1`，交互式契约见 [FastAPI API 文档](http://127.0.0.1:8000/docs)：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 服务状态及演示/配置模型模式 |
| POST | `/sessions` | `{"text":"采购需求"}`，返回 202、Session ID 与 events URL |
| GET | `/sessions/{session_id}` | 当前状态、结构化方案及 trace_id |
| POST | `/sessions/{session_id}/approve` | 批准待审批方案，返回 202 |
| POST | `/sessions/{session_id}/reject` | 拒绝待审批方案，返回 202 |
| POST | `/sessions/{session_id}/modify` | `{"text":"修改条件"}`，同一 Session 继续，返回 202 |
| GET | `/sessions/{session_id}/result` | 最终结果；仍执行中或待审批时返回 409 |
| GET | `/sessions/{session_id}/events` | SSE 执行事件与业务状态快照 |
| GET | `/sessions/{session_id}/trace?limit=500` | 持久化 Trace，最大 2000 条，返回截断标志 |
| GET | `/metrics` | 当前服务进程累计 Metrics，服务重启后重新累计 |

未知 Session 返回 404，输入校验失败返回 422；同一 Session 正在执行或审批状态不匹配时
返回 409。每次 `POST /sessions` 创建新 Session，客户端不应自动重试该请求。
状态包括 `running`、`approval_required`、`completed`、`error` 和 `interrupted`。
`completed` 代表 Agent 已完成处理；实际是否采购成功需读取后端 `data.execution_status`，
可能是 `success`、`not_required`、`blocked`、`failed` 或 `not_started`。

SSE 事件包括 `progress`（真实执行事件）、`snapshot`（REST 同形结构化状态）、`done`
（本轮操作结束，应关闭连接）和 `reset`（回放缓冲过期，读取 Trace）。支持 `Last-Event-ID`
或 `after` 游标；每个 Session 内存中最多保留 1000 条传输事件，最多保留 100 个近期 Session
的回放缓冲。服务重启后通过 Checkpoint 恢复方案，通过 Trace 恢复历史。

初次提交使用现有 `ProcurementApplication.astream()`；审批/修改使用
`aapprove/areject/amodify`，将原有 Observer 审计事件转发为 SSE。Harness 当前没有公开的
状态查询接口，因此适配器的 `_checkpoint_result` 集中使用现有 runtime 的配置解析和 Agent
结果转换方法读取 Checkpoint，再复用 `ProcurementApplication._convert`。这是需要在升级
Harness 时运行 Web 回归测试的兼容点，没有复制 Session、采购业务或 HITL 逻辑。
Trace 读取直接利用现有 JSONL 文件，适合本地演示；长期大量运行时应另行增加日志索引和轮转。

```powershell
# Web API 回归：真实 Harness、SQLite 和确定性模型，无外部模型消耗
.\.venv\Scripts\python.exe -m pytest tests/test_web_api.py
```

### 真实 LLM Benchmark

完成模型服务配置后，一条命令会执行固定采购场景并生成可追溯的 JSON、CSV、Markdown 报告以及
逐场景 JSONL Trace：

```powershell
.\.venv\Scripts\python.exe benchmark/run.py
```

结果写入 `benchmark/results/`，可读报告写入 `benchmark/reports/`。使用
`--label before` 和 `--label after` 保存两个版本的基准数据；当两份数据同时存在时，后续报告
会自动增加 Before / After 对比。`--role-model role=provider:model` 继续复用工厂现有的
`role_models` 配置。`--deterministic` 仅用于调试评测模块，生成结果会明确标记为非真实 LLM，
且不能进入正式版本对比。

```powershell
.\.venv\Scripts\python.exe benchmark/run.py --label before
.\.venv\Scripts\python.exe benchmark/run.py --label after
```

每项 Token 和耗时均直接来自 Harness Trace。模型供应商未返回的指标显示为 `N/A`，不会估算。
所有场景默认固定使用 `2026-09-11` 作为业务日期，并在报告中记录日期与 Git commit；可通过
`--benchmark-date YYYY-MM-DD` 或 `PROCUREMENT_BENCHMARK_DATE` 覆盖。Before / After 仅在模型、
角色模型、业务日期、MCP 开关和场景集合一致时生成正式对比。

### CLI / Python

正式运行时，应用层从部署环境加载默认模型，也可以通过 `--model provider:model-name` 显式指定：

```powershell
.\.venv\Scripts\python.exe main.py --model provider:model-name --data-dir .\data demo --approve --session demo-001
```

外部显式传入 `model`、`role_models` 或使用 `--model provider:model-name` 时会优先使用覆盖值；
未覆盖的角色才使用环境选中的默认模型。完整离线验收示例必须显式启用测试 Mock：

```powershell
.\.venv\Scripts\python.exe main.py --data-dir .\data --deterministic demo --approve --session demo-001
```

分步操作：

```powershell
.\.venv\Scripts\python.exe main.py request "采购500台标准工业平板设备，采购部门为研发部（RND），预算上限80万元，最晚2026-10-31交付。要求企业级、三年质保、批次合格率不低于95%。优先保证按期交付，在满足数量、质量和预算约束的前提下尽量降低总成本。请完成库存、供应商、定价、预算和风险分析，并形成可执行采购方案。" --session buy-001
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
        "采购500台标准工业平板设备，采购部门为研发部（RND），预算上限80万元，最晚2026-10-31交付。要求企业级、三年质保、批次合格率不低于95%。优先保证按期交付，在满足数量、质量和预算约束的前提下尽量降低总成本。请完成库存、供应商、定价、预算和风险分析，并形成可执行采购方案。",
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

`procurement_agent/sql/<table>/schema.sql` 是数据库结构的唯一来源；每张表同目录的
`metadata.md` 说明表用途、列语义和枚举/取值规则。`procurement_agent/database.py` 按依赖顺序
读取这些文件完成初始化，种子数据也位于各表目录，不再在 Python 中维护建表 SQL。目录覆盖：

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

批准后，Execution Agent 使用一次 `DatabaseToolkit.execute_write()` 进入明确的数据库事务
边界，由数据库约束与触发器原子完成：

1. 创建采购申请并记录批准状态；
2. 按供应商分配创建采购订单；
3. 原子条件更新预算占用，避免并发超占；
4. 修改采购状态；
5. 写入审批及操作日志。

任何订单、预算、状态、审批结果或日志步骤失败，整个语句都会回滚，不会留下部分采购申请、
订单或预算占用。数据库约束和触发器是执行不变量的原子验证边界；写 Tool 返回失败时不会报告
成功。公开 Session ID 同时写入采购申请和业务操作日志，便于按会话审计。

## Observability 与 Metrics

每次 Main/SubAgent、Model、Tool、MCP、HITL、Retry 和执行调用都由 Harness Observer 记录。
脱敏后的完整事件追加到 `data/traces.jsonl`。`app.metrics()` 合并 Harness
`MetricsEventSink` 与业务分类，返回任务成功率、Agent/SubAgent/Tool/Database/MCP 调用数、
Retry、Replan、HITL、总耗时、阶段耗时、Token 使用量和错误率。Database 指标来自每一次真实
`DatabaseToolkit` 操作，而不是外层业务 Tool 的数量。

## Eval 与测试

固定评测集 `evals/scenarios.json` 覆盖规划要求中的 16 个场景：正常采购、无需采购、数量调整、
多供应商、预算不足、全部超预算、交期不满足、高风险供应商、条件修改、SQL/SubAgent/MCP
失败、HITL 修改/拒绝以及执行成功/失败。每个场景同时检查 SubAgent 路由、Tool 调用序列、
数据库与 MCP 操作次数、参数提取、最终方案、异常恢复策略、无效调用、耗时和 Token 预算，
并输出逐维准确率。固定评测显式使用确定性测试模型，因此不消耗正式模型额度。

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
