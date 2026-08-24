select
    claim_key,
    member_key,
    cast(service_date as date)  as service_date,
    service_category,
    cast(paid_amount as double) as paid_amount,
    cast(units as integer)      as units,
    provider_key
from read_csv_auto('{{ env_var("EXTRACT_DIR") }}/fct_claim.csv', header = true)
