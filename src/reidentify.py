"""A re-identification attack against our own Safe Harbor output.

WHY THIS FILE EXISTS
--------------------
The method document argued that Safe Harbor removes the 18 identifiers and
still cannot guarantee anonymity, because identifier #18 -- "any other unique
identifying number, characteristic, or code" -- cannot be regexed. That was
reasoning. This measures it.

The right test of a de-identifier is not its recall on the identifiers it knows
about. It is whether an adversary with a plausible external dataset can link
records back. Recall answers "did we remove what we meant to remove"; a linkage
attack answers the question that actually matters, which is "is the residual
still identifying".

THE THREAT MODEL, STATED
------------------------
The attacker has:
  * an external roll -- name, address, ZIP, date of birth, sex. Voter files,
    marketing databases and licence records all fit, and several are purchasable
    for a few hundred dollars. This is the standard assumption in the
    re-identification literature and it is not exotic.
  * the released de-identified extract.
  * no access to the original data, no insider, no cryptographic break.

The attacker links on QUASI-IDENTIFIERS -- fields that are not identifiers
individually and are identifying in combination. After Safe Harbor these are
what survive: 3-digit ZIP, age band, sex, state, and the clinical facts
themselves.

k-ANONYMITY AS THE MEASURE
--------------------------
A record is k-anonymous if at least k records in the release share its
quasi-identifier combination. k=1 means the record is UNIQUE on the released
quasi-identifiers, so anyone who knows those attributes about a person, and
knows the person is in the dataset, has found their row -- and with it every
clinical fact attached to it.

k-anonymity is a weak guarantee and is reported as one. It does not defend
against an attacker who knows something not in the quasi-identifier set, it
says nothing about attribute disclosure when a whole equivalence class shares a
diagnosis (which is what l-diversity was invented for), and it gives no formal
bound the way differential privacy does. It is used here because it is the
measure that maps directly onto the attack being run.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict


# ---------------------------------------------------------------------------
def equivalence_classes(records, quasi_identifiers):
    """Group records by their quasi-identifier tuple."""
    groups = defaultdict(list)
    for r in records:
        key = tuple(r.get(q) for q in quasi_identifiers)
        groups[key].append(r)
    return groups


def k_anonymity(records, quasi_identifiers):
    """Distribution of equivalence-class sizes, and the risk summary."""
    groups = equivalence_classes(records, quasi_identifiers)
    sizes = [len(v) for v in groups.values()]
    n = len(records)
    unique = sum(s for s in sizes if s == 1)
    under5 = sum(s for s in sizes if s < 5)
    return {
        "quasi_identifiers": list(quasi_identifiers),
        "n_records": n,
        "n_classes": len(groups),
        "min_k": min(sizes) if sizes else 0,
        "records_with_k_1": unique,
        "pct_unique": unique / n if n else 0.0,
        "records_with_k_under_5": under5,
        "pct_under_5": under5 / n if n else 0.0,
        "size_histogram": dict(sorted(Counter(sizes).items())[:10]),
    }


# ---------------------------------------------------------------------------
def build_external_roll(members, coverage=0.85, seed=99):
    """The dataset the attacker brings.

    Deliberately does NOT contain any clinical information -- that is the whole
    point of a linkage attack. It has identity plus demographics, which is what
    a voter file or a marketing list has, and the attacker uses it to attach a
    NAME to a clinical record they could not otherwise read.

    `coverage` < 1 because real external rolls are incomplete. An attacker who
    holds 85% of the population can still re-identify anyone in that 85%.
    """
    rng = random.Random(seed)
    roll = []
    for m in members:
        if rng.random() > coverage:
            continue
        roll.append({
            "name": f"{m['first_name']} {m['last_name']}",
            "zip5": m["zip5"], "state": m["state"],
            "age": m["age"], "sex": m["sex"],
            "true_member_id": m["member_id"],       # for SCORING only
        })
    return roll


def linkage_attack(released, roll, quasi_identifiers, id_field="member_key"):
    """Link the external roll to the released extract on quasi-identifiers.

    A link is only counted as a re-identification when the released record is
    UNIQUE on those quasi-identifiers AND exactly one roll entry matches. That
    is the conservative definition: if two people share the combination the
    attacker has narrowed it to two, which is a real privacy loss but is not a
    confident identification and is not counted here.
    """
    released_groups = equivalence_classes(released, quasi_identifiers)
    roll_groups = equivalence_classes(roll, quasi_identifiers)

    reidentified, ambiguous = [], 0
    for key, rel in released_groups.items():
        cand = roll_groups.get(key, [])
        if len(rel) == 1 and len(cand) == 1:
            reidentified.append({"released": rel[0][id_field],
                                 "guessed_name": cand[0]["name"],
                                 "true_member_id": cand[0]["true_member_id"],
                                 "quasi_identifiers": key})
        elif len(rel) == 1 and len(cand) > 1:
            ambiguous += 1

    return {"n_released": len(released), "n_roll": len(roll),
            "n_reidentified": len(reidentified),
            "rate": len(reidentified) / len(released) if released else 0.0,
            "n_unique_but_ambiguous_in_roll": ambiguous,
            "sample": reidentified[:5]}


def verify_attack(reidentified, key_to_member):
    """Was the attacker actually right?

    This is only possible because we hold the truth. An attacker does not get
    to check, which cuts both ways: they cannot confirm a hit, and they do not
    need to in order to cause harm by acting on it.
    """
    correct = sum(1 for r in reidentified
                  if key_to_member.get(r["released"]) == r["true_member_id"])
    return {"n_checked": len(reidentified), "n_correct": correct,
            "precision": correct / len(reidentified) if reidentified else 0.0}


# ---------------------------------------------------------------------------
def generalise_to_k(records, quasi_identifiers, k=5, generalisations=None):
    """Enforce k-anonymity by generalising, then suppressing what remains.

    Order matters: generalise first (coarsen a field for everyone), suppress
    last (drop the rows that still stand out). Suppression is the blunter tool
    and it is also the one that biases the dataset, because the rows it removes
    are exactly the unusual ones -- rare conditions, small geographies, extreme
    ages. A k-anonymised extract is systematically missing its outliers, and any
    analysis of rare disease on it is wrong in a direction nobody can see.
    """
    generalisations = generalisations or {}
    work = [dict(r) for r in records]

    for field, fn in generalisations.items():
        for r in work:
            if field in r:
                r[field] = fn(r[field])

    groups = equivalence_classes(work, quasi_identifiers)
    kept = [r for key, rows in groups.items() if len(rows) >= k for r in rows]
    suppressed = len(work) - len(kept)
    return kept, {"k": k, "n_in": len(records), "n_kept": len(kept),
                  "n_suppressed": suppressed,
                  "suppression_rate": suppressed / len(records) if records else 0.0}


def coarsen_age_band(band):
    """5-year bands -> 10-year bands. The first generalisation to reach for
    because it costs the least analytically."""
    if band == "90+":
        return "90+"
    try:
        lo = int(band.split("-")[0])
    except (ValueError, IndexError):
        return band
    decade = (lo // 10) * 10
    return f"{decade}-{decade + 9}"


def drop_zip(_z):
    """Suppress geography entirely -- state only. The heaviest generalisation
    available before suppression, and it destroys every geographic analysis the
    platform exists to do."""
    return "***"
