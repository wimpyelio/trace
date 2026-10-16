"""Graph storage adapters. Memory mode is explicit and derived from the same seed data as FalkorDB."""
from __future__ import annotations

import os
import re
import time
import uuid
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from typing import Any
from urllib.parse import urlparse

from data import (
    ASSIGNMENTS, CAUSAL_LINKS, CHANGES, DEPENDENCIES, EXPECTED_COUNTS,
    EXPERTISE, INCIDENTS, OWNERSHIP, PEOPLE, RESOLUTIONS, RUNBOOKS, SERVICES,
)

LABEL_KEYS = {
    "Service": ("name",), "Person": ("name",), "Incident": ("id",),
    "Change": ("id",), "Runbook": ("id",), "Session": ("id",),
    "ReasoningStep": ("id",),
}

def stable_id(label: str, props: dict[str, Any]) -> str:
    key = props.get("name") or props.get("id")
    return f"{label.lower()}:{key}"

def _stamp(offset: int | None, now: int) -> int | None:
    return None if offset is None else now + offset

class MemoryGraphStore:
    """Small in-process graph engine used for offline development and deterministic tests."""
    backend_name = "memory-seeded"

    def __init__(self) -> None:
        self.nodes_by_id: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.sessions: dict[str, dict[str, Any]] = {}
        self.steps: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._seeded = False

    def _add_node(self, label: str, props: dict[str, Any]) -> str:
        node_id = stable_id(label, props)
        current = self.nodes_by_id.get(node_id)
        if current:
            current["props"].update(props)
        else:
            self.nodes_by_id[node_id] = {"id": node_id, "type": label, "props": dict(props)}
        return node_id

    def _add_edge(self, source: str, rel: str, target: str, props: dict[str, Any] | None = None) -> None:
        props = props or {}
        for edge in self.edges:
            if edge["from"] == source and edge["rel"] == rel and edge["to"] == target:
                edge["props"].update(props)
                return
        self.edges.append({"from": source, "rel": rel, "to": target, "props": dict(props)})

    def initialize_schema(self) -> None:
        return

    def seed(self) -> dict[str, int]:
        if self._seeded:
            return self.counts()
        now = int(time.time())
        for name, criticality, tier, language in SERVICES:
            self._add_node("Service", {"name": name, "criticality": criticality, "tier": tier, "language": language})
        for name, role, slack, team in PEOPLE:
            self._add_node("Person", {"name": name, "role": role, "slack_handle": slack, "team": team})
        for service, owner in OWNERSHIP:
            self._add_edge(stable_id("Service", {"name": service}), "OWNED_BY", stable_id("Person", {"name": owner}))
        for person, service, depth in EXPERTISE:
            self._add_edge(stable_id("Person", {"name": person}), "EXPERT_IN", stable_id("Service", {"name": service}), {"depth": depth})
        for dep, provider, strength, dep_type in DEPENDENCIES:
            self._add_edge(stable_id("Service", {"name": dep}), "DEPENDS_ON", stable_id("Service", {"name": provider}), {"strength": strength, "type": dep_type})
        for iid, severity, status, opened_offset, closed_offset, service, cause in INCIDENTS:
            self._add_node("Incident", {"id": iid, "severity": severity, "status": status,
                "opened_at": _stamp(opened_offset, now), "closed_at": _stamp(closed_offset, now), "root_cause": cause})
            self._add_edge(stable_id("Incident", {"id": iid}), "AFFECTED", stable_id("Service", {"name": service}))
        for iid, person in RESOLUTIONS:
            self._add_edge(stable_id("Incident", {"id": iid}), "RESOLVED_BY", stable_id("Person", {"name": person}))
        for person, iid in ASSIGNMENTS:
            self._add_edge(stable_id("Person", {"name": person}), "ASSIGNED_TO", stable_id("Incident", {"id": iid}))
        for cid, ctype, title, merged_offset, risk, author, services in CHANGES:
            self._add_node("Change", {"id": cid, "type": ctype, "title": title,
                "merged_at": _stamp(merged_offset, now), "risk_level": risk})
            self._add_edge(stable_id("Change", {"id": cid}), "AUTHORED_BY", stable_id("Person", {"name": author}))
            for service in services:
                self._add_edge(stable_id("Change", {"id": cid}), "MODIFIED", stable_id("Service", {"name": service}))
        for iid, cid, confidence in CAUSAL_LINKS:
            self._add_edge(stable_id("Incident", {"id": iid}), "CAUSED_BY", stable_id("Change", {"id": cid}), {"confidence": confidence})
        for rid, title, service, url in RUNBOOKS:
            self._add_node("Runbook", {"id": rid, "title": title, "url": url})
            self._add_edge(stable_id("Runbook", {"id": rid}), "APPLIES_TO", stable_id("Service", {"name": service}))
        self._seeded = True
        return self.counts()

    def counts(self) -> dict[str, int]:
        out = {label: sum(1 for n in self.nodes_by_id.values() if n["type"] == label)
               for label in EXPECTED_COUNTS}
        out["relationships"] = len(self.edges)
        return out

    def health(self) -> bool:
        return self._seeded

    def graph_data(self, include_working_memory: bool = False) -> dict[str, list[dict[str, Any]]]:
        nodes = list(self.nodes_by_id.values())
        if not include_working_memory:
            nodes = [n for n in nodes if n["type"] not in ("Session", "ReasoningStep")]
        allowed = {n["id"] for n in nodes}
        edges = [e for e in self.edges if e["from"] in allowed and e["to"] in allowed]
        return {"nodes": [{**n, "group": n["type"]} for n in nodes], "edges": edges}

    def resolve_entity(self, query: str) -> dict[str, Any]:
        q = query.strip().lower()
        if not q:
            return {"found": [], "node_ids": [], "related_service_names": [], "narrative": "No entity query was provided."}
        # Users naturally type "PR-445 just merged" rather than the bare identifier.
        # Rank exact IDs/names from the query tokens ahead of loose title matches.
        tokens = [x.lower() for x in re.findall(r"[A-Za-z]+-\d+|[A-Za-z0-9_.-]+", query)]
        candidates = list(dict.fromkeys([*tokens, q]))
        def match(n: dict[str, Any]) -> tuple[int, bool]:
            p, label = n["props"], n["type"]
            value = str(p.get("name") or p.get("id") or "").lower()
            title = str(p.get("title") or "").lower()
            title_tokens = [x.lower() for x in re.findall(r"[A-Za-z]+-\d+|[A-Za-z0-9_.-]+", title)]
            exact = value in candidates
            score = 0 if exact else (1 if value and value in q else (2 if any(c in title_tokens for c in candidates if len(c) > 2) else 9))
            return score, score < 9
        hits = sorted((n for n in self.nodes_by_id.values() if match(n)[1]), key=lambda n: (match(n)[0], n["type"]))[:5]
        related: list[str] = []
        ids = [n["id"] for n in hits]
        for n in hits:
            if n["type"] == "Change":
                for e in self.edges:
                    if e["from"] == n["id"] and e["rel"] == "MODIFIED":
                        target = self.nodes_by_id.get(e["to"])
                        if target and target["type"] == "Service":
                            related.append(target["props"]["name"])
                            ids.append(target["id"])
        return {"found": [{"node_id": n["id"], "type": n["type"], "props": n["props"]} for n in hits],
                "node_ids": list(dict.fromkeys(ids)), "related_service_names": list(dict.fromkeys(related)),
                "narrative": (f"Resolved '{query}' → " + ", ".join(str(n["props"].get("name") or n["props"].get("id")) + f" ({n['type']})" for n in hits)) if hits else f"No entity found matching '{query}'."}

    def impact_radius(self, service_name: str, max_hops: int = 4) -> dict[str, Any]:
        target_id = stable_id("Service", {"name": service_name})
        target = self.nodes_by_id.get(target_id)
        if not target:
            return {"dependents": [], "total": 0, "p0_count": 0, "node_ids": [], "narrative": f"Service '{service_name}' was not found in the graph."}
        incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.edges:
            if edge["rel"] == "DEPENDS_ON":
                incoming[edge["to"]].append(edge)
        found: dict[str, dict[str, Any]] = {}
        def walk(current_id: str, hops: int, strength: float, path_ids: list[str]) -> None:
            if hops >= max_hops:
                return
            for edge in incoming.get(current_id, []):
                dep_id = edge["from"]
                if dep_id in path_ids:
                    continue
                next_hops = hops + 1
                next_strength = strength * float(edge["props"].get("strength", 1.0))
                node = self.nodes_by_id.get(dep_id)
                if not node or node["type"] != "Service":
                    continue
                if next_strength > 0.2:
                    path = [dep_id, *path_ids]
                    entry = {"service": node["props"]["name"], "criticality": node["props"].get("criticality", "P2"),
                             "hops": next_hops, "path_strength": round(next_strength, 3), "tier": node["props"].get("tier"),
                             "path": [self.nodes_by_id[i]["props"].get("name") for i in path]}
                    old = found.get(dep_id)
                    if old is None or next_hops < old["hops"] or (next_hops == old["hops"] and next_strength > old["path_strength"]):
                        found[dep_id] = entry
                walk(dep_id, next_hops, next_strength, [dep_id, *path_ids])
        walk(target_id, 0, 1.0, [target_id])
        order = {"P0": 0, "P1": 1, "P2": 2}
        deps = sorted(found.values(), key=lambda d: (order.get(d["criticality"], 3), d["hops"], d["service"]))
        p0 = sum(1 for d in deps if d["criticality"] == "P0")
        return {"dependents": deps, "total": len(deps), "p0_count": p0,
                "node_ids": [stable_id("Service", {"name": d["service"]}) for d in deps] + [target_id],
                "narrative": (f"{len(deps)} services depend on {service_name} within {max_hops} hops ({p0} are P0)." if deps else f"No dependents found for {service_name}.")}

    def open_incidents(self, service_names: list[str]) -> dict[str, Any]:
        wanted = set(service_names)
        incidents = []
        for node in self.nodes_by_id.values():
            if node["type"] != "Incident" or node["props"].get("status") != "open":
                continue
            affected = [e["to"] for e in self.edges if e["from"] == node["id"] and e["rel"] == "AFFECTED"]
            services = [self.nodes_by_id[x]["props"].get("name") for x in affected if x in self.nodes_by_id]
            matching = wanted.intersection(services)
            if not matching:
                continue
            assignees = []
            for edge in self.edges:
                if edge["rel"] == "ASSIGNED_TO" and edge["to"] == node["id"]:
                    p = self.nodes_by_id.get(edge["from"])
                    if p: assignees.append(p["props"].get("name"))
            service = sorted(matching)[0]
            incidents.append({"service": service, "id": node["props"]["id"], "severity": node["props"].get("severity"),
                              "opened_at": node["props"].get("opened_at"), "assignees": assignees,
                              "root_cause": node["props"].get("root_cause")})
        incidents.sort(key=lambda i: (i.get("severity", "P9"), i["id"]))
        node_ids = []
        for inc in incidents:
            node_ids += [stable_id("Service", {"name": inc["service"]}), stable_id("Incident", {"id": inc["id"]})]
            node_ids += [stable_id("Person", {"name": p}) for p in inc["assignees"]]
        return {"open_incidents": incidents, "node_ids": list(dict.fromkeys(node_ids)),
                "narrative": (f"{len(incidents)} open incidents on affected services: " + ", ".join(f"{i['service']} ({i['severity']}, assigned: {', '.join(i['assignees']) or 'nobody'})" for i in incidents)) if incidents else "No open incidents on affected services."}

    def owners(self, service_names: list[str]) -> dict[str, Any]:
        wanted = set(service_names)
        owners, by_person = [], {}
        for edge in self.edges:
            if edge["rel"] != "OWNED_BY": continue
            service = self.nodes_by_id.get(edge["from"]); person = self.nodes_by_id.get(edge["to"])
            if not service or not person or service["props"].get("name") not in wanted: continue
            active = []
            for assignment in self.edges:
                if assignment["rel"] == "ASSIGNED_TO" and assignment["from"] == person["id"]:
                    inc = self.nodes_by_id.get(assignment["to"])
                    if inc and inc["props"].get("status") == "open": active.append(inc["props"].get("id"))
            active = list(dict.fromkeys(active))
            row = {"service": service["props"]["name"], "person": person["props"]["name"],
                   "slack": person["props"].get("slack_handle"), "active_incidents": len(active), "incident_ids": active}
            owners.append(row)
            if active:
                rec = by_person.setdefault(person["props"]["name"], {"person": person["props"]["name"], "slack": person["props"].get("slack_handle"), "services": [], "active_incidents": active})
                rec["services"].append(service["props"]["name"])
        conflicts = list(by_person.values())
        owners.sort(key=lambda x: (-x["active_incidents"], x["service"]))
        ids = [stable_id("Person", {"name": o["person"]}) for o in owners]
        for o in owners:
            ids.append(stable_id("Service", {"name": o["service"]}))
            ids.extend(stable_id("Incident", {"id": i}) for i in o["incident_ids"])
        return {"owners": owners, "conflicts": conflicts, "node_ids": list(dict.fromkeys(ids)),
                "narrative": (f"CONFLICT: {len(conflicts)} owner(s) already assigned to active incidents — " + ", ".join(f"{c['person']} (owns {', '.join(c['services'])}; on {', '.join(c['active_incidents'])})" for c in conflicts)) if conflicts else f"All {len(owners)} owners are currently available."}

    def experts(self, service_names: list[str]) -> dict[str, Any]:
        wanted = set(service_names)
        coverage: dict[str, set[str]] = defaultdict(set)
        depth: dict[tuple[str, str], str] = {}
        for e in self.edges:
            if e["rel"] != "EXPERT_IN": continue
            person, service = self.nodes_by_id.get(e["from"]), self.nodes_by_id.get(e["to"])
            if not person or not service: continue
            svc_name = service["props"].get("name")
            if svc_name in wanted:
                name = person["props"].get("name")
                coverage[name].add(svc_name)
                depth[(name, svc_name)] = e["props"].get("depth", "familiar")
        out = []
        for name, services in coverage.items():
            person = self.nodes_by_id[stable_id("Person", {"name": name})]
            active = []
            for e in self.edges:
                if e["rel"] == "ASSIGNED_TO" and e["from"] == person["id"]:
                    inc = self.nodes_by_id.get(e["to"])
                    if inc and inc["props"].get("status") == "open": active.append(inc["props"].get("id"))
            if active: continue
            out.append({"name": name, "slack": person["props"].get("slack_handle"), "role": person["props"].get("role"),
                        "covers": sorted(services), "expertise_depth": {s: depth[(name, s)] for s in services}})
        out.sort(key=lambda e: (-len(e["covers"]), e["name"]))
        out = out[:5]
        ids = [stable_id("Person", {"name": e["name"]}) for e in out]
        ids += [stable_id("Service", {"name": s}) for e in out for s in e["covers"]]
        return {"available_experts": out, "node_ids": list(dict.fromkeys(ids)),
                "narrative": (f"{len(out)} available expert(s): " + ", ".join(f"{e['name']} (covers {', '.join(e['covers'][:2])})" for e in out[:3])) if out else "No available experts found for these services."}

    def create_session(self, session_id: str, query: str) -> None:
        now = int(time.time())
        self._add_node("Session", {"id": session_id, "created_at": now, "query": query})
        self.sessions[session_id] = {"id": session_id, "created_at": now, "query": query, "steps": []}

    def write_step(self, session_id: str, hop: int, tool: str, finding: str, node_ids: list[str]) -> dict[str, Any]:
        step_id = f"{session_id}-{hop:02d}-{uuid.uuid4().hex[:6]}"
        props = {"id": step_id, "hop_number": hop, "cypher_issued": tool, "finding": finding, "created_at": int(time.time())}
        sid = self._add_node("ReasoningStep", props)
        self._add_edge(stable_id("Session", {"id": session_id}), "STEP", sid, {"order": hop})
        for nid in node_ids:
            if nid in self.nodes_by_id and self.nodes_by_id[nid]["type"] not in ("Session", "ReasoningStep"):
                self._add_edge(sid, "EXAMINED", nid)
        step = {"id": step_id, "node_id": sid, "hop": hop, "tool": tool, "finding": finding, "created_at": props["created_at"], "node_ids": [sid]}
        self.steps[session_id].append(step)
        return step

    def replay(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "steps": sorted(self.steps.get(session_id, []), key=lambda s: s["hop"])}

class FalkorGraphStore:
    """FalkorDB implementation using fixed Cypher templates and bound parameters."""
    backend_name = "falkordb"

    def __init__(self) -> None:
        try:
            from falkordb import FalkorDB
        except ImportError as exc:
            raise RuntimeError("TRACE_GRAPH_BACKEND=falkordb requires the FalkorDB Python package; install backend/requirements.txt") from exc
        self._client_cls = FalkorDB
        raw_url = os.getenv("FALKORDB_URL")
        if raw_url:
            parsed = urlparse(raw_url)
            self._host = parsed.hostname or "localhost"
            self._port = parsed.port or 6379
            self._username = parsed.username or os.getenv("FALKORDB_USERNAME")
            self._password = parsed.password or os.getenv("FALKORDB_PASSWORD")
            self._ssl = parsed.scheme == "rediss"
        else:
            self._host = os.getenv("FALKORDB_HOST", "localhost")
            self._port = int(os.getenv("FALKORDB_PORT", "6379"))
            self._username = os.getenv("FALKORDB_USERNAME")
            self._password = os.getenv("FALKORDB_PASSWORD")
            self._ssl = os.getenv("FALKORDB_SSL", "false").lower() == "true"
        args = {"host": self._host, "port": self._port}
        if self._username: args["username"] = self._username
        if self._password: args["password"] = self._password
        if self._ssl: args["ssl"] = True
        self.client = FalkorDB(**args)
        self.graph = self.client.select_graph(os.getenv("FALKORDB_GRAPH", "trace"))

    def _q(self, query: str, params: dict[str, Any] | None = None):
        return self.graph.query(query, params or {})

    def initialize_schema(self) -> None:
        for label, prop in [("Service", "name"), ("Person", "name"), ("Incident", "id"), ("Change", "id"), ("Runbook", "id"), ("Session", "id")]:
            name = f"idx_{label.lower()}_{prop}"
            try:
                self._q(f"CREATE INDEX {name} FOR (n:{label}) ON (n.{prop})")
            except Exception as exc:
                # Indexes are immutable after creation; the existing-index case is harmless.
                if not any(token in str(exc).lower() for token in ("already exists", "already indexed", "exists")):
                    raise

    def seed(self) -> dict[str, int]:
        now = int(time.time())
        nodes: list[tuple[str, dict[str, Any], str]] = []
        nodes += [("Service", {"name": n, "criticality": c, "tier": t, "language": l}, "name") for n,c,t,l in SERVICES]
        nodes += [("Person", {"name": n, "role": r, "slack_handle": s, "team": t}, "name") for n,r,s,t in PEOPLE]
        nodes += [("Incident", {"id": iid, "severity": sev, "status": st, "opened_at": _stamp(opened, now), "closed_at": _stamp(closed, now), "root_cause": cause}, "id") for iid,sev,st,opened,closed,svc,cause in INCIDENTS]
        nodes += [("Change", {"id": cid, "type": typ, "title": title, "merged_at": _stamp(merged, now), "risk_level": risk}, "id") for cid,typ,title,merged,risk,author,svcs in CHANGES]
        nodes += [("Runbook", {"id": rid, "title": title, "url": url}, "id") for rid,title,svc,url in RUNBOOKS]
        for label, props, key in nodes:
            self._q(f"MERGE (n:{label} {{{key}: $key}}) SET n += $props", {"key": props[key], "props": props})
        rel_specs: list[tuple[str,str,str,str,str,str,dict[str,Any]]] = []
        for service, owner in OWNERSHIP:
            rel_specs.append(("Service", "name", service, "OWNED_BY", "Person", "name", {"target": owner}))
        for person, service, depth in EXPERTISE:
            rel_specs.append(("Person", "name", person, "EXPERT_IN", "Service", "name", {"target": service, "depth": depth}))
        for dep, provider, strength, typ in DEPENDENCIES:
            rel_specs.append(("Service", "name", dep, "DEPENDS_ON", "Service", "name", {"target": provider, "strength": strength, "type": typ}))
        for iid,sev,status,opened,closed,service,cause in INCIDENTS:
            rel_specs.append(("Incident", "id", iid, "AFFECTED", "Service", "name", {"target": service}))
        for iid,person in RESOLUTIONS:
            rel_specs.append(("Incident", "id", iid, "RESOLVED_BY", "Person", "name", {"target": person}))
        for person,iid in ASSIGNMENTS:
            rel_specs.append(("Person", "name", person, "ASSIGNED_TO", "Incident", "id", {"target": iid}))
        for cid,typ,title,merged,risk,author,svcs in CHANGES:
            rel_specs.append(("Change", "id", cid, "AUTHORED_BY", "Person", "name", {"target": author}))
            for service in svcs:
                rel_specs.append(("Change", "id", cid, "MODIFIED", "Service", "name", {"target": service}))
        for iid,cid,confidence in CAUSAL_LINKS:
            rel_specs.append(("Incident", "id", iid, "CAUSED_BY", "Change", "id", {"target": cid, "confidence": confidence}))
        for rid,title,service,url in RUNBOOKS:
            rel_specs.append(("Runbook", "id", rid, "APPLIES_TO", "Service", "name", {"target": service}))
        for left_label,left_key,left_val,rel,right_label,right_key,details in rel_specs:
            props = {k:v for k,v in details.items() if k != "target"}
            self._q(f"MATCH (a:{left_label} {{{left_key}: $left}}), (b:{right_label} {{{right_key}: $right}}) MERGE (a)-[r:{rel}]->(b) SET r += $props",
                    {"left": left_val, "right": details["target"], "props": props})
        return self.counts()

    def counts(self) -> dict[str, int]:
        out = {}
        for label in EXPECTED_COUNTS:
            res = self._q(f"MATCH (n:{label}) RETURN count(n)")
            out[label] = res.result_set[0][0] if res.result_set else 0
        res = self._q("MATCH ()-[r]->() RETURN count(r)")
        out["relationships"] = res.result_set[0][0] if res.result_set else 0
        return out

    def health(self) -> bool:
        self._q("RETURN 1")
        return True

    def _node_props(self, n: Any) -> dict[str, Any]:
        return dict(getattr(n, "properties", {}) or {})

    def graph_data(self, include_working_memory: bool = False) -> dict[str, list[dict[str, Any]]]:
        where = "" if include_working_memory else "WHERE NOT n:Session AND NOT n:ReasoningStep"
        nr = self._q(f"MATCH (n) {where} RETURN n, labels(n)[0] LIMIT 500")
        nodes = []
        for row in nr.result_set:
            props = self._node_props(row[0]); label = row[1]
            if label not in LABEL_KEYS: continue
            nodes.append({"id": stable_id(label, props), "type": label, "group": label, "props": props})
        where_e = "" if include_working_memory else "WHERE NOT a:Session AND NOT b:Session AND NOT a:ReasoningStep AND NOT b:ReasoningStep"
        er = self._q(f"MATCH (a)-[r]->(b) {where_e} RETURN labels(a)[0], coalesce(a.name, a.id), type(r), labels(b)[0], coalesce(b.name, b.id), properties(r) LIMIT 1000")
        edges = []
        for row in er.result_set:
            la, ka, rel, lb, kb, props = row
            if la not in LABEL_KEYS or lb not in LABEL_KEYS: continue
            left_key = LABEL_KEYS[la][0]; right_key = LABEL_KEYS[lb][0]
            edges.append({"from": stable_id(la, {left_key: ka}), "rel": rel, "to": stable_id(lb, {right_key: kb}), "props": dict(props or {})})
        return {"nodes": nodes, "edges": edges}

    def resolve_entity(self, query: str) -> dict[str, Any]:
        terms = list(dict.fromkeys([x.lower() for x in re.findall(r"[A-Za-z]+-\d+|[A-Za-z0-9_.-]+", query)]))
        terms.append(query.strip().lower())
        res = self._q("MATCH (n) WITH n, labels(n)[0] AS label, toLower(coalesce(n.name, '')) AS nm, toLower(coalesce(n.id, '')) AS nid, toLower(coalesce(n.title, '')) AS title WHERE nm IN $terms OR nid IN $terms OR any(term IN $terms WHERE size(term) >= 4 AND (nm CONTAINS term OR title CONTAINS term)) RETURN n, label ORDER BY CASE WHEN nm IN $terms OR nid IN $terms THEN 0 ELSE 1 END LIMIT 5", {"terms": terms})
        found, ids, related = [], [], []
        for row in res.result_set:
            props, label = self._node_props(row[0]), row[1]
            nid = stable_id(label, props)
            found.append({"node_id": nid, "type": label, "props": props}); ids.append(nid)
            if label == "Change":
                rr = self._q("MATCH (c:Change {id:$id})-[:MODIFIED]->(s:Service) RETURN s.name", {"id": props.get("id")})
                for item in rr.result_set:
                    related.append(item[0]); ids.append(stable_id("Service", {"name": item[0]}))
        return {"found": found, "node_ids": list(dict.fromkeys(ids)), "related_service_names": list(dict.fromkeys(related)),
                "narrative": (f"Resolved '{query}' → " + ", ".join(str(n["props"].get("name") or n["props"].get("id")) + f" ({n['type']})" for n in found)) if found else f"No entity found matching '{query}'."}

    def impact_radius(self, service_name: str, max_hops: int = 4) -> dict[str, Any]:
        # max_hops is validated to 1..4 before interpolation; values are never user-provided Cypher fragments.
        res = self._q(f"MATCH path=(dep:Service)-[:DEPENDS_ON*1..{max_hops}]->(start:Service {{name:$name}}) WITH dep, length(path) AS hops, reduce(s=1.0, r IN relationships(path) | s * coalesce(r.strength, 1.0)) AS strength, [n IN nodes(path) | n.name] AS path_nodes WHERE strength > 0.2 RETURN dep.name, dep.criticality, hops, strength, dep.tier, path_nodes ORDER BY hops ASC, strength DESC", {"name": service_name})
        found = {}
        for row in res.result_set:
            name, crit, hops, strength, tier, path = row
            score = float(strength)
            old = found.get(name)
            item = {"service": name, "criticality": crit, "hops": hops, "path_strength": round(score, 3), "tier": tier, "path": path}
            if old is None or hops < old["hops"] or (hops == old["hops"] and score > old["path_strength"]): found[name] = item
        order = {"P0": 0, "P1": 1, "P2": 2}
        deps = sorted(found.values(), key=lambda d: (order.get(d["criticality"], 3), d["hops"], d["service"]))
        p0 = sum(d["criticality"] == "P0" for d in deps)
        return {"dependents": deps, "total": len(deps), "p0_count": p0,
                "node_ids": [stable_id("Service", {"name": d["service"]}) for d in deps] + [stable_id("Service", {"name": service_name})],
                "narrative": f"{len(deps)} services depend on {service_name} within {max_hops} hops ({p0} are P0)." if deps else f"No dependents found for {service_name}."}

    def open_incidents(self, service_names: list[str]) -> dict[str, Any]:
        res = self._q("MATCH (s:Service)<-[:AFFECTED]-(i:Incident {status:'open'}) WHERE s.name IN $names OPTIONAL MATCH (p:Person)-[:ASSIGNED_TO]->(i) RETURN s.name, i.id, i.severity, i.opened_at, i.root_cause, collect(DISTINCT p.name) ORDER BY i.severity ASC", {"names": service_names})
        rows = [{"service": r[0], "id": r[1], "severity": r[2], "opened_at": r[3], "root_cause": r[4], "assignees": r[5] or []} for r in res.result_set]
        ids = []
        for row in rows:
            ids += [stable_id("Service", {"name": row["service"]}), stable_id("Incident", {"id": row["id"]})]
            ids += [stable_id("Person", {"name": name}) for name in row["assignees"]]
        return {"open_incidents": rows, "node_ids": list(dict.fromkeys(ids)),
                "narrative": f"{len(rows)} open incidents on affected services: " + ", ".join(f"{r['service']} ({r['severity']}, assigned: {', '.join(r['assignees']) or 'nobody'})" for r in rows) if rows else "No open incidents on affected services."}

    def owners(self, service_names: list[str]) -> dict[str, Any]:
        res = self._q("MATCH (s:Service)-[:OWNED_BY]->(p:Person) WHERE s.name IN $names OPTIONAL MATCH (p)-[:ASSIGNED_TO]->(active:Incident {status:'open'}) WITH s,p,collect(DISTINCT active.id) AS active_ids RETURN s.name,p.name,p.slack_handle,active_ids ORDER BY size(active_ids) DESC", {"names": service_names})
        owners = [{"service": r[0], "person": r[1], "slack": r[2], "incident_ids": [x for x in (r[3] or []) if x], "active_incidents": len([x for x in (r[3] or []) if x])} for r in res.result_set]
        conflicts_by_person = {}
        for owner in owners:
            if owner["active_incidents"]:
                c = conflicts_by_person.setdefault(owner["person"], {"person": owner["person"], "slack": owner["slack"], "services": [], "active_incidents": owner["incident_ids"]})
                c["services"].append(owner["service"])
        conflicts = list(conflicts_by_person.values())
        ids = [stable_id("Person", {"name": o["person"]}) for o in owners]
        for o in owners:
            ids.append(stable_id("Service", {"name": o["service"]}))
            ids.extend(stable_id("Incident", {"id": x}) for x in o["incident_ids"])
        return {"owners": owners, "conflicts": conflicts, "node_ids": list(dict.fromkeys(ids)),
                "narrative": f"CONFLICT: {len(conflicts)} owner(s) already assigned to active incidents — " + ", ".join(f"{c['person']} (owns {', '.join(c['services'])}; on {', '.join(c['active_incidents'])})" for c in conflicts) if conflicts else f"All {len(owners)} owners are currently available."}

    def experts(self, service_names: list[str]) -> dict[str, Any]:
        res = self._q("MATCH (p:Person)-[e:EXPERT_IN]->(s:Service) WHERE s.name IN $names WITH p,collect(DISTINCT s.name) AS services OPTIONAL MATCH (p)-[:ASSIGNED_TO]->(active:Incident {status:'open'}) WITH p,services,collect(DISTINCT active.id) AS active_ids WHERE size([x IN active_ids WHERE x IS NOT NULL])=0 RETURN p.name,p.slack_handle,p.role,services ORDER BY size(services) DESC LIMIT 5", {"names": service_names})
        out = [{"name": r[0], "slack": r[1], "role": r[2], "covers": r[3] or []} for r in res.result_set]
        ids = [stable_id("Person", {"name": x["name"]}) for x in out]
        ids += [stable_id("Service", {"name": name}) for x in out for name in x["covers"]]
        return {"available_experts": out, "node_ids": list(dict.fromkeys(ids)),
                "narrative": f"{len(out)} available expert(s): " + ", ".join(f"{e['name']} (covers {', '.join(e['covers'][:2])})" for e in out[:3]) if out else "No available experts found for these services."}

    def create_session(self, session_id: str, query: str) -> None:
        self._q("MERGE (s:Session {id:$id}) SET s.query=$query, s.created_at=$ts", {"id": session_id, "query": query, "ts": int(time.time())})

    def write_step(self, session_id: str, hop: int, tool: str, finding: str, node_ids: list[str]) -> dict[str, Any]:
        step_id = f"{session_id}-{hop:02d}-{uuid.uuid4().hex[:6]}"
        sid = stable_id("ReasoningStep", {"id": step_id})
        self._q("MATCH (s:Session {id:$session_id}) MERGE (r:ReasoningStep {id:$id}) SET r.hop_number=$hop, r.cypher_issued=$tool, r.finding=$finding, r.created_at=$ts MERGE (s)-[e:STEP {order:$hop}]->(r)", {"session_id": session_id, "id": step_id, "hop": hop, "tool": tool, "finding": finding, "ts": int(time.time())})
        for nid in node_ids:
            try: label, key = nid.split(":", 1)
            except ValueError: continue
            labels = {"service": ("Service", "name"), "person": ("Person", "name"), "incident": ("Incident", "id"), "change": ("Change", "id"), "runbook": ("Runbook", "id")}
            if label not in labels: continue
            db_label, db_key = labels[label]
            self._q(f"MATCH (r:ReasoningStep {{id:$step_id}}),(n:{db_label} {{{db_key}:$key}}) MERGE (r)-[:EXAMINED]->(n)", {"step_id": step_id, "key": key})
        return {"id": step_id, "node_id": sid, "hop": hop, "tool": tool, "finding": finding, "created_at": int(time.time()), "node_ids": [sid]}

    def replay(self, session_id: str) -> dict[str, Any]:
        res = self._q("MATCH (s:Session {id:$sid})-[:STEP]->(r:ReasoningStep) RETURN r.id,r.hop_number,r.cypher_issued,r.finding,r.created_at ORDER BY r.hop_number", {"sid": session_id})
        steps = [{"id": r[0], "node_id": stable_id("ReasoningStep", {"id": r[0]}), "hop": r[1], "tool": r[2], "finding": r[3], "created_at": r[4], "node_ids": [stable_id("ReasoningStep", {"id": r[0]})]} for r in res.result_set]
        return {"session_id": session_id, "steps": steps}

def make_store():
    backend = os.getenv("TRACE_GRAPH_BACKEND", "memory").strip().lower()
    if backend in ("memory", "seeded", "development"):
        return MemoryGraphStore()
    if backend == "falkordb":
        return FalkorGraphStore()
    raise ValueError("TRACE_GRAPH_BACKEND must be 'memory' or 'falkordb'")

store = make_store()
