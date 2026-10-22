"""Independent checks run before a recommendation reaches a human approver."""
from __future__ import annotations

from .models import Assessment, Verdict, VerificationCheck, norm_text


def verify(recommendation: Assessment | None, assessments: list[Assessment]) -> list[VerificationCheck]:
    checks: list[VerificationCheck] = []
    if recommendation is None:
        checks.append(VerificationCheck(
            "A recommendation exists", False,
            "No quotation passes every mandatory criterion without open flags."))
        return checks

    failing = [c.label for c in recommendation.checks if c.status != "PASS"]
    checks.append(VerificationCheck(
        "Recommended quote satisfies every mandatory criterion", not failing,
        "All mandatory criteria are proven by the quotation." if not failing
        else "Not proven: " + ", ".join(failing)))

    candidates = [a for a in assessments if a.verdict is Verdict.PASS and not a.flags]
    best = max(candidates, key=lambda a: a.score or 0)
    checks.append(VerificationCheck(
        "Recommendation is the highest-scoring eligible quote", best is recommendation,
        f"{recommendation.vendor} scores {recommendation.score}; best eligible is {best.vendor} at {best.score}."))

    ungrounded = []
    for a in assessments:
        doc = norm_text(a.quote.text)
        for key, snippet in a.quote.evidence.items():
            if norm_text(snippet) not in doc:
                ungrounded.append(f"{a.vendor} ({key})")
    checks.append(VerificationCheck(
        "Every extracted value is traceable to its source text", not ungrounded,
        "All evidence snippets were found in the source documents." if not ungrounded
        else "Untraceable: " + ", ".join(ungrounded)))

    checks.append(VerificationCheck(
        "No open anomaly on the recommended quote", not recommendation.flags,
        "No anomaly flags." if not recommendation.flags else "; ".join(recommendation.flags)))

    checks.append(VerificationCheck(
        "Consequential action requires human approval", True,
        "Nothing is sent to a vendor until a named approver signs off."))
    return checks
