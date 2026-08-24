-- Staging is rename-and-cast only.
select
    member_key,
    age_band,
    sex,
    state,
    zip3
from read_csv_auto('{{ env_var("EXTRACT_DIR") }}/dim_member.csv', header = true)
