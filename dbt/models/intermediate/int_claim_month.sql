-- Claims aggregated to (year, month, category). Kept separate from the spine
-- so that a month with claims but no enrolled members is VISIBLE as a join
-- failure downstream rather than silently dropped.

{% set p_start = "date '" ~ var('period_start') ~ "'" %}
{% set p_end   = "date '" ~ var('period_end')   ~ "'" %}

select
    extract(year  from service_date) as year,
    extract(month from service_date) as month,
    service_category,
    sum(paid_amount) as paid,
    sum(units)       as services,
    count(*)         as claim_count,
    count(distinct member_key) as members_with_a_claim
from {{ ref('stg_claim') }}
where service_date between {{ p_start }} and {{ p_end }}
group by 1, 2, 3
