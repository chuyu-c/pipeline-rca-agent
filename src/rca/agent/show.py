"""`rca show`: an incident and its stored RCA as a markdown report."""

import json
from typing import Any

from rca.db import Connection


def load_incident(
    conn: Connection, incident_id: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    incident = conn.execute(
        "SELECT incident_id, status, root_candidate, created_at, updated_at, rca, trace"
        " FROM ops.incidents WHERE incident_id = %s",
        (incident_id,),
    ).fetchone()
    if incident is None:
        raise LookupError(f"incident {incident_id} not found")
    alerts = conn.execute(
        "SELECT detector, dataset, error_class, severity, logical_date, count FROM ops.alerts"
        " WHERE incident_id = %s"
        " ORDER BY array_position(ARRAY['critical','high','medium','low'], severity), alert_id",
        (incident_id,),
    ).fetchall()
    return dict(incident), alerts


def _short(value: Any, limit: int = 90) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_incident(incident: dict[str, Any], alerts: list[dict[str, Any]]) -> str:
    rca: dict[str, Any] | None = incident.get("rca")
    trace: list[dict[str, Any]] = incident.get("trace") or []
    title = f"# Incident #{incident['incident_id']}"
    if rca:
        title += f": {rca['category']} at `{rca['root_entity']}`"
    out = [
        title,
        "",
        f"Status **{incident['status']}** · opened {incident['created_at']:%Y-%m-%d %H:%M} UTC · "
        f"root candidate `{incident['root_candidate']}`",
        "",
        "## Alerts",
        "",
        "| severity | detector | dataset | error | logical date | count |",
        "|---|---|---|---|---|---|",
        *(
            f"| {a['severity']} | {a['detector']} | `{a['dataset']}` | {a['error_class']} "
            f"| {a['logical_date']} | {a['count']} |"
            for a in alerts
        ),
    ]
    if not rca:
        return "\n".join([*out, "", "_Not triaged yet._", ""])

    usage = rca.get("usage") or {}
    triage = (
        f"Triage: **{rca['source']}** · model `{rca.get('model')}` · {rca.get('steps', 0)} steps · "
        f"{rca.get('tool_calls', 0)} tool calls · {usage.get('total', 0):,} tokens · "
        f"{rca.get('duration_s', 0)}s"
    )
    if rca.get("fallback_reason"):
        triage += f" · fallback reason: {rca['fallback_reason']}"
    out += [
        "",
        "## Root cause",
        "",
        triage,
        "",
        f"- **Category:** {rca['category']}",
        f"- **Root entity:** `{rca['root_entity']}`",
        f"- **Confidence:** {rca['confidence']:.2f}",
        "",
        rca["summary"],
    ]

    tool_names = {
        e["tool_call_id"]: e.get("name", "context")
        for e in trace
        if e.get("type") in {"tool", "context"}
    }
    out += ["", "## Evidence", ""]
    for ev in rca.get("evidence", []):
        tool = tool_names.get(ev["tool_call_id"], ev["tool_call_id"].split(":")[0])
        out.append(f"- `{tool}` ({ev['tool_call_id']}): {_short(ev['excerpt'], 200)}")

    if rca.get("citations"):
        out += ["", "## Knowledge cited", "", *(f"- `{c}`" for c in rca["citations"])]

    out += ["", "## Proposed actions (need human approval)", ""]
    actions = rca.get("proposed_actions") or []
    for action in actions:
        params = ", ".join(f"{k}={v}" for k, v in action.items() if k not in {"type", "rationale"})
        out.append(f"- **{action['type']}** {params}: {action['rationale']}")
    if not actions:
        out.append("- none")

    if rca.get("hypotheses_considered"):
        out += ["", "## Hypotheses considered", ""]
        out += [f"- {h}" for h in rca["hypotheses_considered"]]

    steps = [e for e in trace if e.get("type") in {"tool", "repair", "reminder", "fallback"}]
    if steps:
        out += ["", "## Investigation", "", "| step | action | detail |", "|---|---|---|"]
        for e in steps:
            if e["type"] == "tool":
                if e["ok"]:
                    status = "ok"
                elif e["blocked_reason"]:
                    status = f"blocked: {e['blocked_reason']}"
                else:
                    status = "error"
                detail = f"{_short(e['input'], 80)} → {status}"
                out.append(f"| {e['step']} | `{e['name']}` | {detail} |")
            else:
                detail = _short(e.get("problems") or e.get("reason") or "", 120)
                out.append(f"| {e.get('step', '')} | {e['type']} | {detail} |")
    return "\n".join(out) + "\n"
