"""Load Synthea's CSV export into this project's claims shape.

WHY
---
`src/synth.py` writes claims-shaped data directly, which makes the risk
equation recoverable and the analytics checkable. It is also a closed loop: the
generator emits exactly the five categories the analytics expect, at exactly
the grain they assume. A cost model can be entirely correct inside that loop
and still be unable to describe a real book of business.

Synthea is free, is not written by me, and produces utilisation from clinical
modules rather than from a rate table. Running the analytics over it is the
data-level version of the discipline this portfolio applies to code.

THE CATEGORY TAXONOMY DOES NOT SURVIVE CONTACT
-----------------------------------------------
This project has five service categories: inpatient, outpatient, professional,
emergency, pharmacy. Synthea emits TEN encounter classes, and three of them --
**skilled nursing, hospice, and home health** -- have no bucket here at all.

The tempting fix is to fold them into `inpatient`, and it would be wrong.
Post-acute care has a different cost curve, length of stay and trend from acute
inpatient, and the measured per-claim costs say so: over 2023-2024 home health
runs about **0.04x** inpatient while skilled nursing runs about **1.2x**, a
spread of more than twentyfold across the three. A single merged average
describes none of them.

The exact multiples move with the window -- over 2020-2024 skilled nursing came
out at 1.48x and hospice at 1.27x, where over 2023-2024 hospice sits near
parity -- so `run_synthea.py` COMPUTES the table rather than this docstring
asserting numbers that will drift out of agreement with the run.

Together the three are roughly 4% of spend: small enough to be ignored, large
enough to matter, and invisible unless something reports them separately.

So the mapping is EXPLICIT and INCOMPLETE ON PURPOSE. Unmapped classes keep
their own names and are reported separately, which makes the gap in the
taxonomy visible in the output instead of hidden in an average.

WHAT IS DERIVED
---------------
* medical claims come from `encounters.csv` -- `PAYER_COVERAGE` is the amount
  the plan paid, which is the right numerator for PMPM (`TOTAL_CLAIM_COST`
  includes patient responsibility and would overstate plan cost)
* pharmacy claims come from `medications.csv`, which is a separate file --
  there is no pharmacy encounter class, so a medical-only adapter would report
  a book of business with no drug spend
* eligibility comes from `payer_transitions.csv`
"""

from __future__ import annotations

import csv
import os
from datetime import date

# Synthea encounter class -> this project's service category.
#
# Deliberately PARTIAL. Anything not named here keeps its own Synthea class
# name so the hole in the taxonomy shows up in the output rather than being
# averaged into a category it does not belong to.
CATEGORY_MAP = {
    "inpatient": "inpatient",
    "outpatient": "outpatient",
    "emergency": "emergency",
    "urgentcare": "emergency",      # unscheduled acute; the closest honest fit
    "ambulatory": "professional",
    "wellness": "professional",
    "virtual": "professional",
}

# Classes with NO equivalent here. Named rather than silently dropped or
# folded, because each is a real cost centre this project cannot describe.
UNMAPPED = {
    "snf": "skilled nursing -- post-acute, a different cost curve entirely",
    "hospice": "hospice -- end-of-life, not comparable to acute inpatient",
    "home": "home health -- lower unit cost, higher frequency",
}


def _d(value):
    """Synthea timestamps are ISO with a zone; the date is the first 10."""
    return date.fromisoformat(str(value)[:10]) if value else None


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rows(csv_dir, name):
    path = os.path.join(csv_dir, name)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            yield row


def load(csv_dir, start=None, end=None):
    """Return {members, eligibility, claims, unmapped} in this project's shape."""
    members = []
    for row in _rows(csv_dir, "patients.csv"):
        members.append({
            "member_id": row["Id"],
            "birth_date": row.get("BIRTHDATE"),
            "sex": (row.get("GENDER") or "").upper(),
            "state": row.get("STATE") or "",
            # Synthea writes 00000 when it has no ZIP; carried through as-is so
            # the de-identifier meets a real missing value rather than a
            # convenient one.
            "zip5": row.get("ZIP") or "",
            "race": row.get("RACE") or "",
            "ethnicity": row.get("ETHNICITY") or "",
        })

    eligibility = []
    for row in _rows(csv_dir, "payer_transitions.csv"):
        a, b = _d(row.get("START_DATE")), _d(row.get("END_DATE"))
        if not (row.get("PATIENT") and a and b):
            continue
        eligibility.append({"member_id": row["PATIENT"],
                            "span_start": a, "span_end": b})

    claims = []
    unmapped = {}

    for row in _rows(csv_dir, "encounters.csv"):
        when = _d(row.get("START"))
        if when is None:
            continue
        if (start and when < start) or (end and when > end):
            continue
        klass = (row.get("ENCOUNTERCLASS") or "").lower()
        category = CATEGORY_MAP.get(klass)
        if category is None:
            category = klass or "unknown"
            unmapped[category] = unmapped.get(category, 0) + 1
        claims.append({
            "claim_id": row["Id"],
            "member_id": row.get("PATIENT"),
            "service_date": when,
            "service_category": category,
            # PAYER_COVERAGE, not TOTAL_CLAIM_COST: the latter includes patient
            # responsibility and would overstate what the plan paid.
            "paid_amount": _num(row.get("PAYER_COVERAGE")),
            "units": 1,
            "provider_npi": row.get("PROVIDER") or "",
        })

    for row in _rows(csv_dir, "medications.csv"):
        when = _d(row.get("START"))
        if when is None:
            continue
        if (start and when < start) or (end and when > end):
            continue
        claims.append({
            "claim_id": "%s-%s-%s" % (row.get("PATIENT", "")[:8],
                                      row.get("CODE", ""), str(when)),
            "member_id": row.get("PATIENT"),
            "service_date": when,
            "service_category": "pharmacy",
            "paid_amount": _num(row.get("PAYER_COVERAGE")),
            "units": int(_num(row.get("DISPENSES"), 1)) or 1,
            "provider_npi": "",
        })

    return {"members": members, "eligibility": eligibility, "claims": claims,
            "unmapped": unmapped}


def coverage_summary(data):
    """What the taxonomy does and does not describe, as numbers."""
    import collections

    paid = collections.Counter()
    count = collections.Counter()
    for c in data["claims"]:
        paid[c["service_category"]] += c["paid_amount"]
        count[c["service_category"]] += 1

    known = set(CATEGORY_MAP.values()) | {"pharmacy"}
    mapped_paid = sum(v for k, v in paid.items() if k in known)
    unmapped_paid = sum(v for k, v in paid.items() if k not in known)

    return {
        "by_category": {k: {"paid": paid[k], "claims": count[k]}
                        for k in sorted(paid)},
        "mapped_paid": mapped_paid,
        "unmapped_paid": unmapped_paid,
        "unmapped_share": (unmapped_paid / (mapped_paid + unmapped_paid))
        if (mapped_paid + unmapped_paid) else 0.0,
        "unmapped_categories": {k: UNMAPPED.get(k, "no description")
                                for k in sorted(paid) if k not in known},
    }
