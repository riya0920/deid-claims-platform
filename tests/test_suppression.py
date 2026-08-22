"""Tests for small-cell suppression and the metric dictionary.

The suppression tests are attack tests, matching how the rest of this project
works: build a table, publish it, then try to recover what was withheld. A
suppression rule that is never attacked is a comment.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import metrics as M
import suppression as SUP


def _cell(n, value, top_share=None):
    return {"n": n, "value": value, "top_share": top_share}


# --------------------------------------------------------------------------
# the two reasons a cell is unsafe
# --------------------------------------------------------------------------

def test_a_small_cell_is_unsafe():
    assert SUP.cell_is_unsafe(3)
    assert "below the minimum cell size" in SUP.cell_is_unsafe(3)[0]


def test_a_large_cell_is_safe():
    assert SUP.cell_is_unsafe(500) == []


def test_dominance_makes_a_LARGE_cell_unsafe():
    """The case a threshold alone misses entirely. A 500-member cell where one
    member is 95% of the spend discloses that member's spend to anyone who
    knows they are in it, and n=500 says nothing about that."""
    reasons = SUP.cell_is_unsafe(500, top_contributor_share=0.95)
    assert reasons and "dominance" in reasons[0]


def test_dominance_and_threshold_are_independent():
    both = SUP.cell_is_unsafe(4, top_contributor_share=0.99)
    assert len(both) == 2


# --------------------------------------------------------------------------
# the attack
# --------------------------------------------------------------------------

def test_one_suppressed_cell_in_a_row_with_a_total_is_recoverable():
    """THE BUG THE COMPLEMENTARY PASS EXISTS FOR, demonstrated rather than
    asserted: suppression that leaves exactly one hole publishes the number in
    subtraction form."""
    row = {"cardiology": _cell(400, 310_000.0),
           "oncology": _cell(200, 164_000.0),
           "transplant": _cell(2, 8_000.0)}
    # suppress WITHOUT the complementary pass, as a naive implementation would
    naive = {k: (SUP.SUPPRESSED if k == "transplant" else v["value"])
             for k, v in row.items()}
    findings = SUP.audit({"q3": row}, {"q3": naive})
    assert len(findings) == 1
    assert findings[0]["recovered_value"] == pytest.approx(8_000.0)
    assert findings[0]["true_value"] == pytest.approx(8_000.0)


def test_the_complementary_pass_defeats_that_attack():
    row = {"cardiology": _cell(400, 310_000.0),
           "oncology": _cell(200, 164_000.0),
           "transplant": _cell(2, 8_000.0)}
    pub, notes = SUP.suppress_row(row, total_published=True)
    assert pub["transplant"] == SUP.SUPPRESSED
    assert sum(1 for v in pub.values() if v == SUP.SUPPRESSED) == 2
    assert SUP.audit({"q3": row}, {"q3": pub}) == []
    assert any(n["kind"] == "complementary" for n in notes)


def test_the_complementary_victim_is_the_smallest_remaining_cell():
    """Greedy, and stated as greedy: minimise information lost subject to the
    suppressed cell being underdetermined. Not optimal, but it is the version
    that gets deployed."""
    row = {"big": _cell(400, 900_000.0), "mid": _cell(300, 200_000.0),
           "small": _cell(250, 40_000.0), "tiny_n": _cell(2, 5_000.0)}
    pub, _n = SUP.suppress_row(row, total_published=True)
    assert pub["tiny_n"] == SUP.SUPPRESSED
    assert pub["small"] == SUP.SUPPRESSED       # smallest of the remainder
    assert pub["big"] != SUP.SUPPRESSED


def test_no_complementary_suppression_when_the_total_is_withheld():
    """Often the better trade: one aggregate withheld beats two detail lines
    withheld. The choice belongs to the caller because it depends on which
    number the audience came for."""
    row = {"a": _cell(400, 310_000.0), "b": _cell(2, 8_000.0)}
    pub, notes = SUP.suppress_row(row, total_published=False)
    assert sum(1 for v in pub.values() if v == SUP.SUPPRESSED) == 1
    assert not any(n["kind"] == "complementary" for n in notes)


def test_two_primary_suppressions_need_no_complementary_cell():
    row = {"a": _cell(400, 310_000.0), "b": _cell(2, 8_000.0),
           "c": _cell(5, 12_000.0)}
    pub, notes = SUP.suppress_row(row, total_published=True)
    assert sum(1 for v in pub.values() if v == SUP.SUPPRESSED) == 2
    assert not any(n["kind"] == "complementary" for n in notes)
    assert SUP.audit({"r": row}, {"r": pub}) == []


def test_a_row_where_everything_is_unsafe_is_withheld_entirely():
    row = {"a": _cell(3, 100.0), "b": _cell(2, 90.0)}
    pub, notes = SUP.suppress_row(row, total_published=True)
    assert all(v == SUP.SUPPRESSED for v in pub.values())
    assert any(n["kind"] == "row-withheld" for n in notes)


def test_a_safe_row_is_left_completely_alone():
    """A rule that fires on everything is as useless as one that fires on
    nothing."""
    row = {"a": _cell(400, 1.0), "b": _cell(300, 2.0), "c": _cell(200, 3.0)}
    pub, notes = SUP.suppress_row(row, total_published=True)
    assert SUP.SUPPRESSED not in pub.values()
    assert notes == []


def test_suppress_table_audits_clean_on_the_real_drill_down():
    """End to end on a table shaped like the dashboard's: many rows, a few
    small cells scattered through them."""
    rows = {}
    for i in range(20):
        rows[f"zip{i}"] = {
            "inpatient": _cell(3 if i % 4 == 0 else 200, 50_000.0 + i),
            "outpatient": _cell(500, 90_000.0 + i),
            "pharmacy": _cell(600, 30_000.0 + i),
        }
    pub, notes = SUP.suppress_table(rows, total_published=True)
    assert SUP.audit(rows, pub) == []
    assert any(n["kind"] == "primary" for n in notes)
    assert any(n["kind"] == "complementary" for n in notes)


# --------------------------------------------------------------------------
# the metric dictionary
# --------------------------------------------------------------------------

def test_every_metric_carries_the_fields_that_get_argued_about():
    required = {"name", "definition", "formula", "grain", "denominator",
                "claim_basis", "runout_note", "owner", "known_caveats"}
    for key, m in M.METRICS.items():
        missing = required - set(m)
        assert not missing, f"{key} is missing {missing}"
        assert m["known_caveats"], f"{key} claims to have no caveats"


def test_pmpm_uses_member_months_not_members():
    """The single largest source of disagreement in payer analytics, pinned so
    a future edit cannot quietly change the denominator."""
    assert "MEMBER-MONTHS" in M.metric("pmpm")["denominator"]
    assert M.metric_value("pmpm", paid=1000.0, member_months=250.0) == 4.0


def test_price_uses_services_as_its_denominator_not_member_months():
    assert "NOT member-months" in M.metric("price_per_service")["denominator"]
    assert M.metric_value("price_per_service", paid=1000.0, services=8) == 125.0


def test_a_zero_denominator_returns_zero_rather_than_raising():
    assert M.metric_value("pmpm", paid=1000.0, member_months=0) == 0.0


def test_an_unknown_metric_names_the_ones_that_exist():
    with pytest.raises(KeyError) as e:
        M.metric("pmpy")
    assert "pmpm" in str(e.value)


def test_a_metric_defined_but_computed_elsewhere_says_so():
    """Better than silently returning something. The dictionary is the index of
    every metric, not only the ones this module happens to evaluate."""
    with pytest.raises(NotImplementedError) as e:
        M.metric_value("cost_concentration", paid=1.0)
    assert "analytics.py" in str(e.value)


def test_the_decomposition_declares_its_own_order_dependence():
    caveats = " ".join(M.metric("pmpm_decomposition")["known_caveats"])
    assert "ORDER-DEPENDENT" in caveats
    assert "not causation" in caveats


def test_the_generated_document_contains_every_definition():
    """The document is generated, not written. A hand-maintained copy is a
    second source of truth and starts drifting the day after it is committed."""
    md = M.render_markdown()
    for key, m in M.METRICS.items():
        assert f"`{key}`" in md
        assert m["denominator"][:40] in md
    assert M.VERSION in md
