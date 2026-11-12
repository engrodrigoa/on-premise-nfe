import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))


@pytest.fixture
def env_projeto(monkeypatch):
    """Variáveis mínimas para pipelines.commons.config.carregar() nos testes sem stack."""
    valores = {
        "DB_USER": "nfe", "DB_PASSWORD": "x", "DB_DB": "nfe",
        "S3_ENDPOINT": "http://localhost:9000", "S3_ACCESS_KEY": "teste", "S3_SECRET_KEY": "teste",
        "NFE_BUCKET_RAW": "nfe-raw", "CUSTODIA_LOCK_MODE": "GOVERNANCE", "CUSTODIA_RETENCAO_DIAS": "2190",
    }
    for chave, valor in valores.items():
        monkeypatch.setenv(chave, valor)
    return valores


@pytest.fixture
def conexao_db():
    """Postgres com o bootstrap aplicado. Sem DB_HOST definido (ou sem banco no ar), o teste é pulado."""
    if not os.environ.get("DB_HOST"):
        pytest.skip("DB_HOST não definido: teste de banco pulado")
    import psycopg2

    try:
        conn = psycopg2.connect(
            host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"), user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"], dbname=os.environ["DB_DB"], connect_timeout=5,
        )
    except psycopg2.OperationalError as exc:
        pytest.skip(f"Postgres indisponível: {exc}")
    yield conn
    conn.rollback()   # os testes de banco nunca deixam dado para trás
    conn.close()
