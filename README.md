# DATA-1 — De-identification pipeline + claims analytics (~80% build)

**Govern, then analyse.** The privacy layer is built as engineering with a
measured recall number, not as a disclaimer, and the payer analytics run on its
output.

```bash
python run_pipeline.py     # generate -> plant PHI -> de-identify -> MEASURE -> analyse
python run_attack.py       # re-identification attack on our own output
python write_method.py     # -> docs/DEID_METHOD.md
python dashboard.py           # -> out/dashboard.html + docs/METRIC_DICTIONARY.md
python -m pytest tests -q     # 56 tests
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

## What is still missing

- **No dbt.** Not installed. The analytics are Python functions over
  dictionaries, not models in a warehouse — no `ref()` graph, no incremental
  materialisation, no dbt tests, no docs site, no lineage.
- **The dashboard is one static HTML file.** No server, no auth, no row-level
  security, no export controls, and no access logging — and for a page with a
  member-level drill-down, who viewed what is itself auditable information.
- **The metric dictionary is not a semantic layer.** No `ref()` graph, no
  materialisation, no tests attached to definitions, no access control, and no
  approval workflow for a definition change — which is the control that
  actually matters, since the risk is someone editing PMPM's denominator.
- **Presidio is not used.** Hand-rolled regex + gazetteer + context rules.
  Fine for demonstrating the architecture; a real deployment uses a trained NER
  model and gets the names the gazetteer misses.
- **No Synthea.** `src/synth.py` writes claims-shaped data directly, so the
  clinical trajectories are unearned — rate parameters, not disease modules.
- **Risk adjustment is HCC-*like*, not CMS-HCC.** No ICD-10 → condition-category
  mapping, no hierarchies suppressing lesser categories, no payment
  normalisation. It exists because comparing PMPM across populations without
  risk context is the most common way payer analytics misleads.
- **No differential privacy.** k-anonymity is a weak guarantee and is reported
  as one: it does not defend against an attacker who knows something outside the
  quasi-identifier set, says nothing about attribute disclosure when a whole
  equivalence class shares a diagnosis (l-diversity), and gives no formal bound.
- **Suppression defends rows, not columns.** If a table publishes column
  totals as well, a cell suppressed in its row is recoverable down its column,
  and defending both at once is a linear-programming problem rather than a
  greedy pass. Also absent: controlled rounding, cell perturbation, and — the
  largest gap — **cross-table linkage analysis**, where two separately-safe
  tables intersect to reveal a cell neither exposes on its own. That is where
  real statistical agencies spend most of their effort.
- **The complementary-cell choice is greedy, not optimal.** Suppress the next
  smallest; a real system solves a minimisation.
- **The generator has no realistic long tail of prevalences**, so the
  rarity-versus-exposure relationship can be predicted but not demonstrated.

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
| `tests/test_suppression.py` | 20 tests: the attack, then the defence |
| `tests/test_pipeline.py` | 36 tests |
