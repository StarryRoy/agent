"""Command-line and environment configuration for benchmark runs."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from procurement_agent.factory import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_MODEL_NAME,
    GEMINI_API_KEY_ENV,
    GEMINI_MODEL_NAME,
    GLM_API_KEY_ENV,
    GLM_MODEL_NAME,
    MODEL_PROVIDER_ENV,
)

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
REPORTS_DIR = ROOT / "reports"
MODEL_ROLES = {
    "main",
    "requirement",
    "inventory",
    "supplier",
    "pricing",
    "budget",
    "risk",
    "execution",
}
DEFAULT_BENCHMARK_DATE = date(2026, 9, 11)


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    model: str | None
    role_models: dict[str, str]
    benchmark_date: date
    deterministic: bool
    enable_mcp: bool
    scenario_ids: tuple[str, ...]
    label: str | None
    compare_before: Path | None
    compare_after: Path | None

    @property
    def model_label(self) -> str:
        if self.deterministic:
            return "deterministic test double"
        provider = os.getenv(MODEL_PROVIDER_ENV, "glm").strip().lower()
        if provider == "deepseek":
            default_name = DEEPSEEK_MODEL_NAME
        elif provider == "glm":
            default_name = GLM_MODEL_NAME
        elif provider == "gemini":
            default_name = GEMINI_MODEL_NAME
        else:
            default_name = "N/A"
        primary = self.model or default_name
        if not self.role_models:
            return primary
        roles = ", ".join(f"{key}={value}" for key, value in sorted(self.role_models.items()))
        return f"primary={primary}; {roles}"


def _role_models(values: list[str]) -> dict[str, str]:
    configured: dict[str, str] = {}
    raw = os.getenv("PROCUREMENT_BENCHMARK_ROLE_MODELS", "").strip()
    if raw:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("PROCUREMENT_BENCHMARK_ROLE_MODELS must be a JSON object")
        configured.update({str(key): str(value) for key, value in parsed.items()})
    for value in values:
        if "=" not in value:
            raise ValueError(f"Invalid --role-model {value!r}; expected role=provider:model")
        role, model = value.split("=", 1)
        configured[role.strip()] = model.strip()
    return configured


def load_config(argv: list[str] | None = None) -> BenchmarkConfig:
    parser = argparse.ArgumentParser(description="Run the Procurement Agent real-LLM benchmark")
    parser.add_argument("--model", help="Existing Harness model identifier, e.g. provider:model")
    parser.add_argument(
        "--role-model",
        action="append",
        default=[],
        metavar="ROLE=MODEL",
        help="Override a role using the factory's existing role_models argument",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Debug the benchmark only; output is marked ineligible as a real-LLM report",
    )
    parser.add_argument("--without-mcp", action="store_true", help="Disable MCP for diagnosis")
    parser.add_argument(
        "--benchmark-date",
        type=date.fromisoformat,
        default=date.fromisoformat(
            os.getenv("PROCUREMENT_BENCHMARK_DATE", DEFAULT_BENCHMARK_DATE.isoformat())
        ),
        metavar="YYYY-MM-DD",
        help=f"Fixed business date for every scenario (default: {DEFAULT_BENCHMARK_DATE})",
    )
    parser.add_argument("--scenario", action="append", default=[], help="Run only this scenario")
    parser.add_argument(
        "--label", choices=("before", "after"), help="Also save this run as before.json/after.json"
    )
    parser.add_argument("--compare-before", type=Path)
    parser.add_argument("--compare-after", type=Path)
    args = parser.parse_args(argv)
    config = BenchmarkConfig(
        model=args.model,
        role_models=_role_models(args.role_model),
        benchmark_date=args.benchmark_date,
        deterministic=args.deterministic,
        enable_mcp=not args.without_mcp,
        scenario_ids=tuple(args.scenario),
        label=args.label,
        compare_before=args.compare_before,
        compare_after=args.compare_after,
    )
    provider = os.getenv(MODEL_PROVIDER_ENV, "glm").strip().lower()
    provider_keys = {
        "deepseek": DEEPSEEK_API_KEY_ENV,
        "gemini": GEMINI_API_KEY_ENV,
        "glm": GLM_API_KEY_ENV,
    }
    if provider not in provider_keys:
        parser.error(f"{MODEL_PROVIDER_ENV} must be 'deepseek', 'gemini', or 'glm'")
    key_name = provider_keys[provider]
    provider_has_key = os.getenv(key_name)
    has_primary = bool(config.model or provider_has_key)
    has_all_roles = MODEL_ROLES.issubset(config.role_models)
    if not config.deterministic and not (has_primary or has_all_roles):
        parser.error(f"set {key_name}, pass --model, or configure every role model")
    return config
