-- THE MEMBER-MONTH SPINE. Everything per-member-per-month divides by this.
--
-- PRORATED by enrolled days over days-in-month, which is a CONVENTION and not
-- the only one. The alternative -- count a whole month if enrolled on the 15th
-- -- gives a different denominator and therefore a different PMPM, and neither
-- is wrong. What is wrong is not saying which one you used, because the two
-- are within a few percent of each other and nobody notices the swap.
--
-- This mirrors analytics.member_month_spine(prorate=True). The two are
-- independent implementations and tests/test_dbt_parity.py requires them to
-- agree to the cent.

{% set p_start = "date '" ~ var('period_start') ~ "'" %}
{% set p_end   = "date '" ~ var('period_end')   ~ "'" %}

with months as (
    select
        range::date as month_start,
        (range + interval 1 month - interval 1 day)::date as month_end
    from range({{ p_start }},
               {{ p_end }} + interval 1 day,
               interval 1 month)
),

overlap as (
    select
        e.member_key,
        m.month_start,
        extract(year  from m.month_start) as year,
        extract(month from m.month_start) as month,
        date_diff('day', m.month_start, m.month_end) + 1 as days_in_month,
        greatest(e.span_start, m.month_start, {{ p_start }}) as lo,
        least(e.span_end, m.month_end, {{ p_end }})          as hi
    from {{ ref('stg_eligibility') }} e
    join months m
      on e.span_start <= m.month_end
     and e.span_end   >= m.month_start
)

select
    member_key,
    year,
    month,
    month_start,
    -- a member with two spans inside one month contributes both fractions, so
    -- this sums rather than taking a max
    sum((date_diff('day', lo, hi) + 1)::double / days_in_month) as member_months
from overlap
where hi >= lo
group by 1, 2, 3, 4
