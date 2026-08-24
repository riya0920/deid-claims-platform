-- THE GOVERNANCE BOUNDARY, AS A BUILD FAILURE.
--
-- The whole architecture of this project is that the warehouse reads a
-- de-identified extract and therefore CANNOT hold an identifier. That is only
-- true while nobody adds a column, and "nobody will add a column" is not a
-- control.
--
-- This walks the information schema of every model dbt built and fails if any
-- of them carries a column whose name matches a direct identifier. It is a
-- name check, not a content check -- it cannot catch PHI smuggled into a column
-- called `notes` -- but it catches the realistic failure, which is somebody
-- joining the raw member table back in "just for debugging".

select
    table_name,
    column_name
from information_schema.columns
where lower(column_name) in (
        'first_name', 'last_name', 'name', 'ssn', 'mrn', 'email', 'phone',
        'street', 'address', 'account_number', 'member_id', 'patient_id',
        'birth_date', 'dob', 'zip5', 'zip', 'provider_npi', 'npi'
      )
  and table_schema = 'main'
