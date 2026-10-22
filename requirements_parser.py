"""Parse a plain-language purchase request into measurable, mandatory criteria."""
from __future__ import annotations

import re

from .extractor import norm_cpu, parse_money, word_to_int
from .models import Criterion, Requirement


def parse_requirement(text: str) -> Requirement:
    flat = " ".join(text.split())
    criteria: list[Criterion] = []

    m = re.search(r"\b(?:buy|purchase|need|order|procure)\w*\s+(\d+)\b", flat, re.I)
    quantity = int(m.group(1)) if m else None

    m = re.search(r"(?:minimum|min\.?|at least)\s+(?:processor\s+|cpu\s+)?(i[3579]|ryzen\s?[3579])\b", flat, re.I)
    if m:
        cpu = norm_cpu(m.group(1))
        criteria.append(Criterion("cpu", "min_tier", cpu, f"Processor (min {cpu})"))

    m = re.search(r"(?:(?:at least|minimum|min\.?)\s+)?(\d+)\s*GB\s*(?:of\s+)?RAM\b", flat, re.I)
    if m:
        gb = int(m.group(1))
        criteria.append(Criterion("ram_gb", ">=", gb, f"RAM (min {gb} GB)"))

    m = re.search(r"(?:(?:at least|minimum|min\.?)\s+)?(\d+)\s*(GB|TB)\s*(?:of\s+)?(?:SSD|storage)\b", flat, re.I)
    if m:
        gb = int(m.group(1)) * (1024 if m.group(2).upper() == "TB" else 1)
        criteria.append(Criterion("storage_gb", ">=", gb, f"Storage (min {m.group(1)} {m.group(2).upper()})"))

    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if re.search(r"budget|limit|maximum|up to|under|\bmax\b", sentence, re.I) and re.search(r"unit|each|per", sentence, re.I):
            amount = parse_money(sentence, strict=True)
            if amount:
                criteria.append(Criterion("unit_price", "<=", amount, f"Unit price (max ₹{amount:,.0f})"))
                break

    m = re.search(r"warranty[^.\n]*?(\d+|one|two|three|four|five)\s*(year|month)s?\b", flat, re.I)
    if m:
        months = word_to_int(m.group(1)) * (12 if m.group(2).lower() == "year" else 1)
        criteria.append(Criterion("warranty_months", ">=", months, f"Warranty (min {months} months)"))

    m = re.search(r"deliver\w*[^.\n]*?(?:within|in|under|by)\s+(\d+)\s*(day|week)s?\b", flat, re.I)
    if m:
        days = int(m.group(1)) * (7 if m.group(2).lower() == "week" else 1)
        criteria.append(Criterion("delivery_days", "<=", days, f"Delivery (max {days} days)"))

    return Requirement(text=text, quantity=quantity, criteria=criteria)
