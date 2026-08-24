"""dbt vs the Python analytics, and the measured cost of date shifting.

TWO SEPARATE CLAIMS LIVE HERE
-----------------------------
1. The dbt models and `src/analytics.py` are independent implementations of the
   same measures and must agree to the cent.
2. De-identification by date shifting is NOT free, and the size of the cost is
   pinned so it cannot quietly grow.

PARITY MEANS THE SAME INPUT
---------------------------
The dbt project reads the SHIFTED extract. Comparing it against the Python
analytics run on RAW data would report a difference that is the date shift, not
an implementation disagreement -- so the Python side is run over
`export.shifted_view`, which applies exactly the same shift in memory. Anything
left over after that is a genuine difference between the two implementations.

SKIPS when duckdb or the dbt build is absent.
"""

import os
import sys
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import analytics
import export
import synth

duckdb = pytest.importorskip("duckdb", reason="dbt parity audit only")

DUCK = os.path.join(ROOT, "dbt", "claims.duckdb")
SALT = "demo-salt-not-a-secret"
START, END = date(2023, 1, 1), date(2024, 12, 31)

pytestmark = pytest.mark.skipif(
    not os.path.exists(DUCK),
    reason="run `python run_dbt.py` once to build the dbt models")


@pytest.fixture(scope="module")
def world():
    raw = synth.generate(n_members=8000, seed=17)
    shifted = export.shifted_view(raw, SALT)
    spine, _ = analytics.member_month_spine(shifted["eligibility"], START, END)
    py = analytics.pmpm_by_category(shifted["claims"], spine, START, END)
    dk = duckdb.connect(DUCK, read_only=True)
    yield raw, py, dk
    dk.close()


def _dbt_monthly(dk):
    # member_months lives on the month x category GRID, so it is taken ONCE per
    # month rather than summed -- summing it across categories multiplies the
    # denominator by the number of categories, which is a mistake worth
    # naming because the resulting PMPM looks plausible.
    rows = dk.execute(
        "select year, month, any_value(member_months), sum(paid) "
        "from fct_pmpm_monthly group by 1, 2").fetchall()
    return {(int(y), int(m)): (mm, p) for y, m, mm, p in rows}


def test_member_months_agree_to_the_cent(world):
    _raw, py, dk = world
    got = _dbt_monthly(dk)
    for key, row in py.items():
        assert key in got, key
        assert row["member_months"] == pytest.approx(got[key][0], abs=1e-6)


def test_paid_agrees_to_the_cent(world):
    _raw, py, dk = world
    got = _dbt_monthly(dk)
    for key, row in py.items():
        assert row["total_paid"] == pytest.approx(got[key][1], abs=0.01)


def test_there_is_something_to_compare(world):
    """A parity test over an empty result passes and proves nothing."""
    _raw, py, dk = world
    assert len(py) == 24
    assert sum(r["total_paid"] for r in py.values()) > 1_000_000


def test_member_ids_are_unique(world):
    """REGRESSION TEST FOR THE BUG THE dbt BUILD FOUND.

    `phi.make_person` draws a 9-digit id at random, and over 8,000 members the
    birthday bound gives roughly a 3% chance of a collision -- which duly
    happened on the default seed. A duplicate member_id puts the member in the
    dimension twice and inflates every denominator that joins through them.

    Nothing in this suite asserted it before, because a primary key is the kind
    of thing one declares in a schema and never writes a unit test for. A dbt
    `unique` test found it on the first build.
    """
    raw, _py, _dk = world
    ids = [m["member_id"] for m in raw["members"]]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------- shift cost
def test_date_shifting_moves_a_quarter_of_the_claims_out_of_window(world):
    """THE COST OF DE-IDENTIFICATION, AS A NUMBER.

    Every offset here is negative, so every member's history walks earlier.
    Claims near the start of the extract walk off the front of the reporting
    window entirely. This does not fail loudly -- it silently depresses volumes
    at the edges, which reads as a real trend.
    """
    raw, _py, _dk = world
    cost = export.shift_cost(raw, SALT, START, END)
    assert 0.20 < cost["fraction_lost"] < 0.35
    assert cost["claims_lost"] > 40_000


def test_the_usable_window_is_much_narrower_than_the_requested_one(world):
    """The intersection of every member's shifted span is a shift-width
    narrower at each edge. Here that is 368 usable days out of 731 requested --
    the shift costs HALF the reporting period.

    The fix is to extract a wider raw range than you intend to report on, not
    to report over a window the data no longer covers.
    """
    raw, _py, _dk = world
    cost = export.shift_cost(raw, SALT, START, END)
    assert cost["usable_days"] < cost["requested_days"] / 1.5
    assert cost["usable_days"] > 300


def test_offsets_are_one_directional_which_is_why_the_loss_is_one_sided(world):
    """Documents WHY the loss looks the way it does.

    `deid.patient_offset` returns a negative offset by construction. A
    symmetric offset would split the loss across both edges rather than
    removing it -- the edge effect is inherent to shifting, not to the sign.
    """
    raw, _py, _dk = world
    cost = export.shift_cost(raw, SALT, START, END)
    assert cost["offset_min"] < 0 and cost["offset_max"] < 0
