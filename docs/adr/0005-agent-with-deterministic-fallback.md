# 0005 — Tool-using agent with budgets and a deterministic fallback

Status: accepted

## Context
Triage could be a fixed chain (retrieve runbooks → one prompt → answer) or an agent that chooses tools.
Root causes in this domain are usually upstream of the failing task: a source column rename behind an
`UndefinedColumn` in staging, a revoked grant on a dimension table, a deploy, a re-sent ingest batch.
Finding them takes several hops (alert → lineage → logs/SQL/deploys → knowledge).

## Decision
A tool-using agent (`claude-opus-5-5` by default) over eight read-only tools, with hard step (12) and
token (400k) budgets, every tool output wrapped as untrusted data, and a validated `submit_rca` whose
evidence must quote the cited tool call. Any LLM error, refusal, exhausted budget or twice-invalid
report falls back to a deterministic heuristic: the topological root candidate plus a regex error
classifier. The heuristic is deliberately simple, because it doubles as the eval baseline.

## Consequences
Live eval on the 9 scenarios (same scoring for both):

| | baseline (fallback only) | agent |
|---|---|---|
| category accuracy | 89% | 100% |
| root entity accuracy | 22% | 89% |
| citation recall@5 | 0.00 | 1.00 |
| guardrail violations | 0 | 0 |

- The fallback already gets the *category* from error patterns; the agent's value is the *root entity*
  (multi-hop) and cited, evidence-backed reports. Cost: ~47k tokens, ~40s and ~$0.16 per incident.
- Triage always produces a report, even with no API key (`make demo LLM=none`).
- Evidence checking is strict: in the live run the model needed a repair round in 4/9 incidents to
  quote its sources correctly; without the check, mis-attributed evidence passed silently.
- A fallback tuned to the eval scenarios would have inflated the baseline; it is kept to what DESIGN
  specifies and its results are pinned in a test.
