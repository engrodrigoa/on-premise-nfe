-- nfe_item: 1 linha por (chave, n_item)
select chave, n_item, count(*) from {{ ref('nfe_item') }} group by 1, 2 having count(*) > 1
