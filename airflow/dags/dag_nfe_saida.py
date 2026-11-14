"""Carga das NF-e de saída do Grupo Xis: um lote por execução, da pasta de rede até a silver (marcos 3 e 4).

    aguardar_lote -> reservar_lote -> custodiar_lote -> carregar_bronze -> concluir_lote
                  -> transformar_silver -> registrar_silver -> ha_mais_lotes -> proximo_lote

- Agenda de hora em hora (D024); o sensor olha a inbox a cada minuto por até 55 minutos e, sem lote, pula a
  execução (skip). Lote publicado entra em até ~1 minuto, com 24 execuções por dia em vez de ~144.
- Um lote por execução, do mais antigo para o mais novo (max_active_runs=1). Havendo fila na inbox, a
  execução dispara a próxima no fim, em vez de esperar a agenda.
- Toda a lógica está em pipelines/ e no dbt. A DAG só encadeia.
- A pasta do lote sai de processando/ antes do dbt: falha na silver não refaz custódia nem bronze.
- Silver: `dbt build` da pasta models/silver, recriada inteira a partir da bronze (D021), e depois o registro
  nas tabelas de controle (pipelines/qualidade/registrar.py). Lote INCOMPLETO também passa (D023).
- Saúde da carga: ctrl.lote, não a cor da execução. Falha definitiva até a bronze marca o lote como ERRO;
  falha na silver fica em ctrl.lote.erro_transformacao, sem apagar o status da entrega (D019).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import BaseSensorOperator, Param, PokeReturnValue, dag, get_current_context, task

DAG_ID = "dag_nfe_saida"
DBT_SILVER = ('"$DBT_BIN" build --select +path:models/silver '
              '--project-dir "$DBT_PROJECT_DIR" --profiles-dir "$DBT_PROFILES_DIR"')


class LoteNaInboxSensor(BaseSensorOperator):
    """Espera um lote publicado na inbox. O XCom é o nome do lote mais antigo.

    Sensor próprio em vez de @task.sensor: o do provider standard que acompanha o 3.4.0 ainda importa uma
    API marcada como obsoleta, e tests/test_dag_integrity.py não aceita aviso de obsolescência nas DAGs.
    """

    def poke(self, context) -> PokeReturnValue:
        from pipelines.carga.lote import lotes_prontos
        from pipelines.commons.config import carregar

        prontos = lotes_prontos(carregar())
        if prontos:
            self.log.info("%d lote(s) na fila; este run leva %s", len(prontos), prontos[0])
        return PokeReturnValue(is_done=bool(prontos), xcom_value=prontos[0] if prontos else None)


def _marcar_erro(context) -> None:
    """on_failure_callback: só roda depois da última tentativa."""
    from pipelines.carga.lote import marcar_erro
    from pipelines.commons.config import carregar

    ti = context["ti"]
    nome = ti.xcom_pull(task_ids="aguardar_lote")
    if nome:
        erro = context.get("exception")
        marcar_erro(carregar(), nome, f"{ti.task_id}: {erro!r}")


def _marcar_erro_silver(context) -> None:
    """on_failure_callback da silver (D019): registra a falha sem trocar o status da entrega."""
    from pipelines.commons.config import carregar
    from pipelines.qualidade.registrar import marcar_erro_transformacao

    ti = context["ti"]
    nome = ti.xcom_pull(task_ids="aguardar_lote")
    if nome:
        erro = context.get("exception")
        marcar_erro_transformacao(carregar(), nome, f"{ti.task_id}: {erro!r}")


@dag(
    dag_id=DAG_ID,
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["nfe", "carga"],
    params={"encadear": Param(True, type="boolean",
                              description="ao terminar, disparar a próxima execução se houver lote na inbox")},
    default_args={"retries": 2, "retry_delay": timedelta(minutes=1), "on_failure_callback": _marcar_erro},
    doc_md=__doc__,
)
def dag_nfe_saida():
    @task
    def reservar_lote(nome_lote: str) -> dict:
        from pipelines.carga.lote import reservar
        from pipelines.commons.config import carregar

        run_id = get_current_context()["run_id"]
        return {"nome": nome_lote, "lote_id": reservar(carregar(), nome_lote, run_id)}

    @task
    def custodiar_lote(lote: dict) -> dict:
        from pipelines.carga.lote import custodiar
        from pipelines.commons.config import carregar

        resumo = custodiar(carregar(), lote["lote_id"], lote["nome"])
        print(f"custódia de {lote['nome']}: {resumo}")
        return lote

    @task
    def carregar_bronze(lote: dict) -> dict:
        from pipelines.carga.lote import carregar_bronze as carregar_lote
        from pipelines.commons.config import carregar

        resumo = carregar_lote(carregar(), lote["lote_id"])
        print(f"bronze de {lote['nome']}: {resumo}")
        return lote

    @task
    def concluir_lote(lote: dict) -> str:
        from pipelines.carga.lote import concluir
        from pipelines.commons.config import carregar

        return concluir(carregar(), lote["nome"])

    transformar_silver = BashOperator(task_id="transformar_silver", bash_command=DBT_SILVER,
                                      on_failure_callback=_marcar_erro_silver)

    @task(on_failure_callback=_marcar_erro_silver)
    def registrar_silver() -> dict:
        from pipelines.commons.config import carregar
        from pipelines.qualidade.registrar import registrar

        resumo = registrar(carregar(), get_current_context()["run_id"])
        print(f"silver: {resumo}")
        return resumo

    @task.short_circuit
    def ha_mais_lotes(_resumo: dict) -> bool:
        from pipelines.carga.lote import lotes_prontos
        from pipelines.commons.config import carregar

        if not get_current_context()["params"]["encadear"]:
            return False
        return bool(lotes_prontos(carregar()))

    proximo = TriggerDagRunOperator(task_id="proximo_lote", trigger_dag_id=DAG_ID, retries=0,
                                    on_failure_callback=None)

    aguardar = LoteNaInboxSensor(task_id="aguardar_lote", poke_interval=60, timeout=55 * 60, mode="reschedule",
                                 soft_fail=True)
    lote = reservar_lote(aguardar.output)
    concluido = concluir_lote(carregar_bronze(custodiar_lote(lote)))
    registrado = registrar_silver()
    concluido >> transformar_silver >> registrado
    ha_mais_lotes(registrado) >> proximo


dag_nfe_saida()
