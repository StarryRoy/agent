"""Composition root: database, MCP, persistence, observability, and Agents."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_harness import (
    ContextPolicy,
    ObservabilityConfig,
    RuntimeConfig,
    SkillLoader,
    SQLiteConfig,
    create_agent,
    load_mcp_tools,
)
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from pydantic import TypeAdapter

from .application import ProcurementApplication
from .context import ProcurementContextMiddleware
from .database import ProcurementSQLiteBackend, initialize_database
from .metrics import JsonLinesEventSink, ObservedDatabaseToolkit, ProcurementMetricSink
from .middleware import ProcurementOrchestrationMiddleware
from .models import DeterministicProcurementModel, build_mock_execute_query_tool
from .persistence import PersistentSQLiteSaver
from .schemas import SUBAGENT_OUTPUT_TYPES, ProcurementDecision, ProcurementState
from .services import ProcurementServices
from .sql_catalog import role_database_context

_MCP_TOOL_CACHE: list[Any] | None = None
GEMINI_MODEL_NAME = "gemini-3.5-flash-lite"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GLM_MODEL_NAME = "glm-4.7-flash"
GLM_API_KEY_ENV = "GLM_API_KEY"
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
DEEPSEEK_MODEL_NAME = "deepseek-flash"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MODEL_PROVIDER_ENV = "PROCUREMENT_MODEL_PROVIDER"
AGENT_ROLES = (
    "requirement",
    "inventory",
    "supplier",
    "pricing",
    "budget",
    "risk",
    "execution",
    "main",
)


def _create_gemini_model() -> ChatGoogleGenerativeAI:
    api_key = os.getenv(GEMINI_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"Real mode requires the {GEMINI_API_KEY_ENV} environment variable. "
            "Set it before starting the application, or pass model/role_models explicitly."
        )
    model = ChatGoogleGenerativeAI(
        model=GEMINI_MODEL_NAME,
        api_key=api_key,
        thinking_level="minimal",
    )
    # langchain-google-genai 4.4.0 injects candidate_count=1 by default, but
    # Gemini 3.x rejects candidate_count. Setting its internal alias to None
    # makes the request serializer omit the unsupported field entirely.
    if getattr(model, "model", None) == GEMINI_MODEL_NAME and hasattr(model, "n"):
        model.n = None
    return model


def _create_glm_model() -> ChatOpenAI:
    api_key = os.getenv(GLM_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"GLM real mode requires the {GLM_API_KEY_ENV} environment variable. "
            "Set it before starting the application."
        )
    return ChatOpenAI(
        model=GLM_MODEL_NAME,
        api_key=api_key,
        base_url=GLM_BASE_URL,
    )


def _create_deepseek_model() -> ChatOpenAI:
    api_key = os.getenv(DEEPSEEK_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"DeepSeek real mode requires the {DEEPSEEK_API_KEY_ENV} environment variable. "
            "Set it before starting the application."
        )
    return ChatOpenAI(
        model=DEEPSEEK_MODEL_NAME,
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _create_default_model() -> BaseChatModel:
    provider = os.getenv(MODEL_PROVIDER_ENV, "glm").strip().lower()
    if provider not in {"deepseek", "gemini", "glm"}:
        raise RuntimeError(
            f"Unsupported {MODEL_PROVIDER_ENV}={provider!r}; "
            "expected 'deepseek', 'gemini', or 'glm'."
        )
    if provider == "deepseek":
        return _create_deepseek_model()
    if provider == "gemini":
        return _create_gemini_model()
    return _create_glm_model()


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


def _instructions(role: str, business_today: date) -> str:
    common = (
        "你是企业采购应用的专业 SubAgent，只处理分配给你的领域。你收到的是业务目标和"
        "应用层确认的上游结构化 State；在这个黑盒边界内自主决定是否加载 Skill、调用哪些"
        "Tool、如何组织分析和是否重试。依据 Tool Schema、可用 Skill、Schema/Metadata 和"
        "Tool 返回结果工作，不等待 Main 指定内部步骤或参数。严格按本角色 Schema 返回 JSON，"
        "不编造事实；核心字段必须完整，场景外补充信息只写入 remarks。原样保留 Tool 已确认的"
        "数量、金额、方案及 evidence，不通过 facts/conclusion 摘要重建或改写；最终结果不返回"
        "SQL 或原始 MCP 包装。查询错误、数据不足或外部服务错误时，自主选择安全的修正/降级"
        "路径，并在结构化结果中保留证据和状态。"
        f"当前业务日期为 {business_today.isoformat()}。"
    )
    details = {
        "requirement": "提取产品、数量、预算、交期、质量、优先级和供应商约束；缺失字段必须明确。",
        "inventory": "完成库存、在途、预测消耗、安全库存和采购缺口分析。",
        "supplier": "筛选供应商能力、MOQ、交期、合作状态和历史表现，并核验可用的实时状态。",
        "pricing": "比较当前与历史价格，形成满足数量、交期和其他硬约束的候选方案。",
        "budget": "核验部门余额、用户预算上限和候选方案预计占用。",
        "risk": "评估履约、交付、质量、预算、集中度和规模风险，确认可行方案。",
        "execution": "执行已由 Main Agent 交付且通过应用层校验的采购方案，不重新决策。",
    }
    database_context = role_database_context(role)
    return f"{common}{details[role]}" + (f"\n\n{database_context}" if database_context else "")


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

    Real mode defaults to one application-owned Gemini or GLM model selected from
    environment configuration. Explicit ``model`` and ``role_models`` values
    override that default. The deterministic model is an explicit test double.
    """

    if deterministic and (model is not None or role_models):
        raise ValueError("deterministic test mode cannot be combined with configured LLMs")

    configured_roles = dict(role_models or {})
    default_model = model
    if (
        not deterministic
        and default_model is None
        and any(role not in configured_roles for role in AGENT_ROLES)
    ):
        default_model = _create_default_model()

    root = Path(data_dir).resolve() if data_dir else Path(__file__).resolve().parents[1] / "data"
    root.mkdir(parents=True, exist_ok=True)
    business_path = initialize_database(root / "procurement.sqlite", reset=reset_database)
    checkpoint_path = root / "checkpoints.sqlite"
    if reset_database and checkpoint_path.exists():
        checkpoint_path.unlink()
    trace_path = root / "traces.jsonl"
    if reset_database and trace_path.exists():
        trace_path.unlink()

    checkpointer = PersistentSQLiteSaver(checkpoint_path)
    metric_sink = ProcurementMetricSink()
    event_sink = JsonLinesEventSink(trace_path)
    sinks = [metric_sink, event_sink]
    database = ObservedDatabaseToolkit(
        backend=ProcurementSQLiteBackend(SQLiteConfig(database=business_path)),
        max_rows=200,
        include_write=True,
        require_write_approval=False,
        event_sinks=sinks,
    )
    business_today = today or datetime.now(UTC).date()
    services = ProcurementServices(database, business_today)
    tools = services.tools()
    database_tools = {tool.name: tool for tool in database.get_tools(include_write=False)}
    execute_query_tool = database_tools["execute_query"]
    mcp_tools = await _supplier_mcp_tools() if enable_mcp else []

    sub_config = RuntimeConfig(
        max_iterations=8,
        retry_attempts=2,
        timeout_seconds=60,
        call_limit=16,
        context_policy=ContextPolicy(
            summary_token_threshold=24_000,
            model_max_input_tokens=128_000,
            summary_keep_recent=16,
            max_skill_candidates=2,
            skill_retention_turns=0,
        ),
        observability=ObservabilityConfig(payload_detail="standard"),
    )
    main_config = RuntimeConfig(
        max_iterations=20,
        retry_attempts=2,
        timeout_seconds=90,
        call_limit=48,
        context_policy=ContextPolicy(
            summary_token_threshold=64_000,
            model_max_input_tokens=128_000,
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
        "inventory": "calculate_inventory",
        "supplier": "calculate_suppliers",
        "pricing": "calculate_pricing",
        "budget": "calculate_budget",
        "risk": "calculate_risk",
        "execution": "execute_procurement_plan",
    }
    skill_root = Path(__file__).with_name("skills")
    skills = [SkillLoader().load(path) for path in sorted(skill_root.iterdir()) if path.is_dir()]
    role_skills = {
        "requirement": {"urgent-procurement"},
        "inventory": {"inventory-analysis", "urgent-procurement"},
        "supplier": {
            "supplier-analysis",
            "delivery-recovery",
            "supplier-risk-review",
            "urgent-procurement",
        },
        "pricing": {
            "pricing-analysis",
            "cost-optimization",
            "delivery-recovery",
            "urgent-procurement",
        },
        "budget": {"budget-analysis", "urgent-procurement"},
        "risk": {"risk-analysis", "supplier-risk-review", "urgent-procurement"},
        "execution": set(),
    }
    def model_for(role: str) -> BaseChatModel | str | None:
        if deterministic:
            return DeterministicProcurementModel(role=role)
        return configured_roles.get(role, default_model)

    for role in ("requirement", "inventory", "supplier", "pricing", "budget", "risk", "execution"):
        selected_skills = [skill for skill in skills if skill.name in role_skills[role]]
        role_tools = [tools[role_tool_names[role]]]
        if role in {"inventory", "supplier", "pricing", "budget", "risk"}:
            # Production receives the native Harness Tool object. Deterministic
            # tests substitute an explicit Mock Tool with the same contract.
            role_tools.insert(
                0,
                build_mock_execute_query_tool(role) if deterministic else execute_query_tool,
            )
        if role == "supplier":
            role_tools.extend(mcp_tools)
        subagents.append(
            create_agent(
                name=f"{role}_agent",
                description=f"企业采购{role}专业分析与处理",
                instructions=_instructions(role, business_today),
                model=model_for(role),
                skills=selected_skills,
                response_format=TypeAdapter(SUBAGENT_OUTPUT_TYPES[f"{role}_agent"]).json_schema(),
                middleware=[
                    ProcurementContextMiddleware(),
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
            "你是企业采购 Main Agent，只负责根据对话和正式 State 判断应调用哪个 SubAgent，"
            "并传入该 SubAgent 完成目标所必需的上游结构化 State。首次调用 requirement_agent 时，"
            "task.goal 必须原样携带当前用户采购消息；修改会话时携带最新用户修改文本。"
            "不要在 task 中传入内部 Tool 参数、SQL、Skill 名称、分析策略或重试步骤。"
            "不要替 SubAgent 选择或加载 Skill，也不要规定 SubAgent 应调用哪个 Tool；只描述业务目标。"
            "禁止按预设固定流水线机械调用。先判断已有证据和缺失字段；库存足够时直接结束。"
            "业务依赖必须严格遵守：requirement 无上游；inventory 依赖 requirement；supplier 依赖"
            "requirement+inventory；pricing 依赖 requirement+inventory+supplier；budget 依赖"
            "requirement+pricing；risk 依赖 requirement+supplier+pricing+budget；execution 依赖"
            "requirement+risk+budget。不得在上游正式结果返回前调用下游；互不依赖且依赖均满足的"
            "任务可并行。同一次模型响应中不得同时安排有前后依赖的 SubAgent，必须等上游结果写入"
            "State 后在下一轮再安排下游。每次委派的 task 只能包含 goal 和相关 upstream_state。"
            "会话修改时只重跑受影响的分析并复用其他有效结果。\n"
            "重规划时只传业务目标，例如重新优化成本、优先满足交期或降低供应商风险；"
            "不要把实现该目标的内部策略、Tool、Skill 或参数写进 task。若仍无满足硬约束的方案，"
            "明确阻断执行并请求用户调整，不得伪造可行性。\n"
            "形成可行方案后，把执行所需的正式 State 交给 Execution Agent。所有下游业务数据只读取"
            "应用层注入的已确认结构化 State，不得根据 facts、conclusion 或其他摘要重建数量、金额"
            "与方案；最新用户修改使相关旧结果失效，必须重新验证。"
            "不要复制 SQL、原始查询行或 MCP 包装；需要更多事实时重新委派对应分析 SubAgent。"
            "Execution Agent 内部敏感 Tool 会触发 Harness HITL；未审批不得执行。最终必须返回指定"
            "结构化格式和可读 summary。"
        ),
        model=model_for("main"),
        skills=(),
        subagents=subagents,
        response_format=ProcurementDecision.model_json_schema(),
        state_schema=ProcurementState,
        runtime_config=main_config,
        middleware=[
            ProcurementContextMiddleware(enable_test_config=deterministic),
            ProcurementOrchestrationMiddleware(enable_test_config=deterministic),
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
