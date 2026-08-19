"""Claims + demographics + eligibility, with PHI planted and a known PMPM shock.

TWO THINGS ARE PLANTED HERE ON PURPOSE.

1. PHI, with ground-truth character offsets (see `phi.py`), so de-identification
   recall is a measurement rather than a claim.

2. A KNOWN COST SHOCK in 2024 Q3, so the price/utilisation/mix decomposition
   can be checked against a cause that was written down in advance:

       * inpatient unit price rises 20% (a contract renegotiation)
       * membership falls 8% (a group termination)
       * utilisation per member is unchanged

   That combination is chosen because it is the one that makes analysts wrong.
   PMPM jumps, and the instinctive reading -- "utilisation is up, members are
   getting sicker" -- is false in both of its clauses. The truth is a price
   effect plus a denominator effect, and an analyst who does not check the
   denominator first will chase the wrong problem for a week.

   `analytics.py` recovers it, and `run_pipeline.py` prints the recovered
   attribution next to the planted truth.

Nothing here is Synthea. Synthea is the right tool and is named in the spec; it
is a Java application and is not runnable in this offline build. The cost is
real: these trajectories come from a small set of rate parameters rather than
from curated disease modules, so the clinical realism is unearned. What is
gained is that the answers are known in advance.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import phi

START = date(2023, 1, 1)
END = date(2024, 12, 31)

# service category -> (annual services per member, mean paid per service)
CATEGORIES = {
    "inpatient":    (0.09,  9800.0),
    "outpatient":   (0.85,   640.0),
    "professional": (5.40,   135.0),
    "emergency":    (0.22,  1350.0),
    "pharmacy":     (9.10,    88.0),
}

# The planted shock.
SHOCK = {
    "quarter": (2024, 3),
    "inpatient_price_multiplier": 1.20,
    "membership_drop": 0.08,
}

# Condition flags used for the HCC-style risk context.
HCC_LIKE = {
    "chf": ("HCC85", "Congestive heart failure", 0.331),
    "copd": ("HCC111", "Chronic obstructive pulmonary disease", 0.328),
    "diabetes_complication": ("HCC18", "Diabetes with chronic complications", 0.302),
    "ckd_severe": ("HCC136", "Chronic kidney disease stage 4", 0.237),
    "cancer": ("HCC12", "Breast, prostate and other cancers", 0.154),
    "vascular": ("HCC108", "Vascular disease", 0.288),
}


def _quarter(d):
    return (d.year, (d.month - 1) // 3 + 1)


def generate(n_members=8000, seed=17):
    rng = random.Random(seed)
    members, eligibility, claims, notes, truth_spans = [], [], [], [], []

    for i in range(n_members):
        p = phi.make_person(rng)
        age = min(94, max(0, int(rng.gauss(52, 20))))
        member = {
            "member_id": p["member_id"], "age": age,
            "sex": rng.choice(["F", "M"]),
            **{k: p[k] for k in ("first_name", "last_name", "street", "city",
                                 "state", "zip5", "phone", "email", "ssn",
                                 "mrn", "account_number")},
            "name_in_gazetteer": p["name_in_gazetteer"],
        }
        # risk flags, more likely with age
        risk = 0.0
        for key, (_code, _desc, weight) in HCC_LIKE.items():
            has = rng.random() < (0.02 + age / 1400)
            member[f"cond_{key}"] = has
            risk += weight if has else 0.0
        member["risk_score"] = round(0.6 + risk + age / 250, 3)
        members.append(member)

        # ---- eligibility spans, with churn -----------------------------
        spans = []
        r = rng.random()
        if r < 0.62:
            spans.append((START, END))
        elif r < 0.75:
            spans.append((START + timedelta(days=rng.randint(30, 400)), END))
        elif r < 0.87:
            spans.append((START, END - timedelta(days=rng.randint(30, 400))))
        else:
            g0 = START + timedelta(days=rng.randint(90, 500))
            g1 = g0 + timedelta(days=rng.randint(40, 200))
            spans.append((START, g0))
            if g1 < END:
                spans.append((g1, END))
        # the planted group termination: 8% of members lose coverage in Q3 2024
        terminated = rng.random() < SHOCK["membership_drop"]
        if terminated:
            cut = date(2024, 7, 1)
            spans = [(a, min(b, cut)) for a, b in spans if a < cut]
        for a, b in spans:
            eligibility.append({"member_id": member["member_id"],
                                "span_start": a, "span_end": b})

        covered_days = sum((b - a).days for a, b in spans)
        if covered_days <= 0:
            continue

        # ---- claims ------------------------------------------------------
        intensity = member["risk_score"]
        for cat, (rate, price) in CATEGORIES.items():
            n = rng.poisson(rate * intensity * covered_days / 365.0) \
                if hasattr(rng, "poisson") else _poisson(rng, rate * intensity
                                                         * covered_days / 365.0)
            for _ in range(n):
                a, b = spans[rng.randrange(len(spans))]
                if (b - a).days <= 1:
                    continue
                d = a + timedelta(days=rng.randrange((b - a).days))
                unit = price * rng.uniform(0.55, 1.6)
                if cat == "inpatient" and _quarter(d) >= SHOCK["quarter"]:
                    unit *= SHOCK["inpatient_price_multiplier"]
                claims.append({
                    "claim_id": f"C{len(claims):09d}",
                    "member_id": member["member_id"],
                    "service_date": d, "service_category": cat,
                    "units": 1, "paid_amount": round(unit, 2),
                    "provider_npi": f"1{rng.randrange(100000000, 999999999)}",
                })

        # ---- free-text notes carrying PHI --------------------------------
        for _ in range(rng.choice([0, 0, 0, 1, 1, 2])):
            d = START + timedelta(days=rng.randrange((END - START).days))
            text, spans_gt = phi.plant_note(rng, p, d)
            note_id = f"N{len(notes):08d}"
            notes.append({"note_id": note_id,
                          "member_id": member["member_id"], "text": text})
            for s in spans_gt:
                truth_spans.append({"note_id": note_id, **s})

    return {"members": members, "eligibility": eligibility, "claims": claims,
            "notes": notes, "truth_spans": truth_spans}


def _poisson(rng, lam):
    """random.Random has no poisson; Knuth's algorithm, adequate at these rates."""
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(rng.gauss(lam, lam ** 0.5)))
    import math
    ell, k, p = math.exp(-lam), 0, 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= ell:
            return k - 1
