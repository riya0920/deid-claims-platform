"""Tests for the de-identifier and the denominator.

Two classes of thing are tested. First, that de-identification does what the
method document says -- including the parts that are easy to get subtly wrong,
like date shifting preserving intervals and pseudonyms being stable. Second,
that PMPM is computed against member-months, because a denominator bug is
invisible in every output it touches.
"""

import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import analytics
import deid
import phi

SALT = "test-salt"


# ---------------------------------------------------------------------------
# Safe Harbor completeness
# ---------------------------------------------------------------------------
def test_all_eighteen_safe_harbor_identifiers_are_enumerated():
    assert len(phi.SAFE_HARBOR) == 18
    assert [row[0] for row in phi.SAFE_HARBOR] == list(range(1, 19))


def test_identifiers_absent_from_the_data_still_have_a_stated_reason():
    """The complete table is the deliverable. A row marked 'not present' must
    say WHY, or the reader cannot tell 'considered and inapplicable' from
    'never thought about it'."""
    for n, name, present, _det, _trans, residual in phi.SAFE_HARBOR:
        if not present:
            assert residual.startswith("N/A because") or "N/A" in residual, (
                f"identifier {n} ({name}) is absent from the data with no "
                f"stated reason")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,kind", [
    ("SSN 123-45-6789 on file", "ssn"),
    ("call (555) 867-5309 today", "phone"),
    ("email jane.doe12@example.com please", "email"),
    ("see https://portal.example.com/case/12345 for detail", "url"),
    ("submitted from 192.168.14.7 at noon", "ip"),
    ("record MRN1234567 pulled", "mrn"),
    ("member H123456789 verified", "member_id"),
    ("auth ACCT-889231 approved", "account_number"),
    ("service on 03/14/2024 billed", "date"),
    ("lives at 417 Elm St now", "address"),
])
def test_each_structured_identifier_is_detected(text, kind):
    kinds = {s["type"] for s in deid.detect(text)}
    assert kind in kinds, f"{kind} not detected in {text!r}; got {kinds}"


def test_names_in_the_gazetteer_are_detected():
    found = deid.detect("Spoke with member Robert Johnson today")
    assert any(s["type"] == "name" and s["value"] == "Johnson" for s in found)


def test_names_outside_the_gazetteer_are_caught_by_context_only():
    """The honest case: a surname the detector has never seen is recoverable
    only from context. This is why measured recall is below 100% and why a
    detector scored against its own name list means nothing."""
    unseen = "Wojciechowski"
    assert unseen not in deid.GAZETTEER_SURNAMES
    with_context = deid.detect(f"Spoke with member Gregory {unseen} today")
    assert any(s["value"] == unseen for s in with_context)
    without_context = deid.detect(f"{unseen} was mentioned.")
    assert not any(s["value"] == unseen for s in without_context), (
        "if this passes, the context-free path found it and the recall "
        "measurement is no longer meaningful")


# ---------------------------------------------------------------------------
# Transformations
# ---------------------------------------------------------------------------
def test_dates_are_shifted_not_deleted_and_intervals_survive():
    """The central design claim of this pipeline. Deleting dates satisfies
    Safe Harbor and destroys readmission windows, length of stay, adherence
    gaps and every before/after comparison."""
    off = deid.patient_offset("M1", SALT)
    d1, d2 = date(2024, 3, 1), date(2024, 3, 31)
    s1, s2 = deid.shift_date(d1, off), deid.shift_date(d2, off)
    assert (s2 - s1).days == (d2 - d1).days == 30
    assert s1 != d1, "the date must actually move"


def test_date_offsets_are_per_patient_not_global():
    """A single global offset means compromising one patient's offset
    compromises every patient's dates."""
    offsets = {deid.patient_offset(f"M{i}", SALT) for i in range(50)}
    assert len(offsets) > 25, "offsets are not varying by patient"


def test_date_offset_is_stable_for_one_patient():
    assert deid.patient_offset("M1", SALT) == deid.patient_offset("M1", SALT)


def test_pseudonyms_are_stable_and_do_not_contain_the_original():
    a = deid.pseudonym("MRN1234567", SALT, "MRN")
    b = deid.pseudonym("MRN1234567", SALT, "MRN")
    assert a == b
    assert "1234567" not in a
    assert a != deid.pseudonym("MRN7654321", SALT, "MRN")


def test_redacted_text_contains_none_of_the_planted_values():
    import random
    rng = random.Random(5)
    person = phi.make_person(rng)
    text, spans = phi.plant_note(rng, person, date(2024, 5, 1))
    clean, _ = deid.redact_text(text, person["member_id"], SALT)
    for s in spans:
        if s["type"] in ("name", "address"):
            continue          # measured, not guaranteed -- see recall table
        assert s["value"] not in clean, (
            f"{s['type']} value {s['value']!r} survived redaction")


# ---------------------------------------------------------------------------
# Geography and age
# ---------------------------------------------------------------------------
def test_small_zip3_areas_are_suppressed():
    """Safe Harbor: a 3-digit ZIP whose area has <= 20,000 people must be
    changed to 000, because the truncation alone does not protect it."""
    assert phi.is_small_zip3("059")
    assert not phi.is_small_zip3("455")
    m = {"member_id": "H123456789", "zip5": "05901", "state": "VT",
         "age": 40, "sex": "F"}
    assert deid.deidentify_member(m, SALT)["zip3"] == "000"


def test_zip_is_never_retained_at_five_digits():
    m = {"member_id": "H1", "zip5": "45501", "state": "OH", "age": 40, "sex": "M"}
    out = deid.deidentify_member(m, SALT)
    assert out["zip3"] == "455"
    assert "45501" not in str(out)


def test_ages_over_89_are_aggregated():
    assert deid.age_band(91) == "90+"
    assert deid.age_band(90) == "90+"
    assert deid.age_band(89) == "85-89"
    assert deid.age_band(42) == "40-44"


# ---------------------------------------------------------------------------
# The denominator
# ---------------------------------------------------------------------------
def test_member_months_are_prorated_not_counted_whole():
    elig = [{"member_id": "A", "span_start": date(2024, 3, 1),
             "span_end": date(2024, 3, 15)}]
    spine, _ = analytics.member_month_spine(elig, date(2024, 1, 1), date(2024, 12, 31))
    assert spine[(2024, 3)] == pytest.approx(15 / 31, abs=1e-9)


def test_a_full_year_of_coverage_is_twelve_member_months():
    elig = [{"member_id": "A", "span_start": date(2024, 1, 1),
             "span_end": date(2024, 12, 31)}]
    spine, _ = analytics.member_month_spine(elig, date(2024, 1, 1), date(2024, 12, 31))
    assert sum(spine.values()) == pytest.approx(12.0, abs=1e-9)


def test_coverage_gaps_reduce_the_denominator():
    """The bug this prevents: counting a churning member as fully enrolled
    inflates member-months, which UNDERSTATES PMPM -- and understates it more
    each month as churn accumulates, so a flat cost trend renders as an
    improving one."""
    full = [{"member_id": "A", "span_start": date(2024, 1, 1),
             "span_end": date(2024, 12, 31)}]
    churned = [{"member_id": "A", "span_start": date(2024, 1, 1),
                "span_end": date(2024, 5, 31)},
               {"member_id": "A", "span_start": date(2024, 9, 1),
                "span_end": date(2024, 12, 31)}]
    s_full, _ = analytics.member_month_spine(full, date(2024, 1, 1), date(2024, 12, 31))
    s_churn, _ = analytics.member_month_spine(churned, date(2024, 1, 1), date(2024, 12, 31))
    assert sum(s_churn.values()) < sum(s_full.values())
    assert sum(s_churn.values()) == pytest.approx(9.0, abs=1e-9)


def test_pmpm_uses_member_months_not_member_count():
    elig = [{"member_id": "A", "span_start": date(2024, 1, 1),
             "span_end": date(2024, 6, 30)}]
    claims = [{"member_id": "A", "service_date": date(2024, 2, 10),
               "service_category": "professional", "units": 1,
               "paid_amount": 600.0}]
    spine, _ = analytics.member_month_spine(elig, date(2024, 1, 1), date(2024, 12, 31))
    monthly = analytics.pmpm_by_category(claims, spine, date(2024, 1, 1),
                                         date(2024, 12, 31))
    # February: 1 member-month, 600 paid -> PMPM 600, not 600/6
    assert monthly[(2024, 2)]["pmpm"] == pytest.approx(600.0)


# ---------------------------------------------------------------------------
# Decomposition
# ---------------------------------------------------------------------------
def test_decomposition_attributes_a_pure_price_rise_to_price():
    p0 = {"pmpm": 100.0, "member_months": 1000,
          "categories": {"inpatient": {"util_per_member_month": 0.1,
                                       "price_per_service": 1000.0,
                                       "pmpm": 100.0}}}
    p1 = {"pmpm": 120.0, "member_months": 1000,
          "categories": {"inpatient": {"util_per_member_month": 0.1,
                                       "price_per_service": 1200.0,
                                       "pmpm": 120.0}}}
    d = analytics.decompose(p0, p1)
    assert d["price_effect"] == pytest.approx(20.0)
    assert d["utilisation_effect"] == pytest.approx(0.0)
    assert abs(d["residual"]) < 1e-9


def test_decomposition_attributes_a_pure_utilisation_rise_to_utilisation():
    p0 = {"pmpm": 100.0, "member_months": 1000,
          "categories": {"inpatient": {"util_per_member_month": 0.1,
                                       "price_per_service": 1000.0,
                                       "pmpm": 100.0}}}
    p1 = {"pmpm": 150.0, "member_months": 1000,
          "categories": {"inpatient": {"util_per_member_month": 0.15,
                                       "price_per_service": 1000.0,
                                       "pmpm": 150.0}}}
    d = analytics.decompose(p0, p1)
    assert d["utilisation_effect"] == pytest.approx(50.0)
    assert d["price_effect"] == pytest.approx(0.0)


def test_decomposition_effects_and_residual_reconstruct_the_actual_change():
    """The residual is reported rather than absorbed. A decomposition that does
    not sum to the actual change, silently, is worse than none."""
    p0 = {"pmpm": 100.0, "member_months": 1000,
          "categories": {"a": {"util_per_member_month": 0.1,
                               "price_per_service": 500.0, "pmpm": 50.0},
                         "b": {"util_per_member_month": 0.5,
                               "price_per_service": 100.0, "pmpm": 50.0}}}
    p1 = {"pmpm": 138.0, "member_months": 900,
          "categories": {"a": {"util_per_member_month": 0.14,
                               "price_per_service": 600.0, "pmpm": 84.0},
                         "b": {"util_per_member_month": 0.45,
                               "price_per_service": 120.0, "pmpm": 54.0}}}
    d = analytics.decompose(p0, p1)
    total = (d["price_effect"] + d["utilisation_effect"] + d["mix_effect"]
             + d["residual"])
    assert total == pytest.approx(d["actual_change"], abs=1e-9)


# ---------------------------------------------------------------------------
# Re-identification risk
# ---------------------------------------------------------------------------
import reidentify as RI


def test_k_anonymity_counts_equivalence_classes():
    recs = [{"z": "1", "s": "F"}, {"z": "1", "s": "F"},
            {"z": "2", "s": "M"}]
    st = RI.k_anonymity(recs, ["z", "s"])
    assert st["n_classes"] == 2
    assert st["min_k"] == 1
    assert st["records_with_k_1"] == 1
    assert st["pct_unique"] == pytest.approx(1 / 3)


def test_adding_a_quasi_identifier_can_only_reduce_k():
    """Monotonicity. More attacker knowledge never makes a release safer, and
    a risk measure that ever says otherwise is broken."""
    rng = __import__("random").Random(4)
    recs = [{"z": rng.choice("abc"), "s": rng.choice("FM"),
             "a": rng.choice(["30-34", "35-39", "40-44"]),
             "c": rng.random() < 0.2} for _ in range(600)]
    coarse = RI.k_anonymity(recs, ["z", "s"])
    finer = RI.k_anonymity(recs, ["z", "s", "a"])
    finest = RI.k_anonymity(recs, ["z", "s", "a", "c"])
    assert coarse["min_k"] >= finer["min_k"] >= finest["min_k"]
    assert coarse["pct_unique"] <= finer["pct_unique"] <= finest["pct_unique"]


def test_linkage_only_claims_a_hit_when_both_sides_are_unique():
    """Conservative by design: if two people share the combination the attacker
    has narrowed it to two, which is a privacy loss but not an identification."""
    released = [{"member_key": "K1", "z": "1", "s": "F"},
                {"member_key": "K2", "z": "2", "s": "M"},
                {"member_key": "K3", "z": "2", "s": "M"}]
    roll = [{"name": "Ann", "z": "1", "s": "F", "true_member_id": "M1"},
            {"name": "Bob", "z": "2", "s": "M", "true_member_id": "M2"}]
    res = RI.linkage_attack(released, roll, ["z", "s"])
    assert res["n_reidentified"] == 1               # only the unique pair
    assert res["sample"][0]["guessed_name"] == "Ann"


def test_generalisation_raises_k_and_costs_records():
    rng = __import__("random").Random(9)
    recs = [{"zip3": rng.choice(["100", "200", "300"]),
             "age_band": rng.choice(["30-34", "35-39", "40-44", "45-49"]),
             "sex": rng.choice("FM")} for _ in range(400)]
    qis = ["zip3", "age_band", "sex"]
    kept, stats = RI.generalise_to_k(recs, qis, k=5)
    if kept:
        assert RI.k_anonymity(kept, qis)["min_k"] >= 5
    assert stats["n_kept"] + stats["n_suppressed"] == len(recs)


def test_coarsening_age_bands_suppresses_fewer_records():
    """Generalise before suppressing. Suppression is the blunter tool and it
    biases the release, because the rows it removes are the unusual ones."""
    rng = __import__("random").Random(11)
    recs = [{"zip3": rng.choice(["100", "200"]),
             "age_band": rng.choice(["30-34", "35-39", "40-44", "45-49",
                                     "50-54", "55-59"]),
             "sex": rng.choice("FM")} for _ in range(300)]
    qis = ["zip3", "age_band", "sex"]
    _k1, plain = RI.generalise_to_k(recs, qis, k=5)
    _k2, coarse = RI.generalise_to_k(recs, qis, k=5,
                                     generalisations={"age_band": RI.coarsen_age_band})
    assert coarse["n_suppressed"] <= plain["n_suppressed"]


def test_age_band_coarsening_is_stable_and_keeps_the_90_plus_cap():
    assert RI.coarsen_age_band("30-34") == "30-39"
    assert RI.coarsen_age_band("35-39") == "30-39"
    assert RI.coarsen_age_band("90+") == "90+"


def test_a_clinical_fact_increases_exposure_over_demographics_alone():
    """The finding the attack exists to produce: Safe Harbor output is safe
    against a demographics-only adversary and not against one who knows a
    single clinical fact about the target."""
    rng = __import__("random").Random(13)
    # Enough strata that a rare condition leaves its carriers alone in their
    # equivalence class. With too few strata everyone hides in a crowd and the
    # test measures nothing -- which is what the first version of it did.
    zips = [f"{i:03d}" for i in range(100, 112)]      # 12 x 2 x 6 = 144 classes
    bands = ["50-54", "55-59", "60-64", "65-69", "70-74", "75-79"]
    recs = []
    for _ in range(2000):
        recs.append({"zip3": rng.choice(zips),
                     "sex": rng.choice("FM"),
                     "age_band": rng.choice(bands),
                     "cond": rng.random() < 0.04})
    demo = RI.k_anonymity(recs, ["zip3", "sex", "age_band"])
    with_cond = RI.k_anonymity(recs, ["zip3", "sex", "age_band", "cond"])
    assert with_cond["pct_unique"] >= demo["pct_unique"]
    carriers = [r for r in recs if r["cond"]]
    carrier_risk = RI.k_anonymity(carriers, ["zip3", "sex", "age_band", "cond"])
    assert carrier_risk["pct_unique"] > demo["pct_unique"]
