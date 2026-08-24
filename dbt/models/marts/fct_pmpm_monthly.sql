-- PMPM by month and category.
--
-- A LEFT JOIN FROM THE SPINE, not an inner join from the claims. A month in
-- which a category generated no spend still has member-months and still has a
-- PMPM -- of zero. Dropping the row instead makes a category look like it
-- disappeared when in fact it cost nothing, and a trend line drawn through the
-- surviving points slopes the wrong way.

with spine as (
    select year, month, sum(member_months) as member_months
    from {{ ref('int_member_month') }}
    group by 1, 2
),

categories as (
    select distinct service_category from {{ ref('int_claim_month') }}
),

grid as (
    select s.year, s.month, s.member_months, c.service_category
    from spine s
    cross join categories c
)

select
    g.year,
    g.month,
    g.service_category,
    g.member_months,
    coalesce(c.paid, 0)      as paid,
    coalesce(c.services, 0)  as services,
    case when g.member_months > 0
         then coalesce(c.paid, 0) / g.member_months end as pmpm,
    case when g.member_months > 0
         then coalesce(c.services, 0) / g.member_months * 1000 end
         as util_per_1000,
    case when coalesce(c.services, 0) > 0
         then coalesce(c.paid, 0) / c.services end as price_per_service
from grid g
left join {{ ref('int_claim_month') }} c
       on c.year = g.year
      and c.month = g.month
      and c.service_category = g.service_category
