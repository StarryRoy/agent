"""Fixed business regression suite and lightweight scoring."""

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
    source = Path(path) if path else Path(__file__).resolve().parents[1] / "evals" / "scenarios.json"
    return json.loads(source.read_text(encoding="utf-8"))


def _check(scenario: dict[str, Any], response: Any) -> list[str]:
    failures: list[str] = []
    data = response.data
    recommended = data.get("recommended_plan") or {}
    checks = {
        "expected_status": response.status,
        "expected_execution_status": data.get("execution_status"),
        "expected_approval_status": data.get("approval_status"),
        "expected_request_quantity": data.get("request", {}).get("quantity"),
        "expected_purchase_quantity": recommended.get("quantity"),
    }
    for expectation, actual in checks.items():
        if expectation in scenario and scenario[expectation] != actual:
            failures.append(f"{expectation}: expected {scenario[expectation]!r}, got {actual!r}")
    if len(recommended.get("allocations", [])) < scenario.get(
        "expected_min_allocations", 0
    ):
        failures.append("recommended plan has fewer allocations than expected")
    if int(data.get("replan_count") or 0) < scenario.get("expected_min_replans", 0):
        failures.append("replan count is below expectation")
    return failures


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
            app = create_procurement_app(data_dir=case_dir, reset_database=True)
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
                failures = _check(scenario, response)
                case_results.append(
                    {
                        "id": scenario["id"],
                        "description": scenario["description"],
                        "passed": not failures,
                        "failures": failures,
                        "status": response.status,
                        "duration_ms": (time.perf_counter() - case_start) * 1000,
                        "routing": list(app.metrics().get("stage_duration_ms", {})),
                        "metrics": app.metrics(),
                    }
                )
            finally:
                app.close()
    passed = sum(item["passed"] for item in case_results)
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
