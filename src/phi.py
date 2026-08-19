"""PHI value pools, the 18 Safe Harbor identifiers, and the re-contamination step.

THE DESIGN PROBLEM THIS FILE SOLVES
-----------------------------------
"The data was anonymised" is a sentence, not an engineering result. To turn it
into one you need de-identification performance to be MEASURABLE, and to
measure it you need to know where the PHI is. Synthetic claims have no PHI, so
this module deliberately puts some back -- into structured fields and into free
text -- and logs the exact character offsets of every planted value.

That log is the ground truth `deid.py` is scored against, which converts a
compliance paragraph into a recall number per identifier type.

THE CIRCULARITY TRAP, AND HOW IT IS AVOIDED
-------------------------------------------
If the name detector used the same name list this generator draws from, recall
on names would be 100% and would mean nothing at all -- it would measure that
two halves of one file agree. The name pools are therefore SPLIT:

    SURNAMES_SHARED     names the generator uses AND the detector knows
    SURNAMES_UNSEEN     names the generator uses and the detector does NOT know
    (deid.py's gazetteer also contains names never used here, which is what
     generates false positives and therefore a meaningful precision number)

The unseen fraction is the whole point. It is what a real de-identifier faces
constantly: a name it has never seen. Recall on those names comes only from
CONTEXT rules ("Patient: X", "seen by Dr. X"), which is exactly how a real
hybrid system behaves, and it is why the reported recall is below 100%.
"""

from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# The 18 HIPAA Safe Harbor identifiers, 45 CFR 164.514(b)(2).
# Every row is enumerated, including the ones absent from this dataset, because
# the COMPLETE table is the deliverable -- a de-identification method document
# that lists only the identifiers you happened to handle tells a reviewer
# nothing about the ones you did not think about.
# ---------------------------------------------------------------------------
SAFE_HARBOR = [
    # (n, identifier, present_in_data, detection, transformation, residual_risk)
    (1, "Names", True, "gazetteer + context rules ('Patient:', 'Dr.')",
     "replace with a per-person consistent pseudonym",
     "Names absent from the gazetteer are caught only by context; measured "
     "recall below is the honest figure and it is not 100%."),
    (2, "Geographic subdivisions smaller than a state", True,
     "regex for street addresses; ZIP5 column",
     "street address removed; ZIP truncated to 3 digits, and ZIP3 replaced by "
     "000 where the 3-digit area has <=20,000 people",
     "The <=20,000 rule needs current Census population data. The table used "
     "here is illustrative, not the Bureau's file."),
    (3, "All elements of dates except year", True,
     "date regex across formats; typed date columns",
     "shifted by a consistent per-patient offset, NOT deleted",
     "Shifting preserves intervals, which is the point, but a determined "
     "attacker with an external date anchor can attack the offset."),
    (4, "Telephone numbers", True, "regex", "removed", "-"),
    (5, "Fax numbers", False, "regex (same as phone)", "removed",
     "N/A because no fax field exists in a claims extract of this shape; the "
     "regex would catch one if it appeared in free text."),
    (6, "Email addresses", True, "regex", "removed", "-"),
    (7, "Social security numbers", True, "regex with separator variants",
     "removed", "-"),
    (8, "Medical record numbers", True, "regex on site-specific MRN formats",
     "replaced with a consistent pseudonym",
     "MRN formats vary by site; a regex tuned to one site's format will miss "
     "another's. This is the identifier most likely to be missed in practice."),
    (9, "Health plan beneficiary numbers", True, "regex on member ID format",
     "replaced with a consistent pseudonym", "-"),
    (10, "Account numbers", True, "regex", "removed", "-"),
    (11, "Certificate/licence numbers", True,
     "regex for NPI and state licence formats",
     "provider NPI retained (see note), licence numbers removed",
     "NPI identifies the PROVIDER, not the patient, and is not patient PHI "
     "under Safe Harbor. It is retained deliberately because provider-level "
     "analytics need it -- but it is a re-identification vector when combined "
     "with dates and a small geography, and that is a documented decision, "
     "not an oversight."),
    (12, "Vehicle identifiers and serial numbers", False, "regex (VIN)", "removed",
     "N/A because no vehicle field exists in claims data."),
    (13, "Device identifiers and serial numbers", False, "regex (UDI)", "removed",
     "N/A in this extract. A real DME or implant claims feed WOULD carry UDIs "
     "and this row would become live."),
    (14, "Web URLs", True, "regex", "removed", "-"),
    (15, "IP addresses", True, "regex (IPv4)", "removed",
     "IPv6 is not handled; it would be a gap on a real portal-derived feed."),
    (16, "Biometric identifiers", False, "n/a", "n/a",
     "N/A because claims data carries no biometrics."),
    (17, "Full-face photographs and comparable images", False, "n/a", "n/a",
     "N/A because claims data carries no images. An imaging feed WOULD, and "
     "would additionally require burned-in-annotation screening."),
    (18, "Any other unique identifying number, characteristic, or code", True,
     "the catch-all: reviewed by hand, not by regex",
     "free-text employer, unusual-occupation and rare-diagnosis mentions "
     "flagged for review; nothing automated",
     "This row cannot be automated and is where Safe Harbor pipelines actually "
     "fail. A rare diagnosis plus a 3-digit ZIP can be identifying on its own, "
     "and no regex catches that."),
]

# ---------------------------------------------------------------------------
# Name pools. SHARED names are in the detector's gazetteer; UNSEEN are not.
# ---------------------------------------------------------------------------
SURNAMES_SHARED = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
]
SURNAMES_UNSEEN = [
    "Okonkwo", "Ferreira", "Nakamura", "Abadi", "Beaulieu", "Castellanos",
    "Dimitrov", "Eshleman", "Fitzwilliam", "Grzywacz", "Haugen", "Iyengar",
    "Jaskolski", "Kowalczyk", "Laghari", "Mbeki", "Nordquist", "Oyelaran",
    "Petrosyan", "Quintanilla", "Rasmussen", "Sowande", "Thibodeaux",
    "Ustinov", "Vandermeer", "Wojciechowski", "Xiong", "Yarborough",
    "Zdunowski", "Achterberg",
]
FIRST_SHARED = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen",
]
FIRST_UNSEEN = [
    "Chidinma", "Oluwaseun", "Anneliese", "Bartholomew", "Csilla", "Dashiell",
    "Eulalia", "Ferdinand", "Genoveva", "Hyacinth", "Ignatius", "Jolanta",
]

STREETS = ["Elm St", "Oak Ave", "Maple Dr", "Cedar Ln", "Pine Rd", "Birch Way",
           "Willow Ct", "Ash Blvd", "Chestnut Pl", "Spruce Ter"]
CITIES = [("Springfield", "OH", "45501"), ("Riverside", "CA", "92501"),
          ("Fairview", "TX", "75069"), ("Greenville", "NC", "27834"),
          ("Bristol", "PA", "19007"), ("Clinton", "MI", "49236"),
          ("Auburn", "NY", "13021"), ("Milford", "FL", "32003")]

# Illustrative ZIP3 populations. NOT the Census file. Any ZIP3 listed at or
# below 20,000 must be replaced with 000 under Safe Harbor.
ZIP3_POPULATION = {
    "455": 240_000, "925": 1_100_000, "750": 890_000, "278": 310_000,
    "190": 620_000, "492": 145_000, "130": 205_000, "320": 780_000,
    "059": 12_400,    # <= 20,000 -> must become 000
    "823": 8_900,     # <= 20,000 -> must become 000
    "889": 17_500,    # <= 20,000 -> must become 000
}
SMALL_ZIP3_THRESHOLD = 20_000


def is_small_zip3(zip3):
    return ZIP3_POPULATION.get(zip3, 100_000) <= SMALL_ZIP3_THRESHOLD


# ---------------------------------------------------------------------------
def make_person(rng, unseen_fraction=0.30):
    """A person, whose name may or may not be in the detector's gazetteer."""
    unseen = rng.random() < unseen_fraction
    first = rng.choice(FIRST_UNSEEN if unseen else FIRST_SHARED)
    last = rng.choice(SURNAMES_UNSEEN if unseen else SURNAMES_SHARED)
    city, state, zip5 = rng.choice(CITIES)
    if rng.random() < 0.08:                       # some members in small ZIP3s
        zip5 = rng.choice(["05901", "82312", "88905"])
    return {
        "first_name": first, "last_name": last, "name_in_gazetteer": not unseen,
        "street": f"{rng.randint(1, 9999)} {rng.choice(STREETS)}",
        "city": city, "state": state, "zip5": zip5,
        "phone": f"({rng.randint(200, 989)}) {rng.randint(200, 999)}-{rng.randint(1000, 9999)}",
        "email": f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@example.com",
        "ssn": f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
        "mrn": f"MRN{rng.randint(1000000, 9999999)}",
        "member_id": f"{rng.choice(['H', 'P'])}{rng.randint(100000000, 999999999)}",
        "account_number": f"ACCT-{rng.randint(100000, 999999)}",
    }


# ---------------------------------------------------------------------------
# Free-text templates. Free text is where de-identification actually fails:
# structured columns are easy because you know which column holds the phone
# number, and free text is where a name shows up in the middle of a sentence
# written by a human at 4pm on a Friday.
# ---------------------------------------------------------------------------
NOTE_TEMPLATES = [
    "Prior auth requested for {first} {last}, MRN {mrn}. Contact at {phone}. "
    "Requested drug {drug}, reviewed {month}.",
    "Spoke with member {first} {last} on {date}; callback number {phone}. "
    "Referred to {dept}.",
    "Appeal filed by {first} {last} ({member_id}). Correspondence to {street}, "
    "{city} {state} {zip5}.",
    "Case note: {first} {last} seen by Dr. {provider_last} at {city} clinic. "
    "Started {drug}.",
    "Member {first} {last} requests records be emailed to {email}. "
    "Records span {month} to {month2}.",
    "COB inquiry for {first} {last}, SSN {ssn}, secondary coverage verified "
    "with {payer}.",
    "Left voicemail for {first} {last} at {phone} regarding auth "
    "{account_number}. {dept} to follow up.",
    "Referral: {first} {last} to Dr. {provider_last}; portal {url}. "
    "{drug} continued.",
    "Claim submitted from {ip} for member {member_id}; {dept} review in {month}.",
]

# Capitalised tokens that are NOT PHI. Without these the free text contains
# almost nothing a name detector could wrongly fire on, precision comes back at
# a meaningless 100%, and the over-redaction column measures nothing. Real notes
# are dense with capitalised non-PHI: drug brands, departments, months, payer
# names, device brands, section headers.
DISTRACTOR_DRUGS = ["Lipitor", "Lasix", "Coumadin", "Zithromax", "Synthroid",
                    "Norvasc", "Prilosec", "Zoloft", "Neurontin", "Spiriva"]
DISTRACTOR_DEPTS = ["Cardiology", "Nephrology", "Utilization", "Pharmacy",
                    "Radiology", "Oncology", "Behavioral", "Endocrinology",
                    # Facility names that COLLIDE with surnames. This is the
                    # realistic source of over-redaction: no gazetteer can tell
                    # "Parker Regional" from "Mr Parker" without more context,
                    # and a de-identifier that redacts the hospital's name
                    # destroys the site variable every provider analysis needs.
                    "Parker Regional", "Baker Memorial", "Mason General",
                    "Harris County Clinic"]
DISTRACTOR_MONTHS = ["January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November",
                     "December"]
DISTRACTOR_PAYERS = ["Medicare", "Medicaid", "Tricare", "Aetna", "Cigna"]

IDENTIFIER_FIELDS = {
    "first": "name", "last": "name", "provider_last": "name",
    "mrn": "mrn", "phone": "phone", "email": "email", "ssn": "ssn",
    "member_id": "member_id", "account_number": "account_number",
    "street": "address", "city": "address", "zip5": "zip",
    "date": "date", "url": "url", "ip": "ip",
}


def plant_note(rng, person, service_date):
    """Render one free-text note and return (text, [ground-truth spans]).

    Spans are recorded by building the string incrementally, so the offsets are
    exact by construction rather than by searching for the value afterwards --
    searching would mis-locate any value that also occurs elsewhere in the text.
    """
    template = rng.choice(NOTE_TEMPLATES)
    values = {
        "first": person["first_name"], "last": person["last_name"],
        "mrn": person["mrn"], "phone": person["phone"],
        "email": person["email"], "ssn": person["ssn"],
        "member_id": person["member_id"], "street": person["street"],
        "city": person["city"], "state": person["state"], "zip5": person["zip5"],
        "account_number": person["account_number"],
        "provider_last": rng.choice(SURNAMES_SHARED + SURNAMES_UNSEEN),
        "date": service_date.strftime("%m/%d/%Y"),
        "url": f"https://portal.example.com/case/{rng.randint(10000, 99999)}",
        "ip": f"{rng.randint(10, 220)}.{rng.randint(0, 255)}."
              f"{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        # non-PHI capitalised distractors; deliberately NOT in IDENTIFIER_FIELDS
        # so they are never ground truth, and any detection of them is a false
        # positive that shows up in the over-redaction column
        "drug": rng.choice(DISTRACTOR_DRUGS),
        "dept": rng.choice(DISTRACTOR_DEPTS),
        "month": rng.choice(DISTRACTOR_MONTHS),
        "month2": rng.choice(DISTRACTOR_MONTHS),
        "payer": rng.choice(DISTRACTOR_PAYERS),
    }
    out, spans, i = [], [], 0
    pos = 0
    while i < len(template):
        if template[i] == "{":
            j = template.index("}", i)
            key = template[i + 1:j]
            val = str(values[key])
            if key in IDENTIFIER_FIELDS:
                spans.append({"start": pos, "end": pos + len(val),
                              "type": IDENTIFIER_FIELDS[key], "value": val})
            out.append(val)
            pos += len(val)
            i = j + 1
        else:
            out.append(template[i])
            pos += 1
            i += 1
    return "".join(out), spans
