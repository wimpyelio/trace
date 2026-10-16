# Trace execution evidence

**Evidence date:** 2026-10-15  
**Scope:** source implementation and local, memory-backed demo. This is not evidence of a live hosted deployment.

## Commands run

| Command / check | Result | What it establishes |
|---|---|---|
| `python -m py_compile backend/*.py` | PASS | Backend Python files compile. |
| `PYTHONPATH=backend pytest -q backend/tests` | PASS — 13 tests | Seed repeatability, graph path logic, target incident/owner/expert findings, ordered reasoning replay, input validation, API/SSE behavior, provider fallback handling, confirmation gate and mocked integration success/failure paths. |
| `node --test frontend/src/lib/sse-parser.test.mjs` | PASS — 3 tests | SSE parser handles arbitrary chunk splits, multi-line data, CRLF/comments, multiple events and end-of-stream flush. |
| `python backend/seed.py` | PASS | Seed output contains 12 services, 8 people, 5 incidents, 10 changes, 3 runbooks and 75 initial graph relationships in memory mode. The computed PR-445 impact contains 8 dependent services and 3 P0s. |
| `ts.transpileModule` on `frontend/app/page.tsx` and `frontend/app/layout.tsx` | PASS — syntax transpilation | TypeScript/JSX syntax parses. This is not a full semantic typecheck or Next.js build. |
| `tsc --noEmit --pretty false` in frontend | BLOCKED | The command cannot typecheck because React, Next.js, vis-data, vis-network and their type dependencies are absent; npm install did not complete in this environment. |
| `npm install --no-audit --no-fund` | BLOCKED / timed out | Frontend dependencies were not installed, so `next build`, browser rendering and visual QA remain unverified. |
| `docker compose up --build` | NOT RUN | The Docker CLI is absent; live FalkorDB adapter and cold-start behavior remain unverified. |

## Local HTTP smoke test

A temporary Uvicorn server was started on `127.0.0.1:8765` using the memory adapter and then stopped.

- `GET /health`: HTTP 200; graph backend `memory-seeded`; graph connected; provider `offline`.
- `GET /graph/nodes`: HTTP 200; 38 base graph nodes and 75 edges.
- `POST /trace` with `PR-445 just merged`: HTTP 200, `text/event-stream`; events were `start`, five `step` events, `final`, `done`.
- Final trace data: 8 dependents, 3 P0s, open incidents `INC-004` and `INC-005`, ownership conflicts for Diego Reyes and Yuki Tanaka, Ana Kovač as the top available expert, and two action proposals.
- `GET /replay/{session_id}`: HTTP 200; five persisted steps returned in order.
- `POST /action` with `confirmed: false`: HTTP 409 with an explicit “Action not executed” response.

## External dependencies not verified

No external provider/action/deployment credentials were present. Therefore no real Groq or Gemini request, Slack delivery, GitHub issue creation, FalkorDB instance, or cloud deployment was performed. Integration tests use mocked HTTP responses and prove response handling only. No URL, message delivery, issue, screenshot, or recording is claimed to exist.

## Source-data reconciliation

The supplied Bible's displayed changes array contains six entries despite stating a target of ten; four explicitly documented historical changes were added to meet the target. The source seed's `payment-gateway`, `fraud-detector`, and `billing-core` services are all P0 and depend on `auth-service`, so graph-derived impact has three P0 dependents, not the prose's claimed two. The graph output is left faithful to the listed seed relationships.
