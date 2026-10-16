"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { DataSet as VisDataSet } from "vis-data/peer";
import type { Network as VisNetwork } from "vis-network/peer";
import { SseParser } from "../src/lib/sse-parser.mjs";

type GraphNode = { id: string; type: string; group?: string; props: Record<string, unknown>; label?: string; shape?: string; color?: unknown; font?: unknown; size?: number; title?: string };
type GraphEdge = { from: string; to: string; rel: string; props?: Record<string, unknown>; arrows?: string; color?: unknown; label?: string };
type TraceStep = { hop: number; tool: string; narrative: string; result?: Record<string, unknown>; conflicts?: Array<Record<string, unknown>> };
type TraceAction = { id: string; type: "slack_notify" | "github_issue"; label: string; title: string; message?: string; body?: string };
type FinalData = { text: string; session_id: string; actions: TraceAction[]; conflicts?: Array<Record<string, unknown>>; open_incidents?: Array<Record<string, unknown>>; available_experts?: Array<Record<string, unknown>>; impact?: Record<string, unknown>; agent_mode?: string };
type TraceEvent = { event: string; data: any };

const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const COLORS = { canvas: "#050C18", amber: "#E8A838", red: "#FF5C57", blue: "#5BA4F5", green: "#4DC97A", purple: "#9D86F0", inactive: "#1D3250", edge: "#5E7896", text: "#A7B7CA" };

function nodeLabel(node: GraphNode) {
  const p = node.props || {};
  return String(p.name || p.id || p.title || node.id);
}
function nodeColor(node: GraphNode) {
  if (node.type === "Person") return "#142A46";
  if (node.type === "Incident") return "#301D27";
  if (node.type === "Change") return "#241D3B";
  if (node.type === "Runbook") return "#1D2A36";
  if (node.type === "ReasoningStep") return "#1A2510";
  if (node.type === "Session") return "#1C2B26";
  return "#14243B";
}
function nodeShape(node: GraphNode) {
  if (node.type === "ReasoningStep") return "diamond";
  if (node.type === "Session") return "hexagon";
  if (node.type === "Person") return "dot";
  if (node.type === "Incident") return "triangle";
  if (node.type === "Change") return "star";
  if (node.type === "Runbook") return "square";
  return "dot";
}
function finalNodeColor(node: GraphNode, conflicts: Set<string>, experts: Set<string>) {
  if (conflicts.has(String(node.id))) return COLORS.red;
  if (experts.has(String(node.id))) return COLORS.green;
  if (node.type === "Person") return COLORS.blue;
  if (node.type === "Change") return COLORS.purple;
  if (node.type === "ReasoningStep" || node.type === "Session") return COLORS.amber;
  if (node.type === "Incident") return node.props?.status === "open" ? COLORS.red : "#39465A";
  if (node.type === "Service") {
    const criticality = node.props?.criticality;
    return criticality === "P0" ? COLORS.red : criticality === "P1" ? COLORS.amber : COLORS.green;
  }
  return COLORS.edge;
}
function toVisNode(node: GraphNode, active = false, isConflict = false, isExpert = false) {
  const label = nodeLabel(node);
  const p = node.props || {};
  const groupColor = isConflict ? COLORS.red : isExpert ? COLORS.green : active ? finalNodeColor(node, new Set(), new Set()) : nodeColor(node);
  return {
    id: node.id, label, group: node.type, shape: nodeShape(node), size: node.type === "ReasoningStep" ? 12 : node.type === "Session" ? 11 : 9,
    color: { background: groupColor, border: active || isConflict || isExpert ? groupColor : "#263D59", highlight: { background: COLORS.amber, border: COLORS.amber }, hover: { background: groupColor, border: "#B5C9DE" } },
    font: { color: active ? "#E7EEF7" : "#526A85", size: node.type === "ReasoningStep" ? 10 : 11, face: "monospace", strokeWidth: 0 },
    borderWidth: active ? 2 : 1, title: `${node.type}\n${label}\n${Object.entries(p).filter(([k]) => k !== "url").map(([k, v]) => `${k}: ${String(v)}`).join("\n")}`,
  };
}

export default function Home() {
  const graphContainer = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<VisNetwork | null>(null);
  const cancelRef = useRef<AbortController | null>(null);
  const nodesRef = useRef<VisDataSet<any> | null>(null);
  const edgesRef = useRef<VisDataSet<any> | null>(null);
  const graphNodesRef = useRef<Map<string, GraphNode>>(new Map());
  const conflictsRef = useRef<Set<string>>(new Set());
  const expertsRef = useRef<Set<string>>(new Set());
  const [query, setQuery] = useState("PR-445 just merged");
  const [health, setHealth] = useState<{ ok?: boolean; graph_backend?: string; llm_provider?: string } | null>(null);
  const [steps, setSteps] = useState<TraceStep[]>([]);
  const [finalData, setFinalData] = useState<FinalData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [pendingAction, setPendingAction] = useState<TraceAction | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [actionResult, setActionResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [graphCount, setGraphCount] = useState(0);

  const insertGraphNode = useCallback((node: GraphNode, active = false) => {
    graphNodesRef.current.set(node.id, node);
    if (!nodesRef.current) return;
    const conflict = conflictsRef.current.has(node.id);
    const expert = expertsRef.current.has(node.id);
    nodesRef.current.update(toVisNode(node, active, conflict, expert));
  }, []);

  const activate = useCallback((ids: string[], conflictPeople: Set<string> = new Set(), expertPeople: Set<string> = new Set()) => {
    conflictsRef.current = new Set([...conflictsRef.current, ...conflictPeople]);
    expertsRef.current = new Set([...expertsRef.current, ...expertPeople]);
    if (!nodesRef.current) return;
    const found: string[] = [];
    for (const id of ids) {
      const node = graphNodesRef.current.get(id);
      if (!node) continue;
      found.push(id);
      nodesRef.current.update({ id, size: node.type === "ReasoningStep" ? 13 : 15, color: { background: COLORS.amber, border: COLORS.amber }, font: { color: "#E7EEF7", size: 11, face: "monospace" } });
    }
    window.setTimeout(() => {
      for (const id of found) {
        const node = graphNodesRef.current.get(id);
        if (!node) continue;
        const c = finalNodeColor(node, conflictsRef.current, expertsRef.current);
        nodesRef.current?.update({ id, size: node.type === "ReasoningStep" ? 12 : 10, color: { background: c, border: c }, font: { color: "#C0D0E1", size: node.type === "ReasoningStep" ? 10 : 11, face: "monospace" } });
      }
    }, 420);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [healthResponse, graphResponse] = await Promise.all([fetch(`${API_BASE}/health`), fetch(`${API_BASE}/graph/nodes`)]);
        if (!healthResponse.ok || !graphResponse.ok) throw new Error("API returned an unhealthy response");
        const healthData = await healthResponse.json();
        const graphData = await graphResponse.json();
        // Load graph libraries only in the browser: Next.js still pre-renders Client Components.
        const [{ DataSet }, { Network }] = await Promise.all([import("vis-data/peer"), import("vis-network/peer")]);
        if (cancelled || !graphContainer.current) return;
        setHealth(healthData);
        const rawNodes: GraphNode[] = graphData.nodes || [];
        const rawEdges: GraphEdge[] = graphData.edges || [];
        setGraphCount(rawNodes.length);
        graphNodesRef.current = new Map(rawNodes.map(n => [n.id, n]));
        const visNodes = new DataSet(rawNodes.map(n => toVisNode(n)));
        const visEdges = new DataSet(rawEdges.map((e, i) => ({ id: `edge:${i}:${e.from}:${e.rel}:${e.to}`, from: e.from, to: e.to, label: e.rel, arrows: { to: { enabled: true, scaleFactor: 0.45 } }, color: { color: "#243A55", highlight: COLORS.blue, hover: COLORS.edge }, width: 1, font: { color: "#3C536D", size: 8, align: "middle", strokeWidth: 0 } })));
        nodesRef.current = visNodes;
        edgesRef.current = visEdges;
        networkRef.current = new Network(graphContainer.current, { nodes: visNodes, edges: visEdges }, {
          autoResize: true, height: "100%", width: "100%", interaction: { hover: true, tooltipDelay: 100, navigationButtons: false, keyboard: true },
          physics: { enabled: true, solver: "forceAtlas2Based", forceAtlas2Based: { gravitationalConstant: -34, centralGravity: 0.012, springLength: 115, springConstant: 0.06, damping: 0.45 }, stabilization: { enabled: true, iterations: 180, updateInterval: 20 } },
          nodes: { borderWidth: 1.3, chosen: false }, edges: { smooth: { type: "continuous", roundness: 0.2 } },
        });
        networkRef.current.on("stabilizationIterationsDone", () => networkRef.current?.setOptions({ physics: { enabled: false } }));
      } catch (e) {
        if (!cancelled) setError(`Could not load Trace graph. Start the API and retry. ${e instanceof Error ? e.message : ""}`);
      }
    };
    void load();
    return () => { cancelled = true; networkRef.current?.destroy(); networkRef.current = null; };
  }, []);

  const processEvent = useCallback((evt: TraceEvent) => {
    const data = evt.data || {};
    if (evt.event === "start") {
      setSessionId(data.session_id || "");
      if (data.session_node) insertGraphNode(data.session_node as GraphNode, true);
      setNotice(`Trace started · ${data.provider || "agent"}`);
    } else if (evt.event === "step") {
      if (data.reasoning_node) insertGraphNode(data.reasoning_node as GraphNode, true);
      for (const edge of data.reasoning_edges || []) {
        const edgeId = `reasoning:${edge.from}:${edge.to}:${edge.rel}`;
        edgesRef.current?.update({ id: edgeId, from: edge.from, to: edge.to, label: edge.rel, arrows: { to: { enabled: true, scaleFactor: 0.45 } }, color: { color: COLORS.amber, highlight: COLORS.amber }, width: 1.5, dashes: true, font: { color: "#687C93", size: 8, strokeWidth: 0 } });
      }
      const conflictIds = new Set<string>();
      for (const c of data.conflicts || []) {
        if (c.person) conflictIds.add(`person:${c.person}`);
        for (const iid of c.active_incidents || []) conflictIds.add(`incident:${iid}`);
      }
      const expertResult: any[] = data.result?.available_experts || [];
      const expertIds = new Set(expertResult.map(e => `person:${e.name}`));
      activate(data.activate_nodes || [], conflictIds, expertIds);
      const entry: TraceStep = { hop: data.hop, tool: data.tool, narrative: data.narrative || "Tool completed.", result: data.result, conflicts: data.conflicts || [] };
      setSteps(previous => [...previous, entry]);
      setNotice(`Graph discovery ${String(data.hop).padStart(2, "0")} · ${data.tool}`);
    } else if (evt.event === "final") {
      setFinalData(data as FinalData);
      setNotice("Trace complete · review findings before sending actions");
      const conflictIds = new Set<string>();
      for (const c of data.conflicts || []) {
        if (c.person) conflictIds.add(`person:${c.person}`);
        for (const iid of c.active_incidents || []) conflictIds.add(`incident:${iid}`);
      }
      const expertIds = new Set((data.available_experts || []).map((e: any) => `person:${e.name}`));
      conflictsRef.current = conflictIds;
      expertsRef.current = expertIds;
      activate([], conflictIds, expertIds);
    } else if (evt.event === "provider_fallback") {
      setNotice(`Provider fallback · ${data.from} → ${data.to}`);
    } else if (evt.event === "error") {
      setError(data.message || "Trace failed.");
    } else if (evt.event === "done") {
      setBusy(false);
      if (!data.error) setNotice("Stream closed");
    }
  }, [activate, insertGraphNode]);

  const runTrace = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) { cancelRef.current?.abort(); setNotice("Cancelling trace stream…"); return; }
    if (!query.trim()) return;
    const controller = new AbortController();
    cancelRef.current = controller;
    setBusy(true); setError(""); setNotice("Connecting to the trace agent…"); setSteps([]); setFinalData(null); setActionResult(null); setPendingAction(null); setSessionId("");
    conflictsRef.current = new Set(); expertsRef.current = new Set();
    try {
      const response = await fetch(`${API_BASE}/trace`, { method: "POST", headers: { "Content-Type": "application/json", "Accept": "text/event-stream" }, body: JSON.stringify({ query: query.trim() }), signal: controller.signal });
      if (!response.ok) throw new Error(`Trace API returned HTTP ${response.status}`);
      if (!response.body) throw new Error("This browser did not provide a readable SSE stream.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SseParser();
      let streamDone = false;
      while (!streamDone) {
        const { done, value } = await reader.read();
        if (done) { streamDone = true; }
        const chunk = decoder.decode(value, { stream: !done });
        for (const evt of parser.push(chunk)) processEvent(evt);
      }
      for (const evt of parser.finish()) processEvent(evt);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") { setNotice("Trace cancelled · partial reasoning steps were preserved"); }
      else { setError(e instanceof Error ? e.message : "Unexpected stream error"); setNotice("Trace stopped"); }
    } finally {
      cancelRef.current = null;
      setBusy(false);
    }
  };

  const sendAction = async (action: TraceAction) => {
    if (actionBusy) return;
    setActionBusy(true); setError(""); setNotice(`Sending ${action.type === "slack_notify" ? "Slack notification" : "GitHub issue"}…`); setActionResult(null);
    try {
      const payload = action.type === "slack_notify" ? { message: action.message } : { title: action.title, body: action.body };
      const response = await fetch(`${API_BASE}/action`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action_type: action.type, payload, confirmed: true }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Action failed with HTTP ${response.status}`);
      const text = action.type === "github_issue" ? `GitHub issue #${data.number} created: ${data.url}` : "Slack accepted the notification.";
      setActionResult({ ok: true, text }); setNotice(text); setPendingAction(null);
    } catch (e) {
      const text = e instanceof Error ? e.message : "Action failed.";
      setActionResult({ ok: false, text }); setError(text); setNotice("No action success was reported");
    } finally { setActionBusy(false); }
  };

  const impact = finalData?.impact || {};
  const conflicts = finalData?.conflicts || [];
  const incidents = finalData?.open_incidents || [];
  const experts = finalData?.available_experts || [];

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-mark">◈</div><div><div className="brand-name">TRACE<span>.</span></div><div className="brand-sub">ENGINEERING DECISION MEMORY</div></div></div>
        <div className="track-tag">GRAPH HACKS <span>·</span> TRACK 01</div>
        <div className="live-state"><span className={`pulse ${health?.ok ? "ok" : "bad"}`} />{health?.ok ? "GRAPH CONNECTED" : "CONNECTING"}</div>
      </header>

      <section className="querybar">
        <div className="query-caption"><span className="micro-label">TRACE INPUT</span><span className="hint">PR · SERVICE · INCIDENT</span></div>
        <form onSubmit={runTrace} className="query-form">
          <span className="terminal-caret">›</span>
          <input aria-label="Trace query" value={query} onChange={e => setQuery(e.target.value)} placeholder="PR-445 just merged" disabled={busy} maxLength={500} />
          <button className={`trace-button ${busy ? "cancel-trace" : ""}`} disabled={!busy && !query.trim()} type="submit">{busy ? <>CANCEL TRACE <span>×</span></> : <>TRACE IMPACT <span>↗</span></>}</button>
        </form>
        <div className="query-meta"><span>GRAPH: {health?.graph_backend || "loading"}</span><span>AGENT: {health?.llm_provider || "loading"}</span><span>{graphCount || "—"} NODES IN VIEW</span></div>
      </section>

      <section className="workspace">
        <div className="graph-panel panel">
          <div className="panel-head"><div><span className="section-index">01 / CONNECTED DATA</span><h2>Dependency graph</h2></div><div className="canvas-status"><span className={`tiny-dot ${busy ? "moving" : ""}`} />{busy ? "DISCOVERING" : "LIVE GRAPH"}</div></div>
          <div className="graph-wrap"><div ref={graphContainer} className="graph-canvas" aria-label="Interactive dependency graph" />
            <div className="graph-top-hud"><span>FINTRACK / SYSTEM MAP</span><span>{sessionId ? `SESSION ${sessionId.toUpperCase()}` : "SEED GRAPH"}</span></div>
            {!graphCount && <div className="graph-placeholder"><div className="placeholder-spinner" />Loading graph nodes…</div>}
            <div className="graph-bottom-hud"><span>PHYSICS LAYOUT</span><span>HOVER NODE FOR DETAILS</span><span>SCROLL TO ZOOM</span></div>
          </div>
          <div className="legend">
            <span><i className="legend-dot service" />Service</span><span><i className="legend-dot person" />Person</span><span><i className="legend-dot incident" />Incident</span><span><i className="legend-dot change" />Change / PR</span><span><i className="legend-diamond" />Reasoning step</span>
          </div>
        </div>

        <aside className="reasoning-panel panel">
          <div className="panel-head"><div><span className="section-index">02 / AGENT WORKING MEMORY</span><h2>Reasoning trace <span className="step-count">{steps.length.toString().padStart(2, "0")}</span></h2></div><span className={`run-indicator ${busy ? "running" : finalData ? "complete" : "idle"}`}>{busy ? "RUNNING" : finalData ? "COMPLETE" : "READY"}</span></div>
          <div className="status-strip"><div className="status-bullet" />{notice || "Awaiting a trace query"}</div>
          <div className="reasoning-scroll">
            {steps.length === 0 && !busy && <div className="empty-trace"><div className="empty-icon">⌁</div><div className="empty-title">No active trace</div><p>Run a query to watch Trace traverse dependencies, correlate incidents, and find ownership conflicts.</p><div className="empty-path"><span>RESOLVE</span><b>→</b><span>IMPACT</span><b>→</b><span>CONFLICT</span></div></div>}
            {steps.map((step, index) => <article className="step-card" key={`${step.hop}-${step.tool}`}>
              <div className="step-rail"><div className={`step-node ${step.tool === "find_owners" && (step.conflicts?.length || 0) ? "rail-conflict" : ""}`}>{String(step.hop).padStart(2, "0")}</div>{index < steps.length - 1 && <div className="step-line" />}</div>
              <div className="step-content"><div className="step-title-row"><span className="step-tool">{step.tool}</span>{step.tool === "find_owners" && (step.conflicts?.length || 0) > 0 && <span className="conflict-chip">CONFLICT</span>}</div><p>{step.narrative}</p>
                {step.tool === "get_impact_radius" && <div className="step-metrics"><div><b>{String(step.result?.total ?? 0).padStart(2, "0")}</b><span>DEPENDENTS</span></div><div className="metric-p0"><b>{String(step.result?.p0_count ?? 0).padStart(2, "0")}</b><span>P0 SERVICES</span></div></div>}
                {step.tool === "find_open_incidents" && <div className="inline-tags">{((step.result?.open_incidents as any[]) || []).map(i => <span className="incident-tag" key={i.id}>{i.id} · {i.service}</span>)}</div>}
                {step.tool === "find_owners" && <div className="inline-tags">{((step.result?.conflicts as any[]) || []).map(c => <span className="conflict-tag" key={String(c.person)}>⚠ {String(c.person)}</span>)}</div>}
                {step.tool === "find_experts" && <div className="inline-tags">{((step.result?.available_experts as any[]) || []).slice(0, 3).map(e => <span className="expert-tag" key={e.name}>✳ {e.name}</span>)}</div>}
              </div>
            </article>)}
            {busy && <div className="streaming-row"><span className="typing-dots"><i /><i /><i /></span><span>Traversing connected data…</span></div>}
            {error && <div className="inline-error"><b>TRACE ERROR</b><p>{error}</p></div>}
            {finalData && <div className="final-card"><div className="final-label"><span>✓</span> TRACE SYNTHESIS</div><div className="final-copy">{finalData.text}</div><div className="final-foot"><span>SESSION {finalData.session_id}</span><span>{finalData.agent_mode || "AGENT"}</span></div></div>}
          </div>
        </aside>
      </section>

      <section className="action-panel panel">
        <div className="action-intro"><div className="action-symbol">↗</div><div><span className="section-index">03 / CONFIRMED SIDE EFFECTS</span><h2>Take action</h2><p>Actions stay locked until the trace finishes. Review the findings, then confirm each external side effect.</p></div></div>
        <div className="action-controls">
          {(finalData?.actions || []).length === 0 && <div className="action-locked"><span>⌑</span><div><b>Actions locked</b><small>Complete a resolvable trace to prepare evidence-backed actions.</small></div></div>}
          {(finalData?.actions || []).map(action => <div className={`action-item ${pendingAction?.id === action.id ? "selected" : ""}`} key={action.id}>
            {pendingAction?.id === action.id ? <><div className="action-text"><b>Confirm {action.type === "slack_notify" ? "Slack notification" : "GitHub issue"}?</b><small>This will contact an external service using your configured credentials.</small><small className="action-preview">{action.type === "slack_notify" ? (action.message || "No message content") : `Title: ${action.title}\n${action.body || "No issue body"}`}</small></div><button className="confirm-button" disabled={actionBusy} onClick={() => void sendAction(action)}>{actionBusy ? "SENDING…" : "CONFIRM & SEND"}</button><button className="cancel-button" disabled={actionBusy} onClick={() => setPendingAction(null)}>CANCEL</button></> : <><div className="action-logo">{action.type === "slack_notify" ? "S" : "GH"}</div><div className="action-text"><b>{action.label}</b><small>{action.type === "slack_notify" ? "Real incoming webhook" : "Real GitHub Issues API"}</small></div><button className="outline-button" disabled={busy || !finalData || actionBusy} onClick={() => { setPendingAction(action); setActionResult(null); }}>REVIEW <span>→</span></button></>}
          </div>)}
          {actionResult && <div className={`action-result ${actionResult.ok ? "success" : "failed"}`}><span>{actionResult.ok ? "✓" : "!"}</span>{actionResult.text}</div>}
        </div>
      </section>

      <footer className="footer"><div>◈ TRACE <span>·</span> ENGINEERING DECISION MEMORY</div><div>GRAPH IS THE MEMORY. PATH IS THE PROOF.</div><div>TRACK 01 / AGENTS THAT ACT ON CONNECTED DATA</div></footer>
    </main>
  );
}
