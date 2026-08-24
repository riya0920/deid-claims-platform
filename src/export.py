"""Build the de-identified ANALYTIC EXTRACT that the dbt project reads.

THE POINT OF THIS FILE IS A BOUNDARY
------------------------------------
Everything upstream of here has seen PHI. Everything downstream of here has
not, and cannot, because the extract does not contain it. The dbt project reads
only these files, so "the warehouse never sees an identifier" stops being a
policy somebody has to remember and becomes a property of the file layout --
`tests/no_phi_columns_reach_the_warehouse.sql` fails the build if it lapses.

That is the whole argument for exporting rather than pointing dbt at the raw
generator output, which would have been one line shorter and would have made
the guarantee unenforceable.

WHAT IS APPLIED
---------------
* `member_id` becomes an HMAC pseudonym (`member_key`), the same one
  `deid.deidentify_member` issues, so the extract joins to the de-identified
  member dimension and to nothing else.
* Dates are SHIFTED per member by that member's own offset -- the same offset
  applied to their demographics. Shifting rather than truncating keeps the
  interval between two events, which is what carries the clinical meaning.
* `provider_npi` becomes a pseudonym too. An NPI is a public identifier of the
  provider, not the patient, but it is also a very effective quasi-identifier
  for a member: a rare specialty in a small ZIP3 narrows a panel fast.

AND WHAT IT COSTS -- THIS IS NOT FREE
--------------------------------------
Shifting each member by a different offset MOVES CLAIMS ACROSS MONTH
BOUNDARIES. A monthly PMPM computed on the shifted extract is therefore not the
same series as one computed on the raw data. That is a real utility cost of
de-identification, it is measurable, and `run_export.py` measures it rather
than leaving the reader to assume the extract is a lossless copy.

The member and their eligibility move TOGETHER, so a member is never billed
into a month they were not enrolled in -- the shift preserves the relationship
between claims and coverage even while it moves both.
"""

from __future__ import annotations

import csv
import os
from datetime import date, timedelta

import deid


def _shift(d, days):
    return d + timedelta(days=days) if d else None


def build_extract(data, salt, out_dir):
    """Write dim_member / fct_eligibility / fct_claim as CSV. Returns paths."""
    os.makedirs(out_dir, exist_ok=True)

    members = [deid.deidentify_member(m, salt) for m in data["members"]]
    by_id = {}
    for raw, clean in zip(data["members"], members):
        by_id[raw["member_id"]] = clean

    paths = {}

    paths["dim_member"] = _write(
        os.path.join(out_dir, "dim_member.csv"),
        ["member_key", "age_band", "sex", "state", "zip3"],
        ({"member_key": m["member_key"], "age_band": m["age_band"],
          "sex": m["sex"], "state": m["state"], "zip3": m["zip3"]}
         for m in members))

    def _elig():
        for span in data["eligibility"]:
            m = by_id.get(span["member_id"])
            if m is None:
                continue
            off = m["date_offset_days"]
            yield {"member_key": m["member_key"],
                   "span_start": _shift(span["span_start"], off).isoformat(),
                   "span_end": _shift(span["span_end"], off).isoformat()}

    paths["fct_eligibility"] = _write(
        os.path.join(out_dir, "fct_eligibility.csv"),
        ["member_key", "span_start", "span_end"], _elig())

    def _claims():
        for c in data["claims"]:
            m = by_id.get(c["member_id"])
            if m is None:
                continue
            off = m["date_offset_days"]
            yield {
                "claim_key": deid.pseudonym(c["claim_id"], salt, "CLM"),
                "member_key": m["member_key"],
                "service_date": _shift(c["service_date"], off).isoformat(),
                "service_category": c["service_category"],
                "paid_amount": "%.2f" % c["paid_amount"],
                "units": c["units"],
                "provider_key": deid.pseudonym(c["provider_npi"], salt, "PRV"),
            }

    paths["fct_claim"] = _write(
        os.path.join(out_dir, "fct_claim.csv"),
        ["claim_key", "member_key", "service_date", "service_category",
         "paid_amount", "units", "provider_key"], _claims())

    return paths


def _write(path, fields, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def shifted_view(data, salt):
    """The same shift, in memory, as dicts the Python analytics can consume.

    Needed so parity can be measured HONESTLY: comparing dbt-on-shifted against
    Python-on-raw would report a difference that is the date shift, not an
    implementation disagreement. Parity means two implementations over the SAME
    input, so this produces that input for the Python side.
    """
    members = {}
    for raw in data["members"]:
        members[raw["member_id"]] = deid.deidentify_member(raw, salt)

    elig = []
    for span in data["eligibility"]:
        m = members.get(span["member_id"])
        if m is None:
            continue
        off = m["date_offset_days"]
        elig.append({"member_id": m["member_key"],
                     "span_start": _shift(span["span_start"], off),
                     "span_end": _shift(span["span_end"], off)})

    claims = []
    for c in data["claims"]:
        m = members.get(c["member_id"])
        if m is None:
            continue
        off = m["date_offset_days"]
        claims.append({"member_id": m["member_key"],
                       "service_date": _shift(c["service_date"], off),
                       "service_category": c["service_category"],
                       "paid_amount": c["paid_amount"],
                       "units": c["units"]})

    return {"members": list(members.values()), "eligibility": elig,
            "claims": claims}


def usable_window(data, salt, raw_start, raw_end):
    """The window over which the SHIFTED extract is fully populated.

    THE COST OF DATE SHIFTING IS AN EDGE EFFECT, AND IT IS LARGE.

    A member with offset `o` (negative here) has their raw span [raw_start,
    raw_end] moved to [raw_start+o, raw_end+o]. For a reporting window to be
    covered for EVERY member, it must sit inside the intersection of all those
    shifted spans -- which is [raw_start + max(o), raw_end + min(o)].

    With offsets spanning a full year, that intersection is a year narrower
    than the extract. Reporting over the original window instead does not fail
    loudly; it silently reports depressed volumes at both edges, because
    members' claims have walked off the end. Measured on this corpus, 25.3% of
    claims land outside the original window.

    This is not a bug in the shift. It is the price of the shift, and the fix
    is to extract a wider raw range than you intend to report on -- not to
    quietly report on a window the data no longer covers.
    """
    offsets = [deid.deidentify_member(m, salt)["date_offset_days"]
               for m in data["members"]]
    if not offsets:
        return raw_start, raw_end
    return (_shift(raw_start, max(offsets)), _shift(raw_end, min(offsets)))


def shift_cost(data, salt, raw_start, raw_end):
    """How much of the corpus the shift moves outside the reporting window."""
    shifted = shifted_view(data, salt)
    raw_in = sum(1 for c in data["claims"]
                 if raw_start <= c["service_date"] <= raw_end)
    sh_in = sum(1 for c in shifted["claims"]
                if raw_start <= c["service_date"] <= raw_end)
    lo, hi = usable_window(data, salt, raw_start, raw_end)
    offsets = [deid.deidentify_member(m, salt)["date_offset_days"]
               for m in data["members"]]
    return {
        "claims_in_window_raw": raw_in,
        "claims_in_window_shifted": sh_in,
        "claims_lost": raw_in - sh_in,
        "fraction_lost": (raw_in - sh_in) / raw_in if raw_in else 0.0,
        "offset_min": min(offsets),
        "offset_max": max(offsets),
        "usable_start": lo,
        "usable_end": hi,
        "usable_days": (hi - lo).days + 1 if hi >= lo else 0,
        "requested_days": (raw_end - raw_start).days + 1,
    }
