"""Human approval gate. Records who decided what, and when, in an append-only JSONL audit log."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .agent import RunResult

DEFAULT_LOG = Path(os.environ.get("PROCUREMENT_AUDIT_LOG", "audit_log.jsonl"))


def record_decision(run: RunResult, decision: str, approver: str, comment: str = "",
                    log_path: str | Path | None = None) -> dict:
    decision = decision.upper()
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("Decision must be APPROVED or REJECTED.")
    if not approver or not approver.strip():
        raise ValueError("Enter the approver's name before deciding.")
    if run.recommendation is None:
        raise ValueError("There is no recommendation to decide on.")

    entry = {
        "run_id": run.run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decision": decision,
        "approver": approver.strip(),
        "comment": comment.strip(),
        "recommended_vendor": run.recommendation.vendor,
        "recommended_score": run.recommendation.score,
        "run_status": run.status,
        "verification_passed": all(v.passed for v in run.verification),
    }
    path = Path(log_path) if log_path else DEFAULT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry
