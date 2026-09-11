在当前 `codex` 分支基础上补充完整的前端和后端，使项目可以作为一个可直接演示的企业采购 Agent 应用运行。

1. 新增后端服务
   使用 FastAPI 封装现有 `ProcurementApplication`，不要重写 Agent、Session、HITL、数据库和业务逻辑。后端只负责 HTTP/API 层以及前端需要的数据转换。

至少提供以下能力：

* 提交自然语言采购需求
* 查询当前 Session 状态和采购方案
* 批准方案
* 拒绝方案
* 修改采购条件并继续同一 Session
* 查询最终执行结果
* 查询 Metrics / Trace 等可观测信息
* 支持前端展示 Agent 执行过程，优先使用 Harness 已有 streaming 能力

保持 `session_id` 作为前后端会话标识，不暴露 LangGraph `thread_id` 等 Harness 内部概念。

2. 新增前端
   实现一个简洁的企业采购 Agent Web 页面，重点用于完整展示业务闭环，不追求复杂 UI。

页面至少包括：

* 自然语言需求输入区域
* 当前 Session / 任务状态
* Agent 执行过程展示
* 库存分析
* 候选供应商
* 价格方案
* 预算分析
* 风险分析
* 推荐方案与备选方案
* HITL 审批区域
* 批准 / 拒绝 / 修改方案操作
* 最终采购执行结果
* 基础 Metrics / Trace 展示

前端应根据后端返回的结构化结果展示数据，不在前端重复采购业务判断。

3. 前后端交互
   设计清晰稳定的 REST API；Agent streaming 可额外使用 SSE 或其他适合 FastAPI 的轻量方式。

用户提交需求后，前端能够完整看到：

用户需求
→ Agent 分析过程
→ 推荐采购方案
→ 等待人工审批
→ 用户批准 / 修改 / 拒绝
→ Agent 恢复执行
→ 最终采购结果

4. 工程结构
   保持现有 `procurement_agent` 核心代码基本不动，在应用外层增加 Web 层，例如：

`backend/`

* FastAPI application
* routers
* API schemas
* Agent application adapter

`frontend/`

* Web UI
* API client
* 页面和组件

具体目录和实现方式可以根据当前项目结构合理调整。

5. 设计原则
   现有 Harness + Multi-Agent 架构仍然是项目核心。FastAPI 和前端只是应用入口与展示层，不要把 Agent 编排、数据库逻辑、HITL 或 Session 管理重新实现一遍。

优先复用现有公开接口：
`submit / approve / reject / modify / metrics / stream`

完成后确保本地可以分别启动 FastAPI 后端和前端，并在 README 中补充最简启动方式。
