from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import httpx
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import integrations
from integrations import ActionRequest

class FakeClient:
    response = None
    error = None
    def __init__(self, *args, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def post(self, *args, **kwargs):
        if FakeClient.error: raise FakeClient.error
        return FakeClient.response

def test_slack_success_only_when_webhook_returns_ok(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr(integrations.httpx, "AsyncClient", FakeClient)
    FakeClient.error = None
    FakeClient.response = SimpleNamespace(status_code=200, text="ok")
    result = __import__('asyncio').run(integrations.execute_confirmed_action(ActionRequest(action_type="slack_notify", payload={"message":"trace"}, confirmed=True)))
    assert result["ok"] is True
    FakeClient.response = SimpleNamespace(status_code=500, text="failure")
    with pytest.raises(RuntimeError, match="no success is claimed"):
        __import__('asyncio').run(integrations.execute_confirmed_action(ActionRequest(action_type="slack_notify", payload={"message":"trace"}, confirmed=True)))

def test_github_issue_requires_valid_real_response(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")
    monkeypatch.setattr(integrations.httpx, "AsyncClient", FakeClient)
    FakeClient.error = None
    FakeClient.response = SimpleNamespace(status_code=201, json=lambda: {"html_url":"https://github.com/owner/repo/issues/17", "number":17})
    result = __import__('asyncio').run(integrations.execute_confirmed_action(ActionRequest(action_type="github_issue", payload={"title":"Trace follow-up", "body":"actual trace"}, confirmed=True)))
    assert result["number"] == 17
    assert result["url"].endswith("/17")
    FakeClient.response = SimpleNamespace(status_code=201, json=lambda: {"number":18})
    with pytest.raises(RuntimeError, match="did not contain"):
        __import__('asyncio').run(integrations.execute_confirmed_action(ActionRequest(action_type="github_issue", payload={"title":"Trace follow-up", "body":"actual trace"}, confirmed=True)))

def test_external_timeout_is_not_false_success(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr(integrations.httpx, "AsyncClient", FakeClient)
    FakeClient.error = httpx.TimeoutException("timeout")
    with pytest.raises(httpx.TimeoutException):
        __import__('asyncio').run(integrations.execute_confirmed_action(ActionRequest(action_type="slack_notify", payload={"message":"trace"}, confirmed=True)))
    FakeClient.error = None
