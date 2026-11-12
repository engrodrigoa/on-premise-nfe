"""Smoke test da fundação (marco 1): o ambiente está pronto para receber o pipeline?

Disparo manual. Não move dado nenhum; só confere as três peças das quais os próximos marcos dependem:

    postgres_schemas   os schemas e as tabelas de controle existem
    bucket_custodia    o bucket da custódia existe, com versionamento (e informa o bloqueio de objeto)
    dbt_debug          o dbt do venv isolado conecta no banco

Cada task grava no log o que encontrou. Falha = a fundação não está pronta.
"""
from __future__ import annotations

from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, task

OBJETOS_ESPERADOS = [
    "ctrl.lote",
    "ctrl.dq",
    "ctrl.reconciliacao",
    "ctrl.integridade_custodia",
    "custodia.documento",
    "bronze.documento_xml",
]
SCHEMAS_ESPERADOS = ["custodia", "bronze", "silver", "gold", "ctrl"]


@task
def postgres_schemas() -> dict:
    from pipelines.commons.db import conectar

    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(%s)",
                    (SCHEMAS_ESPERADOS,))
        schemas = {r[0] for r in cur.fetchall()}
        faltando = sorted(set(SCHEMAS_ESPERADOS) - schemas)
        for objeto in OBJETOS_ESPERADOS:
            cur.execute("SELECT to_regclass(%s)", (objeto,))
            if cur.fetchone()[0] is None:
                faltando.append(objeto)
    if faltando:
        raise RuntimeError(f"objetos ausentes no Postgres: {faltando}. Rode: make db-bootstrap")
    return {"schemas": sorted(schemas), "tabelas": OBJETOS_ESPERADOS}


@task
def bucket_custodia() -> dict:
    from pipelines.commons.config import carregar
    from pipelines.commons.s3 import cliente
    from pipelines.custodia.setup_bucket import estado

    cfg = carregar()
    atual = estado(cliente(cfg), cfg.bucket_raw)
    print(f"estado do bucket: {atual}")
    if atual["versionamento"] != "Enabled":
        raise RuntimeError(f"bucket {cfg.bucket_raw} sem versionamento: {atual}")
    if not atual["lock"]["ligado"]:
        print("AVISO: bucket sem bloqueio de objeto (ver decisão D003)")
    return atual


with DAG(
    dag_id="dag_fundacao_smoke",
    description="Marco 1: confere Postgres, bucket da custódia e dbt",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["nfe", "fundacao"],
    doc_md=__doc__,
):
    dbt_debug = BashOperator(
        task_id="dbt_debug",
        bash_command='"$DBT_BIN" debug --project-dir "$DBT_PROJECT_DIR" --profiles-dir "$DBT_PROFILES_DIR"',
    )
    postgres_schemas() >> bucket_custodia() >> dbt_debug
