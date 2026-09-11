"""Transport adapter, not a second procurement workflow or session store.

The installed Harness exposes no public snapshot reader. The only compatibility
seam is _checkpoint_result: it resolves the existing runtime configuration and
asks the Agent to convert its persisted state. No graph IDs leave this module.
All business conversion and every mutation remain in ProcurementApplication.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from procurement_agent.application import ProcurementApplication

from .schemas import SessionView

LOG = logging.getLogger(__name__)
INTERNAL_KEYS = {"thread_id", "checkpoint_id", "checkpoint_ns", "configurable", "interrupts"}


def public(value: Any) -> Any:
    """Remove runtime configuration recursively, including nested trace metadata."""
    if isinstance(value, dict):
        return {key: public(item) for key, item in value.items() if key not in INTERNAL_KEYS}
    if isinstance(value, (tuple, list)):
        return [public(item) for item in value]
    return value


class ProcurementAdapter:
    def __init__(self, application: ProcurementApplication, data_dir: Path):
        self.application = application
        self.trace_path = data_dir / "traces.jsonl"
        # Only active HTTP operations and bounded replay buffers live in memory.
        # Procurement state is always read from Harness checkpoints.
        self.tasks: dict[str, asyncio.Task] = {}
        self.errors: dict[str, str] = {}
        self.events: dict[str, deque] = {}
        self.sequence: dict[str, int] = {}
        # Shared SQLite connection/model objects are used by this single process.
        self.execution_lock = asyncio.Lock()

    async def _checkpoint_result(self, session_id: str):
        runtime = self.application.agent.runtime
        config, _, _ = runtime._config(None, session_id)
        snapshot = await runtime.graph.aget_state(config)
        if not snapshot.values:
            raise HTTPException(404, "Session 不存在")
        result = await self.application.agent._aresult(dict(snapshot.values))
        return result, bool(snapshot.next)

    async def state(self, session_id: str) -> SessionView:
        running = session_id in self.tasks
        try:
            result, unfinished = await self._checkpoint_result(session_id)
            response = self.application._convert(result, count_task=False)
            view = SessionView.model_validate(public(response.as_dict()))
            if unfinished and result.status != "paused":
                view.status = "interrupted"
                view.message = "上次执行未完成。请检查服务日志；不要将此状态视为采购成功。"
        except HTTPException:
            if not running and session_id not in self.errors:
                raise
            view = SessionView(session_id=session_id, status="running")
        if running:
            view.status = "running"
            view.message = "Agent 正在处理，请等待分析或执行完成。"
        elif session_id in self.errors:
            view.status = "error"
            view.message = self.errors[session_id]
        return view

    def emit(self, session_id: str, event_type: str, data: dict) -> None:
        seq = self.sequence.get(session_id, 0) + 1
        self.sequence[session_id] = seq
        self.events.setdefault(session_id, deque(maxlen=1000)).append(
            {
                "id": seq,
                "event_type": event_type,
                "session_id": session_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "data": public(data),
            }
        )

    def trace(self, session_id: str, limit: int = 500) -> dict:
        # Read the existing redacted audit log. Include child spans by trace_id,
        # never by exposing the Harness child-session or graph configuration.
        roots: set[str] = set()
        rows = []
        if self.trace_path.exists():
            with self.trace_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:  # an in-progress trailing append
                        continue
                    rows.append(row)
                    if row.get("session_id") == session_id and row.get("trace_id"):
                        roots.add(row["trace_id"])
        selected = [
            public({**row, "session_id": session_id})
            for row in rows
            if row.get("session_id") == session_id or row.get("trace_id") in roots
        ]
        return {
            "session_id": session_id,
            "events": selected[-limit:],
            "truncated": len(selected) > limit,
        }

    async def start(self, operation: str, session_id: str | None = None, text: str = "") -> str:
        session_id = session_id or f"purchase-{uuid4().hex}"
        if session_id in self.tasks:
            raise HTTPException(409, "该 Session 正在执行，请勿重复提交")
        if operation != "submit":
            view = await self.state(session_id)
            if operation in {"approve", "reject"} and view.status != "approval_required":
                raise HTTPException(409, "当前 Session 没有等待审批的方案")
            if operation == "modify" and view.status not in {"approval_required", "completed"}:
                raise HTTPException(409, "当前状态无法修改条件")
        # No await between reservation and task publication: duplicate requests
        # cannot both cross this boundary in the single-worker event loop.
        if session_id in self.tasks:
            raise HTTPException(409, "该 Session 正在执行，请勿重复提交")
        self.errors.pop(session_id, None)
        self.emit(session_id, "operation", {"operation": operation, "text": text})
        self.tasks[session_id] = asyncio.create_task(self._run(operation, session_id, text))
        return session_id

    async def _run(self, operation: str, session_id: str, text: str) -> None:
        try:
            async with self.execution_lock:
                if operation == "submit":
                    async for event in self.application.astream(text, session_id=session_id):
                        # Native final/approval events are progress; the authoritative
                        # business response comes from the same application converter.
                        self.emit(
                            session_id,
                            event.event_type,
                            {**event.as_dict(), "session_id": session_id},
                        )
                    result, _ = await self._checkpoint_result(session_id)
                    self.application._convert(result, count_task=True)
                else:
                    await self._resume_with_events(operation, session_id, text)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("Procurement operation failed: %s", session_id)
            self.errors[session_id] = "执行发生错误，请查看后端日志及 Trace，再核实当前采购状态。"
            self.emit(session_id, "operation_error", {"message": self.errors[session_id]})
        finally:
            self.tasks.pop(session_id, None)
            # Buffers for recent sessions only; checkpoints/traces remain durable.
            if len(self.events) > 100:
                for old in list(self.events):
                    if old not in self.tasks and old != session_id:
                        self.events.pop(old, None)
                        self.sequence.pop(old, None)
                        if len(self.events) <= 100:
                            break

    async def _resume_with_events(self, operation: str, session_id: str, text: str) -> None:
        # Harness has no resume-stream facade. Reuse existing async operations
        # and forward their real observer events while they run.
        method = getattr(self.application, f"a{operation}")
        previous = self.trace(session_id, limit=100000)["events"]
        offset = len(previous)
        job = asyncio.create_task(
            method(session_id, text) if operation == "modify" else method(session_id)
        )
        try:
            while True:
                await asyncio.wait({job}, timeout=0.15)
                rows = self.trace(session_id, limit=100000)["events"]
                for row in rows[offset:]:
                    self.emit(session_id, "trace", row)
                offset = len(rows)
                if job.done():
                    await job
                    return
        finally:
            if not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)

    async def close(self) -> None:
        # Browser disconnects never cancel operations; graceful shutdown waits.
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
        self.application.close()
