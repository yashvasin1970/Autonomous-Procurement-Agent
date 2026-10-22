"""The agent loop: plan -> read -> normalise -> constrain -> compare -> verify -> recommend -> approve.

Every stage writes a TraceStep, so the reasoning behind a recommendation is visible and auditable.
Failures are recovered from where possible (an unreadable file becomes a gap, a missing value becomes
a clarification) and are never silently filled in with guesses.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .constraints import evaluate, fmt, verdict_for
from .extractor import extract_quote
from .ingest import read_document
from .models import SPEC_KEYS, Assessment, Requirement, Verdict, VerificationCheck
from .requirements_parser import parse_requirement
from .scoring import detect_anomalies, rank, score_passing
from .verify import verify

STATUS_LABELS = {
    "AWAITING_APPROVAL": "Recommendation ready, awaiting human approval",
    "NEEDS_CLARIFICATION": "Paused: clarification needed before a recommendation",
    "NO_COMPLIANT_QUOTE": "No quotation satisfies every mandatory requirement",
}


@dataclass
class TraceStep:
    stage: str
    title: str
    detail: str = ""
    status: str = "ok"          # ok | warn | error


@dataclass
class RunResult:
    run_id: str
    created_at: str
    requirement: Requirement
    assessments: list[Assessment]
    recommendation: Optional[Assessment]
    verification: list[VerificationCheck]
    clarifications: list[str]
    gaps: list[str]
    trace: list[TraceStep]
    status: str
    draft_message: str = ""


def _draft_message(rec: Assessment, req: Requirement) -> str:
    qty = f"{req.quantity} units" if req.quantity else "the requested items"
    price = fmt("unit_price", rec.quote.values.get("unit_price"))
    return (
        f"Subject: Quotation under review - {qty}\n\n"
        f"Dear {rec.vendor},\n\n"
        f"Thank you for your quotation. Your offer for {qty} at {price} per unit is under review. "
        "Before a purchase order is issued, please confirm in writing the stock availability, "
        "delivery timeline and warranty terms.\n\nRegards,\nProcurement Team"
    )


def run_procurement(requirement_text: str, documents: list[tuple[str, bytes]], use_llm: bool = True) -> RunResult:
    run_id = uuid.uuid4().hex[:8]
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    trace: list[TraceStep] = []

    def step(stage: str, title: str, detail: str = "", status: str = "ok") -> None:
        trace.append(TraceStep(stage, title, detail, status))

    # 1. INPUT and PLAN
    step("INPUT", "Purchase request received",
         f"{len(requirement_text.split())} words, {len(documents)} quotation document(s).")
    requirement = parse_requirement(requirement_text)
    if not requirement.criteria:
        step("PLAN", "No measurable criteria found", "Nothing to check quotations against.", "error")
        return RunResult(
            run_id, created, requirement, [], None, [],
            ["The request has no measurable criteria (processor, RAM, storage, budget, warranty or delivery). "
             "Please restate it with specific figures."],
            [], trace, "NEEDS_CLARIFICATION")
    step("PLAN", "Mandatory criteria extracted", "; ".join(c.label for c in requirement.criteria))

    # 2. TOOL USE: read and extract each quotation
    quotes, gaps = [], []
    for name, data in documents:
        try:
            text = read_document(name, data)
        except Exception as exc:
            gaps.append(f"{name} could not be read ({exc}). Please resend it as PDF, XLSX or TXT.")
            step("TOOL_USE", f"Could not read {name}", str(exc), "warn")
            continue
        if not text.strip():
            gaps.append(f"{name} has no extractable text (it may be a scanned image). Please resend a text-based file.")
            step("TOOL_USE", f"No text in {name}", "Possibly a scanned image.", "warn")
            continue
        quote = extract_quote(name, text, use_llm=use_llm)
        quotes.append(quote)
        found = sum(quote.values.get(k) is not None for k in SPEC_KEYS)
        step("TOOL_USE", f"Read {name}",
             f"Vendor: {quote.vendor}; {found}/{len(SPEC_KEYS)} fields found; method: {quote.method}")
        for note in quote.notes:
            step("RECOVER", f"{quote.vendor}: recovery", note, "warn")

    # 3. NORMALIZE
    for q in quotes:
        missing = [k for k in SPEC_KEYS if q.values.get(k) is None]
        step("NORMALIZE", f"{q.vendor}: mapped to common schema",
             ("Missing: " + ", ".join(missing)) if missing else "All fields mapped.",
             "warn" if missing else "ok")

    # 4. CONSTRAINT: check mandatory rules first
    assessments: list[Assessment] = []
    for q in quotes:
        checks = evaluate(q, requirement)
        a = Assessment(quote=q, checks=checks, verdict=verdict_for(checks))
        assessments.append(a)
        detail = " ".join(c.reason for c in checks if c.status != "PASS") or "Meets every mandatory criterion."
        status = {Verdict.PASS: "ok", Verdict.NEEDS_REVIEW: "warn", Verdict.FAIL: "error"}[a.verdict]
        step("CONSTRAINT", f"{a.vendor}: {a.verdict.value}", detail, status)

    # 5. COMPARE: only compliant quotes are scored against each other
    detect_anomalies(assessments)
    score_passing(assessments)
    passing = [a for a in assessments if a.verdict is Verdict.PASS]
    step("COMPARE", "Scored compliant quotes",
         f"{len(passing)} of {len(assessments)} quotation(s) pass every mandatory criterion.")
    for a in assessments:
        for flag in a.flags:
            step("COMPARE", f"Anomaly flagged: {a.vendor}", flag, "warn")

    ranked = rank(assessments)
    candidates = [a for a in assessments if a.verdict is Verdict.PASS and not a.flags]
    recommendation = max(candidates, key=lambda a: a.score or 0) if candidates else None

    # 6. RECOVER: escalate gaps instead of guessing
    clarifications = list(gaps)
    for a in assessments:
        for c in a.unknown_checks:
            clarifications.append(f"{a.vendor}: please confirm '{c.label}', which is not stated in the quotation.")
        for flag in a.flags:
            clarifications.append(f"{a.vendor}: {flag}")
    if clarifications:
        step("RECOVER", f"{len(clarifications)} open item(s) escalated",
             "Values the documents do not state are not invented; a person must resolve them.", "warn")

    # 7. VERIFY: independent checks before anything reaches a human
    verification = verify(recommendation, assessments)
    for v in verification:
        step("VERIFY", v.name, v.detail, "ok" if v.passed else "warn")

    # 8. RECOMMEND and ACTION (drafted, never sent)
    draft = ""
    if recommendation is not None:
        step("RECOMMEND", f"Recommend {recommendation.vendor}",
             f"Score {recommendation.score}/100. " + " ".join(recommendation.notes))
        draft = _draft_message(recommendation, requirement)
        step("ACTION", "Vendor message drafted (not sent)", "Held until a named approver signs off.")
        status = "AWAITING_APPROVAL" if all(v.passed for v in verification) else "NEEDS_CLARIFICATION"
    else:
        step("RECOMMEND", "No recommendation issued",
             "No quotation passes every check without open items.", "warn")
        status = "NEEDS_CLARIFICATION" if clarifications else "NO_COMPLIANT_QUOTE"

    # 9. APPROVE: the final action stays with a person
    step("APPROVAL", "Waiting for human approval" if status == "AWAITING_APPROVAL" else "Waiting for clarification",
         "No vendor action is taken automatically.")

    return RunResult(
        run_id=run_id,
        created_at=created,
        requirement=requirement,
        assessments=ranked,
        recommendation=recommendation,
        verification=verification,
        clarifications=clarifications,
        gaps=gaps,
        trace=trace,
        status=status,
        draft_message=draft,
    )
