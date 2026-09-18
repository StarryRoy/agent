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
    LexicalSkillSelector,
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

from .application import ProcurementApplication
from .context import ProcurementContextMiddleware
from .database import ProcurementSQLiteBackend, initialize_database
from .metrics import JsonLinesEventSink, ObservedDatabaseToolkit, ProcurementMetricSink
from .middleware import ProcurementOrchestrationMiddleware
from .models import DeterministicProcurementModel, build_mock_execute_query_tool
from .persistence import PersistentSQLiteSaver
from .schemas import ProcurementAnalysis, ProcurementDecision, ProcurementState
from .services import ProcurementServices
from .skill_policy import ProcurementSkillMiddleware
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
        "你是企业采购应用的专业 SubAgent。只处理分配给你的领域，使用工具获取事实，"
        "返回稳定 JSON，不编造数据库结果。保留 Tool 的业务字段、关键数值、约束、facts、"
        "conclusion、status、evidence 和 source_ref；最终结果不返回 SQL 或原始 MCP 包装。"
        "若本角色提供 Skill，必须先加载并遵循。查询角色先根据 Skill、Schema 和 Metadata 动态生成 SQL，"
        "直接调用 Harness execute_query；返回 ok=false 时把 Harness error 作为反馈，修正 SQL 后"
        "重试，成功且字段契约完整后才调用确定性计算 Tool。不得让计算 Tool 生成或执行 SQL。"
        f"当前业务日期为 {business_today.isoformat()}。"
    )
    details = {
        "requirement": "提取产品、数量、预算、交期、质量、优先级和供应商约束；缺失字段必须明确。",
        "inventory": (
            "查询后调用 calculate_inventory 计算可用、在途、安全库存、预测消耗和采购缺口。"
            "task.analysis_strategy 支持 standard、schema_recovery、fallback_recovery。"
        ),
        "supplier": (
            "查询后调用 calculate_suppliers 筛选能力、MOQ、交期、合作状态和历史表现，并核验外部实时状态。"
            "重规划时按 task.analysis_strategy 使用 delivery_first、risk_first 或 fallback_recovery。"
        ),
        "pricing": (
            "查询后调用 calculate_pricing 比较当前与历史价格，计算单一及组合方案。"
            "重规划时按 task.analysis_strategy 使用 cost_reduction、delivery_recovery 或"
            "risk_diversification，明确条件性方案。"
        ),
        "budget": "查询后调用 calculate_budget 核验部门余额、用户上限和方案预计占用。",
        "risk": "查询后调用 calculate_risk 评估履约、交付、质量、预算、集中度和规模风险。",
        "execution": "只执行 Main Agent 已确认的采购方案，不重新决策。所有写操作使用专用工具。",
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
    selector = LexicalSkillSelector()
    role_skills = {
        "requirement": set(),
        "inventory": {"inventory-analysis"},
        "supplier": {"supplier-analysis"},
        "pricing": {"pricing-analysis"},
        "budget": {"budget-analysis"},
        "risk": {"risk-analysis"},
        "execution": set(),
    }
    main_skill_names = {
        "cost-optimization",
        "delivery-recovery",
        "supplier-risk-review",
        "urgent-procurement",
    }
    main_skills = [skill for skill in skills if skill.name in main_skill_names]

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
            "首次调用 requirement_agent 时，task.text 必须原样携带当前用户的采购消息，不得只传"
            "空 procurement_context；修改会话时携带最新用户修改文本。"
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
        skills=main_skills,
        skill_selector=selector,
        subagents=subagents,
        response_format=ProcurementDecision.model_json_schema(),
        state_schema=ProcurementState,
        runtime_config=main_config,
        middleware=[
            ProcurementContextMiddleware(),
            ProcurementSkillMiddleware("main", main_skills, selector),
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
