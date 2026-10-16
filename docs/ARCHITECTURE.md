# Trace architecture

```mermaid
flowchart LR
    U[Browser · Next.js + vis-network] -- POST /trace + SSE --> A[FastAPI agent loop]
    U -- confirmed POST /action --> X[Slack webhook / GitHub Issues API]
    A -- parameterized Cypher --> G[(FalkorDB · graph trace)]
    A --> L{LLM provider configured?}
    L -->|Primary| Q[Groq tool calling]
    L -->|Fallback| M[Gemini tool calling]
    L -->|No credentials| O[Explicit offline deterministic tool loop]
    Q --> A
    M --> A
    O --> A
    A -- ReasoningStep nodes + findings --> G
```

## Trust boundaries

- The browser never receives model keys, a Slack webhook, a GitHub token, or graph credentials.
- A trace can propose actions, but it cannot execute a side effect. `POST /action` requires `confirmed: true` independently of the LLM.
- Offline mode is openly identified in `/health`; its conclusions are calculated by graph tools over the seeded memory adapter, not presented as LLM output or external live state.
- A live FalkorDB adapter uses bound parameters for values. The only interpolated Cypher fragments are fixed labels/relationship names and the validated integer hop limit (1–4).
- Compound path-strength is a ranking score formed by multiplying edge strengths, not a calibrated production incident probability.
