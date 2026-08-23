"""Tests for cross-table linkage and l-diversity.

The l-diversity tests carry a load the demo cannot: `run_linkage.py` finds no
class disclosing a real diagnosis, because `src/synth.py` draws each condition
flag independently and equivalence classes come out diverse. That is a clean
negative and it is reported as one — but a detector that has never fired is not
evidence it works, so the failure is constructed here.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import linkage as L
import suppression as SUP


# --------------------------------------------------------------------------
# nested-population differencing
# --------------------------------------------------------------------------

def _counts(rows):
    return {r: dict(c) for r, c in rows.items()}


def test_two_separately_safe_tables_are_refused_together():
    """The whole point. Both tables pass the small-cell rule; their difference
    is a table over a population nobody published and nobody suppressed."""
    reg = L.Register(min_cell=11)
    whole = _counts({"z1": {"ip": 100, "op": 200}})
    part = _counts({"z1": {"ip": 95, "op": 150}})     # residual ip = 5
    reg.check(name="all", dims=("zip", "cat"), counts=whole,
              values={"z1": {"ip": 1.0, "op": 2.0}}, population="all")
    with pytest.raises(L.DisclosureRefused) as e:
        reg.check(name="diabetics", dims=("zip", "cat"), counts=part,
                  values={"z1": {"ip": 1.0, "op": 2.0}},
                  population="diabetic")
    assert "expose" in str(e.value)


def test_the_exposed_cell_appears_in_neither_table():
    """The sharpest property: the disclosed population is the complement, and
    no published table contains it."""
    reg = L.Register(min_cell=11)
    reg.record(name="all", dims=("zip", "cat"),
               counts=_counts({"z1": {"ip": 100}}),
               values={"z1": {"ip": 1.0}}, population="all")
    findings = reg.attacks({"name": "sub", "dims": ("zip", "cat"),
                            "counts": _counts({"z1": {"ip": 96}}),
                            "values": {"z1": {"ip": 1.0}},
                            "population": "diabetic", "margins": ()})
    assert findings[0]["residual_n"] == 4
    assert "NEITHER table" in findings[0]["detail"]


def test_a_large_residual_is_not_a_disclosure():
    """The control. A rule that refuses everything is as useless as one that
    refuses nothing."""
    reg = L.Register(min_cell=11)
    reg.record(name="all", dims=("zip", "cat"),
               counts=_counts({"z1": {"ip": 100}}),
               values={"z1": {"ip": 1.0}}, population="all")
    out = reg.check(name="sub", dims=("zip", "cat"),
                    counts=_counts({"z1": {"ip": 40}}),
                    values={"z1": {"ip": 1.0}}, population="diabetic")
    assert out["safe_against_implemented_attacks"] is True


def test_a_zero_residual_is_not_flagged():
    """A residual of exactly zero discloses that the subset IS the whole
    population, which is a different statement and not a small-cell exposure."""
    reg = L.Register(min_cell=11)
    reg.record(name="all", dims=("z", "c"), counts=_counts({"z1": {"ip": 50}}),
               values={"z1": {"ip": 1.0}}, population="all")
    assert reg.attacks({"name": "s", "dims": ("z", "c"),
                        "counts": _counts({"z1": {"ip": 50}}),
                        "values": {"z1": {"ip": 1.0}},
                        "population": "sub", "margins": ()}) == []


def test_two_tables_over_different_populations_neither_of_which_is_all():
    """Nested differencing needs a whole-population table to subtract from.
    Two disjoint subsets do not difference into anything."""
    reg = L.Register(min_cell=11)
    reg.record(name="a", dims=("z", "c"), counts=_counts({"z1": {"ip": 50}}),
               values={"z1": {"ip": 1.0}}, population="diabetic")
    assert reg.attacks({"name": "b", "dims": ("z", "c"),
                        "counts": _counts({"z1": {"ip": 47}}),
                        "values": {"z1": {"ip": 1.0}},
                        "population": "copd", "margins": ()}) == []


def test_tables_on_different_dimensions_are_not_differenced():
    reg = L.Register(min_cell=11)
    reg.record(name="a", dims=("zip", "cat"),
               counts=_counts({"z1": {"ip": 100}}),
               values={"z1": {"ip": 1.0}}, population="all")
    assert reg.attacks({"name": "b", "dims": ("age", "cat"),
                        "counts": _counts({"z1": {"ip": 98}}),
                        "values": {"z1": {"ip": 1.0}},
                        "population": "diabetic", "margins": ()}) == []


# --------------------------------------------------------------------------
# shared-margin differencing
# --------------------------------------------------------------------------

def test_a_suppressed_cell_recoverable_from_a_prior_is_refused():
    """The suppression is real, correct, and worthless: the same total is
    published twice, broken down two ways, and the reader subtracts."""
    reg = L.Register(min_cell=11)
    reg.record(name="by age", dims=("zip", "age"),
               counts=_counts({"z1": {"u65": 60, "o65": 40}}),
               values={"z1": {"u65": 600.0, "o65": 400.0}}, population="all")
    findings = reg.attacks({
        "name": "by category", "dims": ("zip", "cat"),
        "counts": _counts({"z1": {"ip": 90, "op": 10}}),
        "values": {"z1": {"ip": 950.0, "op": SUP.SUPPRESSED}},
        "population": "all", "margins": ()})
    assert findings
    assert findings[0]["attack"] == "shared-margin differencing"
    assert findings[0]["recovered_value"] == pytest.approx(50.0)


def test_two_suppressed_cells_are_not_recoverable_by_subtraction():
    """The complementary-suppression rule from src/suppression.py, checked
    across tables rather than within one row."""
    reg = L.Register(min_cell=11)
    reg.record(name="by age", dims=("zip", "age"),
               counts=_counts({"z1": {"u65": 60}}),
               values={"z1": {"u65": 1000.0}}, population="all")
    findings = reg.attacks({
        "name": "by cat", "dims": ("zip", "cat"),
        "counts": _counts({"z1": {"ip": 90, "op": 10, "rx": 5}}),
        "values": {"z1": {"ip": 950.0, "op": SUP.SUPPRESSED,
                          "rx": SUP.SUPPRESSED}},
        "population": "all", "margins": ()})
    assert [f for f in findings
            if f["attack"] == "shared-margin differencing"] == []


# --------------------------------------------------------------------------
# the register itself
# --------------------------------------------------------------------------

def test_a_refused_table_is_not_added_to_the_register():
    """A refusal must not poison the history, or the next check compares
    against something that was never published."""
    reg = L.Register(min_cell=11)
    reg.check(name="all", dims=("z", "c"), counts=_counts({"z1": {"ip": 100}}),
              values={"z1": {"ip": 1.0}}, population="all")
    with pytest.raises(L.DisclosureRefused):
        reg.check(name="bad", dims=("z", "c"),
                  counts=_counts({"z1": {"ip": 95}}),
                  values={"z1": {"ip": 1.0}}, population="sub")
    assert [p["name"] for p in reg.published] == ["all"]


def test_a_clean_result_says_what_it_does_not_prove():
    """The general problem is integer programming over the whole release
    history. Clean here means two attacks failed."""
    reg = L.Register()
    out = reg.check(name="a", dims=("z", "c"), counts={}, values={})
    assert "NOT that the release is safe" in out["caveat"]


def test_the_first_table_is_always_accepted():
    reg = L.Register()
    assert reg.check(name="first", dims=("z", "c"),
                     counts=_counts({"z1": {"ip": 3}}),
                     values={"z1": {"ip": 1.0}})["name"] == "first"


# --------------------------------------------------------------------------
# l-diversity -- constructed, because the real data cannot produce it
# --------------------------------------------------------------------------

def test_a_homogeneous_class_is_caught_despite_being_k_anonymous():
    """THE CASE THE PROPERTY EXISTS FOR. Twenty records sharing quasi-
    identifiers is 20-anonymous, and discloses the diagnosis completely if all
    twenty share one. The attacker never identifies which record is the
    target."""
    rows = [{"zip3": "021", "sex": "F", "age_band": 60, "dx": "hiv"}
            for _ in range(20)]
    out = L.l_diversity(rows, "dx", ["zip3", "sex", "age_band"], l=2)
    assert out["n_violations"] == 1
    v = out["violations"][0]
    assert v["size"] == 20
    assert v["disclosed_value"] == "hiv"
    assert "k-anonymity is satisfied" in v["detail"]


def test_a_diverse_class_of_the_same_size_passes():
    rows = ([{"zip3": "021", "sex": "F", "age_band": 60, "dx": "hiv"}] * 10
            + [{"zip3": "021", "sex": "F", "age_band": 60, "dx": "copd"}] * 10)
    assert L.l_diversity(rows, "dx", ["zip3", "sex", "age_band"],
                         l=2)["n_violations"] == 0


def test_a_higher_l_demands_more_diversity():
    rows = ([{"z": "1", "dx": "a"}] * 5 + [{"z": "1", "dx": "b"}] * 5)
    assert L.l_diversity(rows, "dx", ["z"], l=2)["n_violations"] == 0
    assert L.l_diversity(rows, "dx", ["z"], l=3)["n_violations"] == 1


def test_a_skewed_class_clears_l_two_and_is_flagged_as_a_limitation():
    """19 of 20 sharing a value clears distinct l-diversity and discloses
    almost as much. The module says so rather than implying l=2 is enough."""
    rows = ([{"z": "1", "dx": "hiv"}] * 19 + [{"z": "1", "dx": "copd"}])
    out = L.l_diversity(rows, "dx", ["z"], l=2)
    assert out["n_violations"] == 0
    assert "skewed class" in out["note"]
    assert "t-closeness" in out["note"]


def test_the_worst_diversity_is_reported():
    rows = ([{"z": "1", "dx": "a"}] * 3 + [{"z": "2", "dx": "a"}]
            + [{"z": "2", "dx": "b"}])
    out = L.l_diversity(rows, "dx", ["z"], l=2)
    assert out["worst_diversity"] == 1
