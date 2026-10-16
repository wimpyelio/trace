"""Agent loop: Groq/Gemini tool-use (LLM mode) and offline deterministic graph-tool path."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from typing import Any, AsyncIterator

import httpx

if __package__:
    from .graph_store import stable_id, store
else:
    from graph_store import stable_id, store

MAX_AGENT_ROUNDS = 10

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "resolve_entity", "description": "Resolve a PR, service name, incident, person, or change ID to graph entities. For a Change, return its modified service names.", "parameters": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 160}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_impact_radius", "description": "Find the bounded transitive dependents of a provider service. DEPENDS_ON is dependent -> provider, so this query traces dependents of the named service.", "parameters": {"type": "object", "properties": {"service_name": {"type": "string"}, "max_hops": {"type": "integer", "minimum": 1, "maximum": 4}}, "required": ["service_name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "find_open_incidents", "description": "Check open incidents affecting a list of impacted services and return current assignees.", "parameters": {"type": "object", "properties": {"service_names": {"type": "array", "items": {"type": "string"}, "maxItems": 60}}, "required": ["service_names"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "find_owners", "description": "Find impacted service owners and their active-incident assignments to surface ownership conflicts.", "parameters": {"type": "object", "properties": {"service_names": {"type": "array", "items": {"type": "string"}, "maxItems": 60}}, "required": ["service_names"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "find_experts", "description": "Recommend graph-linked experts covering impacted services who are not assigned to open incidents.", "parameters": {"type": "object", "properties": {"service_names": {"type": "array", "items": {"type": "string"}, "maxItems": 60}}, "required": ["service_names"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "execute_action", "description": "Record a requested Slack or GitHub action proposal. This tool NEVER executes a side effect; the user must confirm through the API action endpoint afterward.", "parameters": {"type": "object", "properties": {"action_type": {"type": "string", "enum": ["slack_notify", "github_issue"]}, "payload": {"type": "object"}}, "required": ["action_type", "payload"], "additionalProperties": False}}},
]

SYSTEM_PROMPT = """You are Trace, an engineering decision-memory agent for a seeded software system graph.
Use graph tool results as the only source of facts. Never invent nodes, incidents, owners, expertise, or paths. Follow these steps when possible: resolve the query; identify the modified service; calculate reverse dependency impact up to four hops; inspect open incidents; evaluate ownership conflicts; find available experts; then synthesize. Dependency relationships point from dependent to provider. Cite concrete service paths, hop counts, and product-of-edge-strength scores, which are graph ranking scores, not calibrated incident probabilities. State counts exactly as the tools returned them. Highlight the highest-risk conflict(s). Propose exactly two actionable recommendations. Any execute_action tool call is only a proposal; never claim a Slack message or issue was sent/created because no external action can be executed in this trace loop. Keep conclusions concise and traceable."""


def active_provider() -> str:
    choice = os.getenv("TRACE_LLM_PROVIDER", "auto").lower().strip()
    if choice == "offline": return "offline"
    if choice == "groq": return "groq" if os.getenv("GROQ_API_KEY") else "groq-missing-key"
    if choice == "gemini": return "gemini" if (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")) else "gemini-missing-key"
    if os.getenv("GROQ_API_KEY"): return "groq"
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"): return "gemini"
    return "offline"


def _provider_config(provider: str) -> tuple[str, str, str]:
    if provider == "groq":
        # Default model: llama-3.3-70b-versatile (confirmed Groq tool-use model as of Aug 2025).
        # If your account has access to a newer tier (e.g. openai/gpt-oss-20b), set GROQ_MODEL in .env.
        return ("https://api.groq.com/openai/v1", os.environ["GROQ_API_KEY"], os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"]
        # Trailing slash required: OpenAI SDK appends /chat/completions to the base URL
        return ("https://generativelanguage.googleapis.com/v1beta/openai/", key, os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
    raise ValueError(f"Unknown provider: {provider}")


def _normalise_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be a JSON object")
    if name == "resolve_entity":
        q = args.get("query")
        if not isinstance(q, str) or not q.strip() or len(q) > 160: raise ValueError("query must be a non-empty string of at most 160 characters")
        return {"query": q.strip()}
    if name == "get_impact_radius":
        service = args.get("service_name")
        hops = args.get("max_hops", 4)
        if not isinstance(service, str) or not service.strip(): raise ValueError("service_name is required")
        if not isinstance(hops, int) or isinstance(hops, bool) or not 1 <= hops <= 4: raise ValueError("max_hops must be an integer between 1 and 4")
        return {"service_name": service.strip(), "max_hops": hops}
    if name in ("find_open_incidents", "find_owners", "find_experts"):
        names = args.get("service_names")
        if not isinstance(names, list) or len(names) > 60 or any(not isinstance(x, str) or len(x) > 120 for x in names):
            raise ValueError("service_names must be a list of at most 60 short strings")
        return {"service_names": list(dict.fromkeys(x.strip() for x in names if x.strip()))}
    if name == "execute_action":
        typ, payload = args.get("action_type"), args.get("payload")
        if typ not in ("slack_notify", "github_issue") or not isinstance(payload, dict): raise ValueError("invalid action proposal")
        return {"action_type": typ, "payload": payload}
    raise ValueError(f"Unknown tool: {name}")


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    cleaned = _normalise_tool_args(name, args)
    if name == "resolve_entity": return store.resolve_entity(**cleaned)
    if name == "get_impact_radius": return store.impact_radius(**cleaned)
    if name == "find_open_incidents": return store.open_incidents(**cleaned)
    if name == "find_owners": return store.owners(**cleaned)
    if name == "find_experts": return store.experts(**cleaned)
    if name == "execute_action":
        # Intentionally side-effect-free. The confirmed /action endpoint performs real actions.
        return {"ok": False, "status": "deferred", "deferred": True, "action_type": cleaned["action_type"], "node_ids": [],
                "narrative": "Action proposal recorded only. No message or issue was sent; explicit user confirmation via POST /action is required."}
    raise ValueError(f"Unknown tool: {name}")


def _event(session_id: str, hop: int, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Persist a tool result as a ReasoningStep and package a frontend-ready SSE step event."""
    step = store.write_step(
        session_id,
        hop,
        tool_name,
        result.get("narrative", ""),
        result.get("node_ids", []),
    )
    reasoning_edges = [
        {"from": step["node_id"], "to": nid, "rel": "EXAMINED"}
        for nid in result.get("node_ids", [])
        if nid != step["node_id"]
    ]
    return {
        "event": "step",
        "data": {
            "hop": hop,
            "tool": tool_name,
            "narrative": result.get("narrative", ""),
            "activate_nodes": result.get("node_ids", []),
            "conflicts": result.get("conflicts", []),
            "result": {k: v for k, v in result.items() if k not in ("node_ids", "narrative")},
            "reasoning_node": step,
            "reasoning_edges": reasoning_edges,
        },
    }


def _build_actions(
    service: str | None,
    impact: dict[str, Any],
    conflicts: list[dict[str, Any]],
    open_incidents: list[dict[str, Any]],
    available_experts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build two concrete action proposals from graph-derived findings."""
    if not service or (not conflicts and not open_incidents):
        return []
    total = impact.get("total", 0)
    p0 = impact.get("p0_count", 0)
    lead = available_experts[0]["name"] if available_experts else "the on-call lead"

    notify_people: list[str] = list({c["person"] for c in conflicts})
    for inc in open_incidents:
        notify_people.extend(a for a in inc.get("assignees", []) if a not in notify_people)
    notify_label = ", ".join(notify_people[:2]) if notify_people else lead

    msg_parts = [f"⚠️ Trace alert for {service}:"]
    if total:
        msg_parts.append(f"{total} dependent service(s) ({p0} P0) affected.")
    for c in conflicts:
        msg_parts.append(f"CONFLICT: {c['person']} owns {', '.join(c['services'])} and is on {', '.join(c['active_incidents'])}.")
    if available_experts:
        msg_parts.append(f"Available expert: {lead}.")
    slack_msg = " ".join(msg_parts)

    body_parts = [f"## Trace impact: `{service}`\n\n"]
    body_parts.append(f"**Blast radius:** {total} service(s), {p0} P0\n\n")
    if conflicts:
        body_parts.append("### Ownership conflicts\n")
        for c in conflicts:
            body_parts.append(f"- **{c['person']}** owns `{', '.join(c['services'])}` — on active incident(s): `{', '.join(c['active_incidents'])}`\n")
        body_parts.append("\n")
    if open_incidents:
        body_parts.append("### Open incidents on affected services\n")
        for inc in open_incidents:
            body_parts.append(f"- `{inc['id']}` ({inc['severity']}) on `{inc['service']}` — assignees: {', '.join(inc['assignees']) or 'nobody'}\n")
        body_parts.append("\n")
    if available_experts:
        body_parts.append(f"### Available expert\n- **{lead}** covers `{', '.join(available_experts[0].get('covers', [])[:3])}`\n\n")
    body_parts.append("*Generated by Trace. Confirm before taking action.*")

    return [
        {
            "id": "action-slack",
            "type": "slack_notify",
            "label": f"Notify {notify_label} on Slack",
            "title": f"Trace alert: {service} — {total} dependents",
            "message": slack_msg,
        },
        {
            "id": "action-github",
            "type": "github_issue",
            "label": "Open GitHub checklist ticket",
            "title": f"Trace: {service} change impact — {len(conflicts)} conflict(s)",
            "body": "".join(body_parts),
        },
    ]


def _offline_synthesis(
    resolved: dict[str, Any],
    impact: dict[str, Any],
    incidents: dict[str, Any],
    ownership: dict[str, Any],
    experts_result: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Deterministic offline synthesis from tool results. Never invents facts."""
    found = resolved.get("found", [])
    if not found:
        return "could not resolve the query to any entity in the graph.", []

    service = (resolved.get("related_service_names") or [None])[0]
    if not service:
        for f in found:
            if f["type"] == "Service":
                service = f["props"]["name"]
                break

    total = impact.get("total", 0)
    p0 = impact.get("p0_count", 0)
    conflicts = ownership.get("conflicts", [])
    open_incs = incidents.get("open_incidents", [])
    avail = experts_result.get("available_experts", [])

    lines: list[str] = []
    entity_label = str(found[0]["props"].get("id") or found[0]["props"].get("name"))
    lines.append(f"Resolved to {entity_label}.")
    if service and total:
        lines.append(f"Impact radius: {total} service(s) depend on {service} within 4 hops ({p0} P0).")
    if conflicts:
        for c in conflicts:
            lines.append(
                f"CONFLICT: {c['person']} owns {', '.join(c['services'])} and is assigned to active incident(s) {', '.join(c['active_incidents'])}."
            )
    if open_incs:
        lines.append(
            f"Open incident(s) on affected services: {', '.join(i['id'] for i in open_incs)}."
        )
    if avail:
        lines.append(f"Available expert(s) with no active incidents: {', '.join(e['name'] for e in avail[:2])}.")
    if not conflicts and not open_incs and service:
        lines.append("No ownership conflicts or open incidents detected on affected services.")

    text = " ".join(lines)
    actions = _build_actions(service, impact, conflicts, open_incs, avail)
    return text, actions


async def _run_offline(query: str, session_id: str) -> AsyncIterator[dict[str, Any]]:
    """Deterministic graph-tool path: runs the five tools in sequence without an LLM."""
    hop = 0

    # 1 — resolve entity
    hop += 1
    resolved = dispatch("resolve_entity", {"query": query})
    yield _event(session_id, hop, "resolve_entity", resolved)
    await asyncio.sleep(0)

    # Determine the target service (from Change.MODIFIED or direct Service match)
    service: str | None = (resolved.get("related_service_names") or [None])[0]
    if not service:
        for f in resolved.get("found", []):
            if f["type"] == "Service":
                service = f["props"]["name"]
                break

    if not service:
        yield {
            "event": "final",
            "data": {
                "text": "could not resolve the query to a service in the graph.",
                "session_id": session_id,
                "actions": [],
                "conflicts": [],
                "available_experts": [],
                "impact": {"total": 0, "p0_count": 0, "dependents": []},
                "open_incidents": [],
                "agent_mode": "offline",
            },
        }
        return

    # 2 — impact radius
    hop += 1
    impact = dispatch("get_impact_radius", {"service_name": service, "max_hops": 4})
    yield _event(session_id, hop, "get_impact_radius", impact)
    await asyncio.sleep(0)

    dep_names = [d["service"] for d in impact.get("dependents", [])]
    names_for_queries = dep_names if dep_names else [service]

    # 3 — open incidents
    hop += 1
    incidents = dispatch("find_open_incidents", {"service_names": names_for_queries})
    yield _event(session_id, hop, "find_open_incidents", incidents)
    await asyncio.sleep(0)

    # 4 — owners and conflicts
    hop += 1
    ownership = dispatch("find_owners", {"service_names": names_for_queries})
    yield _event(session_id, hop, "find_owners", ownership)
    await asyncio.sleep(0)

    # 5 — available experts
    hop += 1
    experts = dispatch("find_experts", {"service_names": names_for_queries})
    yield _event(session_id, hop, "find_experts", experts)
    await asyncio.sleep(0)

    text, actions = _offline_synthesis(resolved, impact, incidents, ownership, experts)
    yield {
        "event": "final",
        "data": {
            "text": text,
            "session_id": session_id,
            "actions": actions,
            "conflicts": ownership.get("conflicts", []),
            "available_experts": experts.get("available_experts", []),
            "impact": impact,
            "open_incidents": incidents.get("open_incidents", []),
            "agent_mode": "offline",
        },
    }


async def _call_provider(provider: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Single LLM API round-trip. Returns the assistant message as a plain dict."""
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for LLM mode; pip install openai") from exc

    base_url, api_key, model = _provider_config(provider)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30.0)
    response = await client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        tools=TOOL_SCHEMAS,  # type: ignore[arg-type]
        tool_choice="auto",
        max_tokens=2048,
    )
    msg = response.choices[0].message
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in (msg.tool_calls or [])
        ],
    }


async def _run_llm(query: str, session_id: str, provider: str) -> AsyncIterator[dict[str, Any]]:
    """LLM tool-use loop with Groq→Gemini fallback. Only reports facts the graph returned."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    hop = 0
    cumulative: dict[str, Any] = {
        "resolved": {}, "impact": {"total": 0, "p0_count": 0, "dependents": []},
        "conflicts": [], "available_experts": [], "open_incidents": [],
    }
    current_provider = provider

    for _ in range(MAX_AGENT_ROUNDS):
        try:
            response = await _call_provider(current_provider, messages)
        except Exception as exc:
            # Single fallback: Groq → Gemini if a Gemini key is present
            fallback = "gemini" if current_provider == "groq" else None
            fallback_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")) if fallback else None
            if fallback and fallback_key:
                yield {"event": "provider_fallback", "data": {"from": current_provider, "to": fallback}}
                current_provider = fallback
                try:
                    response = await _call_provider(current_provider, messages)
                except Exception as exc2:
                    raise RuntimeError(f"Both providers failed: {exc2}") from exc2
            else:
                raise

        tool_calls = response.get("tool_calls") or []
        content = response.get("content") or ""

        if not tool_calls:
            # LLM final turn: synthesize from accumulated graph data, not from LLM free text,
            # to prevent hallucinated service names or incident IDs.
            if hop == 0:
                text = "could not resolve the query to an entity in the graph."
                actions: list[dict[str, Any]] = []
            else:
                service = (cumulative["resolved"].get("related_service_names") or [None])[0]
                _, actions = _offline_synthesis(
                    cumulative["resolved"], cumulative["impact"],
                    {"open_incidents": cumulative["open_incidents"]},
                    {"conflicts": cumulative["conflicts"]},
                    {"available_experts": cumulative["available_experts"]},
                )
                text = content if content and "unsupported" not in content.lower() else (
                    _offline_synthesis(
                        cumulative["resolved"], cumulative["impact"],
                        {"open_incidents": cumulative["open_incidents"]},
                        {"conflicts": cumulative["conflicts"]},
                        {"available_experts": cumulative["available_experts"]},
                    )[0]
                )
            yield {
                "event": "final",
                "data": {
                    "text": text,
                    "session_id": session_id,
                    "actions": actions,
                    "conflicts": cumulative["conflicts"],
                    "available_experts": cumulative["available_experts"],
                    "impact": cumulative["impact"],
                    "open_incidents": cumulative["open_incidents"],
                    "agent_mode": f"llm:{current_provider}",
                },
            }
            return

        # Process tool calls
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        tool_results: list[dict[str, Any]] = []

        for tc in tool_calls:
            hop += 1
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
                result = dispatch(name, args)
            except Exception as exc:
                result = {"error": str(exc)[:200], "node_ids": [], "narrative": f"Tool {name} failed: {exc}"}

            # Accumulate for synthesis
            if name == "resolve_entity":
                cumulative["resolved"] = result
            elif name == "get_impact_radius":
                cumulative["impact"] = result
            elif name == "find_owners":
                cumulative["conflicts"] = result.get("conflicts", [])
            elif name == "find_experts":
                cumulative["available_experts"] = result.get("available_experts", [])
            elif name == "find_open_incidents":
                cumulative["open_incidents"] = result.get("open_incidents", [])

            yield _event(session_id, hop, name, result)
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, default=str),
            })

        messages.extend(tool_results)

    # Rounds exhausted — synthesise from what the tools returned
    text, actions = _offline_synthesis(
        cumulative["resolved"], cumulative["impact"],
        {"open_incidents": cumulative["open_incidents"]},
        {"conflicts": cumulative["conflicts"]},
        {"available_experts": cumulative["available_experts"]},
    )
    yield {
        "event": "final",
        "data": {
            "text": text,
            "session_id": session_id,
            "actions": actions,
            "conflicts": cumulative["conflicts"],
            "available_experts": cumulative["available_experts"],
            "impact": cumulative["impact"],
            "open_incidents": cumulative["open_incidents"],
            "agent_mode": f"llm:{current_provider}:rounds_exhausted",
        },
    }


async def run_trace(query: str, session_id: str) -> AsyncIterator[dict[str, Any]]:
    """Top-level SSE generator. Routes to offline deterministic path or LLM tool-use loop."""
    store.create_session(session_id, query)
    provider = active_provider()
    session_node = {
        "id": f"session:{session_id}", "type": "Session", "group": "Session",
        "props": {"id": session_id, "query": query, "created_at": int(time.time())},
    }
    yield {"event": "start", "data": {"session_id": session_id, "provider": provider, "session_node": session_node}}

    if provider == "offline":
        async for event in _run_offline(query, session_id):
            yield event
    else:
        async for event in _run_llm(query, session_id, provider):
            yield event