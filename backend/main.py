from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from agent import active_provider, run_trace
from graph_store import store
from integrations import ActionRequest, execute_confirmed_action

@asynccontextmanager
async def lifespan(app: FastAPI):
    store.initialize_schema()
    counts = store.seed()
    app.state.seed_counts = counts
    yield

app = FastAPI(title="Trace API", version="1.0.0", description="Engineering decision memory agent over connected operational data", lifespan=lifespan)
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type", "Authorization"], allow_credentials=False)

class TraceRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    @field_validator("query")
    @classmethod
    def trim_query(cls, value: str) -> str:
        value = value.strip()
        if not value: raise ValueError("query cannot be blank")
        return value

@app.get("/health")
async def health():
    graph_ok = False
    try: graph_ok = bool(store.health())
    except Exception: graph_ok = False
    return {"ok": graph_ok, "graph_backend": store.backend_name, "graph_connected": graph_ok,
            "llm_provider": active_provider(), "seed_counts": getattr(app.state, "seed_counts", None)}

@app.post("/trace")
async def trace(request: TraceRequest):
    session_id = uuid.uuid4().hex[:10]
    async def stream() -> AsyncIterator[str]:
        try:
            async for event in run_trace(request.query, session_id):
                yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False, default=str)}\n\n"
                await asyncio.sleep(0.03)
            yield f"event: done\ndata: {json.dumps({'session_id': session_id})}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Keep internal details server-side; provide a useful, bounded message to the client.
            message = str(exc)[:300] or "Trace failed unexpectedly"
            yield f"event: error\ndata: {json.dumps({'message': message, 'session_id': session_id})}\n\n"
            yield f"event: done\ndata: {json.dumps({'session_id': session_id, 'error': True})}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream; charset=utf-8", headers={
        "X-Session-Id": session_id, "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

@app.get("/graph/nodes")
async def graph_nodes():
    try: return store.graph_data(include_working_memory=False)
    except Exception as exc: raise HTTPException(503, f"Graph query failed: {str(exc)[:200]}") from exc

@app.get("/replay/{session_id}")
async def replay(session_id: str):
    if not session_id or len(session_id) > 80: raise HTTPException(400, "Invalid session ID")
    try: return store.replay(session_id)
    except Exception as exc: raise HTTPException(503, f"Replay query failed: {str(exc)[:200]}") from exc

@app.post("/action")
async def action(request: ActionRequest):
    if not request.confirmed:
        raise HTTPException(409, "Action not executed. Explicit user confirmation is required.")
    try:
        return await execute_confirmed_action(request)
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        # Missing credentials (503) and rejected external requests (502) are never false successes.
        message = str(exc)
        status = 503 if "not configured" in message.lower() else 502
        raise HTTPException(status, message) from exc
    except Exception as exc:
        raise HTTPException(502, f"Action failed; no success is claimed ({type(exc).__name__}).") from exc

@app.get("/")
async def root():
    return {"service": "Trace API", "docs": "/docs", "health": "/health"}
