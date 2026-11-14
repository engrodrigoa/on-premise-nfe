-- Avisos fiscais (D007): a nota é válida e segue para a gold; o aviso vai para o fiscal avaliar.
-- 1 linha por (chave, regra). Toda regra fiscal vem de seed com vigência na data de emissão (D008).
with nota as (
    select * from {{ ref('nfe_nota') }}
),

item as (
    select * from {{ ref('nfe_item') }}
),

soma_item as (
    select chave, sum(v_cbs) as v_cbs, sum(v_bc_ibscbs) as v_bc_ibscbs
    from item
    group by chave
),

-- 1 e 2. total do grupo IBSCBSTot x soma dos itens
tot_cbs as (
    select n.chave, 'tot_cbs_divergente' as regra,
           format('total %s x soma dos itens %s', n.v_cbs, s.v_cbs) as detalhe
    from nota as n join soma_item as s using (chave)
    where coalesce(n.v_cbs, 0) <> coalesce(s.v_cbs, 0)
),

vbc_ibscbs as (
    select n.chave, 'vbc_ibscbs_divergente', format('total %s x soma dos itens %s', n.v_bc_ibscbs, s.v_bc_ibscbs)
    from nota as n join soma_item as s using (chave)
    where coalesce(n.v_bc_ibscbs, 0) <> coalesce(s.v_bc_ibscbs, 0)
),

-- 3. CST x cClassTrib fora da tabela vigente
cclasstrib as (
    select i.chave, 'cclasstrib_incoerente',
           string_agg(format('item %s: CST %s x cClassTrib %s', i.n_item, i.cst_ibscbs, i.cclasstrib), '; '
                      order by i.n_item)
    from item as i
    join nota as n using (chave)
    where not exists (
        select 1 from {{ ref('regra_cst_cclasstrib') }} as r
        where r.cst = i.cst_ibscbs and r.cclasstrib = i.cclasstrib
          and n.dt_emissao >= r.inicio_vigencia and n.dt_emissao <= coalesce(r.fim_vigencia, date '9999-12-31')
    )
    group by i.chave
),

-- 4. alíquota destacada no item x tabela vigente (só itens com o grupo gIBSCBS)
aliquota_vigente as (
    select n.chave,
           max(r.aliquota) filter (where r.tributo = 'ibs_uf')  as ibs_uf,
           max(r.aliquota) filter (where r.tributo = 'ibs_mun') as ibs_mun,
           max(r.aliquota) filter (where r.tributo = 'cbs')     as cbs
    from nota as n
    left join {{ ref('regra_aliquota_ibscbs') }} as r
      on n.dt_emissao >= r.inicio_vigencia and n.dt_emissao <= coalesce(r.fim_vigencia, date '9999-12-31')
    group by n.chave
),

aliquota as (
    select i.chave, 'aliquota_ibscbs_divergente',
           string_agg(format('item %s: IBS UF %s, IBS Mun %s, CBS %s (vigente %s, %s, %s)', i.n_item,
                             i.p_ibs_uf, i.p_ibs_mun, i.p_cbs, a.ibs_uf, a.ibs_mun, a.cbs), '; ' order by i.n_item)
    from item as i
    join aliquota_vigente as a using (chave)
    where i.p_cbs is not null
      and (i.p_ibs_uf is distinct from a.ibs_uf or i.p_ibs_mun is distinct from a.ibs_mun
           or i.p_cbs is distinct from a.cbs)
    group by i.chave
),

-- 5. buraco na numeração da série. Conta todo número recebido do grupo, inclusive o de nota em quarentena
--    (a chave sai do nome do arquivo): nota corrompida não é salto. O aviso fica na nota depois do buraco.
numero_recebido as (
    select distinct d.emit_cnpj, d.serie, d.nnf
    from {{ ref('documento') }} as d
    where d.chave_valida
      and (d.tipo_documento = 'nfe' or (d.tipo_documento = 'invalido' and d.arquivo !~ 'procEventoNFe'))
      and d.emit_cnpj in (select cnpj from {{ ref('cad_estabelecimento_grupo') }})
),

numero_anterior as (
    select emit_cnpj, serie, nnf, lag(nnf) over (partition by emit_cnpj, serie order by nnf) as nnf_anterior
    from numero_recebido
),

salto as (
    select n.chave, 'numeracao_com_salto',
           format('série %s: depois do nº %s veio o nº %s (%s número(s) sem nota)',
                  a.serie, a.nnf_anterior, a.nnf, a.nnf - a.nnf_anterior - 1)
    from numero_anterior as a
    join nota as n on n.emit_cnpj = a.emit_cnpj and n.serie = a.serie and n.nnf = a.nnf
    where a.nnf - a.nnf_anterior > 1
),

-- 6. devolução (finNFe 4) que aponta para uma nota que o pipeline nunca recebeu
devolucao as (
    select n.chave, 'devolucao_ref_inexistente', format('refNFe %s', coalesce(n.chave_referenciada, 'ausente'))
    from nota as n
    where n.fin_nfe = '4'
      and (n.chave_referenciada is null
           or n.chave_referenciada not in (select chave from {{ ref('documento') }}
                                           where tipo_documento in ('nfe', 'invalido') and chave is not null))
),

-- 7. cancelamento depois do prazo vigente
cancelamento as (
    select n.chave, 'cancelamento_fora_do_prazo',
           format('emitida %s, cancelada %s (prazo %s h)', n.dh_emissao, e.dh_evento, p.prazo_horas)
    from {{ ref('nfe_evento') }} as e
    join nota as n using (chave)
    join {{ ref('regra_prazo_evento') }} as p
      on p.tp_evento = e.tp_evento
     and n.dt_emissao >= p.inicio_vigencia and n.dt_emissao <= coalesce(p.fim_vigencia, date '9999-12-31')
    where e.dh_evento > n.dh_emissao + make_interval(hours => p.prazo_horas)
),

todos as (
    select * from tot_cbs
    union all select * from vbc_ibscbs
    union all select * from cclasstrib
    union all select * from aliquota
    union all select * from salto
    union all select * from devolucao
    union all select * from cancelamento
)

select
    t.chave,
    n.lote_id,
    t.regra,
    t.detalhe,
    n.emit_cnpj,
    n.dt_emissao
from todos as t
join nota as n using (chave)
