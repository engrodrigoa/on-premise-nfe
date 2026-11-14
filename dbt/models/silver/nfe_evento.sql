-- Eventos válidos (por enquanto só cancelamento, 110111): 1 linha por (chave, tp_evento, n_seq_evento).
-- Evento é documento próprio (D006); a situação da nota é derivada em nfe_situacao.
with valido as (
    select d.bronze_id, d.lote_id, d.arquivo, d.sha256, d.chave, s.conteudo
    from {{ ref('documento') }} as d
    join {{ ref('stg_documento') }} as s using (bronze_id)
    where d.destino = 'valido' and d.tipo_documento = 'evento'
)

select
    v.chave,
    x.tp_evento,
    x.n_seq_evento,
    v.lote_id,
    v.bronze_id,
    v.arquivo,
    v.sha256,
    x.desc_evento,
    x.dh_evento,
    x.emit_cnpj,
    x.n_prot_nota,
    x.justificativa,
    x.c_stat,
    x.n_prot_evento,
    x.dh_registro
from valido as v
cross join xmltable(
    {{ nfe_ns() }},
    '/n:procEventoNFe'
    passing v.conteudo
    columns
        tp_evento      text        path 'n:evento/n:infEvento/n:tpEvento',
        n_seq_evento   int         path 'n:evento/n:infEvento/n:nSeqEvento',
        desc_evento    text        path 'n:evento/n:infEvento/n:detEvento/n:descEvento',
        dh_evento      timestamptz path 'n:evento/n:infEvento/n:dhEvento',
        emit_cnpj      text        path 'n:evento/n:infEvento/n:CNPJ',
        n_prot_nota    text        path 'n:evento/n:infEvento/n:detEvento/n:nProt',
        justificativa  text        path 'n:evento/n:infEvento/n:detEvento/n:xJust',
        c_stat         text        path 'n:retEvento/n:infEvento/n:cStat',
        n_prot_evento  text        path 'n:retEvento/n:infEvento/n:nProt',
        dh_registro    timestamptz path 'n:retEvento/n:infEvento/n:dhRegEvento'
) as x
