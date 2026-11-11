#!/usr/bin/env bash
# Roda uma vez, com o diretório de dados VAZIO (docker-entrypoint-initdb.d).
# Em um volume já existente: make db-bootstrap (tudo aqui é idempotente).
set -e  # sem -u: se o arquivo perder o +x (checkout no Windows) o entrypoint faz source dele

if [ -z "${GRAFANA_DB_PASSWORD:-}" ]; then echo "GRAFANA_DB_PASSWORD precisa estar definida" >&2; exit 1; fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
  -- metadados do Airflow em banco próprio, separado dos dados do projeto
  SELECT 'CREATE DATABASE airflow OWNER "$POSTGRES_USER"'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow')\gexec

  -- role somente leitura do Grafana (menor privilégio)
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'grafana_ro') THEN
      CREATE ROLE grafana_ro LOGIN PASSWORD '${GRAFANA_DB_PASSWORD}';
    ELSE
      ALTER ROLE grafana_ro PASSWORD '${GRAFANA_DB_PASSWORD}';
    END IF;
  END
  \$\$;
EOSQL

# schemas e tabelas que não são do dbt (fonte única: estes arquivos)
for f in "${INIT_SQL_DIR:-/docker-entrypoint-initdb.d/sql}"/*.sql; do
  echo "aplicando $f"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f"
done

# O painel lê só gold e controle (decisão D009). Silver e bronze ficam fora: têm dado pessoal completo.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO grafana_ro;
  DO \$\$
  DECLARE s text;
  BEGIN
    FOREACH s IN ARRAY ARRAY['gold','ctrl','custodia'] LOOP
      EXECUTE format('GRANT USAGE ON SCHEMA %I TO grafana_ro', s);
      EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO grafana_ro', s);
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO grafana_ro', current_user, s);
    END LOOP;
  END
  \$\$;
EOSQL
