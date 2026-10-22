"""Shared data structures for every stage of the procurement agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Fields that decide compliance. Vendor is identity, not a specification.
SPEC_KEYS = ["unit_price", "cpu", "ram_gb", "storage_gb", "warranty_months", "delivery_days"]
# Every quotation is normalised into these canonical fields.
CANONICAL_FIELDS = ["vendor"] + SPEC_KEYS


class Verdict(str, Enum):
    PASS = "PASS"                  # every mandatory criterion is proven by the quotation
    FAIL = "FAIL"                  # at least one mandatory criterion is violated
    NEEDS_REVIEW = "NEEDS_REVIEW"  # nothing violated, but evidence is missing


@dataclass
class Criterion:
    key: str             # canonical field this criterion checks
    op: str              # ">=", "<=" or "min_tier" (ordered CPU family)
    value: Any           # required value
    label: str           # human-readable, e.g. "RAM (min 16 GB)"
    mandatory: bool = True


@dataclass
class Requirement:
    text: str
    quantity: Optional[int]
    criteria: list[Criterion] = field(default_factory=list)


@dataclass
class Quote:
    source: str                  # file name the quotation came from
    text: str                    # extracted plain text
    values: dict[str, Any]       # canonical field -> normalised value, or None if not stated
    evidence: dict[str, str]     # canonical field -> verbatim line or sentence from the source
    method: str                  # "llm", "heuristic", "llm+heuristic" or "none"
    notes: list[str] = field(default_factory=list)

    @property
    def vendor(self) -> str:
        return self.values.get("vendor") or Path(self.source).stem.replace("_", " ").title()


@dataclass
class CheckResult:
    key: str
    label: str
    status: str                  # "PASS", "FAIL" or "UNKNOWN"
    actual: Any
    reason: str


@dataclass
class Assessment:
    quote: Quote
    checks: list[CheckResult]
    verdict: Verdict
    score: Optional[float] = None
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def vendor(self) -> str:
        return self.quote.vendor

    @property
    def unknown_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "UNKNOWN"]


@dataclass
class VerificationCheck:
    name: str
    passed: bool
    detail: str


def norm_text(s: str) -> str:
    """Collapse whitespace and lower-case, for grounding comparisons."""
    return " ".join(s.split()).lower()
