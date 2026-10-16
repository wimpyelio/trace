# Execution notes and requirements rubric

## Baseline inventory

- Workspace contained the two supplied Markdown/text files only; there was no source repository, Git history, remote, or existing application to preserve.
- Available runtimes: Python 3.13.5, Node.js 22.16.0, npm 10.9.2.
- Docker CLI was not installed. No FalkorDB, Redis, API server, or Next.js process was running.
- No LLM, Slack, GitHub, Railway, Vercel, or FalkorDB credentials were present in the environment (presence checked without printing values).
- Existing Python environment had FastAPI, Uvicorn, Pydantic 2, HTTPX, and pytest. FalkorDB's Python client was not installed at inspection time.

## Architecture decisions

- Preserve the Project Bible's graph schema, FinTrack entity identities, dependency direction, and PR-445 conflict story.
- A `MemoryGraphStore` runs the same graph-backed tools offline to support deterministic local development and tests. It is labelled `memory-seeded` and is not described as FalkorDB or live production data.
- `FalkorGraphStore` is a separate selectable adapter; Docker Compose chooses it and local default chooses memory. Failure to connect in FalkorDB mode is not silently converted into memory mode.
- LLM selection: Groq OpenAI-compatible tool use (default model `openai/gpt-oss-20b`); Gemini OpenAI-compatible tool use fallback (default `gemini-3.8-flash`); explicit offline deterministic tool loop when neither API key is configured. Auto mode falls back from Groq request failures to Gemini only when a Gemini key exists.
- Actions are exclusively executed by the confirmation endpoint. Model tool calls named `execute_action` only record a deferred proposal.
- Next.js 14.2.35 is retained to match the supplied architecture. It is out of upstream support as of the current verification date; do not expose an internet-facing deployment before upgrading to a supported Next.js version and re-running the build/security review.

## Rubric ledger

| # | Rubric | Status in this workspace | Evidence / remaining verification |
|---|---|---|---|
| 1 | Graph integrity | Partial / local memory verified | Seed adapter includes all 7 labels and specified relationship kinds; 38 seed nodes and 75 relationships. Live FalkorDB query unverified because Docker/service unavailable. |
| 2 | Target finding | Verified against seed graph | Backend tests assert PR-445 → auth-service, 8 dependents, active INC-004 / INC-005, Diego/Yuki conflicts, and Ana availability. Seed data yields 3 P0 dependents, not the Bible's stated 2. |
| 3 | Agent behavior | Offline loop verified; LLM unverified | Typed dispatch, node-backed reasoning steps and ordered replay are tested. No provider credentials available. |
| 4 | SSE | Backend and parser verified | Five-step local trace; incremental JS parser tests split chunks, multiple events, CRLF, multiline data and EOF. Browser runtime not exercised here. |
| 5 | Actions | Unit paths verified; external APIs unverified | Confirmation rejection, missing-credential behavior, Slack success/failure, GitHub valid/malformed response and timeout cases tested with mocked HTTP. No real webhook/token present. |
| 6 | Graph UI | Implemented; syntax parsed, build/runtime pending | Next.js page loads `/graph/nodes`, uses vis-network, processes SSE, adds reasoning diamonds, highlights conflicts/experts and previews action payloads before confirmation. TypeScript/JSX syntax transpilation passed; full typecheck, build and browser rendering remain unverified because dependencies could not be installed. |
| 7 | API/security | Core tests passed | Input bounds, limited CORS defaults, bounded errors, server-side keys, confirmation gate implemented. Live CORS/deployment config remains to be set for a real hosted domain. |
| 8 | Reproducibility | Files created | Requirements, `.env.example`, Dockerfiles, Compose, schema and seed script present. Docker cold-start unverified because Docker CLI is absent. |
| 9 | Free-stack feasibility | Configurable; not account-verified | Groq/Gemini model IDs checked against current provider docs; account quotas, free access, and billing requirements not tested. |
| 10 | Demo readiness | Backend demo verified | `PR-445 just merged` completes in the local memory adapter; browser animation and live integrations unverified. |
| 11 | Submission compliance | Partial | README, architecture diagram, Cypher notes, track declaration, AI-use disclosure and recording checklist included. No repository remote, public URL, or demo recording created. |
| 12 | Evidence | Local checks documented | See [`TEST_EVIDENCE.md`](TEST_EVIDENCE.md) for actual test output, local HTTP smoke checks and exact unverified dependencies. Cloud, FalkorDB, Next build and real external-action claims remain unverified. |

## Source-data discrepancies and corrections

1. The Bible says 10 `Change` nodes but its displayed seed list has 6. Four historical nodes were added: PR-439 (audit logging), DEPLOY-87 (rate policy), PR-438 (billing lookup), and PR-436 (notification idempotency). These are schema-consistent and do not alter PR-445's target path.
2. The Bible claims 2 P0 services in the impact set, but its own seed marks payment-gateway, fraud-detector, and billing-core as P0 and directly includes `billing-core -> auth-service`. The computed result therefore has 3 P0 dependents. Seed labels and edges were preserved instead of suppressing a valid P0 node.
3. The Bible's `resolve_entity` approach only searches the full query against a node's name/ID. It is corrected to recognize an ID within natural query text such as `PR-445 just merged`.
4. Action success responses now check actual HTTP outcomes. Missing credentials and provider failures never return success.


## Final verification pass

- `python -m py_compile backend/*.py`: passed.
- `PYTHONPATH=backend pytest -q backend/tests`: 13 passed.
- `node --test frontend/src/lib/sse-parser.test.mjs`: 3 passed.
- TypeScript JSX syntax transpilation: passed for the page and layout.
- `tsc --noEmit` was attempted but could not typecheck without the missing frontend dependency tree; the npm install attempt timed out.
- Local Uvicorn smoke checks verified `/health`, `/graph/nodes`, `/trace`, `/replay/{session_id}`, and unconfirmed-action rejection. Exact observed results are in `TEST_EVIDENCE.md`.
- No Docker/FalkorDB, external provider, real Slack/GitHub action, or deployment was verified.
