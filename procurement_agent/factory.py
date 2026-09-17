"""Composition root: database, MCP, persistence, observability, and Agents."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_harness import (
    ContextPolicy,
    LexicalSkillSelector,
    ObservabilityConfig,
    RuntimeConfig,
    SkillLoader,
    SQLiteConfig,
    create_agent,
    load_mcp_tools,
)
from langchain_core.language_models import BaseChatModel

from .application import ProcurementApplication
from .context import ProcurementContextMiddleware
from .database import ProcurementSQLiteBackend, initialize_database
from .metrics import JsonLinesEventSink, ObservedDatabaseToolkit, ProcurementMetricSink
from .middleware import ProcurementOrchestrationMiddleware
from .models import DeterministicProcurementModel
from .persistence import PersistentSQLiteSaver
from .schemas import ProcurementAnalysis, ProcurementDecision, ProcurementState
from .services import ProcurementServices
from .skill_policy import ProcurementSkillMiddleware

_MCP_TOOL_CACHE: list[Any] | None = None


async def _supplier_mcp_tools() -> list[Any]:
    global _MCP_TOOL_CACHE
    if _MCP_TOOL_CACHE is None:
        server_path = Path(__file__).with_name("mcp_server.py").resolve()
        _MCP_TOOL_CACHE = await load_mcp_tools(
            {
                "supplier_status_service": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(server_path)],
                }
            }
        )
    return list(_MCP_TOOL_CACHE)


def _instructions(role: str) -> str:
    common = (
        "你是企业采购应用的专业 SubAgent。只处理分配给你的领域，使用工具获取事实，"
        "返回稳定 JSON，不编造数据库结果。保留 Tool 的业务字段、关键数值、约束、facts、"
        "conclusion、status、evidence 和 source_ref；不返回 SQL、queries 或原始 MCP 包装。"
        "基于 procurement_context 和当前任务使用已加载 Skill，必要时调用 load_skill；"
        "Skill 只指导策略，计算和校验必须交给现有 Tool，禁止自行改写计算结果。"
    )
    details = {
        "requirement": "提取产品、数量、预算、交期、质量、优先级和供应商约束；缺失字段必须明确。",
        "inventory": (
            "计算可用、锁定、在途、安全库存、预测消耗、采购缺口和建议数量。"
            "task.analysis_strategy 支持 standard、schema_recovery、fallback_recovery。"
        ),
        "supplier": (
            "筛选供应能力、MOQ、交付周期、合作状态和历史表现，并核验外部实时状态。"
            "重规划时按 task.analysis_strategy 使用 delivery_first、risk_first 或 fallback_recovery。"
        ),
        "pricing": (
            "比较当前与历史价格，计算单一及组合供应方案并识别报价异常。"
            "重规划时按 task.analysis_strategy 使用 cost_reduction、delivery_recovery 或"
            "risk_diversification，明确条件性方案。"
        ),
        "budget": "核验部门总额、已用、已审批未执行、可用预算和本次预计占用。",
        "risk": "评估履约、交付、质量、报价、集中度、历史异常和规模风险。",
        "execution": "只执行 Main Agent 已确认的采购方案，不重新决策。所有写操作使用专用工具。",
    }
    return f"{common}{details[role]}"


async def create_procurement_app_async(
    *,
    data_dir: str | Path | None = None,
    reset_database: bool = False,
    enable_mcp: bool = True,
    today: date | None = None,
    model: BaseChatModel | str | None = None,
    role_models: Mapping[str, BaseChatModel | str] | None = None,
    deterministic: bool = False,
) -> ProcurementApplication:
    """Create the application.

    Production callers configure a real model through ``model``, ``role_models``,
    Harness' configured default, or ``AGENT_HARNESS_MODEL``. The deterministic
    model is an explicit test double and is never the formal default.
    """

    if deterministic and (model is not None or role_models):
        raise ValueError("deterministic test mode cannot be combined with configured LLMs")

    root = Path(data_dir).resolve() if data_dir else Path(__file__).resolve().parents[1] / "data"
    root.mkdir(parents=True, exist_ok=True)
    business_path = initialize_database(root / "procurement.sqlite", reset=reset_database)
    checkpoint_path = root / "checkpoints.sqlite"
    if reset_database and checkpoint_path.exists():
        checkpoint_path.unlink()

    checkpointer = PersistentSQLiteSaver(checkpoint_path)
    metric_sink = ProcurementMetricSink()
    event_sink = JsonLinesEventSink(root / "traces.jsonl")
    sinks = [metric_sink, event_sink]
    database = ObservedDatabaseToolkit(
        backend=ProcurementSQLiteBackend(SQLiteConfig(database=business_path)),
        max_rows=200,
        include_write=True,
        require_write_approval=False,
        event_sinks=sinks,
    )
    services = ProcurementServices(database, today or datetime.now(UTC).date())
    tools = services.tools()
    mcp_tools = await _supplier_mcp_tools() if enable_mcp else []

    sub_config = RuntimeConfig(
        max_iterations=5,
        retry_attempts=2,
        timeout_seconds=30,
        call_limit=16,
        context_policy=ContextPolicy(
            summary_token_threshold=24_000,
            summary_keep_recent=16,
            max_skill_candidates=2,
            skill_retention_turns=0,
        ),
        observability=ObservabilityConfig(payload_detail="standard"),
    )
    main_config = RuntimeConfig(
        max_iterations=20,
        retry_attempts=2,
        timeout_seconds=60,
        call_limit=48,
        context_policy=ContextPolicy(
            summary_token_threshold=64_000,
            summary_keep_recent=32,
            max_tool_results=24,
            max_tool_result_chars=50_000,
            max_skill_candidates=2,
            skill_retention_turns=0,
        ),
        observability=ObservabilityConfig(payload_detail="standard"),
    )

    subagents = []
    role_tool_names = {
        "requirement": "parse_requirement",
        "inventory": "analyze_inventory",
        "supplier": "analyze_suppliers",
        "pricing": "analyze_pricing",
        "budget": "analyze_budget",
        "risk": "analyze_risk",
        "execution": "execute_procurement_plan",
    }
    configured_roles = dict(role_models or {})
    skill_root = Path(__file__).with_name("skills")
    skills = [SkillLoader().load(path) for path in sorted(skill_root.iterdir()) if path.is_dir()]
    selector = LexicalSkillSelector()
    role_skills = {
        "requirement": {"urgent-procurement"},
        "inventory": {"urgent-procurement", "delivery-recovery"},
        "supplier": {"urgent-procurement", "supplier-risk-review", "delivery-recovery"},
        "pricing": {skill.name for skill in skills},
        "budget": {"cost-optimization"},
        "risk": {"supplier-risk-review", "delivery-recovery", "cost-optimization"},
        "execution": set(),
    }

    def model_for(role: str) -> BaseChatModel | str | None:
        if deterministic:
            return DeterministicProcurementModel(role=role)
        return configured_roles.get(role, model)

    for role in ("requirement", "inventory", "supplier", "pricing", "budget", "risk", "execution"):
        selected_skills = [skill for skill in skills if skill.name in role_skills[role]]
        role_tools = [tools[role_tool_names[role]]]
        if role == "supplier":
            role_tools.extend(mcp_tools)
        subagents.append(
            create_agent(
                name=f"{role}_agent",
                description=f"企业采购{role}专业分析与处理",
                instructions=_instructions(role),
                model=model_for(role),
                skills=selected_skills,
                skill_selector=selector,
                response_format=ProcurementAnalysis.model_json_schema(),
                middleware=[
                    ProcurementContextMiddleware(),
                    ProcurementSkillMiddleware(role, selected_skills, selector),
                ],
                tools=role_tools,
                runtime_config=sub_config,
                checkpointer=checkpointer,
                event_sinks=sinks,
            )
        )

    main = create_agent(
        name="procurement_main_agent",
        description="动态协调采购分析、审批和执行",
        instructions=(
            "你是企业采购 Main Agent，由你基于对话上下文动态选择、组合和重复调用专业 SubAgent，"
            "禁止按预设固定流水线机械调用。先判断已有证据和缺失字段；库存足够时直接结束。每次委派"
            "的 task 使用 JSON，携带相关既有结构化结果、analysis_strategy、replan_reason 和"
            "replan_start。会话修改时只重跑受影响的分析并复用其他有效结果。\n"
            "反思策略：数据库结构或查询失败时以 schema_recovery 重试受影响 Agent；SubAgent 数据"
            "不足时使用 fallback_recovery 并缩小查询目标；全部超预算时让 Pricing Agent 使用"
            "cost_reduction，生成谈判目标或预算内分阶段备选，再重跑 Budget/Risk；交期不满足时先"
            "让 Supplier Agent 使用 delivery_first，再让 Pricing Agent 使用 delivery_recovery；"
            "供应商风险过高时让 Supplier Agent 使用 risk_first，Pricing Agent 使用"
            "risk_diversification。一次重规划只在首个调整调用设置 replan_start=true。若新策略仍"
            "违反硬约束，明确阻断执行并请求用户调整，不得伪造可行性。\n"
            "形成可行方案后，把 request、recommended_plan、budget_analysis 交给 Execution Agent。"
            "使用结构化采购 Context 中的目标、已确认事实、结论、当前方案、budget_gap、风险、"
            "约束和 evidence/source_ref；最新用户修改使相关旧结论失效，必须重新验证。"
            "超预算时基于 budget_gap 与当前方案加载 cost-optimization，再委派 Pricing "
            "以 cost_reduction 重规划；交期失败使用 delivery-recovery，供应商风险使用 "
            "supplier-risk-review，紧急需求使用 urgent-procurement。先 load_skill 再应用策略。"
            "不要复制 SQL、原始查询行或 MCP 包装；需要更多事实时重新委派对应分析 Tool 查询。"
            "Execution Agent 内部敏感 Tool 会触发 Harness HITL；未审批不得执行。最终必须返回指定"
            "结构化格式和可读 summary。"
        ),
        model=model_for("main"),
        skills=skills,
        skill_selector=selector,
        subagents=subagents,
        response_format=ProcurementDecision.model_json_schema(),
        state_schema=ProcurementState,
        runtime_config=main_config,
        middleware=[
            ProcurementContextMiddleware(),
            ProcurementSkillMiddleware("main", skills, selector),
            ProcurementOrchestrationMiddleware(),
        ],
        checkpointer=checkpointer,
        event_sinks=sinks,
    )
    return ProcurementApplication(
        agent=main,
        database=database,
        checkpointer_connection=checkpointer,
        metrics=metric_sink,
    )


def create_procurement_app(**kwargs: Any) -> ProcurementApplication:
    """Synchronous composition helper; async callers should use the async variant."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(create_procurement_app_async(**kwargs))
    raise RuntimeError("An event loop is already running; use create_procurement_app_async")
