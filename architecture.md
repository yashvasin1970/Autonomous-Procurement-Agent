# Architecture and design notes

## Principles

1. **Prove before ranking.** A quote must satisfy every mandatory criterion before it is compared on price,
   warranty or delivery. "Cheapest" is never the first question.
2. **Evidence or nothing.** Every extracted value carries the verbatim line it came from. A value without
   matching evidence is discarded, and the discard is recorded in the trace.
3. **Unknown is not fail, and not pass.** A criterion the quotation does not address becomes `UNKNOWN`, which
   yields `NEEDS_REVIEW` and an open item. A proven violation is `FAIL`, which outranks missing evidence.
4. **Humans own consequential actions.** The vendor message is drafted, never sent. Approval records a named person.

## Pipeline

| Stage | Module | Input | Output | Failure handling |
|---|---|---|---|---|
| INPUT | `agent.py` | requirement text, documents | run id | none |
| PLAN | `requirements_parser.py` | requirement text | `Criterion` list | no criteria → pause with a request to restate |
| TOOL_USE | `ingest.py`, `extractor.py` | file bytes | `Quote` (values + evidence) | unreadable or empty file → gap, run continues |
| NORMALIZE | `models.py` | `Quote` | canonical fields, missing list | missing fields are reported, not filled |
| CONSTRAINT | `constraints.py` | `Quote` × criteria | `CheckResult` per rule, verdict | none (pure function) |
| COMPARE | `scoring.py` | compliant quotes | scores, notes, anomaly flags | fewer than 3 quotes → no anomaly test |
| RECOVER | `agent.py` | gaps, unknowns, flags | open items | escalated, never guessed |
| VERIFY | `verify.py` | recommendation, all quotes | 5 named checks | any failure → status `NEEDS_CLARIFICATION` |
| RECOMMEND / ACTION | `agent.py` | eligible best quote | recommendation, draft message | no eligible quote → `NO_COMPLIANT_QUOTE` or clarification |
| APPROVAL | `approval.py` | run, decision, approver | audit record | approver name required; no recommendation → refused |

## Run statuses

- `AWAITING_APPROVAL`: a recommendation exists and every verification check passed.
- `NEEDS_CLARIFICATION`: open items exist, or verification failed. The UI shows what to resolve.
- `NO_COMPLIANT_QUOTE`: nothing passed and nothing is pending. The request may be unrealistic for the market.

## Extraction and grounding

```
quotation text ──► Claude (JSON: value + verbatim evidence)
                       │
                       ├─ evidence not found in text?  ──► discard value, note in trace
                       │
                       ▼
                 grounded values
                       │
  rules (line predicates) fill any remaining null  ──► "llm+heuristic"
```

The verifier checks grounding a second time over the final record, so a bug in either extractor cannot
put an untraceable value into a recommendation.

## Scoring

Scoring applies only to quotes whose verdict is `PASS`.

`score = 100 × (0.6 × best_price/price + 0.2 × warranty/best_warranty + 0.2 × best_delivery/delivery)`

Weights are constants in `scoring.py`. The weighting reflects the deck's principle: after compliance, price
matters most, then coverage (warranty) and speed (delivery).

Anomaly rule: with at least three quotes, a unit price more than 10% below the median is flagged. A flagged
quote can pass the rules but cannot be recommended until a person confirms its scope.

## Trust model

| Question | How it is answered |
|---|---|
| Did the model invent a value? | Grounding check against the source text, applied twice |
| Is the recommended quote compliant? | Re-checked in `verify.py` from criteria, not from the scorer |
| Is it the best eligible option? | Recomputed; the best eligible quote is compared with the recommendation |
| Who approved it? | Audit log: approver name, decision, timestamp, run id, verification state |
| Did anything go to a vendor? | No. The message is drafted and held |

## Extending the prototype

- **New rule:** add a `Criterion` in `requirements_parser.py`, a field in `models.CANONICAL_FIELDS`, and an
  extraction rule in `extractor.heuristic_extract`. The constraint engine needs no change for `>=`/`<=` rules.
- **New document type:** add a branch in `ingest.read_document`.
- **Policy RAG:** retrieve internal approval rules and add them as criteria. They pass through the same
  constraint engine, so their outcomes stay explainable.
