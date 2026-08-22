"""Payer analytics on the de-identified output. The denominator is the subject.

THE MEMBER-MONTH SPINE
----------------------
PMPM means "per member per month", and the denominator is MEMBER-MONTHS: the
sum over members of the months each was actually enrolled. It is not
`n_members * n_months`, and it is not `count(distinct member_id)` in the
period.

This is the first thing one payer analyst checks in another's work, because
getting it wrong is both easy and invisible. A plan with 100,000 members at the
start of the year and 92,000 at the end does not have 1,200,000 member-months.
If you use a calendar denominator while membership is falling, PMPM is
understated -- and it is understated by MORE each month, so a flat cost trend
renders as a declining one and nobody investigates a metric that is improving.

Enrolment here is prorated by days within each month against the days in that
month, so a member enrolled 1-15 March contributes 15/31 of a member-month.
Whole-month conventions (member-month if enrolled on the 15th, say) are also
defensible and are what many plans use; what is not defensible is leaving the
convention undocumented, so it is stated here and in the metric dictionary.

THE DECOMPOSITION
-----------------
PMPM = U x P where U = services per member-month and P = paid per service.
Splitting a PMPM change into price, utilisation and mix is what turns "costs
are up 12%" into an action, because the three have completely different
owners: price is a contracting problem, utilisation is a care-management
problem, and mix is usually a coding or site-of-service problem.

    PMPM = sum_c (U_c * P_c)
         = U * sum_c (share_c * P_c)

    dPMPM ~= dU * Pbar_0          (utilisation)
           + U_0 * sum_c share_c0 * dP_c   (price)
           + U_0 * sum_c dshare_c * P_c0   (mix)
           + residual                       (interaction, reported not hidden)

The residual is printed. A decomposition that sums to something other than the
actual change, without saying so, is worse than no decomposition.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date


def _month_iter(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def member_month_spine(eligibility, start, end, prorate=True):
    """{(year, month): member_months} plus a per-member breakdown.

    Prorated by enrolled days over days-in-month. See the module docstring for
    why the convention is stated rather than assumed.
    """
    by_month = defaultdict(float)
    by_member_month = defaultdict(float)
    for span in eligibility:
        a, b = span["span_start"], span["span_end"]
        for y, m in _month_iter(max(a, start), min(b, end)):
            dim = calendar.monthrange(y, m)[1]
            m0, m1 = date(y, m, 1), date(y, m, dim)
            lo, hi = max(a, m0), min(b, m1)
            if hi < lo:
                continue
            days = (hi - lo).days + 1
            frac = days / dim if prorate else (1.0 if days >= 15 else 0.0)
            by_month[(y, m)] += frac
            by_member_month[(span["member_id"], y, m)] += frac
    return dict(by_month), dict(by_member_month)


def pmpm_by_category(claims, spine, start, end):
    """{(year, month): {category: {paid, services, pmpm}}} plus totals."""
    paid = defaultdict(lambda: defaultdict(float))
    services = defaultdict(lambda: defaultdict(int))
    for c in claims:
        d = c["service_date"]
        if not (start <= d <= end):
            continue
        key = (d.year, d.month)
        paid[key][c["service_category"]] += c["paid_amount"]
        services[key][c["service_category"]] += c["units"]

    out = {}
    for key, mm in sorted(spine.items()):
        if mm <= 0:
            continue
        cats = {}
        for cat in set(paid[key]) | set(services[key]):
            cats[cat] = {
                "paid": paid[key][cat], "services": services[key][cat],
                "pmpm": paid[key][cat] / mm,
                "util_per_1000": services[key][cat] / mm * 1000,
                "price_per_service": (paid[key][cat] / services[key][cat]
                                      if services[key][cat] else 0.0),
            }
        out[key] = {"member_months": mm, "categories": cats,
                    "total_paid": sum(paid[key].values()),
                    "total_services": sum(services[key].values()),
                    "pmpm": sum(paid[key].values()) / mm}
    return out


def quarter_rollup(monthly):
    q = defaultdict(lambda: {"member_months": 0.0, "paid": defaultdict(float),
                             "services": defaultdict(int)})
    for (y, m), row in monthly.items():
        key = (y, (m - 1) // 3 + 1)
        q[key]["member_months"] += row["member_months"]
        for cat, v in row["categories"].items():
            q[key]["paid"][cat] += v["paid"]
            q[key]["services"][cat] += v["services"]
    out = {}
    for key, v in sorted(q.items()):
        mm = v["member_months"]
        cats = {c: {"paid": v["paid"][c], "services": v["services"][c],
                    "pmpm": v["paid"][c] / mm if mm else 0.0,
                    "price_per_service": (v["paid"][c] / v["services"][c]
                                          if v["services"][c] else 0.0),
                    "util_per_member_month": (v["services"][c] / mm if mm else 0.0)}
                for c in v["paid"]}
        out[key] = {"member_months": mm, "categories": cats,
                    "total_paid": sum(v["paid"].values()),
                    "pmpm": sum(v["paid"].values()) / mm if mm else 0.0}
    return out


def decompose(period0, period1):
    """Split the PMPM change between two periods into price / utilisation / mix."""
    cats = sorted(set(period0["categories"]) | set(period1["categories"]))

    def u(p, c):
        return p["categories"].get(c, {}).get("util_per_member_month", 0.0)

    def price(p, c):
        return p["categories"].get(c, {}).get("price_per_service", 0.0)

    U0 = sum(u(period0, c) for c in cats)
    U1 = sum(u(period1, c) for c in cats)
    share0 = {c: (u(period0, c) / U0 if U0 else 0.0) for c in cats}
    share1 = {c: (u(period1, c) / U1 if U1 else 0.0) for c in cats}
    pbar0 = sum(share0[c] * price(period0, c) for c in cats)

    util_effect = (U1 - U0) * pbar0
    price_effect = U0 * sum(share0[c] * (price(period1, c) - price(period0, c))
                            for c in cats)
    mix_effect = U0 * sum((share1[c] - share0[c]) * price(period0, c)
                          for c in cats)
    actual = period1["pmpm"] - period0["pmpm"]
    residual = actual - (util_effect + price_effect + mix_effect)

    per_cat = {}
    for c in cats:
        per_cat[c] = {
            "price_effect": U0 * share0[c] * (price(period1, c) - price(period0, c)),
            "util_effect": (u(period1, c) - u(period0, c)) * price(period0, c),
            "pmpm_0": period0["categories"].get(c, {}).get("pmpm", 0.0),
            "pmpm_1": period1["categories"].get(c, {}).get("pmpm", 0.0),
        }
    return {
        "pmpm_0": period0["pmpm"], "pmpm_1": period1["pmpm"],
        "actual_change": actual,
        "pct_change": actual / period0["pmpm"] if period0["pmpm"] else 0.0,
        "member_months_0": period0["member_months"],
        "member_months_1": period1["member_months"],
        "member_month_change_pct": (
            (period1["member_months"] - period0["member_months"])
            / period0["member_months"] if period0["member_months"] else 0.0),
        "utilisation_effect": util_effect, "price_effect": price_effect,
        "mix_effect": mix_effect, "residual": residual,
        "per_category": per_cat,
    }


def cost_concentration(claims, start, end):
    """The 5%/50% curve: what share of spend the costliest members account for."""
    per_member = defaultdict(float)
    for c in claims:
        if start <= c["service_date"] <= end:
            per_member[c["member_id"]] += c["paid_amount"]
    if not per_member:
        return {}
    ordered = sorted(per_member.values(), reverse=True)
    total = sum(ordered)
    n = len(ordered)
    out = {}
    for pct in (1, 5, 10, 20, 50):
        k = max(1, int(round(n * pct / 100)))
        out[f"top_{pct}pct_share"] = sum(ordered[:k]) / total
    out["n_members_with_spend"] = n
    out["total_paid"] = total
    return out


def risk_context(members):
    """A simple HCC-like condition-category summary.

    The real system is CMS-HCC: ICD-10 codes map to Condition Categories,
    categories carry coefficients, hierarchies suppress a lesser category when
    a more severe one in the same family is present, and the resulting risk
    score normalises payment. None of that is implemented here.

    It is included at all because comparing PMPM between populations WITHOUT
    risk context is the most common way payer analytics misleads: a plan whose
    costs rose 9% while its risk score rose 11% got cheaper, not dearer, and
    only the risk-adjusted comparison shows it.
    """
    from synth import HCC_LIKE
    n = len(members)
    prev = {}
    for key, (code, desc, weight) in HCC_LIKE.items():
        c = sum(1 for m in members if m.get(f"cond_{key}"))
        prev[code] = {"description": desc, "coefficient": weight,
                      "n_members": c, "prevalence": c / n if n else 0.0}
    scores = [m["risk_score"] for m in members]
    return {"categories": prev,
            "mean_risk_score": sum(scores) / len(scores) if scores else 0.0,
            "n_members": n}

def category_cells(claims, start, end):
    """Per (category, quarter) cell: paid, member count, and top-contributor share.

    THE MEMBER COUNT AND THE SHARE ARE THE POINT. A published cell cannot be
    checked for disclosure risk without them:

      * the COUNT decides the threshold rule (n < 11 is not an aggregate, it is
        a small number of people with their spend printed next to their
        condition);
      * the SHARE decides the dominance rule, which catches the case a threshold
        misses entirely -- a 500-member cell where one member is 95% of the
        spend discloses that member's cost to anyone who knows they are in it.

    Aggregation that throws away the count is aggregation that cannot be
    audited, which is why this is computed alongside the rollup rather than
    reconstructed later.
    """
    from collections import defaultdict

    cells = defaultdict(lambda: defaultdict(float))
    members = defaultdict(lambda: defaultdict(set))
    per_member = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

    for c in claims:
        d = c["service_date"]
        if not (start <= d <= end):
            continue
        q = f"{d.year}Q{(d.month - 1) // 3 + 1}"
        cat = c["service_category"]
        cells[cat][q] += c["paid_amount"]
        members[cat][q].add(c["member_id"])
        per_member[cat][q][c["member_id"]] += c["paid_amount"]

    out = {}
    for cat, per_q in cells.items():
        out[cat] = {}
        for q, paid in per_q.items():
            spends = sorted(per_member[cat][q].values(), reverse=True)
            out[cat][q] = {
                "paid": paid,
                "n_members": len(members[cat][q]),
                "top_share": (spends[0] / paid) if paid and spends else None,
            }
    return out

def geo_cells(claims, members, start, end):
    """Paid by (ZIP3, quarter) x service category, with counts and dominance shares.

    WHY THIS GRAIN. Chosen by measurement, not by guessing:

        zip3 x category                        55 cells,    0 with n<11
        zip3 x category x quarter             440 cells,   45 with n<11
        zip3 x sex x age-decade x category   1052 cells,  433 with n<11

    Category x quarter cells in this population hold 137 to 6,311 members, so
    no suppression rule would ever fire on them. A disclosure control that has
    never fired is not evidence that it works -- it is evidence that nobody has
    drilled in yet. Executives drill down until cells get small; that is what a
    drill-down is FOR, and it is exactly when suppression starts to matter.

    ZIP3 is not an arbitrary dimension either. It is one of the 18 HIPAA Safe
    Harbor identifiers and the same quasi-identifier `reidentify.py` uses to
    attack the member extract. The dashboard and the attack are looking at the
    same column from opposite sides.

    Rows are (ZIP3, quarter) and columns are service categories, so a row total
    is the total spend for that geography and period -- which a dashboard would
    naturally publish, and which is precisely what makes complementary
    suppression necessary.
    """
    zip3 = {m["member_id"]: str(m["zip5"])[:3] for m in members}
    paid = defaultdict(lambda: defaultdict(float))
    seen = defaultdict(lambda: defaultdict(set))
    per_member = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

    for c in claims:
        d = c["service_date"]
        if not (start <= d <= end):
            continue
        z = zip3.get(c["member_id"])
        if z is None:
            continue
        row = f"{z} {d.year}Q{(d.month - 1) // 3 + 1}"
        cat = c["service_category"]
        paid[row][cat] += c["paid_amount"]
        seen[row][cat].add(c["member_id"])
        per_member[row][cat][c["member_id"]] += c["paid_amount"]

    out = {}
    for z, cats in paid.items():
        out[z] = {}
        for cat, total in cats.items():
            spends = sorted(per_member[z][cat].values(), reverse=True)
            out[z][cat] = {
                "paid": total,
                "n_members": len(seen[z][cat]),
                "top_share": (spends[0] / total) if total and spends else None,
            }
    return out
