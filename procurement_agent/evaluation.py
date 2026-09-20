"""Fixed business regression suite with routing, tool, quality, and cost scoring."""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .factory import create_procurement_app


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    cases: list[dict[str, Any]]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"cases": self.cases, "metrics": self.metrics}


def load_scenarios(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = (
        Path(path) if path else Path(__file__).resolve().parents[1] / "evals" / "scenarios.json"
    )
    return json.loads(source.read_text(encoding="utf-8"))


_ALL_SUBAGENTS = {
    "requirement_agent",
    "inventory_agent",
    "supplier_agent",
    "pricing_agent",
    "budget_agent",
    "risk_agent",
    "execution_agent",
}
_ALL_TOOLS = _ALL_SUBAGENTS | {
    "parse_requirement",
    "load_skill",
    "execute_query",
    "calculate_inventory",
    "calculate_suppliers",
    "calculate_pricing",
    "calculate_budget",
    "calculate_risk",
    "execute_procurement_plan",
    "supplier_status",
}
_NORMAL_SUBAGENTS = [
    "requirement_agent",
    "inventory_agent",
    "supplier_agent",
    "pricing_agent",
    "budget_agent",
    "risk_agent",
    "execution_agent",
]
_NORMAL_TOOLS = [
    "requirement_agent",
    "parse_requirement",
    "inventory_agent",
    "execute_query",
    "calculate_inventory",
    "supplier_agent",
    "execute_query",
    "calculate_suppliers",
    "supplier_status",
    "pricing_agent",
    "execute_query",
    "calculate_pricing",
    "budget_agent",
    "execute_query",
    "calculate_budget",
    "risk_agent",
    "execute_query",
    "calculate_risk",
    "execution_agent",
]


def _route_profile(name: str) -> tuple[list[str], list[str]]:
    pair = {
        "requirement_agent": ["requirement_agent", "parse_requirement"],
        "inventory_agent": ["inventory_agent", "execute_query", "calculate_inventory"],
        "supplier_agent": [
            "supplier_agent",
            "execute_query",
            "calculate_suppliers",
            "supplier_status",
        ],
        "pricing_agent": ["pricing_agent", "execute_query", "calculate_pricing"],
        "budget_agent": ["budget_agent", "execute_query", "calculate_budget"],
        "risk_agent": ["risk_agent", "execute_query", "calculate_risk"],
    }
    profiles = {
        "normal": _NORMAL_SUBAGENTS,
        "inventory_only": ["requirement_agent", "inventory_agent"],
        "risk_replan": [
            "requirement_agent",
            "inventory_agent",
            "supplier_agent",
            "pricing_agent",
            "budget_agent",
            "risk_agent",
            "supplier_agent",
            "pricing_agent",
            "budget_agent",
            "risk_agent",
        ],
        "cost_replan": [
            "requirement_agent",
            "inventory_agent",
            "supplier_agent",
            "pricing_agent",
            "budget_agent",
            "risk_agent",
            "pricing_agent",
            "budget_agent",
            "risk_agent",
        ],
        "delivery_replan": [
            "requirement_agent",
            "inventory_agent",
            "supplier_agent",
            "pricing_agent",
            "supplier_agent",
            "pricing_agent",
            "budget_agent",
            "risk_agent",
        ],
        "inventory_recovery": [
            "requirement_agent",
            "inventory_agent",
            "inventory_agent",
            "supplier_agent",
            "pricing_agent",
            "budget_agent",
            "risk_agent",
            "execution_agent",
        ],
        "modify_full": [
            *_NORMAL_SUBAGENTS,
            "execution_agent",
            *_NORMAL_SUBAGENTS,
        ],
        "hitl_resume": [*_NORMAL_SUBAGENTS, "execution_agent"],
    }
    if name not in profiles:
        raise ValueError(f"Unknown evaluation route profile: {name}")
    subagents = list(profiles[name])
    tools: list[str] = []
    for subagent in subagents:
        tools.extend(pair.get(subagent, [subagent]))
    if name == "normal":
        tools = list(_NORMAL_TOOLS)
    return subagents, tools


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


def _check(
    scenario: dict[str, Any],
    response: Any,
    metrics: dict[str, Any],
    duration_ms: float,
) -> tuple[list[str], dict[str, bool], int]:
    dimension_failures: dict[str, list[str]] = {name: [] for name in _DIMENSIONS}

    def fail(dimension: str, message: str) -> None:
        dimension_failures[dimension].append(message)

    data = response.data
    recommended = data.get("recommended_plan") or {}
    result_checks = {
        "expected_status": (response.status, "final_plan"),
        "expected_execution_status": (data.get("execution_status"), "final_plan"),
        "expected_approval_status": (data.get("approval_status"), "final_plan"),
        "expected_purchase_quantity": (recommended.get("quantity"), "final_plan"),
        "expected_plan_id": (recommended.get("plan_id"), "final_plan"),
    }
    for expectation, (actual, dimension) in result_checks.items():
        if expectation in scenario and scenario[expectation] != actual:
            fail(
                dimension,
                f"{expectation}: expected {scenario[expectation]!r}, got {actual!r}",
            )

    request = data.get("request") or {}
    expected_request = dict(scenario.get("expected_request") or {})
    if "expected_request_quantity" in scenario:
        expected_request["quantity"] = scenario["expected_request_quantity"]
    for field, expected in expected_request.items():
        if field.startswith("simulate_"):
            continue
        actual = request.get(field)
        if actual != expected:
            fail(
                "parameter_extraction",
                f"request.{field}: expected {expected!r}, got {actual!r}",
            )

    if len(recommended.get("allocations", [])) < scenario.get("expected_min_allocations", 0):
        fail("final_plan", "recommended plan has fewer allocations than expected")
    expected_suppliers = set(scenario.get("expected_plan_suppliers") or [])
    actual_suppliers = {
        str(item.get("supplier_code")) for item in recommended.get("allocations", [])
    }
    if expected_suppliers and not expected_suppliers.issubset(actual_suppliers):
        fail(
            "final_plan",
            f"plan suppliers must include {sorted(expected_suppliers)!r}; got {sorted(actual_suppliers)!r}",
        )
    if "expected_max_total_cost" in scenario and float(recommended.get("total_cost") or 0) > float(
        scenario["expected_max_total_cost"]
    ):
        fail("final_plan", "recommended plan exceeds expected maximum total cost")

    if int(data.get("replan_count") or 0) < scenario.get("expected_min_replans", 0):
        fail("exception_recovery", "replan count is below expectation")
    if int(data.get("replan_count") or 0) > scenario.get("expected_max_replans", float("inf")):
        fail("exception_recovery", "replan count is above expectation")

    actual_route = list(metrics.get("subagent_route") or [])
    expected_route = scenario.get("expected_subagent_sequence")
    expected_tool_route = scenario.get("expected_tool_sequence")
    if profile_name := scenario.get("expected_route_profile"):
        profile_route, profile_tools = _route_profile(str(profile_name))
        expected_route = expected_route or profile_route
        expected_tool_route = expected_tool_route or profile_tools
    if scenario.get("expect_execution_tool"):
        expected_tool_route = [*(expected_tool_route or []), "execute_procurement_plan"]
    if expected_route is not None and actual_route != expected_route:
        fail("routing", f"expected route {expected_route!r}; got {actual_route!r}")
    for agent, expected_count in (scenario.get("expected_subagent_counts") or {}).items():
        actual_count = actual_route.count(agent)
        if actual_count != expected_count:
            fail(
                "routing",
                f"{agent} calls: expected {expected_count}, got {actual_count}",
            )

    actual_tools = list(metrics.get("tool_route") or [])
    # Skill loading is a required Harness control step but may occur on Main or a
    # SubAgent depending on the scenario. Compare business Tool routing separately.
    routed_tools = [name for name in actual_tools if name != "load_skill"]
    if expected_tool_route is not None and routed_tools != expected_tool_route:
        fail("tool_use", f"expected tool route {expected_tool_route!r}; got {routed_tools!r}")
    for tool_name in scenario.get("expected_tools", []):
        if tool_name not in actual_tools:
            fail("tool_use", f"required tool was not used: {tool_name}")
    for tool_name in scenario.get("forbidden_tools", []):
        if tool_name in actual_tools:
            fail("tool_use", f"forbidden tool was used: {tool_name}")
    if "expected_database_calls" in scenario and int(metrics.get("database_calls") or 0) != int(
        scenario["expected_database_calls"]
    ):
        fail(
            "tool_use",
            "database operation count: expected "
            f"{scenario['expected_database_calls']}, got {metrics.get('database_calls')}",
        )
    if int(metrics.get("mcp_calls") or 0) < int(scenario.get("expected_min_mcp_calls", 0)):
        fail("tool_use", "MCP call count is below expectation")

    strategies = {str(item.get("strategy")) for item in metrics.get("replan_strategies", [])}
    for strategy in scenario.get("expected_recovery_strategies", []):
        if strategy not in strategies:
            fail("exception_recovery", f"recovery strategy was not observed: {strategy}")
    minimum_database_errors = int(scenario.get("expected_min_database_errors", 0))
    if int(metrics.get("database_errors") or 0) < minimum_database_errors:
        fail("exception_recovery", "database error was not observed")
    maximum_error_events = int(scenario.get("expected_max_error_events", 0))
    if int(metrics.get("errors") or 0) > maximum_error_events:
        fail(
            "exception_recovery",
            f"unexpected unresolved error events: {metrics.get('errors')}",
        )

    allowed_subagents = set(scenario.get("allowed_subagents") or expected_route or _ALL_SUBAGENTS)
    allowed_tools = set(scenario.get("allowed_tools") or expected_tool_route or _ALL_TOOLS)
    allowed_tools.add("load_skill")
    invalid_subagent_calls = max(
        sum(name not in allowed_subagents for name in actual_route),
        0,
        len(actual_route)
        - int(
            scenario.get(
                "max_subagent_calls",
                len(expected_route) if expected_route is not None else len(actual_route),
            )
        ),
    )
    invalid_tool_calls = max(
        sum(name not in allowed_tools for name in actual_tools),
        0,
        len(routed_tools)
        - int(
            scenario.get(
                "max_tool_calls",
                len(expected_tool_route) if expected_tool_route is not None else len(routed_tools),
            )
        ),
    )
    invalid_calls = invalid_subagent_calls + invalid_tool_calls
    if invalid_calls > int(scenario.get("max_invalid_calls", 0)):
        fail("invalid_calls", f"invalid/redundant call count is {invalid_calls}")

    maximum_duration = float(scenario.get("max_duration_ms", 30_000))
    if duration_ms <= 0 or duration_ms > maximum_duration:
        fail("duration", f"duration {duration_ms:.2f}ms exceeds 0..{maximum_duration:.2f}ms")
    token_usage = int(metrics.get("total_tokens") or 0)
    minimum_tokens = int(scenario.get("min_token_usage", 1))
    maximum_tokens = int(scenario.get("max_token_usage", 500_000))
    if token_usage < minimum_tokens or token_usage > maximum_tokens:
        fail(
            "token_usage",
            f"token usage {token_usage} outside {minimum_tokens}..{maximum_tokens}",
        )

    dimensions = {name: not messages for name, messages in dimension_failures.items()}
    failures = [
        f"{dimension}: {message}"
        for dimension, messages in dimension_failures.items()
        for message in messages
    ]
    return failures, dimensions, invalid_calls


def score_scenario(
    scenario: dict[str, Any],
    response: Any,
    metrics: dict[str, Any],
    duration_ms: float,
) -> tuple[list[str], dict[str, bool], int]:
    """Score one already-completed deterministic-evaluation scenario."""

    return _check(scenario, response, metrics, duration_ms)


def run_evaluation(
    *, scenarios_path: str | Path | None = None, limit: int | None = None
) -> EvaluationReport:
    scenarios = load_scenarios(scenarios_path)
    if limit is not None:
        scenarios = scenarios[:limit]
    case_results: list[dict[str, Any]] = []
    total_start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="procurement-eval-") as temporary:
        for scenario in scenarios:
            case_start = time.perf_counter()
            case_dir = Path(temporary) / scenario["id"]
            app = create_procurement_app(
                data_dir=case_dir,
                reset_database=True,
                deterministic=True,
                enable_test_config=True,
            )
            try:
                session_id = f"eval-{scenario['id']}"
                response = app.submit(scenario["input"], session_id=session_id)
                for action in scenario.get("actions", []):
                    if action["type"] == "approve":
                        response = app.approve(session_id)
                    elif action["type"] == "reject":
                        response = app.reject(session_id)
                    elif action["type"] == "modify":
                        response = app.modify(session_id, action["value"])
                duration_ms = (time.perf_counter() - case_start) * 1000
                metrics = app.metrics()
                failures, dimensions, invalid_calls = _check(
                    scenario, response, metrics, duration_ms
                )
                case_results.append(
                    {
                        "id": scenario["id"],
                        "description": scenario["description"],
                        "passed": not failures,
                        "failures": failures,
                        "dimensions": dimensions,
                        "invalid_calls": invalid_calls,
                        "status": response.status,
                        "duration_ms": duration_ms,
                        "routing": list(metrics.get("subagent_route", [])),
                        "metrics": metrics,
                    }
                )
            finally:
                app.close()
    passed = sum(item["passed"] for item in case_results)

    def dimension_rate(name: str) -> float:
        if not case_results:
            return 0.0
        return sum(item["dimensions"][name] for item in case_results) / len(case_results)

    aggregate = {
        "case_count": len(case_results),
        "passed": passed,
        "failed": len(case_results) - passed,
        "task_completion_rate": passed / len(case_results) if case_results else 0.0,
        "average_duration_ms": (
            sum(item["duration_ms"] for item in case_results) / len(case_results)
            if case_results
            else 0.0
        ),
        "average_token_usage": (
            sum(item["metrics"]["total_tokens"] for item in case_results) / len(case_results)
            if case_results
            else 0.0
        ),
        "routing_accuracy": dimension_rate("routing"),
        "tool_use_accuracy": dimension_rate("tool_use"),
        "parameter_extraction_accuracy": dimension_rate("parameter_extraction"),
        "final_plan_accuracy": dimension_rate("final_plan"),
        "exception_recovery_rate": dimension_rate("exception_recovery"),
        "zero_invalid_call_rate": dimension_rate("invalid_calls"),
        "duration_budget_rate": dimension_rate("duration"),
        "token_budget_rate": dimension_rate("token_usage"),
        "invalid_calls": sum(item["invalid_calls"] for item in case_results),
        "total_duration_ms": (time.perf_counter() - total_start) * 1000,
        "agent_calls": sum(item["metrics"]["agent_calls"] for item in case_results),
        "subagent_calls": sum(item["metrics"]["subagent_calls"] for item in case_results),
        "tool_calls": sum(item["metrics"]["tool_calls"] for item in case_results),
        "database_calls": sum(item["metrics"]["database_calls"] for item in case_results),
        "mcp_calls": sum(item["metrics"]["mcp_calls"] for item in case_results),
        "retry_count": sum(item["metrics"]["retries"] for item in case_results),
        "replan_count": sum(item["metrics"]["replan_count"] for item in case_results),
        "hitl_count": sum(item["metrics"]["hitl_pauses"] for item in case_results),
        "token_usage": sum(item["metrics"]["total_tokens"] for item in case_results),
        "error_count": sum(item["metrics"]["errors"] for item in case_results),
    }
    return EvaluationReport(case_results, aggregate)
