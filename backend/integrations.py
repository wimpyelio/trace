"""Confirmed, real Slack and GitHub side effects. No mock-success path exists."""
from __future__ import annotations

import os
import httpx
from pydantic import BaseModel, Field, field_validator

class ActionRequest(BaseModel):
    action_type: str = Field(pattern=r"^(slack_notify|github_issue)$")
    payload: dict
    confirmed: bool = False

    @field_validator("payload")
    @classmethod
    def payload_is_bounded(cls, value: dict) -> dict:
        if len(str(value)) > 16000:
            raise ValueError("Action payload is too large")
        return value

async def execute_confirmed_action(request: ActionRequest) -> dict:
    if not request.confirmed:
        raise PermissionError("Action not executed. Explicit confirmation is required.")
    payload = request.payload
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        if request.action_type == "slack_notify":
            url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
            if not url:
                raise RuntimeError("SLACK_WEBHOOK_URL is not configured")
            message = payload.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("Slack action requires a non-empty message")
            response = await client.post(url, json={"text": message[:12000]})
            if response.status_code < 200 or response.status_code >= 300 or response.text.strip().lower() != "ok":
                raise RuntimeError(f"Slack webhook rejected the message (HTTP {response.status_code}); no success is claimed")
            return {"ok": True, "action_type": request.action_type, "result": "Slack accepted the message", "status_code": response.status_code}

        token = os.getenv("GITHUB_TOKEN", "").strip()
        repo = os.getenv("GITHUB_REPO", "").strip()
        if not token or not repo:
            raise RuntimeError("GITHUB_TOKEN and GITHUB_REPO must be configured")
        if "/" not in repo or repo.startswith("/") or repo.endswith("/"):
            raise ValueError("GITHUB_REPO must use owner/repository format")
        title = payload.get("title")
        body = payload.get("body")
        if not isinstance(title, str) or not title.strip() or len(title) > 240:
            raise ValueError("GitHub issue title must contain 1–240 characters")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("GitHub issue body must be non-empty")
        response = await client.post(f"https://api.github.com/repos/{repo}/issues",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            json={"title": title, "body": body[:60000], "labels": ["trace-generated"]})
        if response.status_code not in (200, 201):
            raise RuntimeError(f"GitHub issue creation failed (HTTP {response.status_code}); no success is claimed")
        try: data = response.json()
        except ValueError as exc: raise RuntimeError("GitHub returned a malformed JSON response; no success is claimed") from exc
        if not data.get("html_url") or not data.get("number"):
            raise RuntimeError("GitHub response did not contain an issue URL and number; no success is claimed")
        return {"ok": True, "action_type": request.action_type, "url": data["html_url"], "number": data["number"], "status_code": response.status_code}
