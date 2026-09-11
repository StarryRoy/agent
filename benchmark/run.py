"""One-command real-LLM benchmark runner and report generator."""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmark.comparator import discover_comparison
from benchmark.config import REPORTS_DIR, RESULTS_DIR, BenchmarkConfig, load_config
from benchmark.metrics import collect_metrics, load_events, percentile
from benchmark.scenarios import Scenario, load_scenarios
from procurement_agent import create_procurement_app


def _mean(values: list[int | float | None]) -> float | None:
    available = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(available) / len(available) if available else None


def _sum_available(values: list[int | float | None]) -> float | None:
    available = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(available) if available else None


def _execute_actions(app: Any, scenario: Scenario, session_id: str, response: Any) -> Any:
    for kind, value in scenario.actions:
        if kind == "approve":
            response = app.approve(session_id)
        elif kind == "reject":
            response = app.reject(session_id)
        elif kind == "modify" and value is not None:
            response = app.modify(session_id, value)
        else:
            raise ValueError(f"Unsupported scenario action: {kind}")
    return response


def _assess(scenario: Scenario, response: Any, metrics: dict[str, Any]) -> dict[str, Any]:
    data = response.data
    execution_status = data.get("execution_status")
    plan = data.get("recommended_plan")
    executable = bool(plan) and execution_status not in {"blocked", "failed", "not_started"}
    blocked = execution_status == "blocked"
    hitl = response.status == "approval_required" or metrics["hitl"]["count"] > 0
    replan_count = metrics["replan"]["count"]
    replan_success = executable if replan_count else None
    loaded_skills = set(metrics["skills"]["unique"])
    strategies = set(metrics["replan"]["strategies"])
    checks: dict[str, bool] = {
        "status": response.status in scenario.expected_statuses,
        "skills": set(scenario.expected_skills).issubset(loaded_skills),
        "strategies": set(scenario.expected_strategies).issubset(strategies),
    }
    if scenario.expect_executable_plan is not None:
        checks["executable_plan"] = executable == scenario.expect_executable_plan
    if scenario.expect_blocked:
        checks["correct_block"] = blocked
    if scenario.expect_hitl is not None:
        checks["hitl"] = hitl == scenario.expect_hitl
    if scenario.expect_replan:
        checks["replan"] = replan_count > 0
    if scenario.expect_replan_success is not None:
        checks["replan_success"] = replan_success == scenario.expect_replan_success
    if scenario.expect_mcp:
        checks["mcp_called"] = metrics["mcp"]["calls"] > 0
    if scenario.expect_error_recovery:
        checks["recovery_completed"] = response.status != "error"
    failures = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not failures,
        "checks": checks,
        "failed_checks": failures,
        "task_completed": response.status in {"completed", "approval_required"},
        "executable_plan": executable,
        "correctly_blocked": blocked if scenario.expect_blocked else None,
        "hitl_entered": hitl,
        "replan_success": replan_success,
        "skill_match": checks["skills"],
        "exception_occurred": bool(metrics["errors"]),
    }


def _run_scenario(config: BenchmarkConfig, scenario: Scenario, run_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    session_id = f"benchmark-{run_id}-{scenario.id}"
    trace_target = RESULTS_DIR / "traces" / run_id / f"{scenario.id}.jsonl"
    response = None
    app = None
    error = None
    events: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"procurement-benchmark-{scenario.id}-") as temp:
        data_dir = Path(temp)
        try:
            app = create_procurement_app(
                data_dir=data_dir,
                reset_database=True,
                enable_mcp=config.enable_mcp,
                model=config.model,
                role_models=config.role_models or None,
                deterministic=config.deterministic,
            )
            for statement in scenario.setup_sql:
                result = app.database.execute_write(statement)
                if not result.get("ok"):
                    raise RuntimeError(f"Scenario setup failed: {result.get('error')}")
            response = app.submit(scenario.input, session_id=session_id)
            response = _execute_actions(app, scenario, session_id, response)
        # A provider/runtime failure belongs to this scenario's raw result; the
        # remaining fixed scenarios must still run.
        except Exception as exc:  # noqa: BLE001
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        finally:
            if app is not None:
                app.close()
            trace_source = data_dir / "traces.jsonl"
            if trace_source.exists():
                trace_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(trace_source, trace_target)
                events = load_events(trace_target)
    duration_ms = (time.perf_counter() - started) * 1000
    measured = collect_metrics(events)
    measured["performance"]["total_duration_ms"] = duration_ms
    if response is None:
        assessment = {
            "passed": False,
            "checks": {},
            "failed_checks": ["execution_error"],
            "task_completed": False,
            "executable_plan": False,
            "correctly_blocked": None,
            "hitl_entered": measured["hitl"]["count"] > 0,
            "replan_success": None,
            "skill_match": False,
            "exception_occurred": True,
        }
        final = None
    else:
        assessment = _assess(scenario, response, measured)
        final = response.as_dict()
    return {
        "id": scenario.id,
        "description": scenario.description,
        "input": scenario.input,
        "expectations": scenario.as_dict(),
        "final": final,
        "metrics": measured,
        "assessment": assessment,
        "error": error,
        "trace_file": str(trace_target.relative_to(PROJECT_ROOT))
        if trace_target.exists()
        else None,
    }


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(cases)
    passed = sum(case["assessment"]["passed"] for case in cases)
    durations = [float(case["metrics"]["performance"]["total_duration_ms"]) for case in cases]
    replan_cases = [case for case in cases if case["metrics"]["replan"]["count"] > 0]
    replan_successes = sum(case["assessment"]["replan_success"] is True for case in replan_cases)
    input_values = [case["metrics"]["llm"]["input_tokens"] for case in cases]
    output_values = [case["metrics"]["llm"]["output_tokens"] for case in cases]
    total_values = [case["metrics"]["llm"]["total_tokens"] for case in cases]
    return {
        "scenario_count": count,
        "passed": passed,
        "failed": count - passed,
        "task_success_rate": passed / count if count else None,
        "total_input_tokens": sum(value for value in input_values if value is not None)
        if any(value is not None for value in input_values)
        else None,
        "total_output_tokens": sum(value for value in output_values if value is not None)
        if any(value is not None for value in output_values)
        else None,
        "total_tokens": sum(value for value in total_values if value is not None)
        if any(value is not None for value in total_values)
        else None,
        "average_input_tokens": _mean(input_values),
        "average_total_tokens": _mean(total_values),
        "average_context_tokens": _mean(
            [case["metrics"]["context"]["average_tokens"] for case in cases]
        ),
        "max_context_tokens": max(
            (
                case["metrics"]["context"]["max_tokens"]
                for case in cases
                if case["metrics"]["context"]["max_tokens"] is not None
            ),
            default=None,
        ),
        "average_duration_ms": _mean(durations),
        "p50_duration_ms": percentile(durations, 0.50),
        "p95_duration_ms": percentile(durations, 0.95),
        "total_duration_ms": sum(durations),
        "llm_duration_ms": _sum_available(
            [case["metrics"]["performance"]["llm_duration_ms"] for case in cases]
        ),
        "tool_duration_ms": _sum_available(
            [case["metrics"]["performance"]["tool_duration_ms"] for case in cases]
        ),
        "database_duration_ms": _sum_available(
            [case["metrics"]["performance"]["database_duration_ms"] for case in cases]
        ),
        "mcp_duration_ms": _sum_available(
            [case["metrics"]["performance"]["mcp_duration_ms"] for case in cases]
        ),
        "llm_calls": sum(case["metrics"]["llm"]["calls"] for case in cases),
        "agent_rounds": sum(case["metrics"]["agent"]["rounds"] for case in cases),
        "subagent_calls": sum(case["metrics"]["agent"]["subagent_calls"] for case in cases),
        "tool_calls": sum(case["metrics"]["agent"]["tool_calls"] for case in cases),
        "average_tool_calls": _mean([case["metrics"]["agent"]["tool_calls"] for case in cases]),
        "replan_cases": len(replan_cases),
        "replan_successes": replan_successes,
        "replan_success_rate": replan_successes / len(replan_cases) if replan_cases else None,
        "hitl_cases": sum(case["assessment"]["hitl_entered"] for case in cases),
        "exception_cases": sum(bool(case["error"] or case["metrics"]["errors"]) for case in cases),
        "skill_usage": {
            skill: sum(skill in case["metrics"]["skills"]["unique"] for case in cases)
            for skill in sorted(
                {skill for case in cases for skill in case["metrics"]["skills"]["unique"]}
            )
        },
    }


def _display(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{decimals}f}"
    return str(value)


def _percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _report_markdown(report: dict[str, Any]) -> str:
    meta, summary = report["metadata"], report["summary"]
    lines = [
        f"# Procurement Agent Benchmark — {meta['run_id']}",
        "",
        f"> {'真实 LLM 正式评测' if meta['real_llm'] else 'Deterministic 调试运行（不得作为正式效果报告）'}",
        "",
        f"- 测试时间：{meta['started_at']}",
        f"- 使用模型：{meta['model']}",
        f"- 场景数量：{summary['scenario_count']}",
        f"- 成功 / 失败：{summary['passed']} / {summary['failed']}",
        f"- 总 Input / Output Token：{_display(summary['total_input_tokens'])} / {_display(summary['total_output_tokens'])}",
        f"- 总 Token：{_display(summary['total_tokens'])}",
        f"- 平均总 Token：{_display(summary['average_total_tokens'])}",
        f"- 平均 / 最大上下文 Token：{_display(summary['average_context_tokens'])} / {_display(summary['max_context_tokens'])}",
        f"- 平均耗时：{_display(summary['average_duration_ms'])} ms",
        f"- P50 / P95 耗时：{_display(summary['p50_duration_ms'])} / {_display(summary['p95_duration_ms'])} ms",
        f"- LLM / Tool / Database / MCP 耗时：{_display(summary['llm_duration_ms'])} / {_display(summary['tool_duration_ms'])} / {_display(summary['database_duration_ms'])} / {_display(summary['mcp_duration_ms'])} ms",
        f"- LLM 调用 / Agent 轮数 / SubAgent / Tool 调用：{summary['llm_calls']} / {summary['agent_rounds']} / {summary['subagent_calls']} / {summary['tool_calls']}",
        f"- Replan 成功率：{_percentage(summary['replan_success_rate'])}",
        "",
        "## Skill 使用情况",
        "",
    ]
    usage = summary["skill_usage"]
    lines.extend(
        [f"- `{skill}`：{count} 个场景" for skill, count in usage.items()]
        or ["- 无 Skill 加载事件"]
    )
    lines.extend(
        [
            "",
            "## 场景明细",
            "",
            "| 场景 | 结果 | 状态 | Input Token | 总 Token | 耗时(ms) | Replan | HITL | Skill |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for case in report["cases"]:
        final = case["final"] or {}
        metrics = case["metrics"]
        lines.append(
            "| {id} | {passed} | {status} | {input_tokens} | {total_tokens} | {duration} | {replan} | {hitl} | {skills} |".format(
                id=case["id"],
                passed="PASS" if case["assessment"]["passed"] else "FAIL",
                status=final.get("status", "error"),
                input_tokens=_display(metrics["llm"]["input_tokens"]),
                total_tokens=_display(metrics["llm"]["total_tokens"]),
                duration=_display(metrics["performance"]["total_duration_ms"]),
                replan=metrics["replan"]["count"],
                hitl="是" if case["assessment"]["hitl_entered"] else "否",
                skills=", ".join(metrics["skills"]["unique"]) or "—",
            )
        )
    exceptional = [case for case in report["cases"] if case["error"] or case["metrics"]["errors"]]
    lines.extend(["", "## 异常场景", ""])
    if exceptional:
        for case in exceptional:
            details = case["error"] or case["metrics"]["errors"]
            lines.append(
                f"- `{case['id']}`：`{json.dumps(details, ensure_ascii=False, default=str)}`"
            )
    else:
        lines.append("- 无运行异常。")
    if comparison := report.get("comparison"):
        lines.extend(
            [
                "",
                "## Before / After 对比",
                "",
                "| 指标 | Before | After | 变化 |",
                "|---|---:|---:|---:|",
            ]
        )
        for row in comparison["rows"]:
            lines.append(
                f"| {row['metric']} | {_display(row['before'])} | {_display(row['after'])} | {row['change']} |"
            )
    lines.extend(
        [
            "",
            "## 汇总结论",
            "",
            f"本次共执行 {summary['scenario_count']} 个固定场景，{summary['passed']} 个满足全部关键行为预期，{summary['failed']} 个未满足。所有数值均来自保存的执行 Trace；缺失指标显示为 N/A。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fields = [
        "id",
        "description",
        "passed",
        "status",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "duration_ms",
        "llm_calls",
        "subagent_calls",
        "tool_calls",
        "agent_rounds",
        "replan_count",
        "replan_success",
        "hitl",
        "skills",
        "error",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            metrics = case["metrics"]
            writer.writerow(
                {
                    "id": case["id"],
                    "description": case["description"],
                    "passed": case["assessment"]["passed"],
                    "status": (case["final"] or {}).get("status", "error"),
                    "input_tokens": metrics["llm"]["input_tokens"],
                    "output_tokens": metrics["llm"]["output_tokens"],
                    "total_tokens": metrics["llm"]["total_tokens"],
                    "duration_ms": metrics["performance"]["total_duration_ms"],
                    "llm_calls": metrics["llm"]["calls"],
                    "subagent_calls": metrics["agent"]["subagent_calls"],
                    "tool_calls": metrics["agent"]["tool_calls"],
                    "agent_rounds": metrics["agent"]["rounds"],
                    "replan_count": metrics["replan"]["count"],
                    "replan_success": case["assessment"]["replan_success"],
                    "hitl": case["assessment"]["hitl_entered"],
                    "skills": ";".join(metrics["skills"]["unique"]),
                    "error": (case["error"] or {}).get("message", ""),
                }
            )


def main(argv: list[str] | None = None) -> int:
    config = load_config(argv)
    scenarios = load_scenarios(config.scenario_ids)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    run_id = now.strftime("run_%Y%m%dT%H%M%SZ")
    print(f"Starting {run_id}: {len(scenarios)} scenarios, model={config.model_label}")
    cases = []
    for index, scenario in enumerate(scenarios, 1):
        print(f"[{index}/{len(scenarios)}] {scenario.id} ...", flush=True)
        case = _run_scenario(config, scenario, run_id)
        cases.append(case)
        print("  PASS" if case["assessment"]["passed"] else "  FAIL", flush=True)
    report = {
        "schema_version": 1,
        "metadata": {
            "run_id": run_id,
            "started_at": now.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "model": config.model_label,
            "role_models": config.role_models,
            "real_llm": not config.deterministic,
            "report_kind": "real_llm" if not config.deterministic else "deterministic_debug_only",
            "mcp_enabled": config.enable_mcp,
            "command": "python benchmark/run.py",
        },
        "summary": _summary(cases),
        "cases": cases,
    }
    json_path = RESULTS_DIR / f"{run_id}.json"
    csv_path = RESULTS_DIR / f"{run_id}.csv"
    report_path = REPORTS_DIR / f"{run_id}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    if config.label:
        shutil.copy2(json_path, RESULTS_DIR / f"{config.label}.json")
    try:
        report["comparison"] = discover_comparison(
            RESULTS_DIR, config.compare_before, config.compare_after
        )
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        report["comparison_error"] = str(exc)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    if config.label:
        shutil.copy2(json_path, RESULTS_DIR / f"{config.label}.json")
    _write_csv(csv_path, cases)
    report_path.write_text(_report_markdown(report), encoding="utf-8")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(f"Report: {report_path}")
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
