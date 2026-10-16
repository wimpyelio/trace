"""Initialize schema and idempotently seed the configured graph backend."""
import json
from graph_store import store

if __name__ == "__main__":
    store.initialize_schema()
    counts = store.seed()
    print(json.dumps({"graph_backend": store.backend_name, "counts": counts}, indent=2, ensure_ascii=False))
    target = store.impact_radius("auth-service", 4)
    print(json.dumps({"demo_target": "PR-445 -> auth-service", "impact_total": target["total"], "p0_count": target["p0_count"],
                      "dependents": target["dependents"]}, indent=2, ensure_ascii=False))
