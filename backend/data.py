"""Canonical FinTrack demo dataset from the Trace Project Bible, plus 4 documented historical changes."""
from __future__ import annotations

SERVICES = [
    ("payment-gateway", "P0", "backend", "Python"),
    ("auth-service", "P0", "backend", "Go"),
    ("fraud-detector", "P0", "backend", "Python"),
    ("billing-core", "P0", "data", "Java"),
    ("ledger-service", "P0", "data", "Java"),
    ("user-service", "P1", "backend", "Python"),
    ("notification-svc", "P1", "backend", "Node"),
    ("reporting-api", "P1", "data", "Python"),
    ("webhook-dispatcher", "P1", "backend", "Node"),
    ("email-service", "P2", "backend", "Node"),
    ("rate-limiter", "P1", "infra", "Go"),
    ("audit-log", "P1", "infra", "Go"),
]

PEOPLE = [
    ("Ana Kovač", "backend-lead", "@ana", "payments"),
    ("Marcus Tella", "backend", "@marcus", "identity"),
    ("Diego Reyes", "platform-lead", "@diego", "platform"),
    ("James Okafor", "data-eng", "@james", "data"),
    ("Yuki Tanaka", "fullstack", "@yuki", "comms"),
    ("Priya Shen", "sre", "@priya", "platform"),
    ("Lara Mbuyi", "backend", "@lara", "identity"),
    ("Tom Bradvik", "infra", "@tom", "platform"),
]

OWNERSHIP = [
    ("payment-gateway", "Ana Kovač"),
    ("fraud-detector", "Ana Kovač"),
    ("auth-service", "Marcus Tella"),
    ("user-service", "Lara Mbuyi"),
    ("billing-core", "Diego Reyes"),
    ("webhook-dispatcher", "Diego Reyes"),
    ("ledger-service", "James Okafor"),
    ("reporting-api", "James Okafor"),
    ("notification-svc", "Yuki Tanaka"),
    ("email-service", "Yuki Tanaka"),
    ("rate-limiter", "Priya Shen"),
    ("audit-log", "Tom Bradvik"),
]

EXPERTISE = [(person, service, "owner") for service, person in OWNERSHIP]
EXPERTISE += [("Ana Kovač", "billing-core", "contributor")]

# DEPENDS_ON is intentionally dependent -> provider, matching the Bible.
DEPENDENCIES = [
    ("payment-gateway", "auth-service", 0.95, "sync"),
    ("payment-gateway", "fraud-detector", 0.90, "sync"),
    ("payment-gateway", "rate-limiter", 0.70, "sync"),
    ("fraud-detector", "auth-service", 0.80, "sync"),
    ("billing-core", "auth-service", 0.85, "sync"),
    ("billing-core", "ledger-service", 0.95, "sync"),
    ("user-service", "auth-service", 0.90, "sync"),
    ("notification-svc", "user-service", 0.80, "async"),
    ("notification-svc", "email-service", 0.60, "async"),
    ("webhook-dispatcher", "payment-gateway", 0.70, "async"),
    ("webhook-dispatcher", "audit-log", 0.50, "async"),
    ("reporting-api", "billing-core", 0.75, "data"),
    ("reporting-api", "ledger-service", 0.80, "data"),
    ("email-service", "rate-limiter", 0.60, "sync"),
    ("audit-log", "auth-service", 0.65, "sync"),
]

# Times are relative to the seeding moment so the demo stays meaningful on any date.
INCIDENTS = [
    ("INC-001", "P1", "closed", -14 * 86400, -13 * 86400, "auth-service", "Token refresh bug after deploy"),
    ("INC-002", "P0", "closed", -30 * 86400, -29 * 86400, "payment-gateway", "Timeout cascade from rate-limiter misconfiguration"),
    ("INC-003", "P1", "closed", -3 * 86400, -2 * 86400, "fraud-detector", "False-positive storm after model v2.3 deploy"),
    ("INC-004", "P1", "open", -2 * 3600, None, "notification-svc", "Delivery failures — email-service latency spike"),
    ("INC-005", "P1", "open", -1 * 86400, None, "billing-core", "Schema migration v4.1 causing slow queries"),
]

# First six changes and their relationships are from the Bible. Four additional
# historical changes resolve the Bible's stated-10 / listed-6 discrepancy without
# changing the PR-445 demo path.
CHANGES = [
    ("PR-445", "pr", "auth-service: tighten token expiry validation", -1800, "high", "Marcus Tella", ["auth-service"]),
    ("PR-442", "pr", "rate-limiter: increase burst threshold for EU region", -1 * 86400, "medium", "Tom Bradvik", ["rate-limiter"]),
    ("DEPLOY-89", "deploy", "fraud-detector: updated scoring model v2.3", -3 * 86400, "high", "Ana Kovač", ["fraud-detector"]),
    ("PR-441", "pr", "payment-gateway: adjust retry logic for timeouts", -2 * 86400, "medium", "Ana Kovač", ["payment-gateway"]),
    ("DEPLOY-88", "deploy", "billing-core: schema migration v4.1", -1 * 86400, "high", "Diego Reyes", ["billing-core", "ledger-service"]),
    ("PR-440", "pr", "user-service: migrate session tokens to JWT", -5 * 86400, "high", "Lara Mbuyi", ["user-service"]),
    ("PR-439", "pr", "audit-log: add request correlation fields", -7 * 86400, "medium", "Tom Bradvik", ["audit-log"]),
    ("DEPLOY-87", "deploy", "rate-limiter: roll out per-region quota policy", -8 * 86400, "medium", "Priya Shen", ["rate-limiter"]),
    ("PR-438", "pr", "billing-core: add batched invoice lookup", -9 * 86400, "low", "James Okafor", ["billing-core"]),
    ("PR-436", "pr", "notification-svc: add retry idempotency guard", -12 * 86400, "medium", "Yuki Tanaka", ["notification-svc"]),
]

CAUSAL_LINKS = [("INC-005", "DEPLOY-88", 0.85)]
RESOLUTIONS = [("INC-001", "Marcus Tella"), ("INC-002", "Ana Kovač"), ("INC-003", "Ana Kovač")]
ASSIGNMENTS = [("Diego Reyes", "INC-005"), ("Yuki Tanaka", "INC-004")]
RUNBOOKS = [
    ("RB-01", "Payment Gateway — Incident Runbook", "payment-gateway", "https://wiki.fintrack.io/RB-01"),
    ("RB-02", "Auth Service — On-Call Guide", "auth-service", "https://wiki.fintrack.io/RB-02"),
    ("RB-03", "Billing Core — Migration Checklist", "billing-core", "https://wiki.fintrack.io/RB-03"),
]

EXPECTED_COUNTS = {"Service": 12, "Person": 8, "Incident": 5, "Change": 10, "Runbook": 3}
