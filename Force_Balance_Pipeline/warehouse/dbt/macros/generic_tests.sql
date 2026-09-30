{#
  Two small dependency-free generic tests (no dbt_utils package): expression_is_true, for a column-level boolean
  check against an arbitrary SQL expression (e.g. ingest_lag_seconds >= var('lag_tolerance_seconds')), and
  column_is_null, for a warn-severity "this should usually be empty" check (_rescued_data).
#}
{% test expression_is_true(model, column_name, expression) %}
select *
from {{ model }}
where not ({{ column_name }} {{ expression }})
{% endtest %}

{% test column_is_null(model, column_name) %}
select *
from {{ model }}
where {{ column_name }} is not null
{% endtest %}
