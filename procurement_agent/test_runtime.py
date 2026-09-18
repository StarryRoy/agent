"""Internal fault-injection context, never exposed through business Tool schemas."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProcurementTestConfig(BaseModel):
    """Strict fault-injection controls available only in deterministic runtime."""

    model_config = ConfigDict(extra="forbid")

    simulate_sql_failure: bool = Field(..., description="测试用数据库查询失败开关。")
    simulate_subagent_failure: bool | Literal["supplier"] = Field(
        ..., description="测试用 SubAgent 失败开关。"
    )
    simulate_mcp_failure: bool = Field(..., description="测试用 MCP 失败开关。")
    simulate_execution_failure: bool = Field(..., description="测试用执行前失败开关。")
    simulate_atomic_failure: bool = Field(..., description="测试用事务回滚开关。")


_TEST_CONFIG: ContextVar[ProcurementTestConfig | None] = ContextVar(
    "procurement_test_config", default=None
)


def current_test_config() -> ProcurementTestConfig | None:
    """Return the fault-injection config active for the current internal call tree."""

    return _TEST_CONFIG.get()


@contextmanager
def use_test_config(config: ProcurementTestConfig | None) -> Iterator[None]:
    """Temporarily bind test configuration and always restore the previous value."""

    token = _TEST_CONFIG.set(config)
    try:
        yield
    finally:
        _TEST_CONFIG.reset(token)
