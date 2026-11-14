-- Itens da NF-e: 1 linha por (chave, n_item), só das notas válidas.
with valido as (
    select d.chave, d.lote_id, s.conteudo
    from {{ ref('documento') }} as d
    join {{ ref('stg_documento') }} as s using (bronze_id)
    where d.destino = 'valido' and d.tipo_documento = 'nfe'
)

select
    v.chave,
    i.n_item,
    v.lote_id,
    i.c_prod,
    i.x_prod,
    i.ncm,
    i.cfop,
    i.u_com,
    i.q_com::numeric(15,4)        as q_com,
    i.v_un_com::numeric(21,10)    as v_un_com,
    i.v_prod::numeric(15,2)       as v_prod,
    i.orig,
    i.cst_icms,
    i.v_bc_icms::numeric(15,2)    as v_bc_icms,
    i.p_icms::numeric(7,4)        as p_icms,
    i.v_icms::numeric(15,2)       as v_icms,
    coalesce(i.cst_ipi_trib, i.cst_ipi_nt) as cst_ipi,
    i.v_ipi::numeric(15,2)        as v_ipi,
    i.cst_pis,
    i.v_pis::numeric(15,2)        as v_pis,
    i.cst_cofins,
    i.v_cofins::numeric(15,2)     as v_cofins,
    i.cst_ibscbs,
    i.cclasstrib,
    i.v_bc_ibscbs::numeric(15,2)  as v_bc_ibscbs,
    i.p_ibs_uf::numeric(7,4)      as p_ibs_uf,
    i.v_ibs_uf::numeric(15,2)     as v_ibs_uf,
    i.p_ibs_mun::numeric(7,4)     as p_ibs_mun,
    i.v_ibs_mun::numeric(15,2)    as v_ibs_mun,
    i.v_ibs::numeric(15,2)        as v_ibs,
    i.p_cbs::numeric(7,4)         as p_cbs,
    i.v_cbs::numeric(15,2)        as v_cbs
from valido as v
cross join xmltable(
    {{ nfe_ns() }},
    '/n:nfeProc/n:NFe/n:infNFe/n:det'
    passing v.conteudo
    columns
        n_item        int     path '@nItem',
        c_prod        text    path 'n:prod/n:cProd',
        x_prod        text    path 'n:prod/n:xProd',
        ncm           text    path 'n:prod/n:NCM',
        cfop          text    path 'n:prod/n:CFOP',
        u_com         text    path 'n:prod/n:uCom',
        q_com         numeric path 'n:prod/n:qCom',
        v_un_com      numeric path 'n:prod/n:vUnCom',
        v_prod        numeric path 'n:prod/n:vProd',
        orig          text    path 'n:imposto/n:ICMS/*/n:orig',
        cst_icms      text    path 'n:imposto/n:ICMS/*/n:CST',
        v_bc_icms     numeric path 'n:imposto/n:ICMS/*/n:vBC',
        p_icms        numeric path 'n:imposto/n:ICMS/*/n:pICMS',
        v_icms        numeric path 'n:imposto/n:ICMS/*/n:vICMS',
        cst_ipi_trib  text    path 'n:imposto/n:IPI/n:IPITrib/n:CST',
        cst_ipi_nt    text    path 'n:imposto/n:IPI/n:IPINT/n:CST',
        v_ipi         numeric path 'n:imposto/n:IPI/n:IPITrib/n:vIPI',
        cst_pis       text    path 'n:imposto/n:PIS/*/n:CST',
        v_pis         numeric path 'n:imposto/n:PIS/*/n:vPIS',
        cst_cofins    text    path 'n:imposto/n:COFINS/*/n:CST',
        v_cofins      numeric path 'n:imposto/n:COFINS/*/n:vCOFINS',
        cst_ibscbs    text    path 'n:imposto/n:IBSCBS/n:CST',
        cclasstrib    text    path 'n:imposto/n:IBSCBS/n:cClassTrib',
        v_bc_ibscbs   numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:vBC',
        p_ibs_uf      numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:gIBSUF/n:pIBSUF',
        v_ibs_uf      numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:gIBSUF/n:vIBSUF',
        p_ibs_mun     numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:gIBSMun/n:pIBSMun',
        v_ibs_mun     numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:gIBSMun/n:vIBSMun',
        v_ibs         numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:vIBS',
        p_cbs         numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:gCBS/n:pCBS',
        v_cbs         numeric path 'n:imposto/n:IBSCBS/n:gIBSCBS/n:gCBS/n:vCBS'
) as i
