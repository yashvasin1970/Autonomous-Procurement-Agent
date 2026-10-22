"""Render a run as a Markdown evidence pack and as a JSON run record."""
from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum

from .agent import STATUS_LABELS, RunResult
from .constraints import fmt

TAG = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "OPEN"}


def render_markdown(run: RunResult) -> str:
    req = run.requirement
    out = [
        f"# Procurement recommendation (run {run.run_id})",
        "",
        f"*Generated {run.created_at}*",
        "",
        f"**Status:** {STATUS_LABELS.get(run.status, run.status)}",
        "",
        "## Purchase request",
        "",
        req.text.strip(),
        "",
        "## Mandatory criteria",
        "",
    ]
    out += [f"- {c.label}" for c in req.criteria] or ["- None"]
    out += ["", "## Ranked quotations", "", "| # | Vendor | Verdict | Score | Unit price |", "|---|---|---|---|---|"]
    for i, a in enumerate(run.assessments, 1):
        score = f"{a.score:.1f}" if a.score is not None else "-"
        out.append(f"| {i} | {a.vendor} | {a.verdict.value} | {score} | {fmt('unit_price', a.quote.values.get('unit_price'))} |")

    out += ["", "## Evidence", ""]
    for a in run.assessments:
        out += [f"### {a.vendor} - {a.verdict.value}", ""]
        for c in a.checks:
            out.append(f"- **{TAG[c.status]}** {c.reason}")
            snippet = a.quote.evidence.get(c.key)
            if snippet:
                out.append(f"  - Source: \"{snippet}\"")
        for flag in a.flags:
            out.append(f"- **ANOMALY** {flag}")
        out.append("")

    out += ["## Recommendation", ""]
    if run.recommendation is not None:
        r = run.recommendation
        out.append(f"**{r.vendor}**, score {r.score}/100.")
        out += [f"- {n}" for n in r.notes]
    else:
        out.append("No quotation is recommended yet.")

    out += ["", "## Verification", ""]
    out += [f"- **{'PASS' if v.passed else 'FAIL'}** {v.name}: {v.detail}" for v in run.verification]

    out += ["", "## Open items", ""]
    out += [f"- {c}" for c in run.clarifications] or ["- None"]

    out += ["", "## Execution trace", ""]
    out += [f"- [{s.status}] **{s.stage}** {s.title}" + (f": {s.detail}" if s.detail else "") for s in run.trace]
    return "\n".join(out) + "\n"


def _json_default(obj):
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)


def run_to_json(run: RunResult) -> str:
    return json.dumps(asdict(run), indent=2, ensure_ascii=False, default=_json_default)
