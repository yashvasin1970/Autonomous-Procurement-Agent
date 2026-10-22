"""Deterministic compliance checks. A quotation passes only if every mandatory criterion is proven."""
from __future__ import annotations

import re
from typing import Any

from .models import CheckResult, Criterion, Quote, Requirement, Verdict

CPU_TIERS = {"i3": 1, "i5": 2, "ryzen5": 2, "i7": 3, "ryzen7": 3, "i9": 4, "ryzen9": 4}
OP_WORDS = {">=": "at least", "<=": "at most", "min_tier": "at least"}


def cpu_rank(cpu: Any) -> int | None:
    if not cpu:
        return None
    return CPU_TIERS.get(re.sub(r"\s+", "", str(cpu).lower()))


def fmt(key: str, value: Any) -> str:
    if value is None:
        return "not stated"
    if key == "unit_price":
        return f"₹{value:,.0f}"
    if key == "storage_gb":
        return f"{value / 1024:g} TB" if value >= 1024 and value % 1024 == 0 else f"{value:,.0f} GB"
    if key == "ram_gb":
        return f"{value:,.0f} GB"
    if key == "warranty_months":
        return f"{value:g} months"
    if key == "delivery_days":
        return f"{value:g} days"
    return str(value)


def check(criterion: Criterion, quote: Quote) -> CheckResult:
    actual = quote.values.get(criterion.key)
    label = criterion.label
    if actual is None:
        return CheckResult(criterion.key, label, "UNKNOWN", None,
                           f"{label}: not stated in the quotation; clarification needed.")

    if criterion.op == "min_tier":
        actual_rank, required_rank = cpu_rank(actual), cpu_rank(criterion.value)
        if actual_rank is None or required_rank is None:
            return CheckResult(criterion.key, label, "UNKNOWN", actual,
                               f"{label}: processor '{actual}' is not recognised; clarification needed.")
        ok = actual_rank >= required_rank
        shown_required = str(criterion.value)
    elif criterion.op == ">=":
        ok = actual >= criterion.value
        shown_required = fmt(criterion.key, criterion.value)
    elif criterion.op == "<=":
        ok = actual <= criterion.value
        shown_required = fmt(criterion.key, criterion.value)
    else:
        raise ValueError(f"unknown operator {criterion.op!r}")

    shown = fmt(criterion.key, actual)
    if ok:
        return CheckResult(criterion.key, label, "PASS", actual, f"{label}: quoted {shown}, meets the requirement.")
    return CheckResult(criterion.key, label, "FAIL", actual,
                       f"{label}: quoted {shown}, but the requirement is {OP_WORDS[criterion.op]} {shown_required}.")


def evaluate(quote: Quote, requirement: Requirement) -> list[CheckResult]:
    return [check(c, quote) for c in requirement.criteria]


def verdict_for(checks: list[CheckResult]) -> Verdict:
    if any(c.status == "FAIL" for c in checks):
        return Verdict.FAIL          # a proven violation outranks missing evidence
    if any(c.status == "UNKNOWN" for c in checks):
        return Verdict.NEEDS_REVIEW
    return Verdict.PASS
