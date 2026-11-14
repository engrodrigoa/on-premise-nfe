-- nfe_evento: 1 linha por (chave, tp_evento, n_seq_evento): reenvio e conteúdo divergente não duplicam evento
select chave, tp_evento, n_seq_evento, count(*) from {{ ref('nfe_evento') }} group by 1, 2, 3 having count(*) > 1
