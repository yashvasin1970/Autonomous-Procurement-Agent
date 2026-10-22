# Autonomous Procurement Agent (Project Eclipse, Challenge 04)

An agent that turns a purchase request into a compliant, evidence-backed vendor recommendation.
It reads the requirement and the vendor quotations (PDF, XLSX, TXT, EML), proves which quotes satisfy
every mandatory rule, ranks only those, verifies its own output, and then stops for a named human to approve.

> The agent does not make a "lowest price" choice. It first proves which quotes meet the requirement,
> then compares the remaining trade-offs. Anything it cannot prove becomes an open question, not a guess.

## What it does, mapped to the Round 1 deck

| Deck stage | Implementation |
|---|---|
| 01 Input | `app.py` (Streamlit upload + text) and `cli.py`; `ingest.py` reads PDF, XLSX, TXT, EML |
| 02 Extract | `extractor.py`: Claude extracts canonical fields with verbatim evidence; rule-based fallback |
| 03 Compare | `constraints.py` (pass/fail per mandatory rule), `scoring.py` (weighted score of compliant quotes only) |
| 04 Decide | `agent.py` picks the top eligible quote and drafts a vendor message (not sent) |
| Plan | `requirements_parser.py` turns plain language into mandatory, measurable criteria |
| Verify | `verify.py` re-checks the recommendation: criteria, grounding, anomalies, ranking |
| Recover | Unreadable files and missing values become open items; the agent never invents a value |
| Human approval | `approval.py` records approver, decision, timestamp and verification state to an audit log |

The reasoning loop (from the deck): **Plan → Tool use → Decision → Action → Verify → Approve**, and on failure **Recover**.

## Sample scenario (the deck's laptop example, extended)

Requirement: 20 laptops, minimum i5, at least 16 GB RAM, at least 512 GB SSD, budget ₹60K per unit,
warranty at least 2 years, delivery within 30 days.

| Vendor | Verdict | Why |
|---|---|---|
| Apex Computers | **PASS, recommended** (score 100) | Meets every rule; lowest compliant price (₹55,000); 3-year warranty; 21-day delivery |
| Delta Tech | PASS (score 84) | Compliant; ₹3,000 more per unit; 2-year warranty; 30-day delivery |
| Nimbus Traders | NEEDS REVIEW | Cheapest at ₹49,000, but warranty is not stated, and the price is 11% below the median, so it is flagged |
| Budget Byte | FAIL | 8 GB RAM, below the 16 GB minimum |
| Summit Systems | FAIL | ₹72,000, above the ₹60,000 budget |

Run it:

```bash
python -m procurement_agent.cli --requirement sample_data/requirement.txt --quotes sample_data/quotes/* --out runs --no-llm
```

A full report with per-rule evidence quotes, verification results, and the execution trace is in
`demo/sample_report.md`.

## Architecture

```
 Requirement text ──► parse_requirement ──► mandatory Criteria ─┐
                                                                 ▼
 Quotations ─► ingest ─► extract (LLM + grounded evidence, rules fallback) ─► normalised Quote
                                                                 │
                       constraints (PASS / FAIL / UNKNOWN per rule) ◄┘
                                     │
                scoring (compliant only) ─► anomaly flags ─► recommendation (eligible only)
                                     │
                      verify (independent re-checks) ─► human approval gate ─► audit log
```

More detail, including failure handling and the trust model, is in `docs/architecture.md`.

## AI and agentic integration

- **LLM extraction (Claude).** `extractor.py` asks the model to return JSON with each value and the
  sentence it came from. The model is never trusted on its own: a value is kept only if its evidence
  appears verbatim in the source document (a grounding check). Hallucinated values are discarded and
  logged in the trace.
- **Rule-based fallback.** Deterministic rules fill any gap, so the system runs fully offline and still
  recovers when LLM output is partial.
- **Agentic loop.** The orchestrator in `agent.py` runs named stages, each with a result and a status.
  It recovers from failures (an unreadable file becomes a gap; a missing value becomes a clarification) and
  pauses when it cannot resolve something.
- **Verification as a separate step.** `verify.py` checks the recommendation again instead of trusting
  the scorer. It confirms that the recommendation satisfies every rule, that it is the highest-scoring
  eligible quote, that every value is traceable, and that no anomaly is open.
- **Safety control.** The vendor message is drafted, never sent. Approval requires a named person.

Enable the LLM path with `export ANTHROPIC_API_KEY=...` (optionally `PROCUREMENT_MODEL=...`). Without a
key, the app uses rule-based extraction and says so in the sidebar.

## Technology stack

| Layer | Choice |
|---|---|
| UI | Streamlit (single-page app, trace/evidence/verification/approval tabs) |
| Language | Python 3.10+ |
| Document parsing | pypdf (PDF), openpyxl (XLSX), stdlib email (EML), plain text |
| AI | Anthropic Claude via the `anthropic` SDK (optional); structured JSON output |
| Core logic | Pure Python dataclasses; deterministic rules, scoring and verification |
| Audit | Append-only JSONL audit log; JSON run record per execution |
| Tests | pytest (11 tests: parsing, extraction, rules, pipeline outcomes, recovery, grounding) |

## Setup and run

```bash
pip install -r requirements.txt
python -m pytest -q                       # 11 tests
streamlit run app.py                      # UI at http://localhost:8501; click "Load sample scenario"
python -m procurement_agent.cli --help    # command-line runner
```

Configuration (environment variables, all optional):

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Enables Claude extraction | unset (rules only) |
| `PROCUREMENT_MODEL` | Model used for extraction | `claude-sonnet-5-5` |
| `PROCUREMENT_AUDIT_LOG` | Path of the approval audit log | `audit_log.jsonl` |

## Repository layout

```
app.py                       Streamlit UI
procurement_agent/
  agent.py                   orchestrator and stage trace
  extractor.py               LLM + rule-based extraction, grounding check
  requirements_parser.py     plain language -> mandatory criteria
  constraints.py             pass / fail / unknown per rule
  scoring.py                 compliant-only scoring, anomaly detection
  verify.py                  independent verification checks
  approval.py                human decision gate and audit log
  ingest.py, report.py, models.py, cli.py
sample_data/                 requirement + 5 vendor quotations (TXT, XLSX)
tests/test_pipeline.py       automated tests
docs/architecture.md         design, failure handling, trust model
demo/                        sample run output (report and JSON record)
```

## Limitations and next steps

- Requirement parsing is rule-based. It handles the common phrasings (minimum processor, RAM, storage, budget,
  warranty, delivery), but not free-form requirements. Routing requirements through the LLM with the same
  grounding check is the next step.
- Quotations are read as text. Scanned PDFs (images) are reported as gaps, not OCR'd. Adding OCR (e.g. Tesseract)
  is a straightforward extension.
- The optional vendor lookup API from the deck is not integrated. Policy RAG (for internal approval rules) is
  not built; mandatory rules are passed in as the request.
- The LLM path was verified with a mocked model in tests. Run it once with a real key before a live demo.
- Currency is INR-only. Multi-currency and tax (GST) handling would be needed for production.
- Authentication is not implemented. The approver name is self-declared. Production use would need SSO.
