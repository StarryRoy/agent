"""Run with: python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from procurement_agent.factory import create_procurement_app_async

from .adapter import ProcurementAdapter, public
from .schemas import Accepted, SessionView, TextInput, TraceView


def create_app(
    *,
    data_dir: str | Path | None = None,
    deterministic: bool | None = None,
    enable_mcp: bool | None = None,
) -> FastAPI:
    root = Path(data_dir or os.getenv("PROCUREMENT_DATA_DIR", "data")).resolve()
    demo = deterministic if deterministic is not None else os.getenv("PROCUREMENT_DEMO") == "1"
    mcp = enable_mcp if enable_mcp is not None else os.getenv("PROCUREMENT_MCP", "1") != "0"

    @asynccontextmanager
    async def lifespan(api: FastAPI):
        application = await create_procurement_app_async(
            data_dir=root,
            deterministic=demo,
            enable_mcp=mcp,
        )
        api.state.adapter = ProcurementAdapter(application, root)
        try:
            yield
        finally:
            await api.state.adapter.close()

    api = FastAPI(title="企业采购 Agent API", version="1.0.0", lifespan=lifespan)
    origins = os.getenv(
        "PROCUREMENT_CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173"
    ).split(",")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    def adapter() -> ProcurementAdapter:
        return api.state.adapter

    @api.get("/api/v1/health")
    async def health():
        return {"status": "ok", "mode": "deterministic_demo" if demo else "configured_model"}

    @api.post("/api/v1/sessions", response_model=Accepted, status_code=202)
    async def submit(body: TextInput):
        sid = await adapter().start("submit", text=body.text)
        return Accepted(session_id=sid, events_url=f"/api/v1/sessions/{sid}/events")

    @api.get("/api/v1/sessions/{session_id}", response_model=SessionView)
    async def state(session_id: str):
        return await adapter().state(session_id)

    @api.get("/api/v1/sessions/{session_id}/result", response_model=SessionView)
    async def result(session_id: str):
        view = await adapter().state(session_id)
        if view.status in {"running", "approval_required", "interrupted"}:
            raise HTTPException(409, "Session 尚未产生最终结果")
        return view

    async def action(session_id: str, operation: str, text: str = ""):
        sid = await adapter().start(operation, session_id, text)
        return Accepted(session_id=sid, events_url=f"/api/v1/sessions/{sid}/events")

    @api.post("/api/v1/sessions/{session_id}/approve", response_model=Accepted, status_code=202)
    async def approve(session_id: str):
        return await action(session_id, "approve")

    @api.post("/api/v1/sessions/{session_id}/reject", response_model=Accepted, status_code=202)
    async def reject(session_id: str):
        return await action(session_id, "reject")

    @api.post("/api/v1/sessions/{session_id}/modify", response_model=Accepted, status_code=202)
    async def modify(session_id: str, body: TextInput):
        return await action(session_id, "modify", body.text)

    @api.get("/api/v1/metrics")
    async def metrics():
        return {"scope": "server_process", "metrics": public(adapter().application.metrics())}

    @api.get("/api/v1/sessions/{session_id}/trace", response_model=TraceView)
    async def trace(session_id: str, limit: int = Query(500, ge=1, le=2000)):
        await adapter().state(session_id)
        return adapter().trace(session_id, limit)

    @api.get("/api/v1/sessions/{session_id}/events", response_class=StreamingResponse)
    async def events(
        session_id: str,
        request: Request,
        last_event_id: str | None = Header(None),
        after: int = Query(0, ge=0),
    ):
        await adapter().state(session_id)
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError:
            raise HTTPException(400, "Last-Event-ID 必须为整数") from None

        def encode(kind, payload, seq=None):
            prefix = f"id: {seq}\n" if seq is not None else ""
            return (
                prefix
                + f"event: {kind}\ndata: "
                + json.dumps(payload, ensure_ascii=False, default=str)
                + "\n\n"
            )

        async def generate():
            nonlocal cursor
            yield "retry: 1500\n\n"
            heartbeat = 0
            while not await request.is_disconnected():
                buffer = list(adapter().events.get(session_id, []))
                if buffer and cursor > buffer[-1]["id"]:
                    cursor = 0  # process restarted; replay current buffer
                if buffer and cursor and cursor < buffer[0]["id"] - 1:
                    yield encode("reset", {"message": "部分事件已过期，请读取 Trace 历史"})
                for event in buffer:
                    if event["id"] > cursor:
                        cursor = event["id"]
                        yield encode("progress", event, cursor)
                view = await adapter().state(session_id)
                yield encode("snapshot", view.model_dump())
                if view.status != "running":
                    yield encode("done", {"session_id": session_id})
                    break
                heartbeat += 1
                if heartbeat % 20 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return api


app = create_app()
