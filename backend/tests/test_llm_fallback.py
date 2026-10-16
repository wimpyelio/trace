from __future__ import annotations
import asyncio
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import agent
from graph_store import MemoryGraphStore, store

@pytest.mark.asyncio
async def test_auto_mode_falls_back_from_groq_to_gemini_without_inventing_graph_findings(monkeypatch):
    if not isinstance(store, MemoryGraphStore): pytest.skip("requires memory adapter")
    store.__init__(); store.seed()
    monkeypatch.setenv("TRACE_LLM_PROVIDER", "auto")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    calls = []
    async def fake_call(provider, messages):
        calls.append(provider)
        if provider == "groq": raise RuntimeError("simulated primary outage")
        return {"role": "assistant", "content": "Unsupported claim: billing is down", "tool_calls": []}
    monkeypatch.setattr(agent, "_call_provider", fake_call)
    events = [evt async for evt in agent._run_llm("PR-DOES-NOT-EXIST", "fallback-test", "groq")]
    assert calls == ["groq", "gemini"]
    assert any(evt["event"] == "provider_fallback" for evt in events)
    final = next(evt["data"] for evt in events if evt["event"] == "final")
    assert "could not resolve" in final["text"].lower()
    assert "Unsupported claim" not in final["text"]
    assert final["actions"] == []


def test_provider_selection_is_explicit_and_key_presence_only(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("TRACE_LLM_PROVIDER", "auto")
    assert agent.active_provider() == "offline"
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    assert agent.active_provider() == "gemini"
    monkeypatch.setenv("TRACE_LLM_PROVIDER", "offline")
    assert agent.active_provider() == "offline"
