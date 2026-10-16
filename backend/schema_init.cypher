// FalkorDB indexes. Re-running these statements may report an already-existing index;
// graph_store.py treats that specific condition as idempotent.
CREATE INDEX idx_service_name FOR (s:Service) ON (s.name);
CREATE INDEX idx_person_name FOR (p:Person) ON (p.name);
CREATE INDEX idx_incident_id FOR (i:Incident) ON (i.id);
CREATE INDEX idx_change_id FOR (c:Change) ON (c.id);
CREATE INDEX idx_runbook_id FOR (r:Runbook) ON (r.id);
CREATE INDEX idx_session_id FOR (s:Session) ON (s.id);

// Main traversals are parameterized in graph_store.py. This is the impact query template:
MATCH path=(dep:Service)-[:DEPENDS_ON*1..4]->(start:Service {name:$name})
WITH dep, length(path) AS hops,
     reduce(score=1.0, rel IN relationships(path) | score * coalesce(rel.strength, 1.0)) AS strength,
     [n IN nodes(path) | n.name] AS path_nodes
WHERE strength > 0.2
RETURN dep.name, dep.criticality, hops, strength, dep.tier, path_nodes
ORDER BY hops ASC, strength DESC;

// Ownership conflict query template:
MATCH (s:Service)-[:OWNED_BY]->(p:Person)
WHERE s.name IN $names
OPTIONAL MATCH (p)-[:ASSIGNED_TO]->(active:Incident {status:'open'})
WITH s,p,collect(DISTINCT active.id) AS active_ids
RETURN s.name,p.name,p.slack_handle,active_ids
ORDER BY size(active_ids) DESC;
