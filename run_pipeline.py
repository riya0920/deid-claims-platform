"""Govern, then analyse. The two-stage shape this whole project is about.

    generate -> re-contaminate with PHI -> de-identify -> MEASURE -> analyse

The measurement step in the middle is the one that matters. Everything before
it is setup and everything after it is ordinary analytics; the reason the
analytics are allowed to exist at all is the number the middle step produces.

Run:  python run_pipeline.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import analytics
import deid
import phi
import synth

OUT = "out"
SALT = "demo-salt-not-a-secret"
START, END = date(2023, 1, 1), date(2024, 12, 31)


def main(n_members=8000):
    os.makedirs(OUT, exist_ok=True)
    print("generating claims and planting PHI...")
    data = synth.generate(n_members)
    members = data["members"]
    print(f"  members            {len(members):,}")
    print(f"  eligibility spans  {len(data['eligibility']):,}")
    print(f"  claims             {len(data['claims']):,}")
    print(f"  free-text notes    {len(data['notes']):,}")
    print(f"  planted PHI spans  {len(data['truth_spans']):,}")
    unseen = sum(1 for m in members if not m["name_in_gazetteer"])
    print(f"  members whose name is NOT in the detector's gazetteer: "
          f"{unseen:,} ({unseen/len(members):.0%})")

    # ---- de-identify and score ------------------------------------------
    print("\nde-identifying free text and scoring against planted truth...")
    truth_by_note = {}
    for s in data["truth_spans"]:
        truth_by_note.setdefault(s["note_id"], []).append(s)

    all_truth, all_detected = [], []
    clean_notes = []
    for note in data["notes"]:
        clean, detected = deid.redact_text(note["text"], note["member_id"], SALT)
        clean_notes.append({"note_id": note["note_id"], "text": clean})
        offset_truth = truth_by_note.get(note["note_id"], [])
        all_truth.extend(offset_truth)
        all_detected.extend(detected)

    # score per note so spans from different notes cannot match each other
    agg = {}
    for note in data["notes"]:
        t = truth_by_note.get(note["note_id"], [])
        _clean, d = deid.redact_text(note["text"], note["member_id"], SALT)
        st = deid.score(t, d)
        for k, v in st.items():
            b = agg.setdefault(k, {"tp": 0, "fn": 0, "fp": 0, "n_truth": 0})
            for f in ("tp", "fn", "fp", "n_truth"):
                b[f] += v[f]
    for k, b in agg.items():
        b["recall"] = b["tp"] / b["n_truth"] if b["n_truth"] else float("nan")
        b["precision"] = (b["tp"] / (b["tp"] + b["fp"])
                          if (b["tp"] + b["fp"]) else float("nan"))

    print("\n" + "=" * 76)
    print("DE-IDENTIFICATION PERFORMANCE vs PLANTED GROUND TRUTH")
    print("=" * 76)
    print("  Recall is the number that matters: a miss is a disclosure. A false")
    print("  positive is over-redaction -- a real cost to analytic utility, but")
    print("  a recoverable one. They are never averaged into an F1 here.")
    print()
    print(f"  {'identifier':<16}{'planted':>9}{'found':>7}{'missed':>8}"
          f"{'recall':>9}{'over-redact':>13}")
    for k in sorted(agg, key=lambda x: -agg[x]["n_truth"]):
        b = agg[k]
        if b["n_truth"] == 0:
            continue
        print(f"  {k:<16}{b['n_truth']:>9,}{b['tp']:>7,}{b['fn']:>8,}"
              f"{b['recall']:>9.1%}{b['fp']:>13,}")
    tot_truth = sum(b["n_truth"] for b in agg.values())
    tot_tp = sum(b["tp"] for b in agg.values())
    tot_fp = sum(b["fp"] for b in agg.values())
    print(f"  {'OVERALL':<16}{tot_truth:>9,}{tot_tp:>7,}"
          f"{tot_truth-tot_tp:>8,}{tot_tp/tot_truth:>9.1%}{tot_fp:>13,}")

    name = agg.get("name", {})
    if name.get("n_truth"):
        print(f"\n  Names recall {name['recall']:.1%}. This is BELOW 100% by")
        print("  construction: 30% of members carry surnames deliberately absent")
        print("  from the detector's gazetteer, so those are caught only by")
        print("  context rules. A name detector scored against the same list it")
        print("  was built from reports 100% and measures nothing.")

    print("\n  What this number does NOT establish:")
    print("   * that the residual is not identifiable. Safe Harbor's second")
    print("     limb is 'no actual knowledge' that the remainder could identify")
    print("     someone, and identifier #18 -- any other unique characteristic")
    print("     -- cannot be regexed. A rare diagnosis plus a 3-digit ZIP is")
    print("     identifying with every one of the 17 other identifiers removed.")
    print("   * that de-identification is sufficient. It is one layer. Minimum")
    print("     necessary, access control, and audit are the others, and a")
    print("     missed name in a system with all three is contained in a way it")
    print("     is not in a system with only de-identification.")

    # ---- structured de-identification ------------------------------------
    deid_members = [deid.deidentify_member(m, SALT) for m in members]
    small_zip = sum(1 for d in deid_members if d["zip3"] == "000")
    over89 = sum(1 for d in deid_members if d["age_band"] == "90+")
    print("\n  structured columns:")
    print(f"    ZIP truncated to 3 digits; {small_zip:,} members in a ZIP3 with")
    print(f"      <= {phi.SMALL_ZIP3_THRESHOLD:,} people were set to 000")
    print(f"    {over89:,} members aged 90+ aggregated into a single band")
    print(f"    dates shifted per patient, not deleted -- intervals preserved")

    # ---- analytics --------------------------------------------------------
    print("\n" + "=" * 76)
    print("MEMBER-MONTH SPINE")
    print("=" * 76)
    spine, _ = analytics.member_month_spine(data["eligibility"], START, END)
    monthly = analytics.pmpm_by_category(data["claims"], spine, START, END)
    total_mm = sum(spine.values())
    naive_mm = len(members) * len(spine)
    print(f"  actual member-months        {total_mm:>12,.0f}")
    print(f"  naive (members x months)    {naive_mm:>12,.0f}")
    print(f"  overstatement if you use the naive denominator: "
          f"{naive_mm/total_mm - 1:.1%}")
    print(f"  -> PMPM would be understated by {1 - total_mm/naive_mm:.1%},")
    print("     uniformly, which is exactly the kind of error nobody")
    print("     investigates because it makes the trend look better.")

    quarters = analytics.quarter_rollup(monthly)
    print("\n  PMPM by quarter")
    print(f"    {'quarter':<10}{'member-months':>15}{'total paid':>15}{'PMPM':>10}")
    for (y, q), row in sorted(quarters.items()):
        print(f"    {f'{y}Q{q}':<10}{row['member_months']:>15,.0f}"
              f"{row['total_paid']:>15,.0f}{row['pmpm']:>10,.2f}")

    # ---- the decomposition ------------------------------------------------
    q2, q3 = (2024, 2), (2024, 3)
    dec = analytics.decompose(quarters[q2], quarters[q3])
    print("\n" + "=" * 76)
    print(f"PMPM MOVED {dec['pct_change']:+.1%} FROM 2024Q2 TO 2024Q3. WHAT HAPPENED?")
    print("=" * 76)
    print("  Denominator first, always. A PMPM move is a ratio move, and half")
    print("  the time the numerator is innocent.\n")
    print(f"    member-months  {dec['member_months_0']:>12,.0f} -> "
          f"{dec['member_months_1']:>12,.0f}  "
          f"({dec['member_month_change_pct']:+.1%})")
    print(f"    PMPM           {dec['pmpm_0']:>12,.2f} -> {dec['pmpm_1']:>12,.2f}  "
          f"({dec['pct_change']:+.1%})")
    print(f"\n  Decomposition of the {dec['actual_change']:+,.2f} PMPM change:")
    for label, key in [("price", "price_effect"),
                       ("utilisation", "utilisation_effect"),
                       ("mix", "mix_effect"), ("residual/interaction", "residual")]:
        share = dec[key] / dec["actual_change"] if dec["actual_change"] else 0
        print(f"    {label:<22}{dec[key]:>10,.2f}  ({share:>6.0%})")
    print("\n  by category, price effect:")
    for cat, v in sorted(dec["per_category"].items(),
                         key=lambda kv: -abs(kv[1]["price_effect"])):
        print(f"    {cat:<16}{v['price_effect']:>10,.2f}   "
              f"(PMPM {v['pmpm_0']:,.2f} -> {v['pmpm_1']:,.2f})")

    ip0 = quarters[q2]["categories"]["inpatient"]["price_per_service"]
    ip1 = quarters[q3]["categories"]["inpatient"]["price_per_service"]
    print("\n  PLANTED TRUTH vs RECOVERED:")
    print(f"    planted   inpatient unit price x{synth.SHOCK['inpatient_price_multiplier']:.2f}, "
          f"membership -{synth.SHOCK['membership_drop']:.0%}, utilisation unchanged")
    print(f"    recovered inpatient unit price x{ip1/ip0:.2f}, "
          f"member-months {dec['member_month_change_pct']:+.1%}, "
          f"utilisation effect {dec['utilisation_effect']/dec['actual_change'] if dec['actual_change'] else 0:.0%} of the move")
    print("\n  The instinctive reading -- 'utilisation is up, members are sicker'")
    print("  -- is false in both clauses. It is a price renegotiation and a")
    print("  group termination. An analyst who does not check the denominator")
    print("  first chases the wrong problem for a week.")

    # ---- concentration and risk ------------------------------------------
    conc = analytics.cost_concentration(data["claims"], START, END)
    print("\n" + "=" * 76)
    print("HIGH-COST CONCENTRATION AND RISK CONTEXT")
    print("=" * 76)
    for pct in (1, 5, 10, 20, 50):
        print(f"  top {pct:>2}% of members account for "
              f"{conc[f'top_{pct}pct_share']:.1%} of paid")
    risk = analytics.risk_context(members)
    print(f"\n  mean risk score {risk['mean_risk_score']:.3f} "
          f"across {risk['n_members']:,} members")
    print(f"  {'category':<10}{'description':<44}{'prevalence':>11}")
    for code, v in sorted(risk["categories"].items()):
        print(f"  {code:<10}{v['description']:<44}{v['prevalence']:>11.1%}")
    print("\n  This is HCC-LIKE, not CMS-HCC. The real model maps ICD-10 to")
    print("  condition categories, applies hierarchies that suppress a lesser")
    print("  category when a severe one in the same family is present, and")
    print("  normalises payment. None of that is implemented. It is here")
    print("  because comparing PMPM across populations without risk context is")
    print("  the most common way payer analytics misleads.")

    payload = {
        "deid": {k: {kk: (None if vv != vv else vv) for kk, vv in v.items()}
                 for k, v in agg.items()},
        "deid_overall_recall": tot_tp / tot_truth,
        "member_months": total_mm, "naive_member_months": naive_mm,
        "quarters": {f"{y}Q{q}": {"member_months": r["member_months"],
                                  "pmpm": r["pmpm"], "paid": r["total_paid"]}
                     for (y, q), r in quarters.items()},
        "decomposition": dec, "concentration": conc, "risk": risk,
        "planted_shock": synth.SHOCK,
    }
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    with open(f"{OUT}/deidentified_notes.txt", "w", encoding="utf-8") as fh:
        for n in clean_notes[:40]:
            fh.write(n["text"] + "\n")
    print(f"\nwrote {OUT}/results.json and {OUT}/deidentified_notes.txt")
    return payload


if __name__ == "__main__":
    main()
