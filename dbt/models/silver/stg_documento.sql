-- Identificação de cada linha da bronze: 1 linha por arquivo recebido, sem nenhuma decisão.
-- O que vem do XML vem com XMLTABLE; arquivo ilegível só tem o nome, e a chave sai dele quando o nome segue o
-- padrão da origem (<chave>-procNFe.xml). Assim a nota corrompida ainda ocupa o seu número na série.
with bronze as (
    select bronze_id, lote_id, arquivo, sha256, tipo_documento, conteudo, erro_leitura, carregado_em
    from {{ source('bronze', 'documento_xml') }}
),

nfe as (
    select b.bronze_id, x.*
    from bronze as b
    cross join xmltable(
        {{ nfe_ns() }},
        '/n:nfeProc'
        passing b.conteudo
        columns
            chave_protocolo text path 'n:protNFe/n:infProt/n:chNFe',
            id_inf_nfe      text path 'n:NFe/n:infNFe/@Id',
            emit_cnpj       text path 'n:NFe/n:infNFe/n:emit/n:CNPJ',
            c_stat          text path 'n:protNFe/n:infProt/n:cStat'
    ) as x
    where b.tipo_documento = 'nfe' and b.conteudo is not null
),

evento as (
    select b.bronze_id, x.*
    from bronze as b
    cross join xmltable(
        {{ nfe_ns() }},
        '/n:procEventoNFe'
        passing b.conteudo
        columns
            chave        text path 'n:evento/n:infEvento/n:chNFe',
            emit_cnpj    text path 'n:evento/n:infEvento/n:CNPJ',
            tp_evento    text path 'n:evento/n:infEvento/n:tpEvento',
            n_seq_evento int  path 'n:evento/n:infEvento/n:nSeqEvento',
            c_stat       text path 'n:retEvento/n:infEvento/n:cStat'
    ) as x
    where b.tipo_documento = 'evento' and b.conteudo is not null
),

identificado as (
    select
        b.*,
        coalesce(n.chave_protocolo, substr(n.id_inf_nfe, 4), e.chave,
                 substring(b.arquivo from '^([0-9A-Z]{44})-'))                      as chave,
        case
            when coalesce(n.chave_protocolo, substr(n.id_inf_nfe, 4), e.chave) is not null then 'xml'
            when substring(b.arquivo from '^([0-9A-Z]{44})-') is not null then 'nome_arquivo'
        end                                                                           as chave_origem,
        coalesce(n.emit_cnpj, e.emit_cnpj)                                            as emit_cnpj_xml,
        coalesce(n.c_stat, e.c_stat)                                                  as c_stat,
        e.tp_evento,
        e.n_seq_evento,
        -- nome no padrão da origem: desempate quando dois conteúdos da mesma chave chegam no mesmo lote (D022)
        b.arquivo ~ '^[0-9A-Z]{44}(-[0-9]{6}-[0-9]{2})?-proc(NFe|EventoNFe)\.xml$'    as nome_padrao
    from bronze as b
    left join nfe as n using (bronze_id)
    left join evento as e using (bronze_id)
)

select
    bronze_id,
    lote_id,
    arquivo,
    sha256,
    tipo_documento,
    conteudo,
    erro_leitura,
    carregado_em,
    chave,
    chave_origem,
    {{ nfe_chave_valida('chave') }}                                             as chave_valida,
    coalesce(emit_cnpj_xml, case when chave_origem is not null then substr(chave, 7, 14) end) as emit_cnpj,
    case when {{ nfe_chave_valida('chave') }} then substr(chave, 23, 3)::int end  as serie,
    case when {{ nfe_chave_valida('chave') }} then substr(chave, 26, 9)::bigint end as nnf,
    c_stat,
    tp_evento,
    n_seq_evento,
    nome_padrao
from identificado
