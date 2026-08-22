"""The metric dictionary -- as data the code reads, not documentation beside it.

WHY THIS IS A MODULE AND NOT A MARKDOWN FILE
--------------------------------------------
The README's gap list asked for "a metric dictionary as a separate artefact ...
where an analyst reads them, versioned, with owners". The obvious way to build
that is a document. The obvious way is wrong, and predictably so:

    a metric dictionary that can disagree with the code is a metric dictionary
    that WILL disagree with the code

within about two sprints, and nobody will notice, because the two artefacts are
read by different people at different times. The analyst reads the document,
the pipeline runs the code, and the number in the board deck is defined by
whichever the last engineer touched.

So the definitions live here as data, `docs/METRIC_DICTIONARY.md` is GENERATED
from them, and `metric_value()` is how the pipeline computes any metric it
reports. There is one definition of PMPM in this repository and both the
document and the dashboard are downstream of it.

WHAT A DEFINITION HAS TO CARRY
------------------------------
Not just a formula. The fields below are the ones that get argued about in
practice, and every one of them is a real disagreement I have seen or expect:

  grain          PMPM per member-month is a different number from PMPM per
                 member; getting this wrong changes the answer by the average
                 months of enrolment.
  denominator    the single largest source of disagreement in payer analytics.
                 Member-months, not members, and prorated for partial months --
                 which is a choice, and is stated as one.
  claim_basis    incurred (by service date) or paid (by paid date). Incurred is
                 correct for trend and is not knowable for recent months
                 because of runout, so the two answers differ and both are
                 "right".
  runout_note    how mature the data has to be before the metric is stable.
                 The number most often misread in payer reporting is a recent
                 month that has not finished accruing.
  owner          a named function, because a metric without an owner is a
                 metric nobody will correct.
  known_caveats  the things that make the number wrong or misleading, written
                 down where the number is defined rather than in a footnote of
                 a deck from 2023.

WHAT THIS IS NOT
----------------
Not dbt, not a semantic layer, not a metrics store. No `ref()` graph, no
materialisation, no lineage, no tests attached to definitions, no access
control, no approval workflow for a definition change. dbt's `metrics` spec and
tools like Cube exist and do this properly.
"""

from __future__ import annotations

VERSION = "1.2.0"

METRICS = {
    "pmpm": {
        "name": "PMPM (per member per month)",
        "definition": ("Total allowed spend divided by member-months over the "
                       "same period."),
        "formula": "sum(paid) / sum(member_months)",
        "grain": "one value per period, per population",
        "denominator": ("MEMBER-MONTHS, not members. Prorated for partial "
                        "months of enrolment: a member enrolled 12 days of a "
                        "30-day month contributes 0.4 member-months, not 1.0 "
                        "and not 0. Rounding partial months UP is the most "
                        "common error and inflates the denominator, which "
                        "understates PMPM."),
        "claim_basis": "incurred (service date)",
        "runout_note": ("Not stable until roughly 90 days of runout. Facility "
                        "claims here have a ~41-day median receipt lag and a "
                        "long tail; a month reported at 30 days of runout is "
                        "missing spend, not showing a trend improvement."),
        "owner": "payer analytics",
        "known_caveats": [
            "Not risk-adjusted. Comparing PMPM across populations without "
            "risk context is the most common way payer analytics misleads.",
            "Sensitive to a single catastrophic claimant in small populations "
            "-- see cost_concentration before reading a PMPM movement.",
        ],
        "decomposes_into": ["utilisation_per_member_month", "price_per_service"],
    },
    "member_months": {
        "name": "Member-months",
        "definition": ("Sum of enrolled fractions of each month across all "
                       "members in the population."),
        "formula": "sum over members, months of (enrolled_days / days_in_month)",
        "grain": "one value per period",
        "denominator": "n/a -- this IS the denominator",
        "claim_basis": "n/a (eligibility, not claims)",
        "runout_note": ("Eligibility is subject to retroactive term and "
                        "retroactive add. A month's member-months can change "
                        "AFTER it closes, which moves a PMPM that nobody "
                        "recalculated."),
        "owner": "payer analytics",
        "known_caveats": [
            "A member with a coverage gap contributes to both spans and must "
            "not be double-counted across them.",
        ],
    },
    "utilisation_per_member_month": {
        "name": "Utilisation (U)",
        "definition": "Services per member-month.",
        "formula": "sum(service_count) / sum(member_months)",
        "grain": "per category, per period",
        "denominator": "member-months, as for PMPM",
        "claim_basis": "incurred (service date)",
        "runout_note": "Same 90-day maturity as PMPM.",
        "owner": "payer analytics",
        "known_caveats": [
            "A 'service' is a claim line here, so a change in billing "
            "granularity moves U without any change in care delivered.",
        ],
    },
    "price_per_service": {
        "name": "Price (P)",
        "definition": "Allowed amount per service.",
        "formula": "sum(paid) / sum(service_count)",
        "grain": "per category, per period",
        "denominator": "services, NOT member-months",
        "claim_basis": "incurred (service date)",
        "runout_note": "Same 90-day maturity as PMPM.",
        "owner": "payer analytics",
        "known_caveats": [
            "Blends unit price with intensity. A shift to sicker patients "
            "inside a category raises P with no contract change.",
        ],
    },
    "pmpm_decomposition": {
        "name": "PMPM change decomposition (price / utilisation / mix)",
        "definition": ("Attribution of a PMPM change between two periods to "
                       "utilisation, unit price, and category mix."),
        "formula": ("util = (U1-U0)*Pbar0 ; price = U0*sum(share0*(P1-P0)) ; "
                    "mix = U0*sum((share1-share0)*P0)"),
        "grain": "one attribution per period pair",
        "denominator": "member-months, as for PMPM",
        "claim_basis": "incurred (service date)",
        "runout_note": ("Both periods must be equally mature. Comparing a "
                        "closed period against an open one attributes the "
                        "missing runout to a utilisation decrease."),
        "owner": "payer analytics",
        "known_caveats": [
            "ORDER-DEPENDENT. This is a Laspeyres-style decomposition using "
            "period-0 weights; using period-1 weights gives different "
            "attributions from the same data. The residual is reported so the "
            "size of that arbitrariness is visible rather than hidden.",
            "Attribution is not causation. A price effect says the average "
            "paid per service rose, not that a contract was renegotiated.",
        ],
    },
    "cost_concentration": {
        "name": "Cost concentration (top-N share)",
        "definition": "Share of total spend held by the top N% of members.",
        "formula": "sum(paid for top N% of members by paid) / sum(paid)",
        "grain": "per period",
        "denominator": "total paid",
        "claim_basis": "incurred (service date)",
        "runout_note": ("Immature data understates concentration: the largest "
                        "claims are facility claims and those arrive last."),
        "owner": "payer analytics",
        "known_caveats": [
            "Read this BEFORE reading a PMPM movement in a population under "
            "~20,000 members. One transplant moves the whole number.",
        ],
    },
}


def metric(name):
    if name not in METRICS:
        raise KeyError(f"no metric named {name!r}. "
                       f"Known: {sorted(METRICS)}")
    return METRICS[name]


def metric_value(name, **inputs):
    """Compute a metric from its dictionary entry.

    Deliberately narrow: this exists so the pipeline and the document cannot
    disagree about a denominator. It is not a query engine and does not try to
    be one -- the aggregation still happens in `analytics.py`, and this receives
    the aggregates.
    """
    m = metric(name)
    if name == "pmpm":
        mm = inputs["member_months"]
        return inputs["paid"] / mm if mm else 0.0
    if name == "member_months":
        return inputs["member_months"]
    if name == "utilisation_per_member_month":
        mm = inputs["member_months"]
        return inputs["services"] / mm if mm else 0.0
    if name == "price_per_service":
        s = inputs["services"]
        return inputs["paid"] / s if s else 0.0
    raise NotImplementedError(
        f"{name!r} is defined in the dictionary but is computed in "
        f"analytics.py rather than here; see its 'formula' field. "
        f"Definition: {m['definition']}")


def render_markdown():
    """Generate docs/METRIC_DICTIONARY.md from these definitions.

    Generated, not written. A hand-maintained copy is a second source of truth
    and would start drifting the day after it was committed.
    """
    out = [f"# Metric dictionary (v{VERSION})", "",
           "**Generated from `src/metrics.py`. Do not edit by hand — edit the "
           "definitions and re-run `python write_method.py`.**", "",
           "A metric dictionary that can disagree with the code is a metric "
           "dictionary that will. These definitions are the ones the pipeline "
           "reads.", ""]
    for key in sorted(METRICS):
        m = METRICS[key]
        out += [f"## `{key}` — {m['name']}", "",
                m["definition"], "",
                f"```\n{m['formula']}\n```", "",
                f"| field | value |", "|---|---|",
                f"| grain | {m['grain']} |",
                f"| denominator | {m['denominator']} |",
                f"| claim basis | {m['claim_basis']} |",
                f"| owner | {m['owner']} |", "",
                f"**Data maturity.** {m['runout_note']}", "",
                "**Known caveats.**", ""]
        for c in m["known_caveats"]:
            out.append(f"- {c}")
        if m.get("decomposes_into"):
            out += ["", f"Decomposes into: "
                    + ", ".join(f"`{d}`" for d in m["decomposes_into"])]
        out.append("")
    return "\n".join(out)
