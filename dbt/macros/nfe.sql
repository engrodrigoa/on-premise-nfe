{#
  Macros do domínio NF-e (adaptadas do datalabs).
    nfe_ns()               cláusula XMLNAMESPACES do portal fiscal, para usar dentro de XMLTABLE
    nfe_dv_mod11(expr)     DV módulo 11, pesos 2..9 da direita para a esquerda (ASCII - 48: aceita CNPJ alfanumérico)
    nfe_chave_valida(expr) 44 caracteres (UF 2, AAMM 4, CNPJ 14, modelo 55, série 3, nNF 9, tpEmis 1, cNF 8, DV 1) e DV correto
#}

{% macro nfe_ns() -%}
xmlnamespaces('http://www.portalfiscal.inf.br/nfe' as n)
{%- endmacro %}


{% macro nfe_dv_mod11(expr) -%}
(select case when s % 11 < 2 then '0' else (11 - s % 11)::text end
   from (select sum((ascii(substr({{ expr }}, length({{ expr }}) - i + 1, 1)) - 48) * (2 + (i - 1) % 8)) as s
           from generate_series(1, length({{ expr }})) as i) as _dv)
{%- endmacro %}


{% macro nfe_chave_valida(expr) -%}
coalesce({{ expr }} ~ '^[0-9]{6}[0-9A-Z]{12}[0-9]{2}55[0-9]{22}$'
         and right({{ expr }}, 1) = {{ nfe_dv_mod11('left(' ~ expr ~ ', 43)') }}, false)
{%- endmacro %}
