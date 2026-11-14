-- Cabeçalho da NF-e: 1 linha por chave válida (documento.destino = 'valido').
-- Dado pessoal (CLAUDE.md): de pessoa física só fica o hash do CPF; nome e endereço não entram na silver.
with valido as (
    select d.bronze_id, d.lote_id, d.arquivo, d.sha256, d.chave, s.conteudo
    from {{ ref('documento') }} as d
    join {{ ref('stg_documento') }} as s using (bronze_id)
    where d.destino = 'valido' and d.tipo_documento = 'nfe'
)

select
    v.chave,
    v.lote_id,
    v.bronze_id,
    v.arquivo,
    v.sha256,
    x.modelo,
    x.serie,
    x.nnf,
    x.dh_emissao,
    (x.dh_emissao at time zone 'America/Sao_Paulo')::date                    as dt_emissao,
    x.nat_op,
    x.tp_nf,
    x.fin_nfe,
    x.id_dest,
    x.ind_final,
    x.chave_referenciada,
    x.emit_cnpj,
    x.emit_uf,
    x.emit_cod_municipio,
    case when x.dest_cnpj is not null then 'PJ' when x.dest_cpf is not null then 'PF' end as dest_tipo,
    x.dest_cnpj,
    case when x.dest_cpf is not null then encode(sha256(convert_to(x.dest_cpf, 'UTF8')), 'hex') end
                                                                              as dest_cpf_hash,
    case when x.dest_cnpj is not null then x.dest_nome end                    as dest_nome,
    x.dest_uf,
    x.dest_cod_municipio,
    x.v_prod::numeric(15,2)       as v_prod,
    x.v_bc_icms::numeric(15,2)    as v_bc_icms,
    x.v_icms::numeric(15,2)       as v_icms,
    x.v_ipi::numeric(15,2)        as v_ipi,
    x.v_pis::numeric(15,2)        as v_pis,
    x.v_cofins::numeric(15,2)     as v_cofins,
    x.v_nf::numeric(15,2)         as v_nf,
    x.v_bc_ibscbs::numeric(15,2)  as v_bc_ibscbs,
    x.v_ibs_uf::numeric(15,2)     as v_ibs_uf,
    x.v_ibs_mun::numeric(15,2)    as v_ibs_mun,
    x.v_ibs::numeric(15,2)        as v_ibs,
    x.v_cbs::numeric(15,2)        as v_cbs,
    x.n_prot,
    x.dh_recebimento,
    x.c_stat
from valido as v
cross join xmltable(
    {{ nfe_ns() }},
    '/n:nfeProc'
    passing v.conteudo
    columns
        modelo              text        path 'n:NFe/n:infNFe/n:ide/n:mod',
        serie               int         path 'n:NFe/n:infNFe/n:ide/n:serie',
        nnf                 bigint      path 'n:NFe/n:infNFe/n:ide/n:nNF',
        dh_emissao          timestamptz path 'n:NFe/n:infNFe/n:ide/n:dhEmi',
        nat_op              text        path 'n:NFe/n:infNFe/n:ide/n:natOp',
        tp_nf               text        path 'n:NFe/n:infNFe/n:ide/n:tpNF',
        fin_nfe             text        path 'n:NFe/n:infNFe/n:ide/n:finNFe',
        id_dest             text        path 'n:NFe/n:infNFe/n:ide/n:idDest',
        ind_final           text        path 'n:NFe/n:infNFe/n:ide/n:indFinal',
        chave_referenciada  text        path 'n:NFe/n:infNFe/n:ide/n:NFref/n:refNFe',
        emit_cnpj           text        path 'n:NFe/n:infNFe/n:emit/n:CNPJ',
        emit_uf             text        path 'n:NFe/n:infNFe/n:emit/n:enderEmit/n:UF',
        emit_cod_municipio  text        path 'n:NFe/n:infNFe/n:emit/n:enderEmit/n:cMun',
        dest_cnpj           text        path 'n:NFe/n:infNFe/n:dest/n:CNPJ',
        dest_cpf            text        path 'n:NFe/n:infNFe/n:dest/n:CPF',
        dest_nome           text        path 'n:NFe/n:infNFe/n:dest/n:xNome',
        dest_uf             text        path 'n:NFe/n:infNFe/n:dest/n:enderDest/n:UF',
        dest_cod_municipio  text        path 'n:NFe/n:infNFe/n:dest/n:enderDest/n:cMun',
        v_prod              numeric     path 'n:NFe/n:infNFe/n:total/n:ICMSTot/n:vProd',
        v_bc_icms           numeric     path 'n:NFe/n:infNFe/n:total/n:ICMSTot/n:vBC',
        v_icms              numeric     path 'n:NFe/n:infNFe/n:total/n:ICMSTot/n:vICMS',
        v_ipi               numeric     path 'n:NFe/n:infNFe/n:total/n:ICMSTot/n:vIPI',
        v_pis               numeric     path 'n:NFe/n:infNFe/n:total/n:ICMSTot/n:vPIS',
        v_cofins            numeric     path 'n:NFe/n:infNFe/n:total/n:ICMSTot/n:vCOFINS',
        v_nf                numeric     path 'n:NFe/n:infNFe/n:total/n:ICMSTot/n:vNF',
        v_bc_ibscbs         numeric     path 'n:NFe/n:infNFe/n:total/n:IBSCBSTot/n:vBCIBSCBS',
        v_ibs_uf            numeric     path 'n:NFe/n:infNFe/n:total/n:IBSCBSTot/n:gIBS/n:gIBSUF/n:vIBSUF',
        v_ibs_mun           numeric     path 'n:NFe/n:infNFe/n:total/n:IBSCBSTot/n:gIBS/n:gIBSMun/n:vIBSMun',
        v_ibs               numeric     path 'n:NFe/n:infNFe/n:total/n:IBSCBSTot/n:gIBS/n:vIBS',
        v_cbs               numeric     path 'n:NFe/n:infNFe/n:total/n:IBSCBSTot/n:gCBS/n:vCBS',
        n_prot              text        path 'n:protNFe/n:infProt/n:nProt',
        dh_recebimento      timestamptz path 'n:protNFe/n:infProt/n:dhRecbto',
        c_stat              text        path 'n:protNFe/n:infProt/n:cStat'
) as x
