"""Bucket da custódia contra um S3 emulado (moto). O teste contra o Silo real é `make teste-custodia`."""
import boto3
import pytest
from moto import mock_aws

from pipelines.commons import config
from pipelines.custodia import setup_bucket, teste_lock


@pytest.fixture
def s3():
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t")


def test_cria_bucket_com_versionamento_e_bloqueio(s3, env_projeto):
    final = setup_bucket.preparar(s3, config.carregar())
    assert final["criado_agora"] is True
    assert final["versionamento"] == "Enabled"
    assert final["lock"] == {"ligado": True, "modo": "GOVERNANCE", "dias": 2190}


def test_segunda_execucao_nao_recria(s3, env_projeto):
    cfg = config.carregar()
    setup_bucket.preparar(s3, cfg)
    s3.put_object(Bucket=cfg.bucket_raw, Key="nfe/a.xml", Body=b"<a/>")
    final = setup_bucket.preparar(s3, cfg)
    assert final["criado_agora"] is False
    assert s3.get_object(Bucket=cfg.bucket_raw, Key="nfe/a.xml")["Body"].read() == b"<a/>"


def test_mudanca_de_retencao_e_aplicada(s3, env_projeto, monkeypatch):
    setup_bucket.preparar(s3, config.carregar())
    monkeypatch.setenv("CUSTODIA_RETENCAO_DIAS", "3650")
    final = setup_bucket.preparar(s3, config.carregar())
    assert final["lock"]["dias"] == 3650


def test_bucket_preexistente_sem_bloqueio_ganha_versionamento(s3, env_projeto):
    """Bucket criado fora do script (sem lock): o script liga o versionamento e reporta lock desligado."""
    cfg = config.carregar()
    s3.create_bucket(Bucket=cfg.bucket_raw)
    final = setup_bucket.preparar(s3, cfg)
    assert final["versionamento"] == "Enabled"
    assert final["lock"]["ligado"] is False


def test_objeto_gravado_herda_a_retencao_padrao(s3, env_projeto):
    cfg = config.carregar()
    setup_bucket.preparar(s3, cfg)
    s3.put_object(Bucket=cfg.bucket_raw, Key="nfe/a.xml", Body=b"<a/>")
    cabecalho = s3.head_object(Bucket=cfg.bucket_raw, Key="nfe/a.xml")
    assert cabecalho["ObjectLockMode"] == "GOVERNANCE"
    assert cabecalho["ObjectLockRetainUntilDate"] is not None


def test_roteiro_de_teste_do_lock_passa_e_limpa(s3):
    resultados = teste_lock.executar(s3, bucket="nfe-teste-lock-unit")
    assert [nome for nome, _, _ in resultados] == [
        "bucket com bloqueio de objeto", "versionamento", "bloqueio", "exclusão comum"]
    assert all(ok for _, ok, _ in resultados), resultados
    assert "nfe-teste-lock-unit" not in [b["Name"] for b in s3.list_buckets()["Buckets"]]
