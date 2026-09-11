"""Versioned, public HTTP contracts. Graph configuration never crosses this boundary."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=10000)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入采购需求或修改条件")
        return value.strip()


class SessionView(BaseModel):
    session_id: str
    status: Literal["running", "approval_required", "completed", "error", "interrupted"]
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    trace_id: str | None = None


class Accepted(BaseModel):
    session_id: str
    status: Literal["running"] = "running"
    events_url: str


class TraceView(BaseModel):
    session_id: str
    events: list[dict[str, Any]]
    truncated: bool = False
