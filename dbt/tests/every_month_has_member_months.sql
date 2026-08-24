-- A month in the reporting period with no member-months means the spine has a
-- hole, and every rate for that month divides by zero or vanishes.

{% set p_start = "date '" ~ var('period_start') ~ "'" %}
{% set p_end   = "date '" ~ var('period_end')   ~ "'" %}

with months as (
    select range::date as month_start
    from range({{ p_start }}, {{ p_end }} + interval 1 day, interval 1 month)
),
have as (
    select distinct month_start from {{ ref('int_member_month') }}
)
select m.month_start
from months m
left join have h using (month_start)
where h.month_start is null
