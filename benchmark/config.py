"""Command-line and environment configuration for benchmark runs."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    model: str | None
    role_models: dict[str, str]
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
        primary = self.model or os.getenv("AGENT_HARNESS_MODEL") or "N/A"
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
        deterministic=args.deterministic,
        enable_mcp=not args.without_mcp,
        scenario_ids=tuple(args.scenario),
        label=args.label,
        compare_before=args.compare_before,
        compare_after=args.compare_after,
    )
    has_primary = bool(config.model or os.getenv("AGENT_HARNESS_MODEL"))
    has_all_roles = MODEL_ROLES.issubset(config.role_models)
    if not config.deterministic and not (has_primary or has_all_roles):
        parser.error("configure AGENT_HARNESS_MODEL, pass --model, or configure every role model")
    return config
