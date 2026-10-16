from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import dispatch, run_trace
from graph_store import MemoryGraphStore, stable_id, store
from main import app


def reset_store():
    # Tests run only in the memory-backed adapter. A real FalkorDB adapter is an opt-in integration check.
    if not isinstance(store, MemoryGraphStore):
        pytest.skip("Default unit suite uses the memory adapter; set up FalkorDB for separate integration testing")
    store.__init__()
    return store.seed()


def test_seed_counts_and_idempotence():
    counts = reset_store()
    assert {k: counts[k] for k in ("Service", "Person", "Incident", "Change", "Runbook")} == {
        "Service": 12, "Person": 8, "Incident": 5, "Change": 10, "Runbook": 3
    }
    node_count, edge_count = len(store.nodes_by_id), len(store.edges)
    assert store.seed() == counts
    assert len(store.nodes_by_id) == node_count
    assert len(store.edges) == edge_count
    assert counts["relationships"] == 75


def test_pr_resolution_and_reverse_dependency_impact():
    reset_store()
    resolved = store.resolve_entity("PR-445 just merged")
    assert resolved["found"][0]["props"]["id"] == "PR-445"
    assert resolved["related_service_names"] == ["auth-service"]
    impact = store.impact_radius("auth-service", 4)
    assert impact["total"] == 8
    assert impact["p0_count"] == 3  # Bible seed inconsistency: billing-core is P0 and directly depends on auth-service.
    names = {x["service"] for x in impact["dependents"]}
    assert names == {"payment-gateway", "fraud-detector", "billing-core", "user-service", "audit-log", "webhook-dispatcher", "notification-svc", "reporting-api"}
    assert next(x for x in impact["dependents"] if x["service"] == "billing-core")["path"] == ["billing-core", "auth-service"]
    assert store.impact_radius("auth-service", 1)["total"] == 5


def test_current_incidents_owner_conflicts_and_available_expert():
    reset_store()
    names = [x["service"] for x in store.impact_radius("auth-service")["dependents"]]
    incidents = store.open_incidents(names)["open_incidents"]
    assert {x["id"] for x in incidents} == {"INC-004", "INC-005"}
    assert {x["service"] for x in incidents} == {"notification-svc", "billing-core"}
    owners = store.owners(names)
    assert {c["person"] for c in owners["conflicts"]} == {"Diego Reyes", "Yuki Tanaka"}
    assert next(c for c in owners["conflicts"] if c["person"] == "Diego Reyes")["active_incidents"] == ["INC-005"]
    experts = store.experts(names)["available_experts"]
    assert experts[0]["name"] == "Ana Kovač"
    assert "billing-core" in experts[0]["covers"]


def test_reasoning_steps_persist_and_replay_in_order():
    reset_store()
    store.create_session("test-session", "PR-445 just merged")
    a = store.write_step("test-session", 1, "resolve_entity", "resolved PR-445", ["change:PR-445"])
    b = store.write_step("test-session", 2, "get_impact_radius", "8 dependents", ["service:auth-service"])
    replay = store.replay("test-session")
    assert [x["hop"] for x in replay["steps"]] == [1, 2]
    assert replay["steps"][0]["node_id"] == a["node_id"]
    graph = store.graph_data(include_working_memory=True)
    assert a["node_id"] in {n["id"] for n in graph["nodes"]}
    assert any(e["rel"] == "EXAMINED" and e["to"] == "change:PR-445" for e in graph["edges"])


def test_tool_arguments_are_validated_and_actions_are_deferred():
    reset_store()
    with pytest.raises(ValueError):
        dispatch("get_impact_radius", {"service_name": "auth-service", "max_hops": 100})
    result = dispatch("execute_action", {"action_type": "slack_notify", "payload": {"message": "hi"}})
    assert result["deferred"] is True
    assert result["ok"] is False
    assert "No message or issue was sent" in result["narrative"]


def test_api_health_graph_sse_and_replay():
    reset_store()
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["graph_connected"] is True
        graph = client.get("/graph/nodes").json()
        assert len(graph["nodes"]) == 38
        assert all(n.get("id") for n in graph["nodes"])
        assert all(e["from"] and e["to"] for e in graph["edges"])
        response = client.post("/trace", json={"query": "PR-445 just merged"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        blocks = [b for b in response.text.strip().split("\n\n") if b]
        events = []
        for block in blocks:
            event_name = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), "message")
            data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), "{}")
            events.append((event_name, json.loads(data)))
        assert events[0][0] == "start"
        assert sum(name == "step" for name, _ in events) == 5
        assert events[-1][0] == "done"
        final = next(d for name, d in events if name == "final")
        assert final["impact"]["total"] == 8
        assert len(final["actions"]) == 2
        sid = final["session_id"]
        replay = client.get(f"/replay/{sid}")
        assert replay.status_code == 200
        assert [x["hop"] for x in replay.json()["steps"]] == [1, 2, 3, 4, 5]


def test_actions_reject_unconfirmed_and_missing_credentials():
    reset_store()
    with TestClient(app) as client:
        denied = client.post("/action", json={"action_type": "slack_notify", "payload": {"message": "hello"}, "confirmed": False})
        assert denied.status_code == 409
        original = os.environ.pop("SLACK_WEBHOOK_URL", None)
        try:
            missing = client.post("/action", json={"action_type": "slack_notify", "payload": {"message": "hello"}, "confirmed": True})
            assert missing.status_code == 503
            assert "not configured" in missing.json()["detail"].lower()
        finally:
            if original is not None: os.environ["SLACK_WEBHOOK_URL"] = original


def test_sse_trace_unknown_query_does_not_invent_findings():
    reset_store()
    events = asyncio.run(_collect("PR-DOES-NOT-EXIST"))
    final = next(e["data"] for e in events if e["event"] == "final")
    assert "could not resolve" in final["text"].lower()
    assert final["impact"].get("total", 0) == 0
    assert final["actions"] == []


async def _collect(query):
    out = []
    async for event in run_trace(query, "test-unknown"):
        out.append(event)
    return out
