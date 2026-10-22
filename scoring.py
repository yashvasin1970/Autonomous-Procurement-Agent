"""Anomaly detection and ranking. Scoring only ranks quotes that already passed every mandatory check."""
from __future__ import annotations

import statistics

from .constraints import fmt
from .models import Assessment, Verdict

PRICE_WEIGHT = 0.6
WARRANTY_WEIGHT = 0.2
DELIVERY_WEIGHT = 0.2
ANOMALY_THRESHOLD = 0.9  # flag unit prices below 90% of the median quotation


def detect_anomalies(assessments: list[Assessment]) -> None:
    prices = [a.quote.values["unit_price"] for a in assessments if a.quote.values.get("unit_price")]
    if len(prices) < 3:
        return
    median = statistics.median(prices)
    for a in assessments:
        price = a.quote.values.get("unit_price")
        if price and price < ANOMALY_THRESHOLD * median:
            gap = (1 - price / median) * 100
            a.flags.append(
                f"Unit price {fmt('unit_price', price)} is {gap:.0f}% below the median of all quotes "
                f"({fmt('unit_price', median)}); confirm scope and line items before relying on it."
            )


def score_passing(assessments: list[Assessment]) -> None:
    passing = [a for a in assessments if a.verdict is Verdict.PASS]
    if not passing:
        return

    def column(key):
        return [a.quote.values[key] for a in passing if a.quote.values.get(key)]

    prices, warranties, deliveries = column("unit_price"), column("warranty_months"), column("delivery_days")
    best_price = min(prices) if prices else None
    best_warranty = max(warranties) if warranties else None
    best_delivery = min(deliveries) if deliveries else None

    for a in passing:
        v = a.quote.values
        price, warranty, delivery = v.get("unit_price"), v.get("warranty_months"), v.get("delivery_days")
        score = 0.0
        if price and best_price:
            score += PRICE_WEIGHT * best_price / price
        if warranty and best_warranty:
            score += WARRANTY_WEIGHT * warranty / best_warranty
        if delivery and best_delivery:
            score += DELIVERY_WEIGHT * best_delivery / delivery
        a.score = round(100 * score, 1)

        notes = []
        if price and best_price:
            if price == best_price:
                notes.append("Lowest compliant unit price.")
            else:
                notes.append(f"{fmt('unit_price', price - best_price)} per unit above the lowest compliant price.")
        if warranty and best_warranty:
            if warranty == best_warranty:
                notes.append(f"Longest warranty among compliant quotes ({fmt('warranty_months', warranty)}).")
            else:
                notes.append(f"Warranty {fmt('warranty_months', warranty)} vs {fmt('warranty_months', best_warranty)} best offered.")
        if delivery and best_delivery:
            if delivery == best_delivery:
                notes.append(f"Fastest delivery among compliant quotes ({fmt('delivery_days', delivery)}).")
            else:
                notes.append(f"Delivery {fmt('delivery_days', delivery)} vs {fmt('delivery_days', best_delivery)} fastest.")
        a.notes = notes


def rank(assessments: list[Assessment]) -> list[Assessment]:
    order = {Verdict.PASS: 0, Verdict.NEEDS_REVIEW: 1, Verdict.FAIL: 2}
    return sorted(assessments, key=lambda a: (order[a.verdict], bool(a.flags), -(a.score or 0), a.vendor))
