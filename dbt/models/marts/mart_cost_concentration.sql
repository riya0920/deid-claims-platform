-- What share of spend the top 1% / 5% of members account for.
--
-- Reported because the mean is a poor summary of a distribution this skewed: a
-- PMPM built from a population where 5% of members drive half the spend is not
-- describing a typical member at all, and a care-management programme sized off
-- the mean will be sized wrong.

with by_member as (
    select member_key, sum(paid_amount) as paid
    from {{ ref('stg_claim') }}
    group by 1
),

ranked as (
    select
        member_key,
        paid,
        row_number() over (order by paid desc) as rn,
        count(*)  over ()                      as n_members,
        sum(paid) over ()                      as total_paid
    from by_member
)

select
    pct.label,
    pct.cutoff,
    count(*)                                    as n_members_in_band,
    sum(paid)                                   as paid_in_band,
    max(total_paid)                             as total_paid,
    sum(paid) / max(total_paid)                 as share_of_spend
from ranked
join (values ('top 1%', 0.01), ('top 5%', 0.05), ('top 10%', 0.10))
        as pct(label, cutoff)
  on ranked.rn <= greatest(1, floor(ranked.n_members * pct.cutoff))
group by 1, 2
