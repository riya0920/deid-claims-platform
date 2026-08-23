"""Cross-table linkage: two safe tables that are unsafe together.

WHAT THE README CALLED ITS LARGEST GAP
---------------------------------------
"Suppression defends rows, not columns ... Also absent: controlled rounding,
cell perturbation, and -- the largest gap -- cross-table linkage analysis, where
two separately-safe tables intersect to reveal a cell neither exposes on its
own. That is where real statistical agencies spend most of their effort."

THE ATTACK
----------
Every cell in every published table is a linear equation over the underlying
counts. Publish enough tables and the equations become solvable, and nothing
about any single table looks wrong while it happens.

    Table A   spend by ZIP3 x category
    Table B   spend by ZIP3 x age band

Both pass the small-cell rule. But their shared margin -- total spend per ZIP3 --
lets a reader subtract across them, and a cell suppressed in A can fall out of
B's arithmetic. The suppression in A was real, correct, and worthless.

This is the DIFFERENCING ATTACK, and it is why statistical agencies run a
release register rather than checking each table as it goes out. The check that
matters is not "is this table safe" but "is this table safe GIVEN EVERYTHING
ALREADY PUBLISHED", and that question cannot be answered by looking at the
table.

WHAT IS IMPLEMENTED
-------------------
`Register` remembers every published table and, before a new one goes out, runs
the differencing attack against the whole history:

  * SHARED-MARGIN DIFFERENCING. If two tables share a margin and one has a
    single suppressed cell in a row whose total is recoverable from the other,
    that cell is solvable. Found by construction rather than by search.

  * NESTED-POPULATION DIFFERENCING. The sharper case: a table over ALL members
    and a table over a SUBSET (say, diabetics). Subtract and you have a table
    over non-diabetics that nobody published and nobody suppressed. If the
    difference produces a cell below the threshold, the pair discloses it --
    and neither table on its own contains that cell at all.

`check()` REFUSES the release rather than warning, because a warning on a
publication path is a warning that gets clicked through, and the disclosure is
permanent once the table is out.

WHAT THIS IS NOT
----------------
Not a complete audit. The general problem -- can any linear combination of the
published cells resolve a suppressed one -- is an integer-programming problem
over the whole release history, and real agencies solve it with dedicated
solvers. This checks two specific families that cover the common cases and says
so. A clean result here means "the two attacks I implemented do not work", not
"the release is safe".
"""

from __future__ import annotations

from itertools import combinations

SUPPRESSED = "SUPPRESSED"


class DisclosureRefused(Exception):
    """Raised when a release would expose a cell in combination with a prior.

    Deliberately fatal. A warning on a publication path is a warning that gets
    clicked through, and a table cannot be unpublished.
    """


class Register:
    """Every table published so far, and the attacks against the next one.

    THE REGISTER IS THE POINT. Checking a table in isolation is what makes a
    release unsafe, because a table's safety is a property of the SET it joins,
    not of the table. That is the whole reason statistical agencies keep one.
    """

    def __init__(self, min_cell=11):
        self.min_cell = min_cell
        self.published = []

    def record(self, *, name, dims, counts, values, population="all",
               margins=()):
        """Register a released table.

        `counts` and `values` are {row_key: {col_key: n}} / {...: value}.
        `population` names the subset the table covers -- the field the nested
        attack turns on, and the one a release process most often fails to
        record because it feels like documentation rather than data.
        """
        self.published.append({
            "name": name, "dims": tuple(dims), "counts": counts,
            "values": values, "population": population,
            "margins": tuple(margins)})
        return self.published[-1]

    # -- attacks ----------------------------------------------------------
    def _nested_differencing(self, candidate):
        """A table over a SUBSET, differenced against one over the whole.

        The sharpest of the two, because the exposed cell exists in NEITHER
        table. Publish spend for all members and spend for diabetics, and the
        difference is spend for non-diabetics -- a population nobody chose to
        publish, nobody suppressed, and nobody checked.
        """
        findings = []
        for prior in self.published:
            if prior["dims"] != candidate["dims"]:
                continue
            pops = {prior["population"], candidate["population"]}
            if len(pops) < 2 or "all" not in pops:
                continue
            whole = prior if prior["population"] == "all" else candidate
            part = candidate if whole is prior else prior
            for row, cols in part["counts"].items():
                for col, n_part in cols.items():
                    n_whole = whole["counts"].get(row, {}).get(col)
                    if n_whole is None:
                        continue
                    residual = n_whole - n_part
                    if 0 < residual < self.min_cell:
                        findings.append({
                            "attack": "nested-population differencing",
                            "against": prior["name"],
                            "row": row, "cell": col,
                            "residual_n": residual,
                            "detail": (
                                f"{whole['name']} covers 'all' and "
                                f"{part['name']} covers "
                                f"'{part['population']}'. Subtracting gives a "
                                f"cell of n={residual} for the complement, "
                                f"which is below the minimum of "
                                f"{self.min_cell}. That cell appears in "
                                f"NEITHER table and was never suppressed by "
                                f"either."),
                        })
        return findings

    def _shared_margin_differencing(self, candidate):
        """A cell suppressed here, recoverable from a prior sharing its margin.

        The suppression is real, correct, and worthless: the same total is
        published twice, broken down two ways, and the reader subtracts.
        """
        findings = []
        for prior in self.published:
            shared = set(prior["dims"]) & set(candidate["dims"])
            if not shared:
                continue
            for row, cols in candidate["values"].items():
                hidden = [c for c, v in cols.items() if v == SUPPRESSED]
                if len(hidden) != 1:
                    continue
                prior_row = prior["values"].get(row)
                if not prior_row:
                    continue
                if any(v == SUPPRESSED for v in prior_row.values()):
                    continue
                # the prior publishes this row's total, broken down differently
                prior_total = sum(v for v in prior_row.values()
                                  if v != SUPPRESSED)
                shown = sum(v for v in cols.values() if v != SUPPRESSED)
                findings.append({
                    "attack": "shared-margin differencing",
                    "against": prior["name"],
                    "row": row, "cell": hidden[0],
                    "recovered_value": prior_total - shown,
                    "detail": (
                        f"'{prior['name']}' publishes the total for row "
                        f"{row!r} broken down by {set(prior['dims']) - shared} "
                        f"with no suppression. Subtracting the cells published "
                        f"here recovers the suppressed one exactly."),
                })
        return findings

    def attacks(self, candidate):
        return (self._nested_differencing(candidate)
                + self._shared_margin_differencing(candidate))

    def check(self, *, name, dims, counts, values, population="all",
              margins=(), publish=True):
        """Run every attack against the history. REFUSE rather than warn."""
        candidate = {"name": name, "dims": tuple(dims), "counts": counts,
                     "values": values, "population": population,
                     "margins": tuple(margins)}
        findings = self.attacks(candidate)
        if findings:
            raise DisclosureRefused(
                f"publishing {name!r} would expose {len(findings)} cell(s) in "
                f"combination with an already-published table. "
                f"First: {findings[0]['detail']}")
        if publish:
            self.published.append(candidate)
        return {"name": name, "safe_against_implemented_attacks": True,
                "checked_against": [p["name"] for p in self.published
                                    if p is not candidate],
                "caveat": (
                    "clean here means the two implemented attacks do not work, "
                    "NOT that the release is safe. The general problem -- can "
                    "any linear combination of published cells resolve a "
                    "suppressed one -- is integer programming over the whole "
                    "release history.")}


def l_diversity(rows, sensitive_key, quasi_keys, l=2):
    """Does every equivalence class contain at least `l` distinct values?

    THE GAP k-ANONYMITY LEAVES, and the README named it: k-anonymity "says
    nothing about attribute disclosure when a whole equivalence class shares a
    diagnosis".

    A class of 20 members who are all the same age, sex and ZIP3 is 20-anonymous
    and completely discloses their diagnosis if all 20 have the same one. The
    attacker never has to identify WHICH record is their target -- knowing the
    target is in the class is enough to learn the sensitive value.

    This is distinct l-diversity, the weakest form. It does not defend against a
    skewed class (19 of 20 sharing a value clears l=2 and discloses almost as
    much), which is what entropy l-diversity and t-closeness address and this
    does not.
    """
    classes = {}
    for r in rows:
        key = tuple(r.get(q) for q in quasi_keys)
        classes.setdefault(key, []).append(r.get(sensitive_key))
    violations = []
    for key, values in classes.items():
        distinct = len(set(values))
        if distinct < l:
            violations.append({
                "equivalence_class": dict(zip(quasi_keys, key)),
                "size": len(values), "distinct_sensitive_values": distinct,
                "disclosed_value": values[0] if distinct == 1 else None,
                "detail": (
                    f"{len(values)} records share these quasi-identifiers and "
                    f"only {distinct} distinct value(s) of {sensitive_key!r}. "
                    f"k-anonymity is satisfied at k={len(values)} and the "
                    f"sensitive value is disclosed anyway -- knowing the "
                    f"target is in this class is enough."),
            })
    worst = min((v["distinct_sensitive_values"] for v in violations),
                default=None)
    return {"l": l, "n_classes": len(classes), "violations": violations,
            "n_violations": len(violations), "worst_diversity": worst,
            "note": ("distinct l-diversity, the weakest form. A skewed class "
                     "-- 19 of 20 sharing a value -- clears l=2 and discloses "
                     "almost as much. Entropy l-diversity and t-closeness "
                     "address that and are not implemented.")}
