"""A bronze é reconstruível a partir do raw? (D003, critério do marco 3)

Não apaga nada: monta uma bronze paralela numa tabela temporária, lendo cada objeto do bucket pela versão
registrada no inventário, e compara com a bronze real linha a linha.

    python -m pipelines.carga.reconstruir_bronze             todos os lotes
    python -m pipelines.carga.reconstruir_bronze --lote lote_2026-01-02

Saída: PASSOU/FALHOU por verificação; código 1 se alguma falhar.
"""
from __future__ import annotations

import argparse
import sys

from pipelines.carga.lote import SQL_INSERIR_BRONZE, ler_do_raw, parametros_bronze
from pipelines.commons.config import Config, carregar
from pipelines.commons.db import conectar
from pipelines.commons.s3 import cliente

SQL_COMPARAR = """
WITH r AS (SELECT lote_id, arquivo, sha256, md5(conteudo::text) AS h, erro_leitura IS NULL AS legivel
             FROM pg_temp.bronze_reconstruida),
     b AS (SELECT lote_id, arquivo, sha256, md5(conteudo::text) AS h, erro_leitura IS NULL AS legivel
             FROM bronze.documento_xml WHERE lote_id = ANY(%(lotes)s))
SELECT (SELECT count(*) FROM b),
       (SELECT count(*) FROM r),
       (SELECT count(*) FROM r FULL JOIN b USING (lote_id, arquivo) WHERE r.arquivo IS NULL OR b.arquivo IS NULL),
       (SELECT count(*) FROM r JOIN b USING (lote_id, arquivo)
         WHERE r.sha256 <> b.sha256 OR r.h IS DISTINCT FROM b.h OR r.legivel <> b.legivel)
"""


def verificar(cfg: Config, nome_lote: str | None = None) -> dict:
    s3 = cliente(cfg)
    with conectar(cfg) as conn, conn.cursor() as cur:
        cur.execute("SELECT lote_id FROM ctrl.lote WHERE (%s::text IS NULL OR nome_lote = %s) ORDER BY lote_id",
                    (nome_lote, nome_lote))
        lotes = [r[0] for r in cur.fetchall()]
        cur.execute("CREATE TEMP TABLE bronze_reconstruida (LIKE bronze.documento_xml INCLUDING DEFAULTS) ON COMMIT DROP")
        cur.execute("ALTER TABLE pg_temp.bronze_reconstruida ADD UNIQUE (lote_id, arquivo)")
        sql = SQL_INSERIR_BRONZE.replace("bronze.documento_xml", "pg_temp.bronze_reconstruida")
        cur.execute(
            """SELECT r.lote_id, r.arquivo, r.documento_id, d.tipo_documento, d.sha256, d.tamanho_bytes,
                      d.bucket, d.objeto, d.version_id
                 FROM custodia.recebimento r JOIN custodia.documento d USING (documento_id)
                WHERE r.lote_id = ANY(%s) ORDER BY r.lote_id, r.arquivo""",
            (lotes,),
        )
        colunas = [c.name for c in cur.description]
        registros = [dict(zip(colunas, linha)) for linha in cur.fetchall()]
        for rec in registros:
            dados = ler_do_raw(s3, rec["bucket"], rec["objeto"], rec["version_id"], rec["sha256"])
            cur.execute(sql, parametros_bronze(rec["lote_id"], rec, dados))
        cur.execute(SQL_COMPARAR, {"lotes": lotes})
        na_bronze, reconstruidas, sem_par, diferentes = cur.fetchone()
    return {"lotes": len(lotes), "bronze": na_bronze, "reconstruidas": reconstruidas,
            "sem_par": sem_par, "diferentes": diferentes}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lote", help="nome do lote (padrão: todos)")
    r = verificar(carregar(), ap.parse_args().lote)
    checagens = [
        (r["bronze"] == r["reconstruidas"],
         f"contagem: {r['bronze']} linhas na bronze, {r['reconstruidas']} reconstruídas do raw ({r['lotes']} lotes)"),
        (r["sem_par"] == 0, f"pareamento: {r['sem_par']} arquivo(s) em só um dos lados"),
        (r["diferentes"] == 0, f"conteúdo: {r['diferentes']} linha(s) com sha256 ou XML diferente"),
    ]
    for ok, texto in checagens:
        print(f"{'PASSOU' if ok else 'FALHOU'}  {texto}")
    return 0 if all(ok for ok, _ in checagens) else 1


if __name__ == "__main__":
    sys.exit(main())
