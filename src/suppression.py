"""Small-cell suppression on the analytics output.

WHY THE ANALYTICS NEED THIS AND NOT JUST THE EXTRACT
----------------------------------------------------
`reidentify.py` measures k-anonymity on the member-level extract, which is the
obvious place to worry about disclosure. But a payer dashboard is also a
disclosure channel, and it is the one nobody guards: an aggregate table looks
safe because it is aggregate, and a cell of n=1 is not an aggregate. It is a
member, with their paid amount printed next to their condition.

THE PART EVERYONE GETS WRONG
----------------------------
Suppressing the small cells is the easy half and is not sufficient. Consider a
table published with its row total:

    Q3 inpatient spend      total $482,000
      cardiology            $310,000
      oncology              $164,000
      transplant            SUPPRESSED (n=2)

The transplant figure is $8,000 and anyone can compute it. The suppression is
decorative; the number is still published, just in subtraction form.

Preventing that requires COMPLEMENTARY suppression: once a cell is suppressed,
at least one more cell in the same row must be suppressed too, or the total
must be withheld. Choosing the second cell is a real optimisation problem
(minimise information lost subject to every suppressed cell being
underdetermined). This implements the standard greedy heuristic -- suppress the
NEXT SMALLEST cell -- which is not optimal but is the version that gets
deployed, and it is the difference between a suppression rule that works and
one that only looks like it does.

WHAT THIS IS NOT
----------------
Not a disclosure-control system. No differential privacy, no cell perturbation,
no controlled rounding, no dominance rule (the (n,k) rule, where a cell is
unsafe if one contributor accounts for most of it, EVEN IF n is large -- a
five-member cell where one member is 95% of the spend discloses that member's
cost to anyone who knows they are in it). No linkage analysis ACROSS published
tables, which is where real statistical agencies spend most of their effort:
two separately-safe tables can intersect to reveal a cell neither exposes.

The dominance rule is implemented below because it is cheap and its absence is
indefensible. The cross-table analysis is not, and is the largest gap here.
"""

from __future__ import annotations

MIN_CELL = 11          # HHS convention for public-use health data
DOMINANCE_SHARE = 0.85  # one contributor holding this much makes a cell unsafe
SUPPRESSED = "SUPPRESSED"


def cell_is_unsafe(n, top_contributor_share=None, min_cell=MIN_CELL,
                   dominance=DOMINANCE_SHARE):
    """Two independent reasons a cell cannot be published.

    THRESHOLD: fewer than `min_cell` members. 11 is the HHS convention for
    public-use health data; CMS uses 11 for beneficiary counts. It is a
    convention, not a theorem, and it is stated as one.

    DOMINANCE: one contributor accounts for most of the cell, which discloses
    their value to anyone who knows they are in it -- and that holds however
    large n is. A 500-member cell where one member is 95% of the spend is a
    disclosure of that member's spend. Threshold-only suppression misses this
    entirely, which is why the (n,k) rule exists.
    """
    reasons = []
    if n < min_cell:
        reasons.append(f"n={n} below the minimum cell size of {min_cell}")
    if top_contributor_share is not None and top_contributor_share >= dominance:
        reasons.append(
            f"one contributor holds {top_contributor_share:.0%} of the cell, "
            f"at or above the {dominance:.0%} dominance threshold")
    return reasons


def suppress_row(cells, *, total_published=True, min_cell=MIN_CELL,
                 dominance=DOMINANCE_SHARE):
    """Suppress unsafe cells in one row, plus complementary cells.

    `cells` is {label: {"n": int, "value": float, "top_share": float|None}}.
    Returns (published, notes) where a suppressed cell's value is SUPPRESSED.

    THE COMPLEMENTARY PASS is the reason this function is longer than one line.
    If the row total is published and exactly one cell is suppressed, that cell
    is recoverable by subtraction and the suppression achieved nothing. A second
    cell must go -- the next smallest, so the least information is lost.

    If the caller withholds the total instead (`total_published=False`), no
    complementary suppression is needed, and that is often the better trade: one
    aggregate withheld beats two detail lines withheld. The choice is the
    caller's because it depends on which number the audience came for.
    """
    published, notes = {}, []
    primary = []
    for label, c in cells.items():
        reasons = cell_is_unsafe(c["n"], c.get("top_share"), min_cell, dominance)
        if reasons:
            primary.append(label)
            notes.append({"cell": label, "kind": "primary", "reasons": reasons})
    for label in cells:
        published[label] = (SUPPRESSED if label in primary
                            else cells[label]["value"])

    if primary and total_published:
        remaining = [l for l in cells if l not in primary]
        if len(primary) == 1 and remaining:
            # exactly one hole in a row whose total is known: solvable
            victim = min(remaining, key=lambda l: cells[l]["value"])
            published[victim] = SUPPRESSED
            notes.append({
                "cell": victim, "kind": "complementary",
                "reasons": [f"'{primary[0]}' was the only suppressed cell in a "
                            f"row with a published total, so its value was "
                            f"recoverable by subtraction. This cell was "
                            f"suppressed as well -- the smallest remaining, so "
                            f"the least information is lost."]})
        elif not remaining:
            notes.append({"cell": "*", "kind": "row-withheld",
                          "reasons": ["every cell in this row is unsafe"]})
    return published, notes


def suppress_table(rows, *, total_published=True, min_cell=MIN_CELL,
                   dominance=DOMINANCE_SHARE):
    """Apply `suppress_row` across a table of {row_label: {cell_label: cell}}.

    ROW BY ROW, AND THAT IS A STATED LIMITATION. If the table also publishes
    COLUMN totals, a cell suppressed in its row can still be recovered down its
    column, and defending both directions at once is a linear-programming
    problem rather than a greedy pass. This function does not attempt it, and a
    table with both margins published should not be considered protected by it.
    """
    out, all_notes = {}, []
    for label, cells in rows.items():
        pub, notes = suppress_row(cells, total_published=total_published,
                                  min_cell=min_cell, dominance=dominance)
        out[label] = pub
        for n in notes:
            all_notes.append({**n, "row": label})
    return out, all_notes


def audit(rows, published):
    """Can any suppressed value be recovered from what was published?

    A suppression rule that is never checked is a comment. This recomputes the
    obvious attack -- subtract the published cells from the total -- and reports
    any cell whose value falls out of it. It is the same discipline as
    `reidentify.py`: attack your own output and report what the attack gets.
    """
    findings = []
    for label, cells in rows.items():
        pub = published[label]
        hidden = [c for c in cells if pub[c] == SUPPRESSED]
        if len(hidden) == 1:
            total = sum(c["value"] for c in cells.values())
            shown = sum(v for v in pub.values() if v != SUPPRESSED)
            findings.append({
                "row": label, "cell": hidden[0],
                "recovered_value": total - shown,
                "true_value": cells[hidden[0]]["value"],
                "how": "row total minus the published cells"})
    return findings
