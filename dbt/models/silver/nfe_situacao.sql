-- Situação de cada nota válida, derivada dos eventos (D006). A nota nunca é alterada: ela muda de situação.
-- A ordem de chegada não importa: cancelamento no mesmo lote, num lote posterior ou antes da nota dá o mesmo
-- resultado, porque a tabela é recalculada inteira a cada carga (D021).
with cancelamento as (
    select distinct on (chave) chave, dh_evento, lote_id, arquivo
    from {{ ref('nfe_evento') }}
    where tp_evento = '110111'
    order by chave, dh_evento
)

select
    n.chave,
    n.lote_id                                              as lote_id_nota,
    case when c.chave is null then 'autorizada' else 'cancelada' end as situacao,
    c.dh_evento                                            as dh_cancelamento,
    c.lote_id                                              as lote_id_cancelamento,
    -- chegada relativa do evento: só informativa (linha 2); não muda a regra
    case
        when c.chave is null           then null
        when c.lote_id > n.lote_id     then 'depois_da_nota'
        when c.lote_id < n.lote_id     then 'antes_da_nota'
        else 'com_a_nota'
    end                                                    as chegada_cancelamento
from {{ ref('nfe_nota') }} as n
left join cancelamento as c using (chave)
