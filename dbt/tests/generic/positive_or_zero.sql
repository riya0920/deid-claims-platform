{% test positive_or_zero(model, column_name) %}
-- A negative PMPM means paid or member-months went negative, which can only
-- happen through a sign error or a bad join.
select *
from {{ model }}
where {{ column_name }} is not null and {{ column_name }} < 0
{% endtest %}
