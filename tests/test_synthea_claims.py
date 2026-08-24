"""What running the analytics on Synthea says about this project's own data.

The mapping tests need no Synthea and always run. The comparison tests skip
unless a population has been generated -- DATA-2 owns that step
(`cd ../data2-fhir-hedis && python run_synthea.py --generate`), because a
second 6GB copy would be pure waste.
"""

import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import synth
import synthea_claims as SC


# ------------------------------------------------------------- the taxonomy
def test_the_category_map_is_deliberately_incomplete():
    """Skilled nursing, hospice and home health have NO bucket here.

    Leaving them unmapped is the decision, not an oversight: they keep their
    own Synthea names so the hole shows up in the output instead of being
    averaged into a category they do not belong to.
    """
    mapped = set(SC.CATEGORY_MAP)
    for klass in ("snf", "hospice", "home"):
        assert klass not in mapped
        assert klass in SC.UNMAPPED


def test_every_unmapped_class_carries_a_reason():
    """A gap without a reason becomes folklore. Each of these says why it
    cannot simply be folded into an existing category."""
    for klass, why in SC.UNMAPPED.items():
        assert why and len(why) > 20


def test_urgentcare_maps_to_emergency_not_professional():
    """A judgement call, made explicitly. Urgent care is unscheduled acute
    presentation, which behaves like emergency and nothing like a scheduled
    office visit -- the alternative would flatter the emergency trend."""
    assert SC.CATEGORY_MAP["urgentcare"] == "emergency"


def test_the_five_project_categories_are_all_reachable():
    """Except pharmacy, which comes from a different file entirely."""
    produced = set(SC.CATEGORY_MAP.values())
    assert {"inpatient", "outpatient", "emergency", "professional"} <= produced


# -------------------------------------------------------------- integration
CSV = SC and os.path.normpath(os.path.join(
    ROOT, "..", "data2-fhir-hedis", "synthea_out", "output", "csv"))

synthea_only = pytest.mark.skipif(
    not os.path.isdir(CSV),
    reason="no Synthea CSV export; generate one from ../data2-fhir-hedis")


@pytest.fixture(scope="module")
def loaded():
    return SC.load(CSV, start=date(2023, 1, 1), end=date(2024, 12, 31))


@synthea_only
def test_pharmacy_claims_come_from_a_separate_file(loaded):
    """There is no pharmacy encounter class. A medical-only adapter would
    report a book of business with no drug spend at all."""
    cats = {c["service_category"] for c in loaded["claims"]}
    assert "pharmacy" in cats
    rx = [c for c in loaded["claims"] if c["service_category"] == "pharmacy"]
    assert len(rx) > 1000


@synthea_only
def test_unmapped_spend_is_real_but_small(loaded):
    """Large enough to matter, small enough that nobody would notice it
    missing -- which is exactly why it needs reporting."""
    cov = SC.coverage_summary(loaded)
    assert 0.01 < cov["unmapped_share"] < 0.15
    assert set(cov["unmapped_categories"]) <= set(SC.UNMAPPED)


@synthea_only
def test_folding_post_acute_into_inpatient_would_distort_it(loaded):
    """THE ARGUMENT, CHECKED RATHER THAN ASSERTED.

    Home health and skilled nursing sit on opposite sides of inpatient by a
    wide margin, so any single merged average misdescribes at least one of
    them. The exact multiples move with the window, so this asserts the SPREAD
    rather than a number.
    """
    cov = SC.coverage_summary(loaded)["by_category"]
    per = {k: v["paid"] / v["claims"]
           for k, v in cov.items() if v["claims"]}
    assert per["snf"] > 5 * per["home"]
    assert per["inpatient"] > 5 * per["home"]


# ------------------------------------------------- the finding about US
@synthea_only
def test_our_own_generator_has_a_much_lighter_tail(loaded):
    """THE FINDING THAT IS ABOUT THIS PROJECT, NOT ABOUT SYNTHEA.

    Cost concentration is what a care-management programme is sized on, and
    the README argues for reporting it precisely because the distribution is
    skewed. Ours is markedly less skewed than Synthea's.
    """
    import run_synthea as RS

    syn, own, _cov = RS.run(CSV)
    assert syn["top_5pct"] > own["top_5pct"] * 1.5
    assert syn["p99_over_mean"] > own["p99_over_mean"] * 1.5


def test_the_light_tail_is_structural_not_bad_luck():
    """The mechanism, not the symptom.

    `synth.generate` draws each category as Poisson(rate x risk_score x
    covered_days) and prices it with a bounded uniform. The ONLY between-member
    driver is `risk_score`, and it spans a narrow range -- so no member can be
    fifty times the mean, because nothing in the model lets them be.
    """
    data = synth.generate(n_members=2000, seed=17)
    scores = [m["risk_score"] for m in data["members"]]
    assert max(scores) / min(scores) < 6, (
        "if this ever gets wide, revisit the light-tail claim in "
        "docs/SYNTHEA_COMPARISON.md")
