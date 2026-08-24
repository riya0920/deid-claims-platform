-- Suppression must actually suppress. A published cell with fewer than the
-- policy minimum is a disclosure, and it is the exact failure that makes a
-- de-identification pipeline worthless while looking like it worked.

select *
from {{ ref('mart_published_cells') }}
where n_members_published is not null
  and n_members_published < {{ var('min_cell_size') }}
