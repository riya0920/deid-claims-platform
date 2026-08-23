"""Two safe tables that are unsafe together, and the attribute disclosure
k-anonymity does not see.

Run:  python run_linkage.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import linkage as L
import suppression as SUP
import synth

OUT = "out"


def _tab(members, claims, row_fn, col_fn, subset=None):
    counts = defaultdict(lambda: defaultdict(set))
    values = defaultdict(lambda: defaultdict(float))
    mem = {m["member_id"]: m for m in members}
    for c in claims:
        m = mem.get(c["member_id"])
        if not m or (subset and not subset(m)):
            continue
        r, col = row_fn(m, c), col_fn(m, c)
        counts[r][col].add(c["member_id"])
        values[r][col] += c["paid_amount"]
    return ({r: {c: len(v) for c, v in cols.items()}
             for r, cols in counts.items()},
            {r: dict(cols) for r, cols in values.items()})


def main():
    os.makedirs(OUT, exist_ok=True)
    d = synth.generate(n_members=8000, seed=17)
    members, claims = d["members"], d["claims"]

    print("=" * 78)
    print("CROSS-TABLE LINKAGE -- the gap this README called its largest")
    print("=" * 78)

    reg = L.Register(min_cell=11)

    # ---- table A: everyone, ZIP3 x category --------------------------------
    # THE GRAIN IS CHOSEN BY MEASUREMENT, not guessed -- the same discipline
    # src/analytics.py uses for suppression. At ZIP3 x category the residual
    # (all minus diabetics) is 29 members at its smallest and no differencing
    # attack succeeds, so publishing at that grain would show the register
    # accepting everything and prove nothing. Measured:
    #
    #   zip3 x category                       55 cells,   0 residuals < 11
    #   zip3 x quarter x category            440 cells,  51 residuals < 11
    #   zip3 x sex x age-decade x category  1052 cells, 456 residuals < 11
    #
    # A disclosure control that has never refused anything is not evidence it
    # works. This is the first grain at which the attack bites.
    zip3 = lambda m, c: (f"{str(m['zip5'])[:3]} {c['service_date'].year}"
                         f"Q{(c['service_date'].month - 1) // 3 + 1}")
    cat = lambda _m, c: c["service_category"]
    cA, vA = _tab(members, claims, zip3, cat)
    pubA, _n = SUP.suppress_table(
        {r: {c: {"n": cA[r][c], "value": vA[r][c], "top_share": None}
             for c in cA[r]} for r in cA}, total_published=True)
    reg.check(name="A: spend by ZIP3 x category (all members)",
              dims=("zip3", "category"), counts=cA, values=pubA,
              population="all")
    print(f"\n  Table A published: {len(cA)} ZIP3 rows x "
          f"{len(next(iter(cA.values())))} categories, all members.")

    # ---- table B: diabetics only, same dims ---------------------------------
    diabetic = lambda m: m.get("cond_diabetes_complication")
    cB, vB = _tab(members, claims, zip3, cat, subset=diabetic)
    pubB, _n = SUP.suppress_table(
        {r: {c: {"n": cB[r][c], "value": vB[r][c], "top_share": None}
             for c in cB[r]} for r in cB}, total_published=True)

    print("\n  Table B: the SAME breakdown, restricted to members with a")
    print("  diabetes-with-complications flag. It passes the small-cell rule")
    print("  on its own. Publishing it:")
    try:
        reg.check(name="B: spend by ZIP3 x category (diabetics)",
                  dims=("zip3", "category"), counts=cB, values=pubB,
                  population="diabetes_complication")
        print("    ...accepted (no differencing attack succeeded)")
        refused = None
    except L.DisclosureRefused as exc:
        refused = str(exc)
        print(f"    REFUSED.\n    {refused[:280]}")

    if refused:
        print("\n  THE ATTACK. Subtract B from A and you have a table over")
        print("  NON-diabetics -- a population nobody chose to publish, nobody")
        print("  suppressed, and nobody checked. The exposed cell appears in")
        print("  NEITHER table. Checking each table in isolation cannot find")
        print("  this, which is why statistical agencies keep a release")
        print("  register rather than a per-table gate.")
        print("\n  The refusal is fatal, not a warning. A warning on a")
        print("  publication path is a warning that gets clicked through, and")
        print("  a table cannot be unpublished.")

    # ---- l-diversity ---------------------------------------------------------
    print("\n" + "=" * 78)
    print("l-DIVERSITY -- the gap k-anonymity leaves")
    print("=" * 78)
    rows = [{"zip3": str(m["zip5"])[:3], "sex": m["sex"],
             "age_band": (m["age"] // 10) * 10,
             "dx": next((k.replace("cond_", "") for k in m
                         if k.startswith("cond_") and m[k]), "none")}
            for m in members]
    quasi = ["zip3", "sex", "age_band"]
    res = L.l_diversity(rows, "dx", quasi, l=2)
    print(f"  {res['n_classes']} equivalence classes on {quasi}")
    print(f"  classes failing l=2: {res['n_violations']}")
    # SPLIT BY WHAT IS DISCLOSED. A class that discloses "no recorded
    # condition" is a violation of the definition and is nearly harmless; one
    # that discloses a diagnosis is the case the property exists for, and
    # reporting a single count merges them.
    real = [v for v in res["violations"] if v["disclosed_value"] not in
            (None, "none")]
    none_only = [v for v in res["violations"] if v not in real]
    print(f"    disclosing an actual diagnosis : {len(real)}")
    print(f"    disclosing 'no condition'      : {len(none_only)}")
    for v in real[:3]:
        print(f"      {v['equivalence_class']}  size={v['size']}  "
              f"discloses {v['disclosed_value']!r}")
    for v in none_only[:2]:
        print(f"      {v['equivalence_class']}  size={v['size']}  "
              f"discloses {v['disclosed_value']!r}  (harmless in practice)")
    if not res["violations"]:
        print("    none -- every class carries at least two distinct diagnoses")

    if not real:
        print("")
        print("  A CLEAN NEGATIVE, and it is a property of the generator")
        print("  rather than of the de-identification. src/synth.py draws")
        print("  each HCC-like flag independently at 5-6% prevalence, so an")
        print("  equivalence class almost never shares a diagnosis. Real")
        print("  populations cluster -- by geography, by age, by referral")
        print("  pattern -- and that clustering is what produces homogeneous")
        print("  classes. This data cannot produce the failure, so the")
        print("  detector is demonstrated firing on a constructed class in")
        print("  tests/test_linkage.py rather than reported as a pass.")
    print(f"\n  {res['note']}")
    print("\n  The point stands regardless of the count: a class of 20 members")
    print("  who share age, sex and ZIP3 is 20-ANONYMOUS and discloses their")
    print("  diagnosis completely if all 20 share one. The attacker never has")
    print("  to identify which record is their target.")

    payload = {"linkage_refused": refused, "l_diversity": {
        k: v for k, v in res.items() if k != "violations"},
        "n_violations": res["n_violations"]}
    with open(f"{OUT}/linkage.json", "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(f"\nwrote {OUT}/linkage.json")
    return payload


if __name__ == "__main__":
    main()
