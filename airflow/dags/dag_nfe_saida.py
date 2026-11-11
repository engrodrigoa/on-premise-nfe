"""Carga das NF-e de saída do Grupo Xis: um lote por execução, da pasta de rede até a bronze (marco 3).

    aguardar_lote -> reservar_lote -> custodiar_lote -> carregar_bronze -> concluir_lote -> ha_mais_lotes -> proximo_lote

- Agenda a cada 10 minutos; o sensor espera até 9 minutos por um lote e, sem lote, pula a execução (skip).
- Um lote por execução, do mais antigo para o mais novo (max_active_runs=1). Havendo fila na inbox, a
  execução dispara a próxima no fim, em vez de esperar a agenda.
- Toda a lógica está em pipelines/carga/lote.py. A DAG só encadeia.
- Saúde da carga: ctrl.lote, não a cor da execução. Falha definitiva de uma task marca o lote como ERRO.

Silver e gold (dbt) entram no marco 4, depois de carregar_bronze.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator
from airflow.sdk import BaseSensorOperator, Param, PokeReturnValue, dag, get_current_context, task

DAG_ID = "dag_nfe_saida"


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


@dag(
    dag_id=DAG_ID,
    schedule="*/10 * * * *",
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

    @task.short_circuit
    def ha_mais_lotes(_destino: str) -> bool:
        from pipelines.carga.lote import lotes_prontos
        from pipelines.commons.config import carregar

        if not get_current_context()["params"]["encadear"]:
            return False
        return bool(lotes_prontos(carregar()))

    proximo = TriggerDagRunOperator(task_id="proximo_lote", trigger_dag_id=DAG_ID, retries=0,
                                    on_failure_callback=None)

    aguardar = LoteNaInboxSensor(task_id="aguardar_lote", poke_interval=30, timeout=9 * 60, mode="reschedule",
                                 soft_fail=True)
    lote = reservar_lote(aguardar.output)
    ha_mais_lotes(concluir_lote(carregar_bronze(custodiar_lote(lote)))) >> proximo


dag_nfe_saida()
