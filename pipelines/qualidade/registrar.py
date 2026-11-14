"""Registro da silver nas tabelas de controle (linhas 2 e 3), logo depois do `dbt build` da silver.

O dbt transforma; este módulo só conta e registra (mesma divisão da D004: Python orquestra e registra, SQL
transforma). Como a silver é recriada inteira a cada carga (D021), o registro vale para todos os lotes:

    1. fechamento (D020)  cada linha da bronze tem exatamente um destino na silver, lote a lote.
                          Não fechou = a silver está velha ou perdeu linha: falha de processo, a task quebra.
    2. ctrl.lote          qtd_validos, qtd_quarentena, qtd_reenvios e transformado_em (1ª vez), por lote.
    3. ctrl.dq            uma linha por lote x regra (catálogo em dbt/seeds/cat_regra_qualidade.csv), gravada
                          só quando o número muda. É o histórico: quando o evento sem nota sai da quarentena,
                          aparece uma linha nova com a falha a menos, e a antiga continua lá.

Uso fora da DAG:  python -m pipelines.qualidade.registrar     (ou: make silver)
"""
from __future__ import annotations

import sys

from pipelines.commons.config import Config, carregar
from pipelines.commons.db import conectar

SQL_FECHAMENTO = """
SELECT l.lote_id, l.nome_lote,
       count(b.bronze_id)                                         AS bronze,
       count(d.bronze_id) FILTER (WHERE d.destino = 'valido')     AS validos,
       count(d.bronze_id) FILTER (WHERE d.destino = 'quarentena') AS quarentena,
       count(d.bronze_id) FILTER (WHERE d.destino = 'reenvio_descartado') AS reenvios,
       count(d.bronze_id)                                         AS na_silver
  FROM ctrl.lote l
  JOIN bronze.documento_xml b USING (lote_id)
  LEFT JOIN silver.documento d USING (bronze_id)
 GROUP BY l.lote_id, l.nome_lote
 ORDER BY l.lote_id
"""

SQL_ATUALIZAR_LOTE = """
UPDATE ctrl.lote
   SET qtd_validos = %(validos)s, qtd_quarentena = %(quarentena)s, qtd_reenvios = %(reenvios)s,
       transformado_em = coalesce(transformado_em, now()), erro_transformacao = NULL
 WHERE lote_id = %(lote_id)s
"""

# Avaliados: erro = arquivos do lote na bronze; aviso = notas válidas do lote.
# Falhas: erro = arquivos em quarentena com o motivo; aviso = notas distintas com o aviso.
SQL_DQ = """
WITH lotes AS (SELECT DISTINCT lote_id FROM bronze.documento_xml),
regra AS (SELECT regra, severidade FROM silver.cat_regra_qualidade),
avaliados AS (
    SELECT lote_id, 'erro' AS severidade, count(*) AS n FROM bronze.documento_xml GROUP BY lote_id
    UNION ALL
    SELECT lote_id, 'aviso', count(*) FROM silver.nfe_nota GROUP BY lote_id
),
falhas AS (
    SELECT lote_id, motivo AS regra, count(*) AS n FROM silver.documento WHERE destino = 'quarentena'
     GROUP BY lote_id, motivo
    UNION ALL
    SELECT lote_id, regra, count(DISTINCT chave) FROM silver.nfe_aviso GROUP BY lote_id, regra
),
atual AS (
    SELECT l.lote_id, r.regra, r.severidade, coalesce(a.n, 0) AS qtd_avaliados, coalesce(f.n, 0) AS qtd_falhas
      FROM lotes l
     CROSS JOIN regra r
      LEFT JOIN avaliados a ON a.lote_id = l.lote_id AND a.severidade = r.severidade
      LEFT JOIN falhas f ON f.lote_id = l.lote_id AND f.regra = r.regra
),
ultimo AS (
    SELECT DISTINCT ON (lote_id, regra) lote_id, regra, qtd_avaliados, qtd_falhas
      FROM ctrl.dq
     ORDER BY lote_id, regra, executado_em DESC, dq_id DESC
)
INSERT INTO ctrl.dq (lote_id, regra, severidade, qtd_avaliados, qtd_falhas, dag_run_id)
SELECT a.lote_id, a.regra, a.severidade, a.qtd_avaliados, a.qtd_falhas, %(dag_run_id)s
  FROM atual a
  LEFT JOIN ultimo u USING (lote_id, regra)
 WHERE u.lote_id IS NULL
    OR (u.qtd_avaliados, u.qtd_falhas) IS DISTINCT FROM (a.qtd_avaliados, a.qtd_falhas)
"""


def registrar(cfg: Config, dag_run_id: str | None = None) -> dict:
    with conectar(cfg) as conn, conn.cursor() as cur:
        cur.execute(SQL_FECHAMENTO)
        colunas = [c.name for c in cur.description]
        lotes = [dict(zip(colunas, linha)) for linha in cur.fetchall()]

        abertos = [lt for lt in lotes if lt["na_silver"] != lt["bronze"]]
        if abertos:                                   # linha 2: a silver não cobriu a bronze inteira
            detalhe = ", ".join(f"{lt['nome_lote']} ({lt['bronze']} na bronze, {lt['na_silver']} na silver)"
                                for lt in abertos[:5])
            raise RuntimeError(f"silver não fecha com a bronze em {len(abertos)} lote(s): {detalhe}")

        for lt in lotes:
            cur.execute(SQL_ATUALIZAR_LOTE, lt)
        cur.execute(SQL_DQ, {"dag_run_id": dag_run_id})
        linhas_dq = cur.rowcount

    return {
        "lotes": len(lotes),
        "bronze": sum(lt["bronze"] for lt in lotes),
        "validos": sum(lt["validos"] for lt in lotes),
        "quarentena": sum(lt["quarentena"] for lt in lotes),
        "reenvios": sum(lt["reenvios"] for lt in lotes),
        "linhas_dq": linhas_dq,
    }


def marcar_erro_transformacao(cfg: Config, nome_lote: str, mensagem: str) -> None:
    """Falha da silver (D019). O status do lote não muda (CARREGADO ou INCOMPLETO dizem como a ENTREGA chegou);
    a falha fica em erro_transformacao até a próxima execução bem-sucedida, e o painel de saúde a acusa."""
    with conectar(cfg) as conn, conn.cursor() as cur:
        cur.execute("UPDATE ctrl.lote SET erro_transformacao = %s WHERE nome_lote = %s",
                    (mensagem[:2000], nome_lote))


def main() -> int:
    r = registrar(carregar())
    print(f"silver registrada: {r['lotes']} lote(s), {r['bronze']} arquivo(s) na bronze = {r['validos']} válidos "
          f"+ {r['quarentena']} em quarentena + {r['reenvios']} reenvio(s) descartado(s); "
          f"{r['linhas_dq']} linha(s) novas em ctrl.dq")
    return 0


if __name__ == "__main__":
    sys.exit(main())
