-- Destino de cada arquivo da bronze: 1 linha por linha da bronze, e cada uma cai em exatamente um destino.
--
--   valido              segue para nota, item e evento
--   quarentena          não é um documento íntegro do grupo (D007), com o motivo
--   reenvio_descartado  conteúdo idêntico ao documento que já vale (D016: um objeto, dois recebimentos)
--
-- Fechamento da linha 2 (D020): bronze = valido + quarentena + reenvio_descartado, lote a lote.
-- A tabela é recriada inteira a cada carga (D021): o evento que chegou antes da nota sai da quarentena sozinho
-- quando a nota chega.
with doc as (
    select * from {{ ref('stg_documento') }}
),

grupo as (
    select cnpj from {{ ref('cad_estabelecimento_grupo') }}
),

-- 1. motivos que dependem só do próprio arquivo
intrinseco as (
    select
        doc.*,
        case
            when conteudo is null                                        then 'xml_corrompido'
            when tipo_documento = 'invalido'                             then 'documento_desconhecido'
            when not chave_valida                                        then 'chave_invalida'
            when tipo_documento = 'nfe' and c_stat is distinct from '100' then 'sem_autorizacao'
            when tipo_documento = 'evento' and coalesce(c_stat, '') not in ('135', '136')
                                                                         then 'sem_autorizacao'
            when emit_cnpj is null or emit_cnpj not in (select cnpj from grupo)
                                                                         then 'emitente_fora_do_grupo'
        end as motivo_intrinseco,
        case when tipo_documento = 'nfe' then chave
             else chave || '-' || tp_evento || '-' || n_seq_evento end as identidade
    from doc
),

-- 2. entre os íntegros, um documento vale por identidade: o primeiro recebido (D022).
--    Mesmo lote: vale o arquivo com o nome no padrão da origem; depois, a ordem de chegada na bronze.
ordenado as (
    select
        bronze_id,
        row_number() over w                          as ordem,
        first_value(sha256)    over w                as sha256_vigente,
        first_value(bronze_id) over w                as bronze_id_vigente
    from intrinseco
    where motivo_intrinseco is null
    window w as (partition by tipo_documento, identidade
                 order by lote_id, nome_padrao desc, bronze_id
                 rows between unbounded preceding and unbounded following)
),

notas_validas as (
    select i.chave
    from intrinseco as i
    join ordenado as o using (bronze_id)
    where i.tipo_documento = 'nfe' and o.ordem = 1
),

decidido as (
    select
        i.*,
        o.ordem,
        o.bronze_id_vigente,
        case
            when i.motivo_intrinseco is not null                      then i.motivo_intrinseco
            when o.ordem > 1 and i.sha256 <> o.sha256_vigente         then 'chave_conteudo_divergente'
            when o.ordem = 1 and i.tipo_documento = 'evento'
                 and i.chave not in (select chave from notas_validas) then 'evento_sem_nota'
        end as motivo
    from intrinseco as i
    left join ordenado as o using (bronze_id)
)

select
    bronze_id,
    lote_id,
    arquivo,
    sha256,
    tipo_documento,
    chave,
    chave_origem,
    chave_valida,
    emit_cnpj,
    serie,
    nnf,
    tp_evento,
    n_seq_evento,
    case
        when motivo is not null then 'quarentena'
        when ordem > 1          then 'reenvio_descartado'
        else 'valido'
    end                                                     as destino,
    motivo,
    case when ordem > 1 then bronze_id_vigente end          as bronze_id_vigente,
    erro_leitura,
    carregado_em
from decidido
