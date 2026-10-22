"""Command-line runner.

    python -m procurement_agent.cli --requirement sample_data/requirement.txt \
        --quotes sample_data/quotes/*
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .agent import STATUS_LABELS, run_procurement
from .report import render_markdown, run_to_json

ICON = {"ok": "[ ok ]", "warn": "[warn]", "error": "[ err]"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the procurement agent on a request and its quotations.")
    parser.add_argument("--requirement", required=True, type=Path, help="Text file with the purchase request")
    parser.add_argument("--quotes", required=True, nargs="+", type=Path, help="Quotation files (pdf, xlsx, txt, eml)")
    parser.add_argument("--out", type=Path, default=Path("runs"), help="Folder for the report and run record")
    parser.add_argument("--no-llm", action="store_true", help="Use rule-based extraction only")
    args = parser.parse_args(argv)

    requirement_text = args.requirement.read_text(encoding="utf-8")
    docs = [(p.name, p.read_bytes()) for p in args.quotes]
    run = run_procurement(requirement_text, docs, use_llm=not args.no_llm)

    print(f"Run {run.run_id}: {STATUS_LABELS[run.status]}\n")
    print("Agent trace")
    for s in run.trace:
        print(f"  {ICON[s.status]} {s.stage:<11} {s.title}")
        if s.detail:
            print(f"                {s.detail}")

    print("\nRanked quotations")
    for i, a in enumerate(run.assessments, 1):
        score = f"{a.score:5.1f}" if a.score is not None else "    -"
        print(f"  {i}. {a.vendor:<26} {a.verdict.value:<13} score {score}")

    if run.recommendation is not None:
        print(f"\nRecommendation: {run.recommendation.vendor}")
    if run.clarifications:
        print("\nOpen items")
        for c in run.clarifications:
            print(f"  - {c}")

    out_dir = args.out / run.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.md").write_text(render_markdown(run), encoding="utf-8")
    (out_dir / "run.json").write_text(run_to_json(run), encoding="utf-8")
    print(f"\nReport written to {out_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
