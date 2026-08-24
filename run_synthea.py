"""Run the claims analytics over Synthea, and compare the two populations.

WHY
---
`src/synth.py` writes claims-shaped data directly. That makes the risk equation
recoverable and the analytics checkable, and it is also a closed loop: the
generator emits exactly the five categories the analytics expect, at exactly
the grain they assume.

Synthea is free, is not written by me, and drives utilisation from clinical
modules rather than a rate table. Two things came out of pointing the analytics
at it, and the second is about this project's own data.

1. THE CATEGORY TAXONOMY HAS HOLES
   Synthea emits ten encounter classes; this project has five categories.
   Skilled nursing, hospice and home health have no bucket at all -- around 4%
   of spend. Folding them into `inpatient` would be wrong in a measurable way:
   the per-claim costs span more than an order of magnitude, so the merged
   average would describe none of them. The exact multiples depend on the
   window, so `run()` computes them rather than this docstring asserting them.

2. THIS PROJECT'S GENERATOR HAS A LIGHT TAIL, AND THAT MATTERS MOST
   Cost concentration is the number a care-management programme is sized on,
   and the README argues for reporting it precisely because the distribution is
   skewed. Measured, the generator's distribution is much LESS skewed than
   Synthea's:

       top 1% of members     4.8% of spend   vs Synthea 13.3%
       top 5% of members    18.4% of spend   vs Synthea 43.0%
       p99 / mean spend        4.1x          vs Synthea 10.6x

   This is structural, not bad luck. `synth.generate` draws each category as
   Poisson(rate x risk_score x covered_days) and prices it with a bounded
   uniform. The only between-member driver is `risk_score`, which spans 0.60 to
   2.08 -- a range of 3.5x. **No member can be fifty times the mean, because
   nothing in the model lets them be.** Real books have one transplant.

   So the generator is fine for recovering a planted rate and wrong for
   anything that depends on the shape of the tail.

Run:  python run_synthea.py
"""

from __future__ import annotations

import collections
import os
import statistics
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import analytics
import synth
import synthea_claims as SC

START, END = date(2023, 1, 1), date(2024, 12, 31)

# The Synthea CSV export. DATA-2 generates the population; this project reads
# it rather than generating a second 6GB copy.
DEFAULT_CSV = os.path.normpath(os.path.join(
    ROOT, "..", "data2-fhir-hedis", "synthea_out", "output", "csv"))


def _per_claim_table(cov):
    """Per-claim cost by category, COMPUTED.

    An earlier draft hard-coded the multiples. They are window-dependent --
    over 2020-2024 skilled nursing ran 1.48x inpatient, over 2023-2024 it runs
    closer to 1.2x and hospice lands near parity -- so quoting a fixed number
    in prose guarantees the prose and the run eventually disagree.
    """
    base = cov["by_category"].get("inpatient")
    ref = (base["paid"] / base["claims"]) if base and base["claims"] else None
    lines = ["| category | $ per claim | vs inpatient |", "|---|---|---|"]
    for cat in ("snf", "hospice", "inpatient", "home"):
        info = cov["by_category"].get(cat)
        if not info or not info["claims"]:
            continue
        each = info["paid"] / info["claims"]
        rel = "—" if cat == "inpatient" or not ref else "%.2fx" % (each / ref)
        lines.append("| `%s` | $%s | %s |"
                     % (cat, format(int(each), ","), rel))
    return chr(10).join(lines)


def _member_spend(claims):
    per = collections.Counter()
    for c in claims:
        if START <= c["service_date"] <= END:
            per[c["member_id"]] += c["paid_amount"]
    return sorted(per.values(), reverse=True)


def _profile(name, data):
    spine, _ = analytics.member_month_spine(data["eligibility"], START, END)
    monthly = analytics.pmpm_by_category(data["claims"], spine, START, END)
    conc = analytics.cost_concentration(data["claims"], START, END)
    spend = _member_spend(data["claims"])
    member_months = sum(spine.values())
    paid = sum(r["total_paid"] for r in monthly.values())
    mean = statistics.mean(spend) if spend else 0.0
    return {
        "name": name,
        "members_with_spend": len(spend),
        "member_months": member_months,
        "total_paid": paid,
        "pmpm": paid / member_months if member_months else 0.0,
        "top_1pct": conc.get("top_1pct_share"),
        "top_5pct": conc.get("top_5pct_share"),
        "top_10pct": conc.get("top_10pct_share"),
        "p99_over_mean": (spend[int(len(spend) * 0.01)] / mean)
        if spend and mean else 0.0,
        "max_over_mean": (spend[0] / mean) if spend and mean else 0.0,
    }


def run(csv_dir=None):
    csv_dir = csv_dir or DEFAULT_CSV
    if not os.path.isdir(csv_dir):
        raise SystemExit(
            "no Synthea CSV export at %s -- generate one with\n"
            "  cd ../data2-fhir-hedis && python run_synthea.py --generate"
            % csv_dir)

    syn = SC.load(csv_dir, start=START, end=END)
    own = synth.generate(n_members=8000, seed=17)
    return (_profile("synthea", syn), _profile("own generator", own),
            SC.coverage_summary(syn))


def main():
    a, b, cov = run()

    print("=" * 76)
    print("  %-22s %18s %18s" % ("", "SYNTHEA", "OWN GENERATOR"))
    rows = [
        ("members with spend", "members_with_spend", "%d"),
        ("member months", "member_months", "%.0f"),
        ("total paid", "total_paid", "$%.0f"),
        ("PMPM", "pmpm", "$%.2f"),
        ("top 1% share", "top_1pct", "%.4f"),
        ("top 5% share", "top_5pct", "%.4f"),
        ("top 10% share", "top_10pct", "%.4f"),
        ("p99 / mean spend", "p99_over_mean", "%.1fx"),
        ("max / mean spend", "max_over_mean", "%.1fx"),
    ]
    for label, key, fmt in rows:
        print("  %-22s %18s %18s"
              % (label, fmt % a[key], fmt % b[key]))
    print("=" * 76)
    print()
    print("  categories with NO bucket in this project (%.2f%% of spend):"
          % (100 * cov["unmapped_share"]))
    for cat, why in cov["unmapped_categories"].items():
        info = cov["by_category"][cat]
        print("     %-9s %6d claims  $%-12s  %s"
              % (cat, info["claims"], format(int(info["paid"]), ","), why))
    print()
    print("  per-claim cost, to show why folding them into inpatient is wrong:")
    for cat in ("snf", "hospice", "inpatient", "home"):
        info = cov["by_category"].get(cat)
        if info and info["claims"]:
            print("     %-9s $%s" % (cat,
                                     format(int(info["paid"] / info["claims"]),
                                            ",")))

    doc = os.path.join(ROOT, "docs")
    os.makedirs(doc, exist_ok=True)
    path = os.path.join(doc, "SYNTHEA_COMPARISON.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("""# The analytics on Synthea, and what it says about our own data

`src/synth.py` writes claims-shaped data directly. That makes the risk equation
recoverable and the analytics checkable -- and it is a **closed loop**: the
generator emits exactly the five categories the analytics expect, at exactly
the grain they assume.

[Synthea](https://github.com/synthetichealth/synthea) is free, is not written
by me, and drives utilisation from clinical modules rather than a rate table.

| | Synthea | own generator |
|---|---|---|
| members with spend | %d | %d |
| member months | %.0f | %.0f |
| PMPM | $%.2f | $%.2f |
| **top 1%% share of spend** | **%.1f%%** | **%.1f%%** |
| **top 5%% share of spend** | **%.1f%%** | **%.1f%%** |
| top 10%% share of spend | %.1f%% | %.1f%% |
| p99 / mean member spend | **%.1fx** | **%.1fx** |
| max / mean member spend | %.1fx | %.1fx |

## 1. The category taxonomy has holes

Synthea emits ten encounter classes; this project has five categories. Skilled
nursing, hospice and home health have **no bucket at all** -- %.2f%% of spend.

Folding them into `inpatient` is the tempting fix and it is wrong in a
measurable way:

%s

A merged average describes none of them -- the spread here is more than
twentyfold from home health to skilled nursing. So `CATEGORY_MAP` is explicit and
**deliberately incomplete** -- unmapped classes keep their own names and are
reported separately, which puts the hole in the output instead of hiding it in
an average.

## 2. Our own generator has a light tail, and that is the bigger finding

Cost concentration is the number a care-management programme is sized on, and
this project's README argues for reporting it *precisely because the
distribution is skewed*. Measured against Synthea, our own distribution is
**much less skewed** -- the top 5%% of members carry %.1f%% of spend here
against %.1f%% in Synthea.

**This is structural, not bad luck.** `synth.generate` draws each category as
`Poisson(rate x risk_score x covered_days)` and prices it with a bounded
uniform `U(0.55, 1.6)`. The only between-member driver is `risk_score`, which
spans **0.60 to 2.08 -- a range of 3.5x**.

No member *can* be fifty times the mean, because nothing in the model lets them
be. Real books have one transplant, one long NICU stay, one haemophilia
patient, and those single members move a PMPM.

### What that means for the analytics built on it

- `cost_concentration` is **correct code measured on an unrepresentative
  population**. The function is fine; the number it produces here is not a
  guide to what it would produce on a real book.
- Anything sized off the top-N%% -- outreach capacity, stop-loss attachment,
  high-cost-claimant review -- would be sized wrong.
- The generator remains the right tool for what it was built for: planting a
  rate and recovering it. It is the wrong tool for anything that depends on the
  shape of the tail, and that limitation was not previously written down.

## What this does not show

Synthea is still synthetic. Its module-driven utilisation produces a heavier
tail than a rate table does, which is closer to a real book, but "closer" is
not "correct" and nothing here validates either against actual claims.
""" % (a["members_with_spend"], b["members_with_spend"],
       a["member_months"], b["member_months"],
       a["pmpm"], b["pmpm"],
       100 * a["top_1pct"], 100 * b["top_1pct"],
       100 * a["top_5pct"], 100 * b["top_5pct"],
       100 * a["top_10pct"], 100 * b["top_10pct"],
       a["p99_over_mean"], b["p99_over_mean"],
       a["max_over_mean"], b["max_over_mean"],
       100 * cov["unmapped_share"],
       _per_claim_table(cov),
       100 * b["top_5pct"], 100 * a["top_5pct"]))
    print()
    print("wrote", path)


if __name__ == "__main__":
    main()
