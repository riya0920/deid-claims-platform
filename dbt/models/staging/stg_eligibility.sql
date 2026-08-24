select
    member_key,
    cast(span_start as date) as span_start,
    cast(span_end   as date) as span_end
from read_csv_auto('{{ env_var("EXTRACT_DIR") }}/fct_eligibility.csv',
                   header = true)
