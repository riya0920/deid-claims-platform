-- Quarterly rollup.
--
-- MEMBER-MONTHS SUM ACROSS THE QUARTER; PMPM DOES NOT. Averaging three monthly
-- PMPMs weights a month with 900 member-months the same as one with 1,100,
-- which is wrong whenever enrolment moves -- and enrolment always moves. The
-- quarterly rate is total paid over total member-months.

select
    year,
    (month - 1) / 3 + 1 as quarter,
    service_category,
    sum(member_months) as member_months,
    sum(paid)          as paid,
    sum(services)      as services,
    case when sum(member_months) > 0
         then sum(paid) / sum(member_months) end as pmpm,
    case when sum(services) > 0
         then sum(paid) / sum(services) end as price_per_service
from {{ ref('fct_pmpm_monthly') }}
group by 1, 2, 3
