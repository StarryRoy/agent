"""Real-LLM benchmark runner for the fixed evaluation scenarios.

This module deliberately keeps execution and scoring separate.  A scenario is
used only to provide its user input and HITL actions to the application; its
``expected_*`` fields are handed to :func:`score_scenario` only after the
session has finished.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import traceback
import uuid
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

    duration_ms = (time.perf_counter() - started) * 1000
    if response is None:
        score = _empty_score()
    else:
        try:
            failures, dimensions, invalid_calls = score_scenario(
                scenario, response, runtime_metrics, duration_ms
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
