from pathlib import Path

from procurement_agent.agent import run_procurement
from procurement_agent.extractor import extract_quote, parse_money
from procurement_agent.ingest import read_document
from procurement_agent.models import Verdict
from procurement_agent.requirements_parser import parse_requirement
from procurement_agent.verify import verify

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data"
REQUIREMENT = (SAMPLE / "requirement.txt").read_text(encoding="utf-8")


def load_docs():
    return [(p.name, p.read_bytes()) for p in sorted((SAMPLE / "quotes").iterdir())]


def extract(name):
    data = dict(load_docs())[name]
    return extract_quote(name, read_document(name, data), use_llm=False)


def test_money_parsing():
    assert parse_money("Unit price: ₹55,000 per unit") == 55000
    assert parse_money("Budget limit is ₹60K per unit", strict=True) == 60000
    assert parse_money("Unit price: Rs. 51,000 per unit") == 51000
    assert parse_money("Unit price: INR 72,000") == 72000
    assert parse_money("Delivery within 30 days", strict=True) is None


def test_requirement_becomes_mandatory_criteria():
    req = parse_requirement(REQUIREMENT)
    assert req.quantity == 20
    by_key = {c.key: c for c in req.criteria}
    assert by_key["cpu"].op == "min_tier" and by_key["cpu"].value == "i5"
    assert by_key["ram_gb"].value == 16
    assert by_key["storage_gb"].value == 512
    assert by_key["unit_price"].op == "<=" and by_key["unit_price"].value == 60000
    assert by_key["warranty_months"].value == 24
    assert by_key["delivery_days"].value == 30


def test_extraction_handles_text_and_excel_sources():
    a = extract("vendor_a.txt")
    assert a.vendor == "Apex Computers Pvt Ltd"
    assert a.values["unit_price"] == 55000 and a.values["ram_gb"] == 16
    assert a.values["warranty_months"] == 36 and a.values["cpu"] == "i5"

    c = extract("vendor_c.txt")
    assert c.values["storage_gb"] == 1024 and c.values["cpu"] == "i7"

    d = extract("vendor_d.xlsx")
    assert d.vendor == "Delta Tech Pvt Ltd"
    assert d.values["unit_price"] == 58000 and d.values["warranty_months"] == 24

    e = extract("vendor_e.txt")
    assert e.values["warranty_months"] is None  # not stated: must not be invented


def test_pipeline_recommends_cheapest_compliant_quote():
    run = run_procurement(REQUIREMENT, load_docs(), use_llm=False)
    verdicts = {a.vendor: a.verdict for a in run.assessments}
    assert verdicts == {
        "Apex Computers Pvt Ltd": Verdict.PASS,
        "Budget Byte Ltd": Verdict.FAIL,
        "Summit Systems": Verdict.FAIL,
        "Delta Tech Pvt Ltd": Verdict.PASS,
        "Nimbus Traders": Verdict.NEEDS_REVIEW,
    }
    assert run.recommendation.vendor == "Apex Computers Pvt Ltd"
    assert run.status == "AWAITING_APPROVAL"
    assert all(v.passed for v in run.verification)


def test_failures_carry_specific_reasons():
    run = run_procurement(REQUIREMENT, load_docs(), use_llm=False)
    by_vendor = {a.vendor: a for a in run.assessments}
    ram = [c for c in by_vendor["Budget Byte Ltd"].checks if c.key == "ram_gb"][0]
    assert ram.status == "FAIL" and "8 GB" in ram.reason and "at least 16 GB" in ram.reason
    budget = [c for c in by_vendor["Summit Systems"].checks if c.key == "unit_price"][0]
    assert budget.status == "FAIL" and "₹72,000" in budget.reason and "at most ₹60,000" in budget.reason


def test_suspiciously_cheap_quote_is_escalated_not_recommended():
    run = run_procurement(REQUIREMENT, load_docs(), use_llm=False)
    nimbus = [a for a in run.assessments if a.vendor == "Nimbus Traders"][0]
    assert nimbus.flags, "cheapest quote should be flagged as an anomaly"
    assert any("Nimbus Traders" in c and "warranty" in c.lower() for c in run.clarifications)
    assert any("Nimbus Traders" in c and "below the median" in c for c in run.clarifications)
    assert run.recommendation.vendor != "Nimbus Traders"


def test_unreadable_document_is_a_recorded_gap_not_a_crash():
    docs = load_docs() + [("broken_scan.pdf", b"not a real pdf")]
    run = run_procurement(REQUIREMENT, docs, use_llm=False)
    assert any("broken_scan.pdf" in g for g in run.gaps)
    assert run.recommendation is not None


def test_request_without_measurable_criteria_pauses_for_clarification():
    run = run_procurement("We need some laptops soon, nothing fancy.", load_docs(), use_llm=False)
    assert run.status == "NEEDS_CLARIFICATION"
    assert run.recommendation is None and run.assessments == []


def test_scores_prefer_price_then_warranty_and_delivery():
    run = run_procurement(REQUIREMENT, load_docs(), use_llm=False)
    scores = {a.vendor: a.score for a in run.assessments if a.verdict is Verdict.PASS}
    assert scores["Apex Computers Pvt Ltd"] == 100.0
    assert scores["Apex Computers Pvt Ltd"] > scores["Delta Tech Pvt Ltd"]


def test_verification_catches_a_bad_recommendation():
    run = run_procurement(REQUIREMENT, load_docs(), use_llm=False)
    # Tamper: pretend the failing Budget Byte quote was recommended.
    failing = [a for a in run.assessments if a.vendor == "Budget Byte Ltd"][0]
    checks = verify(failing, run.assessments)
    assert not checks[0].passed  # does not satisfy every mandatory criterion


def test_llm_values_without_evidence_are_discarded(monkeypatch):
    """Grounding: an LLM value whose evidence is not in the document must be dropped."""
    import procurement_agent.extractor as ex

    fake = {
        "vendor": {"value": "Apex Computers Pvt Ltd", "evidence": "Vendor: Apex Computers Pvt Ltd"},
        "unit_price": {"value": 55000, "evidence": "Unit price: ₹55,000 per unit (excluding GST)"},
        # Hallucinated: this sentence does not appear in the document.
        "ram_gb": {"value": 32, "evidence": "RAM: 32 GB LPDDR5 upgradeable"},
    }
    monkeypatch.setattr(ex, "llm_extract", lambda text: (fake, None))
    text = read_document("vendor_a.txt", (SAMPLE / "quotes" / "vendor_a.txt").read_bytes())
    q = extract_quote("vendor_a.txt", text, use_llm=True)

    assert q.values["unit_price"] == 55000
    assert q.values["ram_gb"] == 16  # the hallucinated 32 GB was dropped; rules recovered the real value
    assert any("ram_gb" in n for n in q.notes)
    assert q.method == "llm+heuristic"
