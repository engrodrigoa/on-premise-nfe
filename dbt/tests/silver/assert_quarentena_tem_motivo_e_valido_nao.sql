-- quarentena sempre com motivo; válido e reenvio nunca
select bronze_id, destino, motivo from {{ ref('documento') }}
where (destino = 'quarentena') <> (motivo is not null)
