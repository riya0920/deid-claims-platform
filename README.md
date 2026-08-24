# DATA-1 — De-identification pipeline + claims analytics — working system, 10 known gaps

**Govern, then analyse.** The privacy layer is built as engineering with a
measured recall number, not as a disclaimer, and the payer analytics run on its
output.

```bash
python run_pipeline.py     # generate -> plant PHI -> de-identify -> MEASURE -> analyse
python run_attack.py       # re-identification attack on our own output
python write_method.py     # -> docs/DEID_METHOD.md
python dashboard.py           # -> out/dashboard.html + docs/METRIC_DICTIONARY.md
python run_linkage.py         # cross-table differencing + l-diversity
python -m pytest tests -q     # 79 tests
python run_dbt.py             # de-identified extract -> dbt build + 31 dbt tests
```

Runs offline in about 30 seconds. 8,000 members, ~199,000 claims, ~5,200
free-text notes, ~20,000 planted PHI spans.

---

## The six things worth reading

### 1. De-identification recall, measured against planted ground truth

Synthetic claims contain no PHI, so PHI is deliberately planted back into free
text with **exact character offsets logged**. Those offsets are the ground
truth. This is what turns *"the data was anonymised"* from a sentence into an
engineering result.

| identifier | planted | found | missed | recall | over-redactions |
|---|---|---|---|---|---|
| name | 10,774 | 10,112 | 662 | **93.9%** | 537 |
| phone | 1,862 | 1,862 | 0 | **100.0%** | 0 |
| address | 1,794 | 1,505 | 289 | **83.9%** | 0 |
| member_id | 1,172 | 1,172 | 0 | **100.0%** | 0 |
| date / MRN / SSN / email / URL / IP / account / ZIP | ~3,500 | all | 0 | **100.0%** | 0 |
| **overall** | **20,345** | **19,394** | **951** | **95.3%** | 537 |

**Recall and precision are never averaged into an F1 here**, because they are
not commensurable. A miss is a *disclosure* — PHI surviving into a dataset
everyone downstream believes is clean, and will copy, join, and email
accordingly. A false positive is *over-redaction*: a real cost to analytic
utility, but a recoverable one, because the original is still there to re-run.

### 2. Names are at 93.9%, not 99%, and that is the honest number

The spec's target is >99% on names. This pipeline does not hit it, by
construction: **30% of members carry surnames deliberately absent from the
detector's gazetteer**, so those are recoverable only from context rules
(`Patient X`, `Dr. X`, `spoke with X`).

A name detector evaluated against the same list it was built from reports 100%
and measures nothing but its own internal consistency. The split name pools in
`src/phi.py` exist precisely to prevent that, and
`test_names_outside_the_gazetteer_are_caught_by_context_only` fails if the
context-free path ever starts finding them.

The 537 over-redactions have a nameable cause: **facility names that collide
with surnames** — `Parker Regional`, `Baker Memorial`, `Mason General`. No
gazetteer distinguishes `Mr Parker` from `Parker Regional` without more
context, and over-redacting the facility destroys the site variable every
provider-level analysis needs. That is the actual daily trade-off in this work.

Reaching >99% needs a different architecture — a trained NER model plus human
review of flagged documents. Tuning the pool split until the number looked
better was the dishonest option available, and it is named here so a reader
knows it was declined.

### 3. Dates are shifted, not deleted

Each patient gets a deterministic offset; every date for that patient moves by
it; **within-patient intervals are preserved exactly**
(`test_dates_are_shifted_not_deleted_and_intervals_survive`).

Deleting dates satisfies Safe Harbor and silently corrupts readmission windows,
length of stay, adherence gaps, episode construction, and every before/after
comparison — most of what claims data is *for*.

The cost is stated rather than hidden: shifting is weaker than deletion, since
an attacker who learns one true date for one patient recovers that patient's
offset and all their dates. Offsets are per-patient rather than global, so the
compromise does not propagate.

### 4. Member-month discipline, and a decomposition checked against a planted cause

```
actual member-months         169,884
naive (members × months)     192,000    ← 13.0% overstatement
```

Using the naive denominator understates PMPM by **11.5%**, uniformly — which is
exactly the kind of error nobody investigates, because it makes the trend look
better.

**The planted shock.** 2024 Q3 was generated with inpatient unit price ×1.20, an
8% group termination, and *utilisation unchanged*. That combination is chosen
because it is the one that makes analysts wrong: PMPM jumps, and the instinctive
reading — "utilisation is up, members are sicker" — is false in both clauses.

PMPM moved **+7.1%** (257.51 → 275.74) while member-months fell **9.1%**.
Decomposition of the +18.23:

| effect | PMPM | share |
|---|---|---|
| **price** | +16.34 | **90%** |
| utilisation | +4.40 | 24% |
| mix | −3.01 | −16% |
| residual / interaction | +0.50 | 3% |

By category, the price effect is almost entirely inpatient (+16.45 of +16.34
total). Recovered inpatient unit price ×1.25 against a planted ×1.20.

Two honest notes. The recovery is directionally exact and quantitatively
approximate — the ×1.25 vs ×1.20 gap comes from claim-mix variation across the
quarter boundary. And the +24% utilisation effect **was not planted**: it is
consistent with sampling variation plus mid-quarter truncation edge effects.
That is itself the lesson — a decomposition attributes noise as confidently as
it attributes signal, so a component with no known cause is a reminder that
these numbers have error bars nobody prints.

**The residual is always reported.** A decomposition that does not sum to the
actual change, silently, is worse than no decomposition.

### 5. Attacking our own output — because recall is the wrong question

The method document *argued* that Safe Harbor removes 18 identifiers and still
cannot guarantee anonymity. `run_attack.py` **measures** it.

Recall answers *"did we remove what we meant to remove"*. A linkage attack
answers the question that matters: **is what remains still identifying?**

**k-anonymity as the attacker learns more:**

| quasi-identifiers | min k | unique | k<5 |
|---|---|---|---|
| state | 949 | 0.0% | 0.0% |
| state + sex + age band | 1 | 0.0% | 0.6% |
| ZIP3 + sex + age band | 1 | 0.0% | 0.9% |
| **ZIP3 + sex + age band + full condition profile** | **1** | **11.6%** | **29.9%** |

**11.6% of records are unique** on attributes Safe Harbor explicitly permits.
Nothing was done wrong — 3-digit ZIP is allowed, age bands are allowed, and the
diagnoses are the entire reason the data exists. The *combination* is
identifying though no element of it is. That is identifier #18, measured.

**The linkage attack, and its honest result.** An attacker with a purchasable
voter-file-style roll (name, ZIP5, age, sex — 6,803 records, no clinical data)
re-identifies only **3 records**, verified 3/3 correct. Demographics alone leave
almost everyone in a crowd.

**The attack that actually works** is the one the threat model should have
started with — an adversary who knows the target and therefore knows *one*
clinical fact:

| attacker also knows… | prevalence | carriers uniquely identified |
|---|---|---|
| nothing clinical | — | **0.0%** |
| …they have cancer | 6.0% | **21.2%** |
| …they have CHF | 5.7% | **21.0%** |
| …they have CKD stage 4 | 5.5% | **20.5%** |

A neighbour, employer, relative or journalist who knows one diagnosis
re-identifies roughly **a fifth** of that condition's carriers. The script is
explicit that these three prevalences are too close together to demonstrate a
*rarity gradient* — the mechanism is stated as a prediction, not claimed as a
finding this data supports.

**And what the fix costs:**

| generalisation | kept | suppressed | min k |
|---|---|---|---|
| as released | 5,609 | **29.9%** | 5 |
| age bands widened to 10 years | 6,252 | 21.9% | 5 |
| age widened AND ZIP suppressed | 7,629 | 4.6% | 5 |

Read the suppression column, not the k column. The rows k-anonymity throws away
are exactly the unusual ones — rare conditions, small geographies, extreme ages
— so a k-anonymised release is **systematically missing its outliers**, and any
analysis of rare disease on it is biased in a direction nobody downstream can
see.

**This is the concrete argument for Expert Determination.** Safe Harbor applies
the same rule to a locked-down research partner and a public download. A
statistician assessing a *specific* release to a *specific* recipient with
*specific* controls can permit richer data to a trusted recipient under a DUA,
or demand more suppression for an open one. A dataset that is 11.6% unique
behind a data-use agreement and an audit trail is in a very different position
from the same dataset on a public URL.

---

## Bugs this harness caught

- **Address recall was 32.6%.** The detector had no city gazetteer, and *city*
  is a geographic identifier under Safe Harbor #2 — so every city mention in
  free text survived de-identification. Only the measurement found it; the
  pipeline "worked" and the output looked clean. Now 83.9%, with the residual
  being two cities deliberately left out of the gazetteer.
- **A privacy measurement that measured nothing.** The targeted-attack table
  originally included a condition that did not exist in the dataset, so the
  column was all-False, contributed nothing to the quasi-identifier, and
  reported a reassuring **0.0%** for a field that was simply absent. There is
  now an assertion that every condition named in that table is really present.
- **Over-redaction was 0, which was meaningless.** The note templates contained
  almost no capitalised non-PHI tokens, so a name detector had nothing to
  wrongly fire on and precision came back at a trivial 100%. Real notes are
  dense with capitalised non-PHI — drug brands, departments, months, payers,
  facility names. Adding them made precision a measurement.

## The dashboard, the dictionary, and the suppression that had to be attacked

The gap list asked for three things: a dashboard, a metric dictionary as a real
artefact, and small-cell suppression on the analytics output. All three are
here, and the third one is the interesting one.

### Suppression is enforced at the render boundary

Every cell reaches `out/dashboard.html` through `suppression.suppress_table`,
called by the renderer. Not by convention, not by a review checklist — by the
only code path a number has to the page. Suppression applied in the analytics
layer protects whatever the analytics layer happened to compute; a drill-down
added later gets an unprotected one.

### Suppressing the small cells is the easy half, and is not sufficient

```
Q3 inpatient spend      total $482,000
  cardiology            $310,000
  oncology              $164,000
  transplant            SUPPRESSED (n=2)
```

The transplant figure is $8,000 and anyone can subtract. The suppression is
decorative — the number is still published, in subtraction form. Preventing
that needs **complementary suppression**: a second cell must go, or the total
must be withheld. On the real drill-down:

| | count |
|---|---|
| cells rendered | 440 |
| primary suppressions (n<11) | 45 |
| **complementary suppressions** | **15** |
| cells recoverable by the subtraction attack | **0** |

`suppression.audit` re-runs that attack against what was actually rendered, and
a test demonstrates it *succeeding* against a naive implementation before
showing the complementary pass defeating it. A suppression rule that is never
attacked is a comment.

### The grain was chosen by measurement, because the rule never fired

The first version suppressed nothing — category × quarter cells in this
population hold 137 to 6,311 members. **A disclosure control that has never
fired is not evidence that it works.** So the grains were measured:

```
zip3 x category                        55 cells,    0 with n<11
zip3 x category x quarter             440 cells,   45 with n<11
zip3 x sex x age-decade x category   1052 cells,  433 with n<11
```

The dashboard renders both the coarse grain (where the rule correctly leaves
safe cells alone — a rule that fires on everything is as useless as one that
fires on nothing) and ZIP3 × quarter × category, where it bites. ZIP3 is not an
arbitrary choice: it is one of the 18 Safe Harbor identifiers and the same
quasi-identifier `reidentify.py` uses to attack the member extract. **The
dashboard and the attack are looking at the same column from opposite sides.**

### Two rules, not one

Threshold (n < 11, the HHS public-use convention — a convention, not a theorem)
**and** dominance: one contributor holding ≥85% of a cell discloses their value
to anyone who knows they are in it, *however large n is*. A 500-member cell
that is 95% one member is a disclosure. Threshold-only suppression misses that
case completely, which is why the (n,k) rule exists.

### The metric dictionary is executable

`src/metrics.py` holds the definitions as data; `docs/METRIC_DICTIONARY.md` is
**generated** from them; the pipeline computes from the same entries. A metric
dictionary that *can* disagree with the code is one that **will**, within about
two sprints, and nobody notices because the analyst reads the document and the
pipeline runs the code.

Each definition carries the fields that actually get argued about — grain,
denominator, claim basis (incurred vs paid), runout maturity, owner, and known
caveats — including that the PMPM decomposition is **order-dependent**
(Laspeyres with period-0 weights; period-1 weights give different attributions
from the same data), which is why the residual is published.

## Cross-table linkage — the gap this README called its largest

`src/linkage.py` + `run_linkage.py`. The previous gap list said it plainly:
*"the largest gap — **cross-table linkage analysis**, where two separately-safe
tables intersect to reveal a cell neither exposes on its own. That is where real
statistical agencies spend most of their effort."*

Every published cell is a linear equation over the underlying counts. Publish
enough tables and the equations become solvable, and **nothing about any single
table looks wrong while it happens**.

```
Table A   spend by (ZIP3, quarter) x category, all members       -> published
Table B   the same breakdown, diabetics only                     -> REFUSED
          would expose 19 cell(s) in combination with table A
```

Subtract B from A and you have a table over **non-diabetics** — a population
nobody chose to publish, nobody suppressed, and nobody checked. **The exposed
cell appears in neither table.** Checking each table in isolation cannot find
this, which is exactly why agencies keep a *release register* rather than a
per-table gate: a table's safety is a property of the set it joins.

The refusal is **fatal, not a warning**. A warning on a publication path is a
warning that gets clicked through, and a table cannot be unpublished.

The grain was chosen by measurement, the same discipline the suppression work
uses — at ZIP3 x category the smallest residual is 29 members and no attack
succeeds, so publishing there would show the register accepting everything and
prove nothing:

```
zip3 x category                       55 cells,   0 residuals < 11
zip3 x quarter x category            440 cells,  51 residuals < 11
zip3 x sex x age-decade x category  1052 cells, 456 residuals < 11
```

Two attack families are implemented — nested-population and shared-margin
differencing — and `check()` says what a clean result does **not** prove: the
general problem is integer programming over the whole release history.

## l-diversity, and an honest negative

`k`-anonymity says nothing about *attribute* disclosure. A class of 20 members
sharing age, sex and ZIP3 is **20-anonymous** and discloses their diagnosis
completely if all 20 share one — the attacker never has to work out which record
is the target.

On this data the check finds **18 violating classes and none disclosing a real
diagnosis**; all 18 disclose "no recorded condition". That is a clean negative
and it is a property of the **generator**, not of the de-identification:
`src/synth.py` draws each condition flag independently at 5-6% prevalence, so
classes come out diverse. Real populations cluster — by geography, by age, by
referral pattern — and that clustering is what produces homogeneous classes.

Since this data cannot produce the failure, **the detector is demonstrated
firing on a constructed class in `tests/test_linkage.py`** rather than reported
as a pass. It is distinct l-diversity, the weakest form: 19 of 20 sharing a
value clears `l=2` and discloses almost as much, which entropy l-diversity and
t-closeness address and this does not.

## There is a dbt project, and the PHI boundary is a build failure

`dbt/` is a graph — 3 staging views, 2 intermediate tables, 4 marts, **31 dbt
tests** — built with `dbt-duckdb`.

```bash
python run_dbt.py             # export the de-identified extract, then dbt build
python run_dbt.py --no-export # reuse the existing extract
```

### The architecture is the control

`src/export.py` writes a de-identified extract; **dbt reads that and nothing
else.** There is no connection configured to anything holding PHI, so *"the
warehouse never sees an identifier"* is a property of the wiring rather than a
rule somebody has to remember.

And it is enforced. `no_phi_column_reaches_the_warehouse.sql` walks the
information schema of every model dbt built and **fails the build** if a column
named like a direct identifier appears anywhere. It is a name check, not a
content check — it cannot catch PHI smuggled into a column called `notes` — but
it catches the realistic failure, which is somebody joining the raw member table
back in "just for debugging".

`suppression_actually_fires.sql` applies the other recurring lesson: a
suppression rule that never fires is not protecting anything, so the build
fails if no cell is ever suppressed.

### It is a second implementation, and they agree to the cent

| | |
|---|---|
| months compared | 24 |
| worst `member_months` difference | **0.000000000** |
| worst `paid` difference | **0.00** |

Parity is measured over the **same input**: the Python side runs on
`export.shifted_view`, which applies the identical date shift in memory.
Comparing dbt-on-shifted against Python-on-raw would report a difference that
is the shift, not a disagreement.

### Building it found a real bug in the generator

A dbt `unique` test failed on the first build: **member id `P541509986` was
emitted twice.** `phi.make_person` draws a 9-digit id at random, and over 8,000
members the birthday bound gives roughly a 3% chance of a collision — which duly
happened on the default seed.

That is not cosmetic. A duplicate member lands in the dimension twice, their
eligibility is counted twice, and **every PMPM denominator that joins through
them is inflated.** Seventy-two passing tests never saw it, because a primary
key is the kind of thing one declares in a schema and never writes a unit test
for.

## De-identification costs half the reporting period, and here is the number

Building the extract forced a question the in-memory pipeline never had to
answer: what happens to a **time series** when every member's dates are shifted
by a different amount?

`deid.patient_offset` returns a **negative** offset, between −1 and −364 days.
So every member's history walks earlier, and claims near the start of the
extract walk off the front of the window entirely:

| | |
|---|---|
| claims in window, raw | 199,787 |
| claims in window, shifted | 149,226 |
| **lost** | **50,561 (25.3%)** |
| requested window | 731 days |
| **fully-covered window** | **368 days** |

The usable window is the intersection of every member's shifted span, which is
one shift-width narrower at each edge. With offsets spanning a year, **a
two-year extract yields one usable year.**

This does not fail loudly. It silently depresses volumes at both edges, which
reads as a real trend — a declining-utilisation story that is entirely an
artefact of de-identification.

**It is not a bug in the shift; it is the price of the shift.** The fix is to
extract a wider raw range than you intend to report on, not to quietly report
over a window the data no longer covers. `export.shift_cost()` computes it, and
four tests pin it so it cannot grow unnoticed.

## What is still missing, and why it cannot be closed here

- **The dbt project has no incremental materialisation, no snapshots, and no
  model contracts.** The graph, the tests and the docs site are there (see
  above); every model is a full rebuild, which is fine at 20k claims and is not
  how a real claims warehouse runs.
- **Presidio is not used.** Deliberately: installing it would **downgrade
  numpy 2.5.2 to 2.4.6** on this machine, and it targets free-text PHI while
  this project is structured claims — a real cost for a poor fit. Not a
  blocker, a decision. De-identification is hand-rolled
  regex + gazetteer + context rules, and a real deployment uses a trained NER
  model that gets the names a gazetteer misses. The measured 93.9% name recall
  is reported as the cost.
- **No Synthea.** `src/synth.py` writes claims-shaped data directly, so the
  clinical trajectories are rate parameters rather than disease modules.
- **Risk adjustment is HCC-*like*, not CMS-HCC.** No ICD-10 to
  condition-category mapping, no hierarchies suppressing lesser categories, no
  payment normalisation. Closing it properly needs the published CMS model
  files.
- **No differential privacy.** k-anonymity and l-diversity are weak guarantees
  and are reported as such — neither gives a formal bound, and both are
  defeated by an attacker with information outside the quasi-identifier set. DP
  would change the shape of every number on the dashboard and is a different
  project.
- **The linkage check covers two attack families, not the general problem.**
  "Can any linear combination of published cells resolve a suppressed one" is
  integer programming over the whole release history, and agencies use dedicated
  solvers. A clean result here means the implemented attacks failed.
- **Suppression defends rows, not columns.** With both margins published a cell
  suppressed in its row is recoverable down its column, and defending both at
  once is an LP rather than a greedy pass. Controlled rounding and cell
  perturbation are also absent.
- **The complementary-cell choice is greedy, not optimal.**
- **The dashboard is one static HTML file** — no server, no auth, no row-level
  security, and no access logging, which for a page with member-level drill-down
  is itself auditable information.
- **The metric dictionary is not a semantic layer** — no materialisation, no
  access control, and no approval workflow for a definition change, which is
  the control that actually matters since the risk is someone editing PMPM's
  denominator.

## Files

| path | what |
|---|---|
| `src/phi.py` | 18 Safe Harbor identifiers, split name pools, PHI planting with offsets |
| `src/deid.py` | hybrid detection, transformations, the scorer |
| `src/synth.py` | claims, eligibility, and the planted Q3 shock |
| `src/analytics.py` | member-month spine, PMPM, price/util/mix, concentration |
| `run_pipeline.py` | the whole two-stage pipeline |
| `docs/DEID_METHOD.md` | generated: the identifier table + measured performance |
| `src/reidentify.py` | k-anonymity, linkage attack, generalisation/suppression |
| `run_attack.py` | the attack on our own output, and the cost of the fix |
| `src/suppression.py` | small-cell + dominance rules, complementary pass, self-audit |
| `src/metrics.py` | the metric dictionary as data; generates the doc |
| `dashboard.py` | payer-executive view; suppression enforced at render time |
| `src/linkage.py` | release register, differencing attacks, l-diversity |
| `run_linkage.py` | two safe tables refused together; the honest l-diversity negative |
| `dbt/` | 3 staging, 2 intermediate, 4 marts, 31 dbt tests |
| `src/export.py` | the de-identified extract, and the boundary it creates |
| `run_dbt.py` | export then dbt build |
| `tests/test_dbt_parity.py` | 7 tests: parity, and the measured cost of shifting |
| `tests/test_linkage.py` | 16 tests: both attacks, and l-diversity on a constructed class |
| `tests/test_suppression.py` | 20 tests: the attack, then the defence |
| `tests/test_pipeline.py` | 36 tests |
