# syntax=docker/dockerfile:1.6
FROM apache/airflow:3.4.0-python3.12

# Manter igual à tag do FROM: o pip resolve contra constraints-${AIRFLOW_VERSION}
ARG AIRFLOW_VERSION=3.4.0
ARG PYTHON_VERSION=3.12

# Rede instável (VPN, antivírus inspecionando HTTPS): tentar mais vezes em vez de falhar o build
ENV PIP_RETRIES=10 \
    PIP_DEFAULT_TIMEOUT=60

# Dependências das DAGs, resolvidas contra as constraints oficiais do Airflow: nunca sobem nem descem
# a versão de um pacote do qual o Airflow depende.
COPY requirements.txt /opt/airflow/requirements.txt
RUN pip install --no-cache-dir \
      "apache-airflow==${AIRFLOW_VERSION}" \
      -r /opt/airflow/requirements.txt \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# dbt em venv isolado (as versões dele conflitam com as do Airflow).
# g+rwX: os containers rodam com UID arbitrário e GID 0 (convenção da imagem do Airflow).
COPY requirements-dbt.txt /opt/airflow/requirements-dbt.txt
RUN python -m venv /opt/airflow/dbt_venv \
  && /opt/airflow/dbt_venv/bin/pip install --no-cache-dir -r /opt/airflow/requirements-dbt.txt \
  && chmod -R g+rwX /opt/airflow/dbt_venv
