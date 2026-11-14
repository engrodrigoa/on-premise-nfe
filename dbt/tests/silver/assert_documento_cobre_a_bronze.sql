-- Fechamento (D020) dentro do dbt: toda linha da bronze tem um, e só um, destino na silver.
select b.bronze_id
from {{ source('bronze', 'documento_xml') }} as b
full join {{ ref('documento') }} as d using (bronze_id)
where b.bronze_id is null or d.bronze_id is null
