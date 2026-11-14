-- Arquivos barrados, com o motivo (D007): o que não é um documento íntegro do grupo.
-- Recalculada a cada carga: um evento sem nota deixa a quarentena quando a nota chega.
select
    d.bronze_id,
    d.lote_id,
    l.nome_lote,
    d.arquivo,
    d.tipo_documento,
    d.chave,
    d.motivo,
    r.descricao       as descricao_motivo,
    r.quem_age,
    d.sha256,
    d.erro_leitura,
    d.carregado_em
from {{ ref('documento') }} as d
join {{ source('ctrl', 'lote') }} as l using (lote_id)
left join {{ ref('cat_regra_qualidade') }} as r on r.regra = d.motivo
where d.destino = 'quarentena'
