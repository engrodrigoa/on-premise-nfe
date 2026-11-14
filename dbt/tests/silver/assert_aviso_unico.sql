-- nfe_aviso: 1 linha por (chave, regra)
select chave, regra, count(*) from {{ ref('nfe_aviso') }} group by 1, 2 having count(*) > 1
