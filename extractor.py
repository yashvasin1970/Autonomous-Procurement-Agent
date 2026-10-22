"""Turn a quotation's text into canonical fields.

Two extractors run in order:
1. An LLM extractor (Claude) when ANTHROPIC_API_KEY is set. Every value it returns must be
   backed by evidence copied verbatim from the document; ungrounded values are discarded.
2. A rule-based extractor that fills any field the LLM missed. This keeps the pipeline working
   offline and recovers from partial LLM output.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from .models import CANONICAL_FIELDS, Quote, norm_text

DEFAULT_MODEL = "claude-sonnet-5-5"
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

_CURRENCY_MONEY = re.compile(r"(?:₹|\bRs\.?|\bINR)\s*(\d[\d,]*(?:\.\d+)?)\s*(lakh|lac|k)?\b", re.I)
_SUFFIX_MONEY = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*(lakh|lac|k)\b", re.I)
_PLAIN_MONEY = re.compile(r"\b(\d{1,3}(?:,\d{2,3})+|\d{4,})(?:\.\d+)?\b")
_CPU = re.compile(r"\b(i[3579]|ryzen\s?[3579])\b", re.I)
_VENDOR = re.compile(r"^\s*(?:vendor|supplier|company|seller)\s*[:|\-]\s*(.+?)\s*$", re.I)


def norm_cpu(s: str) -> str:
    return re.sub(r"ryzen\s*", "ryzen ", s.strip().lower())


def word_to_int(token: str) -> int:
    return int(token) if token.isdigit() else WORDS[token.lower()]


def _amount(number: str, suffix: Optional[str]) -> float:
    value = float(number.replace(",", ""))
    if suffix:
        value *= 100000 if suffix.lower() in ("lakh", "lac") else 1000
    return value


def parse_money(text: str, strict: bool = False) -> Optional[float]:
    """Parse an INR amount such as '₹55,000', 'Rs. 51,000', '₹60K' or '2 lakh'.

    In strict mode a bare number is rejected, which is used for requirement sentences
    where a stray number must not be mistaken for a price.
    """
    m = _CURRENCY_MONEY.search(text) or _SUFFIX_MONEY.search(text)
    if m:
        return _amount(m.group(1), m.group(2))
    if not strict:
        m = _PLAIN_MONEY.search(text)
        if m:
            return _amount(m.group(1), None)
    return None


def heuristic_extract(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Rule-based extraction. Each rule is a line predicate plus a value parser."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def pick(pred, extract):
        for line in lines:
            if pred(line.lower()):
                value = extract(line)
                if value is not None:
                    return value, line
        return None, None

    def vendor_of(line):
        m = _VENDOR.match(line)
        return m.group(1) if m else None

    def cpu_of(line):
        m = _CPU.search(line)
        return norm_cpu(m.group(1)) if m else None

    def ram_of(line):
        m = re.search(r"(\d+)\s*GB", line, re.I)
        return int(m.group(1)) if m else None

    def storage_of(line):
        m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|TB)\b", line, re.I)
        if not m:
            return None
        return int(float(m.group(1)) * (1024 if m.group(2).upper() == "TB" else 1))

    def warranty_of(line):
        m = re.search(r"\b(\d+|one|two|three|four|five)\s*(year|yr|month)s?\b", line, re.I)
        if not m:
            return None
        n = word_to_int(m.group(1))
        return n * 12 if m.group(2).lower() in ("year", "yr") else n

    def delivery_of(line):
        m = re.search(r"(\d+)\s*(?:business\s+|working\s+|calendar\s+)?(day|week)s?\b", line, re.I)
        if not m:
            return None
        n = int(m.group(1))
        return n * 7 if m.group(2).lower() == "week" else n

    rules = {
        "vendor": (lambda l: True, vendor_of),
        "unit_price": (lambda l: ("price" in l or "unit cost" in l) and "total" not in l, parse_money),
        "cpu": (lambda l: "processor" in l or "cpu" in l, cpu_of),
        "ram_gb": (lambda l: re.search(r"\bram\b|memory", l) is not None, ram_of),
        "storage_gb": (lambda l: re.search(r"ssd|storage|hdd|hard drive", l) is not None, storage_of),
        "warranty_months": (lambda l: "warranty" in l, warranty_of),
        "delivery_days": (lambda l: re.search(r"deliver|lead time|dispatch", l) is not None, delivery_of),
    }
    values: dict[str, Any] = {}
    evidence: dict[str, str] = {}
    for key, (pred, extract) in rules.items():
        value, line = pick(pred, extract)
        if value is not None:
            values[key] = value
            evidence[key] = line
    return values, evidence


SYSTEM_PROMPT = """You extract structured fields from a vendor quotation for a procurement agent.
Respond with ONLY a JSON object: no prose and no Markdown fences.
Keys: vendor, unit_price, cpu, ram_gb, storage_gb, warranty_months, delivery_days.
Each key maps to {"value": <value or null>, "evidence": "<verbatim sentence or line>"}.
Rules:
- Never guess. If the quotation does not state a field, use {"value": null, "evidence": null}.
- "evidence" must be copied word for word from the quotation.
- unit_price: number in INR per unit, without symbols or commas.
- ram_gb and storage_gb: integers in GB (1 TB = 1024 GB).
- warranty_months and delivery_days: integers (1 year = 12 months; 1 week = 7 days).
- cpu: processor family, for example "i5", "i7" or "ryzen 5"."""


def llm_extract(text: str) -> tuple[Optional[dict], Optional[str]]:
    """Return (parsed_json, error). Both are None when the LLM is not configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, None
    try:
        import anthropic
    except ImportError:
        return None, "the 'anthropic' package is not installed"
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=os.environ.get("PROCUREMENT_MODEL", DEFAULT_MODEL),
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"<quotation>\n{text[:20000]}\n</quotation>"}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            return None, "model returned no JSON object"
        return json.loads(raw[start:end + 1]), None
    except Exception as exc:  # network, auth or JSON errors all fall back to rules
        return None, str(exc)


def _coerce(key: str, value: Any) -> Any:
    try:
        if key == "vendor":
            return str(value).strip() or None
        if key == "cpu":
            return norm_cpu(str(value))
        if key == "unit_price":
            return float(str(value).replace(",", "").replace("₹", "").strip())
        return int(round(float(str(value).replace(",", "").strip())))
    except ValueError:
        return None


def _from_llm(data: dict, text: str):
    """Keep only values whose evidence appears in the source document (grounding check)."""
    values, evidence, dropped = {}, {}, []
    doc = norm_text(text)
    for key in CANONICAL_FIELDS:
        item = data.get(key)
        if not isinstance(item, dict) or item.get("value") in (None, ""):
            continue
        snippet = item.get("evidence") or ""
        if not snippet or norm_text(snippet) not in doc:
            dropped.append(key)
            continue
        value = _coerce(key, item["value"])
        if value is None:
            dropped.append(key)
            continue
        values[key] = value
        evidence[key] = snippet.strip()
    return values, evidence, dropped


def extract_quote(source: str, text: str, use_llm: bool = True) -> Quote:
    values: dict[str, Any] = {k: None for k in CANONICAL_FIELDS}
    evidence: dict[str, str] = {}
    methods: list[str] = []
    notes: list[str] = []

    if use_llm:
        data, error = llm_extract(text)
        if error:
            notes.append(f"LLM extraction unavailable ({error}); relying on rule-based extraction.")
        if data is not None:
            found, ev, dropped = _from_llm(data, text)
            values.update(found)
            evidence.update(ev)
            methods.append("llm")
            if dropped:
                notes.append("Discarded LLM value(s) without matching evidence: " + ", ".join(dropped))

    rule_values, rule_evidence = heuristic_extract(text)
    recovered = []
    for key in CANONICAL_FIELDS:
        if values.get(key) is None and rule_values.get(key) is not None:
            values[key] = rule_values[key]
            evidence[key] = rule_evidence[key]
            recovered.append(key)
    if recovered:
        methods.append("heuristic")
        if "llm" in methods:
            notes.append("Rule-based recovery filled: " + ", ".join(recovered))

    return Quote(
        source=source,
        text=text,
        values=values,
        evidence=evidence,
        method="+".join(dict.fromkeys(methods)) or "none",
        notes=notes,
    )
