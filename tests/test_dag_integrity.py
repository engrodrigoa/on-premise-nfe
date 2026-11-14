"""As DAGs carregam no Airflow real (mesma versão da imagem)? Pulado quando o Airflow não está instalado.

Usa o mesmo DagBag do dag-processor. Pega antes do deploy o que só apareceria na primeira execução:
erro de import e uso de API obsoleta.
"""
import warnings
from pathlib import Path

import pytest

# a pasta airflow/ do repositório não conta como Airflow instalado, por isso airflow.sdk
pytest.importorskip("airflow.sdk", reason="Airflow 3 não instalado (roda no job airflow-dags do CI)")
pytestmark = pytest.mark.airflow

PASTA_DAGS = Path(__file__).resolve().parents[1] / "airflow" / "dags"
DAGS_ESPERADAS = {"dag_fundacao_smoke", "dag_nfe_saida"}


@pytest.fixture(scope="module")
def dagbag_e_avisos():
    from airflow.dag_processing.dagbag import DagBag

    with warnings.catch_warnings(record=True) as avisos:
        warnings.simplefilter("always")
        bag = DagBag(str(PASTA_DAGS))
    return bag, avisos


def test_nenhuma_dag_com_erro_de_import(dagbag_e_avisos):
    bag, _ = dagbag_e_avisos
    assert not bag.import_errors, bag.import_errors
    assert set(bag.dag_ids) == DAGS_ESPERADAS


def test_nenhuma_api_obsoleta_do_airflow(dagbag_e_avisos):
    _, avisos = dagbag_e_avisos
    obsoletas = sorted({str(a.message).splitlines()[0] for a in avisos
                        if "deprecated" in str(a.message).lower() and str(PASTA_DAGS) in str(a.filename)})
    assert not obsoletas, obsoletas


def test_dag_de_smoke_tem_as_tres_verificacoes_em_ordem(dagbag_e_avisos):
    bag, _ = dagbag_e_avisos
    dag = bag.dags["dag_fundacao_smoke"]   # get_dag() consulta o banco de metadados
    assert {t.task_id for t in dag.tasks} == {"postgres_schemas", "bucket_custodia", "dbt_debug"}
    assert dag.get_task("bucket_custodia").upstream_task_ids == {"postgres_schemas"}
    assert dag.get_task("dbt_debug").upstream_task_ids == {"bucket_custodia"}


def test_dag_de_carga_encadeia_as_etapas_do_lote(dagbag_e_avisos):
    bag, _ = dagbag_e_avisos
    dag = bag.dags["dag_nfe_saida"]
    ordem = ["aguardar_lote", "reservar_lote", "custodiar_lote", "carregar_bronze", "concluir_lote",
             "transformar_silver", "registrar_silver", "ha_mais_lotes", "proximo_lote"]
    assert {t.task_id for t in dag.tasks} == set(ordem)
    for anterior, atual in zip(ordem, ordem[1:]):
        assert dag.get_task(atual).upstream_task_ids == {anterior}
    assert dag.max_active_runs == 1          # um lote por vez: a reserva da pasta depende disso
    assert dag.get_task("custodiar_lote").retries == 2
    assert "path:models/silver" in dag.get_task("transformar_silver").bash_command


def test_dag_de_carga_roda_de_hora_em_hora(dagbag_e_avisos):
    """D024: agenda horária e sensor que cobre quase a hora inteira, em vez de */10 com execuções vazias."""
    bag, _ = dagbag_e_avisos
    dag = bag.dags["dag_nfe_saida"]
    sensor = dag.get_task("aguardar_lote")
    assert dag.timetable.expression in ("@hourly", "0 * * * *")
    assert sensor.mode == "reschedule" and sensor.soft_fail
    assert 50 * 60 <= sensor.timeout < 60 * 60


def test_falha_na_silver_nao_vira_erro_de_entrega(dagbag_e_avisos):
    """D019: a falha do dbt fica em erro_transformacao; o status CARREGADO/INCOMPLETO da entrega é preservado."""
    bag, _ = dagbag_e_avisos
    dag = bag.dags["dag_nfe_saida"]
    for task_id in ("transformar_silver", "registrar_silver"):
        assert dag.get_task(task_id).on_failure_callback[0].__name__ == "_marcar_erro_silver"
