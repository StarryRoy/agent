"""Composition root: database, MCP, persistence, observability, and Agents."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_harness import (
    ContextPolicy,
    DatabaseToolkit,
    ObservabilityConfig,
    RuntimeConfig,
    SQLiteConfig,
    create_agent,
    load_mcp_tools,
)

from .application import ProcurementApplication
from .database import initialize_database
from .metrics import JsonLinesEventSink, ProcurementMetricSink
from .models import ProcurementChatModel
from .persistence import PersistentSQLiteSaver
from .schemas import ProcurementDecision, ProcurementState
from .services import ProcurementServices

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
        "返回稳定 JSON，不编造数据库结果。"
    )
    details = {
        "requirement": "提取产品、数量、预算、交期、质量、优先级和供应商约束；缺失字段必须明确。",
        "inventory": "计算可用、锁定、在途、安全库存、预测消耗、采购缺口和建议数量。",
        "supplier": "筛选供应能力、MOQ、交付周期、合作状态和历史表现，并核验外部实时状态。",
        "pricing": "比较当前与历史价格，计算单一及组合供应方案并识别报价异常。",
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
) -> ProcurementApplication:
    """Create the complete application with durable local SQLite state."""

    root = Path(data_dir).resolve() if data_dir else Path(__file__).resolve().parents[1] / "data"
    root.mkdir(parents=True, exist_ok=True)
    business_path = initialize_database(root / "procurement.sqlite", reset=reset_database)
    checkpoint_path = root / "checkpoints.sqlite"
    if reset_database and checkpoint_path.exists():
        checkpoint_path.unlink()

    checkpointer = PersistentSQLiteSaver(checkpoint_path)
    database = DatabaseToolkit(
        config=SQLiteConfig(database=business_path),
        max_rows=200,
        include_write=True,
        require_write_approval=False,
    )
    services = ProcurementServices(database, today or datetime.now(UTC).date())
    tools = services.tools()
    mcp_tools = await _supplier_mcp_tools() if enable_mcp else []

    metric_sink = ProcurementMetricSink()
    event_sink = JsonLinesEventSink(root / "traces.jsonl")
    sinks = [metric_sink, event_sink]
    sub_config = RuntimeConfig(
        max_iterations=5,
        retry_attempts=2,
        timeout_seconds=30,
        call_limit=16,
        context_policy=ContextPolicy(
            summary_threshold=1_000,
            summary_token_threshold=1_000_000,
            summary_keep_recent=50,
        ),
        observability=ObservabilityConfig(payload_detail="standard"),
    )
    main_config = RuntimeConfig(
        max_iterations=20,
        retry_attempts=2,
        timeout_seconds=60,
        call_limit=48,
        context_policy=ContextPolicy(
            summary_threshold=1_000,
            summary_token_threshold=1_000_000,
            summary_keep_recent=100,
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
    for role in ("requirement", "inventory", "supplier", "pricing", "budget", "risk", "execution"):
        role_tools = [tools[role_tool_names[role]]]
        if role == "supplier":
            role_tools.extend(mcp_tools)
        subagents.append(
            create_agent(
                name=f"{role}_agent",
                description=f"企业采购{role}专业分析与处理",
                instructions=_instructions(role),
                model=ProcurementChatModel(role=role),
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
            "你是企业采购 Main Agent。按当前需求动态选择 SubAgent；保留已确认结果，只重跑受影响"
            "领域；遇到超预算、交期冲突或高风险时反思并重规划；形成方案后必须通过 Execution "
            "Agent 的 HITL 审批才能写入业务系统。"
        ),
        model=ProcurementChatModel(role="main"),
        subagents=subagents,
        response_format=ProcurementDecision.model_json_schema(),
        state_schema=ProcurementState,
        runtime_config=main_config,
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
