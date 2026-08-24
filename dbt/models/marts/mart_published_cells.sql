-- CELLS AS THEY WOULD BE PUBLISHED, with small-cell suppression applied HERE
-- rather than in the renderer.
--
-- Suppressing at the presentation layer is the mistake this project exists to
-- point at: every consumer of the unsuppressed model -- an export, a
-- notebook, an API -- then has to remember to do it again, and one of them
-- will not. Applying it in the model means the unsafe number is not in the
-- table anybody queries.
--
-- `suppressed` is kept as a FLAG with the count nulled, not as a deleted row.
-- A missing row is indistinguishable from a cell with no members, and that
-- ambiguity is itself a disclosure risk: an analyst who knows the row should
-- exist learns something from its absence.

{% set k = var('min_cell_size') %}

with cells as (
    select
        m.state,
        m.zip3,
        m.age_band,
        m.sex,
        c.service_category,
        count(distinct c.member_key) as n_members,
        sum(c.paid_amount)           as paid
    from {{ ref('stg_claim') }} c
    join {{ ref('stg_member') }} m using (member_key)
    group by 1, 2, 3, 4, 5
)

select
    state,
    zip3,
    age_band,
    sex,
    service_category,
    n_members < {{ k }} as suppressed,
    case when n_members >= {{ k }} then n_members end as n_members_published,
    case when n_members >= {{ k }} then paid end      as paid_published,
    n_members                                          as n_members_raw
from cells
