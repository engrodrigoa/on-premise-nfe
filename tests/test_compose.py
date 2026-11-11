"""O docker-compose.yml é a fundação: estes testes travam as decisões que ele carrega."""
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICOS = COMPOSE["services"]
AIRFLOW = ["airflow-init", "airflow-apiserver", "airflow-scheduler", "airflow-dag-processor", "airflow-triggerer"]


def test_servicos_esperados():
    assert set(SERVICOS) == {"postgres", "minio", "grafana", "adminer", *AIRFLOW}


def test_imagens_com_versao_fixa():
    for nome, servico in SERVICOS.items():
        imagem = servico.get("image", "")
        assert ":" in imagem and not imagem.endswith(":latest"), f"{nome}: imagem sem versão fixa ({imagem})"
    assert "@sha256:" in SERVICOS["minio"]["image"], "object store da custódia fixado por digest"


def test_versao_do_airflow_igual_no_compose_dockerfile_e_ci():
    dockerfile = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    ci = (RAIZ / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "FROM apache/airflow:3.4.0-python3.12" in dockerfile
    assert "ARG AIRFLOW_VERSION=3.4.0" in dockerfile
    assert SERVICOS["airflow-scheduler"]["image"] == "nfe_onprem/airflow:3.4.0-local"
    assert 'AIRFLOW_VERSION: "3.4.0"' in ci


def test_portas_padrao():
    """Decisão D011: portas padrão, porque o stack não roda junto com o datalabs."""
    def portas(nome):
        return [str(p) for p in SERVICOS[nome].get("ports", [])]

    assert portas("airflow-apiserver") == ["8080:8080"]
    assert portas("grafana") == ["3000:3000"]
    assert portas("minio") == ["9000:9000", "9001:9001"]
    assert portas("postgres") == ["${DB_PORT:-5432}:5432"]


def test_todo_servico_do_airflow_compartilha_os_segredos():
    """Sem o mesmo JWT em todos os containers, toda task falha ao chamar a Execution API."""
    for nome in AIRFLOW:
        ambiente = SERVICOS[nome]["environment"]
        for chave in ("AIRFLOW__API_AUTH__JWT_SECRET", "AIRFLOW__API__SECRET_KEY",
                      "AIRFLOW__CORE__EXECUTION_API_SERVER_URL"):
            assert chave in ambiente, f"{nome} sem {chave}"


def test_env_example_cobre_as_variaveis_do_compose():
    import re

    texto = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")
    # $${...} é variável do container, não do .env
    usadas = set(re.findall(r"(?<!\$)\$\{([A-Z0-9_]+)", texto))
    definidas = {linha.split("=", 1)[0] for linha in (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines()
                 if linha and not linha.startswith("#") and "=" in linha}
    assert usadas - definidas == set(), f"variáveis do compose ausentes no .env.example: {usadas - definidas}"
