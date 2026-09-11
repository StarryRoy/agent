from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app

REQUEST = "下个月需要采购500台设备，预算80万，月底前必须到货。"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, deterministic=True, enable_mcp=False)) as http:
        yield http


def wait(client, session_id):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 200, response.text
        state = response.json()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    pytest.fail("operation did not finish")


def submit(client, text=REQUEST):
    response = client.post("/api/v1/sessions", json={"text": text})
    assert response.status_code == 202, response.text
    return response.json()["session_id"]


def test_native_stream_proposal_approve_result_and_metrics(client):
    sid = submit(client)
    # Observe the actual native stream, including final authoritative snapshot.
    response = client.get(f"/api/v1/sessions/{sid}/events")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "event: progress" in response.text
    assert '"event_type": "subagent_start"' in response.text
    assert "event: done" in response.text
    assert "thread_id" not in response.text
    proposal = wait(client, sid)
    assert proposal["status"] == "approval_required"
    assert proposal["data"]["inventory_analysis"]["recommended_purchase_quantity"] == 445
    assert client.get(f"/api/v1/sessions/{sid}/result").status_code == 409
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    executed = wait(client, sid)
    assert executed["data"]["execution_status"] == "success"
    assert executed["data"]["approval_status"] == "approved"
    assert len(executed["data"]["executed_actions"]) > 0
    assert client.get(f"/api/v1/sessions/{sid}/result").json() == executed
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 409
    trace = client.get(f"/api/v1/sessions/{sid}/trace").json()
    assert trace["events"]
    assert all(event["session_id"] == sid for event in trace["events"])
    assert "thread_id" not in json.dumps(trace)
    metrics = client.get("/api/v1/metrics").json()["metrics"]
    assert metrics["tasks"] == 1
    assert metrics["successful_tasks"] == 1
    assert metrics["subagent_calls"] >= 7


def test_modify_same_session_then_reject_never_writes(client):
    sid = submit(client)
    assert wait(client, sid)["status"] == "approval_required"
    response = client.post(
        f"/api/v1/sessions/{sid}/modify", json={"text": "把数量改成400台，不要供应商A。"}
    )
    assert response.status_code == 202
    revised = wait(client, sid)
    assert revised["session_id"] == sid
    assert revised["status"] == "approval_required"
    assert revised["data"]["request"]["quantity"] == 400
    assert all(
        row["supplier_code"] != "SUP-A"
        for row in revised["data"]["recommended_plan"]["allocations"]
    )
    assert client.post(f"/api/v1/sessions/{sid}/reject").status_code == 202
    rejected = wait(client, sid)
    assert rejected["data"]["approval_status"] == "rejected"
    application = client.app.state.adapter.application
    assert application.database.execute_query("SELECT id FROM purchase_requests")["row_count"] == 0
    assert application.database.execute_query("SELECT id FROM purchase_orders")["row_count"] == 0


def test_checkpoint_query_and_approval_survive_backend_restart(tmp_path):
    with TestClient(create_app(data_dir=tmp_path, deterministic=True, enable_mcp=False)) as first:
        sid = submit(first)
        proposal = wait(first, sid)
    with TestClient(create_app(data_dir=tmp_path, deterministic=True, enable_mcp=False)) as second:
        recovered = second.get(f"/api/v1/sessions/{sid}").json()
        assert recovered["status"] == "approval_required"
        assert recovered["data"]["recommended_plan"] == proposal["data"]["recommended_plan"]
        assert second.get(f"/api/v1/sessions/{sid}/trace").json()["events"]
        assert second.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
        assert wait(second, sid)["data"]["execution_status"] == "success"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("下个月需要50台设备，预算20万。", "not_required"),
        ("下个月需要500台设备，预算10万。", "blocked"),
        ("预算80万，月底前必须到货。", "not_started"),
    ],
)
def test_non_approval_outcomes_and_stream_metrics(client, text, expected):
    sid = submit(client, text)
    result = wait(client, sid)
    assert result["status"] == "completed"
    assert result["data"]["execution_status"] == expected
    assert client.get("/api/v1/metrics").json()["metrics"]["tasks"] == 1
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 409


def test_validation_not_found_cors_and_duplicate_action(client):
    assert client.post("/api/v1/sessions", json={"text": "  "}).status_code == 422
    assert (
        client.post("/api/v1/sessions", json={"text": REQUEST, "thread_id": "x"}).status_code == 422
    )
    assert client.get("/api/v1/sessions/unknown").status_code == 404
    assert client.post("/api/v1/sessions/unknown/approve").status_code == 404
    assert client.get("/api/v1/sessions/unknown/events").status_code == 404
    response = client.options(
        "/api/v1/sessions",
        headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    sid = submit(client)
    wait(client, sid)
    assert (
        client.get(
            f"/api/v1/sessions/{sid}/events", headers={"Last-Event-ID": "invalid"}
        ).status_code
        == 400
    )
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 409
    wait(client, sid)


def test_operation_failure_reports_error_without_exception_leak(client, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("secret checkpoint_id and private details")
        yield  # async generator shape

    monkeypatch.setattr(client.app.state.adapter.application, "astream", broken)
    sid = submit(client)
    state = wait(client, sid)
    assert state["status"] == "error"
    assert "secret" not in json.dumps(state)
    response = client.get(f"/api/v1/sessions/{sid}/events")
    assert "operation_error" in response.text
    assert "event: done" in response.text


def test_trace_isolation_replay_cursor_and_read_queries_do_not_change_metrics(client):
    first = submit(client)
    first_result = wait(client, first)
    second = submit(client, "下个月需要50台设备，预算20万。")
    second_result = wait(client, second)
    first_trace = client.get(f"/api/v1/sessions/{first}/trace?limit=2000").json()
    assert first_result["trace_id"] != second_result["trace_id"]
    assert all(row["trace_id"] != second_result["trace_id"] for row in first_trace["events"])
    short_trace = client.get(f"/api/v1/sessions/{first}/trace?limit=1").json()
    assert len(short_trace["events"]) == 1
    assert short_trace["truncated"]
    before = client.get("/api/v1/metrics").json()
    stream = client.get(f"/api/v1/sessions/{first}/events").text
    last_id = max(int(line[4:]) for line in stream.splitlines() if line.startswith("id: "))
    replay = client.get(
        f"/api/v1/sessions/{first}/events", headers={"Last-Event-ID": str(last_id)}
    ).text
    assert "event: progress" not in replay
    assert "event: snapshot" in replay
    assert "event: done" in replay
    assert client.get("/api/v1/metrics").json() == before


def test_clarification_can_continue_in_same_session(client):
    sid = submit(client, "预算80万，月底前必须到货。")
    initial = wait(client, sid)
    assert initial["data"]["missing_fields"]
    assert (
        client.post(f"/api/v1/sessions/{sid}/modify", json={"text": "需要500台设备。"}).status_code
        == 202
    )
    continued = wait(client, sid)
    assert continued["session_id"] == sid
    assert continued["status"] == "approval_required"


def test_execution_failure_is_a_business_result_not_false_success(client):
    sid = submit(client, json.dumps({"text": REQUEST, "simulate_execution_failure": True}))
    assert wait(client, sid)["status"] == "approval_required"
    assert client.post(f"/api/v1/sessions/{sid}/approve").status_code == 202
    assert wait(client, sid)["data"]["execution_status"] == "failed"
    result = client.get(f"/api/v1/sessions/{sid}/result").json()
    assert result["data"]["executed_actions"] == []
