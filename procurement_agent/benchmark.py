"""Real-LLM benchmark runner for the fixed evaluation scenarios.

This module deliberately keeps execution and scoring separate.  A scenario is
used only to provide its user input and HITL actions to the application; its
``expected_*`` fields are handed to the benchmark scorer only after the
session has finished.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evaluation import load_scenarios, score_scenario
from .factory import DEEPSEEK_MODEL_NAME, MODEL_PROVIDER_ENV, create_procurement_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT_PATH = PROJECT_ROOT / "data" / "benchmark_result.json"
_DIMENSIONS = (
    "routing",
    "tool_use",
    "parameter_extraction",
    "final_plan",
    "exception_recovery",
    "invalid_calls",
    "duration",
    "token_usage",
)

# These rules are intentionally local to the real-LLM benchmark.  The fixed
# deterministic evaluator keeps its exact route/tool/replan checks in
# evaluation.py; real models are allowed to retry and take another valid path.
_AGENT_DEPENDENCIES: dict[str, set[str]] = {
    "requirement_agent": set(),
    "inventory_agent": {"requirement_agent"},
    "supplier_agent": {"requirement_agent", "inventory_agent"},
    "pricing_agent": {"requirement_agent", "inventory_agent", "supplier_agent"},
    "budget_agent": {"requirement_agent", "pricing_agent"},
    "risk_agent": {
        "requirement_agent",
        "inventory_agent",
        "supplier_agent",
        "pricing_agent",
        "budget_agent",
    },
    "execution_agent": {"requirement_agent", "inventory_agent", "risk_agent", "budget_agent"},
}

_PROFILE_SUBAGENTS: dict[str, set[str]] = {
    "normal": set(_AGENT_DEPENDENCIES),
    "inventory_only": {"requirement_agent", "inventory_agent"},
    "risk_replan": {
        "requirement_agent",
        "inventory_agent",
        "supplier_agent",
        "pricing_agent",
        "budget_agent",
        "risk_agent",
    },
    "cost_replan": {
        "requirement_agent",
        "inventory_agent",
        "supplier_agent",
        "pricing_agent",
        "budget_agent",
        "risk_agent",
    },
    "delivery_replan": {
        "requirement_agent",
        "inventory_agent",
        "supplier_agent",
        "pricing_agent",
        "budget_agent",
        "risk_agent",
    },
    "inventory_recovery": {
        "requirement_agent",
        "inventory_agent",
        "supplier_agent",
        "pricing_agent",
        "budget_agent",
        "risk_agent",
        "execution_agent",
    },
    "modify_full": set(_AGENT_DEPENDENCIES),
    "hitl_resume": set(_AGENT_DEPENDENCIES),
}

_AGENT_REQUIRED_TOOLS: dict[str, set[str]] = {
    "requirement_agent": {"parse_requirement"},
    "inventory_agent": {"execute_query", "calculate_inventory"},
    "supplier_agent": {"execute_query", "calculate_suppliers"},
    "pricing_agent": {"execute_query", "calculate_pricing"},
    "budget_agent": {"execute_query", "calculate_budget"},
    "risk_agent": {"execute_query", "calculate_risk"},
}

_SUBAGENT_NAMES = set(_AGENT_DEPENDENCIES)
_KNOWN_TOOLS = _SUBAGENT_NAMES | {
    "parse_requirement",
    "load_skill",
    "unload_skill",
    "read_skill_asset",
    "run_skill_script",
    "agent_harness_structured_response",
    "execute_query",
    "calculate_inventory",
    "calculate_suppliers",
    "calculate_pricing",
    "calculate_budget",
    "calculate_risk",
    "execute_procurement_plan",
    "supplier_status",
}

_TOOL_REQUIRED_ARGUMENTS: dict[str, set[str]] = {
    "parse_requirement": {"text", "previous_request"},
    "execute_query": {"sql"},
    "calculate_inventory": {"request", "query_result", "analysis_strategy"},
    "calculate_suppliers": {"request", "inventory_analysis", "query_result", "analysis_strategy"},
    "calculate_pricing": {
        "request",
        "inventory_analysis",
        "supplier_analysis",
        "query_result",
        "analysis_strategy",
    },
    "calculate_budget": {
        "request",
        "pricing_analysis",
        "query_result",
        "analysis_strategy",
    },
    "calculate_risk": {
        "request",
        "supplier_analysis",
        "pricing_analysis",
        "budget_analysis",
        "query_result",
        "analysis_strategy",
    },
    "execute_procurement_plan": {
        "request",
        "recommended_plan",
        "budget_analysis",
        "session_id",
    },
    "supplier_status": {"supplier_codes"},
}

_TOOL_ALLOWED_AGENTS: dict[str, set[str]] = {
    "parse_requirement": {"requirement_agent"},
    "execute_query": {
        "inventory_agent",
        "supplier_agent",
        "pricing_agent",
        "budget_agent",
        "risk_agent",
    },
    "calculate_inventory": {"inventory_agent"},
    "calculate_suppliers": {"supplier_agent"},
    "calculate_pricing": {"pricing_agent"},
    "calculate_budget": {"budget_agent"},
    "calculate_risk": {"risk_agent"},
    "execute_procurement_plan": {"execution_agent"},
    "supplier_status": {"supplier_agent"},
}

_FAULT_MARKERS: dict[str, tuple[str, ...]] = {
    "simulate_sql_failure": ("table_not_found", "referenced table does not exist"),
    "simulate_subagent_failure": (
        "scripted inventory subagent failure",
        "scripted supplier subagent failure",
    ),
    "simulate_mcp_failure": ("scripted external supplier-status outage",),
    "simulate_execution_failure": (
        "scripted_execution_failure",
        "执行前外部采购系统失败",
    ),
    "simulate_atomic_failure": (
        "atomic_execution_failed",
        "采购事务已整体回滚",
        "rolled_back",
    ),
}


def _error_payload(exc: BaseException, *, phase: str) -> dict[str, str]:
    return {
        "phase": phase,
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }


def _apply_actions(app: Any, scenario: dict[str, Any], session_id: str, response: Any) -> Any:
    """Apply only the user-facing HITL actions declared by a scenario."""

    for action in scenario.get("actions", []):
        action_type = action.get("type")
        if action_type == "approve":
            response = app.approve(session_id)
        elif action_type == "reject":
            response = app.reject(session_id)
        elif action_type == "modify":
            response = app.modify(session_id, action["value"])
        else:
            raise ValueError(f"Unsupported scenario action: {action_type!r}")
    return response


def _empty_score() -> dict[str, Any]:
    return {
        "passed": False,
        "failures": ["execution_error"],
        "dimensions": {name: False for name in _DIMENSIONS},
        "invalid_calls": 0,
    }


def _response_data(response: Any) -> dict[str, Any]:
    if response is None:
        return {}
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    if hasattr(response, "as_dict"):
        payload = response.as_dict()
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload if isinstance(payload, dict) else {}
    return data if isinstance(data, dict) else {}


def _required_subagents(scenario: dict[str, Any]) -> set[str]:
    explicit = scenario.get("expected_subagent_sequence")
    if isinstance(explicit, (list, tuple)):
        return {str(name) for name in explicit}
    profile = str(scenario.get("expected_route_profile") or "")
    return set(_PROFILE_SUBAGENTS.get(profile, set()))


def _required_tools(scenario: dict[str, Any], required_agents: set[str]) -> set[str]:
    required = {str(name) for name in (scenario.get("expected_tools") or [])}
    forbidden = {str(name) for name in (scenario.get("forbidden_tools") or [])}
    for agent in required_agents:
        required.update(_AGENT_REQUIRED_TOOLS.get(agent, set()))
    if scenario.get("expect_execution_tool") and "execute_procurement_plan" not in forbidden:
        required.add("execute_procurement_plan")
    if scenario.get("expected_min_mcp_calls", 0):
        required.add("supplier_status")
    return required - forbidden


def _business_score(
    scenario: dict[str, Any],
    response: Any,
    metrics: dict[str, Any],
    duration_ms: float,
) -> tuple[list[str], dict[str, bool]]:
    """Reuse only deterministic business-result checks for the real benchmark."""

    failures, dimensions, _ = score_scenario(scenario, response, metrics, duration_ms)
    prefixes = ("parameter_extraction:", "final_plan:")
    business_failures = [failure for failure in failures if failure.startswith(prefixes)]
    business_dimensions = {
        "parameter_extraction": bool(dimensions.get("parameter_extraction")),
        "final_plan": bool(dimensions.get("final_plan")),
    }
    return business_failures, business_dimensions


def _trace_tool_calls(trace_events: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str, Any]]:
    calls: list[tuple[dict[str, Any], str, Any]] = []
    for event in trace_events:
        if event.get("event_type") != "tool.start":
            continue
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        name = metadata.get("name", event.get("name"))
        if not name:
            continue
        arguments = metadata.get("arguments", event.get("arguments"))
        calls.append((event, str(name), arguments))
    return calls


def _tool_argument_problem(name: str, arguments: Any) -> str | None:
    if not isinstance(arguments, Mapping):
        return "arguments are not an object"

    if name in _SUBAGENT_NAMES:
        if not isinstance(arguments.get("task"), str) or not arguments["task"].strip():
            return "missing non-empty task"
        return None

    if name == "load_skill":
        return None if isinstance(arguments.get("name"), str) else "missing skill name"
    if name == "unload_skill":
        return None if isinstance(arguments.get("name"), str) else "missing skill name"
    if name in {"read_skill_asset", "run_skill_script"}:
        return None

    required = _TOOL_REQUIRED_ARGUMENTS.get(name)
    if required:
        missing = sorted(key for key in required if key not in arguments)
        if missing:
            return f"missing required arguments {missing!r}"

    if name == "parse_requirement" and not isinstance(arguments.get("text"), str):
        return "text is not a string"
    if name == "execute_query":
        if not isinstance(arguments.get("sql"), str) or not arguments["sql"].strip():
            return "sql is not a non-empty string"
        parameters = arguments.get("parameters")
        if parameters is not None and not isinstance(parameters, (Mapping, list, tuple)):
            return "parameters are not an object or list"
    if name == "supplier_status":
        supplier_codes = arguments.get("supplier_codes")
        if not isinstance(supplier_codes, (list, tuple)) or not all(
            isinstance(code, str) and code for code in supplier_codes
        ):
            return "supplier_codes are not a list of strings"
    if name == "execute_procurement_plan" and not isinstance(
        arguments.get("session_id"), str
    ):
        return "session_id is not a string"
    return None


def _tool_owner_problem(event: dict[str, Any], name: str) -> str | None:
    agent_name = event.get("agent_name")
    if not agent_name:
        return None
    agent_name = str(agent_name)
    if name in _SUBAGENT_NAMES:
        return None if agent_name == "procurement_main_agent" else "subagent tool called outside main agent"
    allowed = _TOOL_ALLOWED_AGENTS.get(name)
    if allowed and agent_name not in allowed:
        return f"tool called by {agent_name!r}, expected one of {sorted(allowed)!r}"
    if name in {"load_skill", "unload_skill", "read_skill_asset", "run_skill_script"} and not agent_name.endswith(
        "_agent"
    ):
        return f"skill tool called by {agent_name!r}"
    return None


def _routing_failures(
    scenario: dict[str, Any], response: Any, metrics: dict[str, Any]
) -> tuple[list[str], int]:
    failures: list[str] = []
    invalid_calls = 0
    actual_route = [str(name) for name in (metrics.get("subagent_route") or [])]
    required = _required_subagents(scenario)
    forbidden = {str(name) for name in (scenario.get("forbidden_subagents") or [])}

    for name in sorted(required - set(actual_route)):
        failures.append(f"routing: required SubAgent {name!r} was not called")
    for name in sorted(forbidden & set(actual_route)):
        failures.append(f"routing: forbidden SubAgent {name!r} was called")
        invalid_calls += actual_route.count(name)

    seen: set[str] = set()
    for name in actual_route:
        dependencies = _AGENT_DEPENDENCIES.get(name)
        if dependencies is None:
            failures.append(f"routing: unknown SubAgent {name!r}")
            invalid_calls += 1
            continue
        missing = sorted(dependencies - seen)
        if missing:
            failures.append(f"routing: {name!r} was called before {missing!r}")
            invalid_calls += 1
        seen.add(name)

    allowed = scenario.get("allowed_subagents")
    if allowed:
        allowed_set = {str(name) for name in allowed}
        for name in actual_route:
            if name not in allowed_set:
                failures.append(f"routing: SubAgent {name!r} is outside the allowed set")
                invalid_calls += 1

    expected_status = scenario.get("expected_status")
    if expected_status is not None and getattr(response, "status", None) != expected_status:
        failures.append(
            f"routing: expected final business status {expected_status!r}; "
            f"got {getattr(response, 'status', None)!r}"
        )
    expected_execution_status = scenario.get("expected_execution_status")
    actual_execution_status = _response_data(response).get("execution_status")
    if expected_execution_status is not None and actual_execution_status != expected_execution_status:
        failures.append(
            f"routing: expected execution status {expected_execution_status!r}; "
            f"got {actual_execution_status!r}"
        )
    return failures, invalid_calls


def _tool_failures(
    scenario: dict[str, Any],
    metrics: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> tuple[list[str], int]:
    failures: list[str] = []
    invalid_calls = 0
    actual_tools = [str(name) for name in (metrics.get("tool_route") or [])]
    required_agents = _required_subagents(scenario)
    required_tools = _required_tools(scenario, required_agents)
    forbidden = {str(name) for name in (scenario.get("forbidden_tools") or [])}

    for name in sorted(required_tools - set(actual_tools)):
        failures.append(f"tool_use: required Tool {name!r} was not called")
    for name in sorted(forbidden & set(actual_tools)):
        failures.append(f"tool_use: forbidden Tool {name!r} was called")
        invalid_calls += actual_tools.count(name)
    for name in actual_tools:
        if name not in _KNOWN_TOOLS:
            failures.append(f"tool_use: unknown Tool {name!r}")
            invalid_calls += 1

    allowed = scenario.get("allowed_tools")
    if allowed:
        allowed_set = {str(name) for name in allowed}
        for name in actual_tools:
            if name not in allowed_set:
                failures.append(f"tool_use: Tool {name!r} is outside the allowed set")
                invalid_calls += 1

    for event, name, arguments in _trace_tool_calls(trace_events):
        if name not in _KNOWN_TOOLS:
            continue
        problem = _tool_argument_problem(name, arguments)
        if problem:
            failures.append(f"tool_use: invalid arguments for {name!r}: {problem}")
            invalid_calls += 1
        owner_problem = _tool_owner_problem(event, name)
        if owner_problem:
            failures.append(f"tool_use: {name!r} has invalid ownership: {owner_problem}")
            invalid_calls += 1
    return failures, invalid_calls


def _active_faults(scenario: dict[str, Any]) -> dict[str, Any]:
    raw_input = scenario.get("input")
    if not isinstance(raw_input, str):
        return {}
    try:
        payload = json.loads(raw_input)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return {
        str(key): value
        for key, value in payload.items()
        if str(key).startswith("simulate_") and bool(value)
    }


def _fault_evidence(
    response: Any,
    metrics: dict[str, Any],
    trace_events: list[dict[str, Any]],
) -> str:
    payload = {
        "response": _response_data(response),
        "metrics": metrics,
        "trace": trace_events,
    }
    return json.dumps(payload, ensure_ascii=False, default=str).casefold()


def _fault_was_observed(
    fault_name: str,
    fault_value: Any,
    evidence: str,
    scenario: dict[str, Any],
    metrics: dict[str, Any],
) -> bool:
    markers = _FAULT_MARKERS.get(fault_name, ())
    if any(marker.casefold() in evidence for marker in markers):
        return True

    route = [str(name) for name in (metrics.get("subagent_route") or [])]
    errors = int(metrics.get("errors", 0) or 0)
    if fault_name == "simulate_sql_failure" and int(metrics.get("database_errors", 0) or 0) > 0:
        return True
    if fault_name == "simulate_subagent_failure":
        target = "supplier_agent" if str(fault_value).casefold() == "supplier" else "inventory_agent"
        return route.count(target) >= 2 and errors > 0
    if fault_name == "simulate_mcp_failure":
        return "supplier_status" in set(metrics.get("tool_route") or []) and errors > 0
    if fault_name == "simulate_execution_failure":
        return (
            "execute_procurement_plan" in set(metrics.get("tool_route") or [])
            and scenario.get("expected_execution_status") == "failed"
            and errors > 0
        )
    return False


def _business_status(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, Mapping):
        return None
    content = value.get("content")
    if isinstance(content, Mapping) and isinstance(content.get("status"), str):
        return str(content["status"])
    if isinstance(value.get("status"), str) and any(
        key in value for key in ("executed_actions", "purchase_request_id", "error", "transaction")
    ):
        return str(value["status"])
    return None


def _successful_execution_observed(trace_events: list[dict[str, Any]]) -> bool:
    for event in trace_events:
        if event.get("event_type") != "tool.end":
            continue
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        if str(metadata.get("name", event.get("name"))) != "execute_procurement_plan":
            continue
        result = metadata.get("result", event.get("result"))
        if _business_status(result) == "success":
            return True
    return False


def _exception_failures(
    scenario: dict[str, Any],
    response: Any,
    metrics: dict[str, Any],
    trace_events: list[dict[str, Any]],
    final_plan_ok: bool,
) -> list[str]:
    active_faults = _active_faults(scenario)
    evidence = _fault_evidence(response, metrics, trace_events)
    failures: list[str] = []
    for fault_name, fault_value in active_faults.items():
        if not _fault_was_observed(fault_name, fault_value, evidence, scenario, metrics):
            failures.append(f"exception_recovery: {fault_name!r} was not observed")
    if active_faults and not final_plan_ok:
        failures.append("exception_recovery: final state did not satisfy the expected safe outcome")

    expected_execution_status = scenario.get("expected_execution_status")
    if expected_execution_status in {"failed", "blocked", "not_required", "awaiting_approval"}:
        if _successful_execution_observed(trace_events):
            failures.append("exception_recovery: successful execution was observed for a non-success outcome")
        if "simulate_atomic_failure" not in active_faults and "execute_write" in set(
            metrics.get("database_route") or []
        ):
            failures.append("exception_recovery: execution wrote state for a non-success outcome")
    return failures


def score_real_llm_scenario(
    scenario: dict[str, Any],
    response: Any,
    metrics: dict[str, Any],
    duration_ms: float,
    trace_events: list[dict[str, Any]] | None = None,
) -> tuple[list[str], dict[str, bool], int]:
    """Score one real-LLM case using semantic path checks.

    Exact route/tool/replan counts, database-call expectations, duration, and
    token budgets are deliberately absent from this function.  Only the two
    business-result dimensions are delegated to the deterministic evaluator.
    """

    trace_events = trace_events or []
    business_failures, business_dimensions = _business_score(
        scenario, response, metrics, duration_ms
    )
    routing_failures, routing_invalid = _routing_failures(scenario, response, metrics)
    tool_failures, tool_invalid = _tool_failures(scenario, metrics, trace_events)
    exception_failures = _exception_failures(
        scenario,
        response,
        metrics,
        trace_events,
        business_dimensions["final_plan"],
    )
    invalid_calls = routing_invalid + tool_invalid
    failures = [*routing_failures, *tool_failures, *business_failures, *exception_failures]
    dimensions = {
        "routing": not routing_failures,
        "tool_use": not tool_failures,
        "parameter_extraction": business_dimensions["parameter_extraction"],
        "final_plan": business_dimensions["final_plan"],
        "exception_recovery": not exception_failures,
        "invalid_calls": invalid_calls == 0,
        # Performance values are recorded separately and never gate the case.
        "duration": True,
        "token_usage": True,
    }
    return failures, dimensions, invalid_calls


def _read_trace_events(trace_path: Path) -> list[dict[str, Any]]:
    if not trace_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if isinstance(event, dict):
            events.append(event)
    return events


def _run_case(
    scenario: dict[str, Any],
    *,
    temporary_root: Path,
    model: Any,
) -> dict[str, Any]:
    """Run one scenario in its own application data directory and session."""

    started = time.perf_counter()
    session_id = f"benchmark-{scenario['id']}-{uuid.uuid4().hex}"
    case_dir = temporary_root / str(scenario["id"])
    app = None
    response = None
    runtime_metrics: dict[str, Any] = {}
    trace_events: list[dict[str, Any]] = []
    execution_error: dict[str, Any] | None = None
    score_error: dict[str, Any] | None = None

    try:
        app = create_procurement_app(
            data_dir=case_dir,
            reset_database=True,
            enable_mcp=True,
            model=model,
            deterministic=False,
            enable_test_config=True,
        )
        # The agent sees only the scenario's user message.  Expected values are
        # not part of this call or any application state passed to the agent.
        response = app.submit(scenario["input"], session_id=session_id)
        response = _apply_actions(app, scenario, session_id, response)
    except Exception as exc:  # noqa: BLE001 - one provider failure must not stop the suite
        execution_error = _error_payload(exc, phase="execution")
    finally:
        if app is not None:
            try:
                runtime_metrics = app.metrics()
            except Exception as exc:  # noqa: BLE001 - preserve the original case result
                execution_error = execution_error or _error_payload(exc, phase="metrics")
            try:
                app.close()
            except Exception as exc:  # noqa: BLE001 - preserve the original case result
                execution_error = execution_error or _error_payload(exc, phase="close")
        try:
            trace_events = _read_trace_events(case_dir / "traces.jsonl")
        except Exception as exc:  # noqa: BLE001 - preserve the original case result
            execution_error = execution_error or _error_payload(exc, phase="trace")

    duration_ms = (time.perf_counter() - started) * 1000
    if response is None:
        score = _empty_score()
    else:
        try:
            failures, dimensions, invalid_calls = score_real_llm_scenario(
                scenario,
                response,
                runtime_metrics,
                duration_ms,
                trace_events,
            )
            score = {
                "passed": not failures and execution_error is None,
                "failures": failures,
                "dimensions": dimensions,
                "invalid_calls": invalid_calls,
            }
        except Exception as exc:  # noqa: BLE001 - scoring failure is recorded per case
            score_error = _error_payload(exc, phase="scoring")
            score = {
                "passed": False,
                "failures": ["scoring_error"],
                "dimensions": {name: False for name in _DIMENSIONS},
                "invalid_calls": 0,
            }

    final = response.as_dict() if response is not None else None
    token_usage = {
        # These values are copied from Harness' provider usage accumulator.  No
        # character-count or input+output fallback is performed here.
        "input_tokens": runtime_metrics.get("input_tokens", 0),
        "output_tokens": runtime_metrics.get("output_tokens", 0),
        "total_tokens": runtime_metrics.get("total_tokens", 0),
    }
    return {
        "id": scenario["id"],
        "description": scenario.get("description", ""),
        "input": scenario["input"],
        "actions": list(scenario.get("actions", [])),
        "session_id": session_id,
        "status": final.get("status") if final else "error",
        "duration_ms": duration_ms,
        "response": final,
        "score": score,
        "metrics": runtime_metrics,
        "token_usage": token_usage,
        "error": execution_error,
        "score_error": score_error,
    }


def _sum_metric(cases: list[dict[str, Any]], name: str) -> int | float:
    return sum(
        value
        for case in cases
        for value in [case["metrics"].get(name, 0)]
        if isinstance(value, (int, float))
    )


def _dimension_rate(cases: list[dict[str, Any]], name: str) -> float:
    if not cases:
        return 0.0
    return sum(bool(case["score"]["dimensions"].get(name)) for case in cases) / len(cases)


def _summary(cases: list[dict[str, Any]], total_duration_ms: float) -> dict[str, Any]:
    scenario_count = len(cases)
    passed = sum(bool(case["score"]["passed"]) for case in cases)
    durations = [float(case["duration_ms"]) for case in cases]
    total_runs = scenario_count
    total_tokens = _sum_metric(cases, "total_tokens")
    input_tokens = _sum_metric(cases, "input_tokens")
    output_tokens = _sum_metric(cases, "output_tokens")
    errors = sum(
        int(case["metrics"].get("errors", 0) or 0)
        + bool(case["error"])
        + bool(case["score_error"])
        for case in cases
    )
    task_status_completed = sum(
        case["status"] in {"completed", "approval_required"} for case in cases
    )

    return {
        "scenario_count": scenario_count,
        "total_runs": total_runs,
        "passed": passed,
        "failed": scenario_count - passed,
        # This keeps the established evaluation meaning of task completion:
        # the complete scenario score passes all required dimensions.
        "task_completion_rate": passed / scenario_count if scenario_count else 0.0,
        "task_status_completion_rate": (
            task_status_completed / scenario_count if scenario_count else 0.0
        ),
        "routing_accuracy": _dimension_rate(cases, "routing"),
        "tool_use_accuracy": _dimension_rate(cases, "tool_use"),
        "parameter_extraction_accuracy": _dimension_rate(cases, "parameter_extraction"),
        "final_plan_accuracy": _dimension_rate(cases, "final_plan"),
        "exception_recovery_rate": _dimension_rate(cases, "exception_recovery"),
        "zero_invalid_call_rate": _dimension_rate(cases, "invalid_calls"),
        "total_tokens": total_tokens,
        "average_tokens": total_tokens / total_runs if total_runs else 0.0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "average_input_tokens": input_tokens / total_runs if total_runs else 0.0,
        "average_output_tokens": output_tokens / total_runs if total_runs else 0.0,
        "average_duration_ms": sum(durations) / total_runs if total_runs else 0.0,
        "total_duration_ms": total_duration_ms,
        "model_calls": _sum_metric(cases, "model_calls"),
        "tool_calls": _sum_metric(cases, "tool_calls"),
        "subagent_calls": _sum_metric(cases, "subagent_calls"),
        "replan_count": _sum_metric(cases, "replan_count"),
        "error_count": errors,
        "errors": errors,
        "token_usage_source": "real_model_usage_metadata",
        "token_usage_estimated": False,
    }


def _relative_to_project(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _model_label(model: Any) -> str:
    if model is not None:
        return str(model)
    provider = os.getenv(MODEL_PROVIDER_ENV, "deepseek").strip().lower()
    if provider == "deepseek":
        return DEEPSEEK_MODEL_NAME
    return provider or "configured-default"


def run_benchmark(
    *,
    scenarios_path: str | Path | None = None,
    result_path: str | Path | None = None,
    model: Any = None,
) -> dict[str, Any]:
    """Run every fixed scenario with the configured real LLM and save JSON.

    Each scenario gets a fresh application data directory and a unique session.
    Scoring happens only after the response and all declared HITL actions have
    completed.  ``deterministic=True`` is intentionally never used here.
    """

    scenarios = load_scenarios(scenarios_path)
    output_path = Path(result_path) if result_path else DEFAULT_RESULT_PATH
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path = output_path.resolve()
    source_path = (
        Path(scenarios_path).resolve()
        if scenarios_path
        else PROJECT_ROOT / "evals" / "scenarios.json"
    )
    started_at = datetime.now(UTC)
    total_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="procurement-benchmark-") as temporary:
        temporary_root = Path(temporary)
        cases = [
            _run_case(scenario, temporary_root=temporary_root, model=model)
            for scenario in scenarios
        ]
    total_duration_ms = (time.perf_counter() - total_started) * 1000

    report: dict[str, Any] = {
        "schema_version": 1,
        "metadata": {
            "mode": "real_llm",
            "real_llm": True,
            "model": _model_label(model),
            "scenarios_path": _relative_to_project(source_path),
            "result_path": _relative_to_project(output_path),
            "command": "python main.py benchmark",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
        },
        "summary": _summary(cases, total_duration_ms),
        "cases": cases,
        "result_path": _relative_to_project(output_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output_path.with_name(f".{output_path.name}.tmp")
    temporary_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary_output.replace(output_path)
    return report
