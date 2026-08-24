-- A SUPPRESSION RULE THAT NEVER FIRES IS NOT PROTECTING ANYTHING.
--
-- If every cell is above the threshold, the rule is untested and would pass
-- just as happily if it were deleted. The same lesson the HEDIS project
-- learned about its one-gap rule: a control has to be seen firing before it is
-- evidence of anything.

select 'suppression never fired: the rule is unexercised' as failure
from (select count(*) as n from {{ ref('mart_published_cells') }}
      where suppressed)
where n = 0
