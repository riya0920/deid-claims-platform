# The analytics on Synthea, and what it says about our own data

`src/synth.py` writes claims-shaped data directly. That makes the risk equation
recoverable and the analytics checkable -- and it is a **closed loop**: the
generator emits exactly the five categories the analytics expect, at exactly
the grain they assume.

[Synthea](https://github.com/synthetichealth/synthea) is free, is not written
by me, and drives utilisation from clinical modules rather than a rate table.

| | Synthea | own generator |
|---|---|---|
| members with spend | 591 | 8000 |
| member months | 14206 | 169877 |
| PMPM | $836.57 | $262.15 |
| **top 1% share of spend** | **13.3%** | **4.8%** |
| **top 5% share of spend** | **43.0%** | **18.4%** |
| top 10% share of spend | 64.2% | 31.6% |
| p99 / mean member spend | **10.6x** | **4.1x** |
| max / mean member spend | 15.8x | 8.3x |

## 1. The category taxonomy has holes

Synthea emits ten encounter classes; this project has five categories. Skilled
nursing, hospice and home health have **no bucket at all** -- 3.94% of spend.

Folding them into `inpatient` is the tempting fix and it is wrong in a
measurable way:

| category | $ per claim | vs inpatient |
|---|---|---|
| `snf` | $13,342 | 1.20x |
| `hospice` | $10,624 | 0.95x |
| `inpatient` | $11,162 | — |
| `home` | $473 | 0.04x |

A merged average describes none of them -- the spread here is more than
twentyfold from home health to skilled nursing. So `CATEGORY_MAP` is explicit and
**deliberately incomplete** -- unmapped classes keep their own names and are
reported separately, which puts the hole in the output instead of hiding it in
an average.

## 2. Our own generator has a light tail, and that is the bigger finding

Cost concentration is the number a care-management programme is sized on, and
this project's README argues for reporting it *precisely because the
distribution is skewed*. Measured against Synthea, our own distribution is
**much less skewed** -- the top 5% of members carry 18.4% of spend here
against 43.0% in Synthea.

**This is structural, not bad luck.** `synth.generate` draws each category as
`Poisson(rate x risk_score x covered_days)` and prices it with a bounded
uniform `U(0.55, 1.6)`. The only between-member driver is `risk_score`, which
spans **0.60 to 2.08 -- a range of 3.5x**.

No member *can* be fifty times the mean, because nothing in the model lets them
be. Real books have one transplant, one long NICU stay, one haemophilia
patient, and those single members move a PMPM.

### What that means for the analytics built on it

- `cost_concentration` is **correct code measured on an unrepresentative
  population**. The function is fine; the number it produces here is not a
  guide to what it would produce on a real book.
- Anything sized off the top-N% -- outreach capacity, stop-loss attachment,
  high-cost-claimant review -- would be sized wrong.
- The generator remains the right tool for what it was built for: planting a
  rate and recovering it. It is the wrong tool for anything that depends on the
  shape of the tail, and that limitation was not previously written down.

## What this does not show

Synthea is still synthetic. Its module-driven utilisation produces a heavier
tail than a rate table does, which is closer to a real book, but "closer" is
not "correct" and nothing here validates either against actual claims.
