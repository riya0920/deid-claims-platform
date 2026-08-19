"""The de-identification engine, and the scorer that measures it.

Hybrid detection, because neither half works alone:

  REGEX / DICTIONARY  for structured patterns -- SSN, phone, email, MRN, ZIP,
                      URL, IP, account numbers, dates. High precision, and the
                      failure mode is format variation rather than ambiguity.
  GAZETTEER + CONTEXT for names in free text. This is the hard half. Presidio
                      or medspaCy would be the tool; neither is installed, so a
                      gazetteer plus context rules stands in, and the gazetteer
                      deliberately does NOT contain every name the generator
                      uses (see phi.py) so that recall is a real measurement.

TRANSFORMATIONS ARE NOT ALL "DELETE"
------------------------------------
Dates are SHIFTED by a consistent per-patient offset rather than removed, and
this is the single most consequential design choice in the file. Deleting dates
satisfies Safe Harbor and destroys the analytics: readmission windows, length
of stay, medication adherence gaps, episode construction, and every
before/after comparison are all interval-dependent. A consistent per-patient
offset preserves every within-patient interval exactly while moving the
absolute dates, so `readmission within 30 days` still computes correctly on the
de-identified data.

The cost is stated rather than hidden: shifting is weaker than deletion. An
attacker who knows one true date for one patient recovers that patient's offset
and therefore all their dates. Offsets are per-patient (not global) so the
compromise does not propagate, but this is a real residual risk and it is why
the method document says what it approximates and what it does not certify.

WHAT THIS IS NOT
----------------
This approximates **Safe Harbor** (45 CFR 164.514(b)(2)): remove the 18
identifiers, and have no actual knowledge that the residual could identify
someone. It is NOT **Expert Determination** (164.514(b)(1)), which requires a
qualified statistician to certify that re-identification risk is "very small"
given the specific data, recipients, and controls -- with a documented method
and a re-certification cadence. Nothing here certifies anything, and the
distinction matters because Expert Determination is what a real organisation
needs when it wants to RETAIN data Safe Harbor would strip: full dates for a
longitudinal study, or 5-digit ZIPs for geographic analysis.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

from phi import (FIRST_SHARED, SURNAMES_SHARED, ZIP3_POPULATION,
                 is_small_zip3)

# The detector's gazetteer: the SHARED names, plus decoys that never appear in
# the data. The decoys exist so that precision is a real number -- a gazetteer
# containing exactly the names present cannot produce a false positive.
GAZETTEER_SURNAMES = set(SURNAMES_SHARED) | {
    "Whitaker", "Ellsworth", "Marchetti", "Delacroix", "Ravensworth",
    "Bianchi", "Kaufman", "Sorensen", "Blackwood", "Ashford",
}
GAZETTEER_FIRST = set(FIRST_SHARED) | {
    "Gregory", "Meredith", "Alastair", "Rosalind", "Terence",
}

# City gazetteer. Unlike surnames, cities ARE enumerable -- a real system loads
# a place-name file -- so a gazetteer is the right tool here rather than a
# concession. It is still deliberately incomplete (6 of the 8 cities this data
# uses), because a place-name list is never current: new developments, renamed
# municipalities, and colloquial neighbourhood names all miss.
GAZETTEER_CITIES = {
    "Springfield", "Riverside", "Fairview", "Greenville", "Bristol", "Clinton",
    # decoys, never present in the data
    "Ashland", "Georgetown", "Salem", "Madison",
}
# Auburn and Milford are deliberately ABSENT, so city recall is measurable.

# Words that look like names in a gazetteer sense but are clinical or
# administrative vocabulary. Without this list, "Case", "Left", "Appeal" and
# "Contact" all get redacted and precision collapses.
STOPWORDS = {
    "Prior", "Auth", "Case", "Note", "Left", "Appeal", "Claim", "Referral",
    "Member", "Contact", "Spoke", "Corres", "Correspondence", "COB", "Dr",
    "Portal", "Secondary", "Coverage", "Verified", "Filed", "Requests",
    "Records", "Emailed", "Voicemail", "Regarding", "Submitted", "Seen",
    "Clinic", "Callback", "Number", "Requested", "Inquiry", "Ext",
}

PATTERNS = [
    # (type, compiled regex). Order matters: SSN before generic number runs.
    ("ssn", re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("url", re.compile(r"https?://[^\s,;]+")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("phone", re.compile(r"\(?\b\d{3}\)?[-. ]\s?\d{3}[-. ]\d{4}\b")),
    ("mrn", re.compile(r"\bMRN[- ]?\d{5,10}\b", re.I)),
    ("member_id", re.compile(r"\b[HP]\d{9}\b")),
    ("account_number", re.compile(r"\bACCT-\d{4,8}\b", re.I)),
    ("date", re.compile(r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])"
                        r"[/-](?:19|20)\d{2}\b")),
    ("date", re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")),
    ("address", re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+\s+"
                           r"(?:St|Ave|Dr|Ln|Rd|Way|Ct|Blvd|Pl|Ter)\b")),
    ("zip", re.compile(r"\b\d{5}(?:-\d{4})?\b")),
]

# Context rules catch names the gazetteer does not know -- the whole reason a
# hybrid system beats either half alone.
CONTEXT_NAME = [
    re.compile(r"(?:Patient|Member|member|patient)\s+([A-Z][a-z]+)\s+([A-Z][a-z]+)"),
    re.compile(r"\bDr\.\s+([A-Z][a-z]+)"),
    re.compile(r"(?:for|with|by|to)\s+([A-Z][a-z]+)\s+([A-Z][a-z]+)"),
    re.compile(r"^([A-Z][a-z]+)\s+([A-Z][a-z]+)\s+(?:requests|seen|filed)"),
]


def _overlaps(a, spans):
    return any(a["start"] < s["end"] and s["start"] < a["end"] for s in spans)


def detect(text):
    """Return detected spans, highest-precision detectors first."""
    found = []
    for kind, pat in PATTERNS:
        for m in pat.finditer(text):
            cand = {"start": m.start(), "end": m.end(), "type": kind,
                    "value": m.group()}
            if not _overlaps(cand, found):
                found.append(cand)

    for pat in CONTEXT_NAME:
        for m in pat.finditer(text):
            for gi in range(1, (m.lastindex or 0) + 1):
                word = m.group(gi)
                if word in STOPWORDS:
                    continue
                cand = {"start": m.start(gi), "end": m.end(gi), "type": "name",
                        "value": word}
                if not _overlaps(cand, found):
                    found.append(cand)

    # City mentions are geographic identifiers under Safe Harbor #2 and are
    # handled BEFORE the name pass, because several city names are also
    # surnames and the geographic reading is the safer one to apply first.
    for m in re.finditer(r"\b[A-Z][a-z]+\b", text):
        if m.group() in GAZETTEER_CITIES:
            cand = {"start": m.start(), "end": m.end(), "type": "address",
                    "value": m.group()}
            if not _overlaps(cand, found):
                found.append(cand)

    for m in re.finditer(r"\b[A-Z][a-z]{2,}\b", text):
        word = m.group()
        if word in STOPWORDS:
            continue
        if word in GAZETTEER_SURNAMES or word in GAZETTEER_FIRST:
            cand = {"start": m.start(), "end": m.end(), "type": "name",
                    "value": word}
            if not _overlaps(cand, found):
                found.append(cand)

    return sorted(found, key=lambda s: s["start"])


# ---------------------------------------------------------------------------
def pseudonym(value, salt, prefix="ID"):
    return f"{prefix}-" + hashlib.sha256(
        (salt + str(value)).encode()).hexdigest()[:10].upper()


def patient_offset(patient_key, salt, max_days=364):
    """Deterministic per-patient date offset.

    Per-PATIENT, not global: a single global offset means compromising one
    patient's offset compromises every patient's dates. Per-patient contains
    the blast radius to one person.
    """
    h = hashlib.sha256((salt + "date" + str(patient_key)).encode()).digest()
    return -(int.from_bytes(h[:4], "big") % max_days) - 1


def shift_date(d, days):
    if isinstance(d, str):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                d = date(*map(int, __import__("datetime").datetime
                              .strptime(d, fmt).date().timetuple()[:3]))
                break
            except ValueError:
                continue
        else:
            return d
    return d + timedelta(days=days)


REPLACEMENT = {
    "ssn": "[SSN]", "email": "[EMAIL]", "url": "[URL]", "ip": "[IP]",
    "phone": "[PHONE]", "address": "[ADDRESS]", "account_number": "[ACCOUNT]",
}


def redact_text(text, patient_key, salt):
    """Apply transformations to free text. Returns (clean_text, spans)."""
    spans = detect(text)
    off = patient_offset(patient_key, salt)
    out, cursor = [], 0
    for s in sorted(spans, key=lambda x: x["start"]):
        out.append(text[cursor:s["start"]])
        kind = s["type"]
        if kind in REPLACEMENT:
            out.append(REPLACEMENT[kind])
        elif kind == "name":
            out.append(pseudonym(s["value"], salt, "NAME"))
        elif kind == "mrn":
            out.append(pseudonym(s["value"], salt, "MRN"))
        elif kind == "member_id":
            out.append(pseudonym(s["value"], salt, "MBR"))
        elif kind == "date":
            shifted = shift_date(s["value"], off)
            out.append(shifted.strftime("%m/%d/%Y")
                       if hasattr(shifted, "strftime") else "[DATE]")
        elif kind == "zip":
            z3 = s["value"][:3]
            out.append("000" + "XX" if is_small_zip3(z3) else z3 + "XX")
        else:
            out.append("[REDACTED]")
        cursor = s["end"]
    out.append(text[cursor:])
    return "".join(out), spans


def deidentify_member(member, salt):
    """Structured-column de-identification for one member row."""
    zip3 = str(member["zip5"])[:3]
    return {
        "member_key": pseudonym(member["member_id"], salt, "MBR"),
        "zip3": "000" if is_small_zip3(zip3) else zip3,
        "state": member["state"],
        "age_band": age_band(member["age"]),
        "sex": member["sex"],
        "date_offset_days": patient_offset(member["member_id"], salt),
    }


def age_band(age):
    """Safe Harbor: ages over 89 must be aggregated into a 90+ category,
    because the number of people at each age above 89 is small enough to be
    identifying."""
    if age >= 90:
        return "90+"
    lo = (age // 5) * 5
    return f"{lo}-{lo + 4}"


# ---------------------------------------------------------------------------
def score(truth_spans, detected_spans, tolerance=0):
    """Per-identifier-type recall and precision against planted ground truth.

    RECALL IS THE NUMBER THAT MATTERS. A miss is a disclosure -- PHI that
    survived into a dataset believed to be de-identified. A false positive is
    over-redaction: it costs analytic utility, which is a real cost but a
    recoverable one. They are not symmetric and are never averaged into an F1
    here for that reason.
    """
    stats = {}

    def bucket(t):
        return stats.setdefault(t, {"tp": 0, "fn": 0, "fp": 0, "n_truth": 0})

    matched = set()
    for t in truth_spans:
        b = bucket(t["type"])
        b["n_truth"] += 1
        hit = None
        for i, d in enumerate(detected_spans):
            if i in matched:
                continue
            if (abs(d["start"] - t["start"]) <= tolerance
                    and abs(d["end"] - t["end"]) <= tolerance
                    and d["type"] == t["type"]):
                hit = i
                break
        if hit is None:                       # allow a type-agnostic overlap
            for i, d in enumerate(detected_spans):
                if i in matched:
                    continue
                if d["start"] < t["end"] and t["start"] < d["end"]:
                    hit = i
                    break
        if hit is not None:
            matched.add(hit)
            b["tp"] += 1
        else:
            b["fn"] += 1

    for i, d in enumerate(detected_spans):
        if i not in matched:
            bucket(d["type"])["fp"] += 1

    for t, b in stats.items():
        b["recall"] = b["tp"] / b["n_truth"] if b["n_truth"] else float("nan")
        b["precision"] = (b["tp"] / (b["tp"] + b["fp"])
                          if (b["tp"] + b["fp"]) else float("nan"))
    return stats
