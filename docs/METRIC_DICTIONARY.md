# Metric dictionary (v1.2.0)

**Generated from `src/metrics.py`. Do not edit by hand — edit the definitions and re-run `python write_method.py`.**

A metric dictionary that can disagree with the code is a metric dictionary that will. These definitions are the ones the pipeline reads.

## `cost_concentration` — Cost concentration (top-N share)

Share of total spend held by the top N% of members.

```
sum(paid for top N% of members by paid) / sum(paid)
```

| field | value |
|---|---|
| grain | per period |
| denominator | total paid |
| claim basis | incurred (service date) |
| owner | payer analytics |

**Data maturity.** Immature data understates concentration: the largest claims are facility claims and those arrive last.

**Known caveats.**

- Read this BEFORE reading a PMPM movement in a population under ~20,000 members. One transplant moves the whole number.

## `member_months` — Member-months

Sum of enrolled fractions of each month across all members in the population.

```
sum over members, months of (enrolled_days / days_in_month)
```

| field | value |
|---|---|
| grain | one value per period |
| denominator | n/a -- this IS the denominator |
| claim basis | n/a (eligibility, not claims) |
| owner | payer analytics |

**Data maturity.** Eligibility is subject to retroactive term and retroactive add. A month's member-months can change AFTER it closes, which moves a PMPM that nobody recalculated.

**Known caveats.**

- A member with a coverage gap contributes to both spans and must not be double-counted across them.

## `pmpm` — PMPM (per member per month)

Total allowed spend divided by member-months over the same period.

```
sum(paid) / sum(member_months)
```

| field | value |
|---|---|
| grain | one value per period, per population |
| denominator | MEMBER-MONTHS, not members. Prorated for partial months of enrolment: a member enrolled 12 days of a 30-day month contributes 0.4 member-months, not 1.0 and not 0. Rounding partial months UP is the most common error and inflates the denominator, which understates PMPM. |
| claim basis | incurred (service date) |
| owner | payer analytics |

**Data maturity.** Not stable until roughly 90 days of runout. Facility claims here have a ~41-day median receipt lag and a long tail; a month reported at 30 days of runout is missing spend, not showing a trend improvement.

**Known caveats.**

- Not risk-adjusted. Comparing PMPM across populations without risk context is the most common way payer analytics misleads.
- Sensitive to a single catastrophic claimant in small populations -- see cost_concentration before reading a PMPM movement.

Decomposes into: `utilisation_per_member_month`, `price_per_service`

## `pmpm_decomposition` — PMPM change decomposition (price / utilisation / mix)

Attribution of a PMPM change between two periods to utilisation, unit price, and category mix.

```
util = (U1-U0)*Pbar0 ; price = U0*sum(share0*(P1-P0)) ; mix = U0*sum((share1-share0)*P0)
```

| field | value |
|---|---|
| grain | one attribution per period pair |
| denominator | member-months, as for PMPM |
| claim basis | incurred (service date) |
| owner | payer analytics |

**Data maturity.** Both periods must be equally mature. Comparing a closed period against an open one attributes the missing runout to a utilisation decrease.

**Known caveats.**

- ORDER-DEPENDENT. This is a Laspeyres-style decomposition using period-0 weights; using period-1 weights gives different attributions from the same data. The residual is reported so the size of that arbitrariness is visible rather than hidden.
- Attribution is not causation. A price effect says the average paid per service rose, not that a contract was renegotiated.

## `price_per_service` — Price (P)

Allowed amount per service.

```
sum(paid) / sum(service_count)
```

| field | value |
|---|---|
| grain | per category, per period |
| denominator | services, NOT member-months |
| claim basis | incurred (service date) |
| owner | payer analytics |

**Data maturity.** Same 90-day maturity as PMPM.

**Known caveats.**

- Blends unit price with intensity. A shift to sicker patients inside a category raises P with no contract change.

## `utilisation_per_member_month` — Utilisation (U)

Services per member-month.

```
sum(service_count) / sum(member_months)
```

| field | value |
|---|---|
| grain | per category, per period |
| denominator | member-months, as for PMPM |
| claim basis | incurred (service date) |
| owner | payer analytics |

**Data maturity.** Same 90-day maturity as PMPM.

**Known caveats.**

- A 'service' is a claim line here, so a change in billing granularity moves U without any change in care delivered.
