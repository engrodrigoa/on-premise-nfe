# on-premise-nfe: tarefas comuns.  `make help`
SHELL := /bin/bash
COMPOSE := docker compose
AF := $(COMPOSE) exec -T airflow-scheduler
PG := $(COMPOSE) exec -T postgres

.PHONY: help env creds up down clean ps logs urls smoke teste-custodia gerar gerar-sujo carregar carregar-um lotes teste-bronze db-bootstrap psql test lint

DIAS ?= 1
ARGS ?=

help: ## lista os alvos
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## cria o .env com senhas aleatórias (mantém um .env existente)
	@test -f .env && echo ".env já existe" || ( \
	  cp .env.example .env && \
	  for k in DB_PASSWORD GRAFANA_DB_PASSWORD AIRFLOW_ADMIN_PASSWORD MINIO_ROOT_PASSWORD GRAFANA_ADMIN_PASSWORD; do \
	    v=$$(openssl rand -hex 12); sed -i.bak "s|^$$k=.*|$$k=$$v|" .env; done && \
	  sed -i.bak "s|^S3_SECRET_KEY=.*|S3_SECRET_KEY=$$(grep ^MINIO_ROOT_PASSWORD= .env | cut -d= -f2)|" .env && \
	  sed -i.bak "s|^AIRFLOW_UID=.*|AIRFLOW_UID=$$(id -u)|" .env && \
	  for k in AIRFLOW__API__SECRET_KEY AIRFLOW__API_AUTH__JWT_SECRET; do \
	    sed -i.bak "s|^$$k=.*|$$k=$$(openssl rand -hex 32)|" .env; done && \
	  sed -i.bak "s|^AIRFLOW__CORE__FERNET_KEY=.*|AIRFLOW__CORE__FERNET_KEY=$$(openssl rand -base64 32 | tr '+/' '-_')|" .env && \
	  rm -f .env.bak && echo ".env criado (senhas aleatórias)" && $(MAKE) --no-print-directory creds )

creds: ## logins DESTA máquina (lidos do .env)
	@test -f .env || { echo "sem .env: make env"; exit 1; }
	@v() { grep -E "^$$1=" .env | cut -d= -f2-; }; \
	 echo ""; \
	 echo "  Airflow       http://localhost:8080  $$(v AIRFLOW_ADMIN_USER) / $$(v AIRFLOW_ADMIN_PASSWORD)"; \
	 echo "  Grafana       http://localhost:3000  $$(v GRAFANA_ADMIN_USER) / $$(v GRAFANA_ADMIN_PASSWORD)"; \
	 echo "  S3 (console)  http://localhost:$$(v MINIO_CONSOLE_PORT | grep . || echo 9001)  $$(v MINIO_ROOT_USER) / $$(v MINIO_ROOT_PASSWORD)"; \
	 echo "  Postgres      localhost:$$(v DB_PORT)  $$(v DB_USER) / $$(v DB_PASSWORD)  (banco $$(v DB_DB))"; \
	 echo ""

up: env ## sobe o stack (build na primeira vez)
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory urls

down: ## para tudo (os dados ficam)
	$(COMPOSE) --profile tools down --remove-orphans

clean: ## para e APAGA todos os dados locais (Postgres, custódia, Grafana)
	$(COMPOSE) --profile tools down -v --remove-orphans
	rm -rf airflow/logs/dag_id=* airflow/logs/scheduler airflow/logs/dag_processor

ps: ## estado dos containers
	$(COMPOSE) ps

logs: ## acompanha scheduler e dag-processor
	$(COMPOSE) logs -f airflow-scheduler airflow-dag-processor

urls: creds

smoke: ## marco 1: confere Postgres, bucket da custódia e dbt por dentro do Airflow
	$(AF) airflow dags test dag_fundacao_smoke

teste-custodia: ## marco 1 (D003): versionamento e bloqueio de objeto no object store real
	$(AF) python -m pipelines.custodia.teste_lock

gerar: ## ERP simulado: entrega os próximos DIAS lotes (DIAS=1). Extra: ARGS="--incompleto 40"
	$(AF) python -m gerador.gen_nfe --dias $(DIAS) $(ARGS)

gerar-sujo: ## idem, com anomalias nas taxas padrão e gabarito em datasource/nfe/gabarito
	$(AF) python -m gerador.gen_nfe --dias $(DIAS) --sujo $(ARGS)

carregar: ## marco 3: liga a dag_nfe_saida e dispara; ela encadeia até esvaziar a inbox
	$(AF) airflow dags unpause dag_nfe_saida
	$(AF) airflow dags trigger dag_nfe_saida

carregar-um: ## carrega só o lote mais antigo, no terminal, sem encadear (depuração)
	$(AF) airflow dags test dag_nfe_saida -c '{"encadear": false}'

lotes: ## saúde da carga: os últimos lotes em ctrl.v_lote
	$(PG) psql -U $$(grep -E '^DB_USER=' .env | cut -d= -f2) -d $$(grep -E '^DB_DB=' .env | cut -d= -f2) -c \
	  "SELECT nome_lote, status, declarado_arquivos AS decl, qtd_arquivos AS receb, qtd_custodiados AS novos, \
	          qtd_ja_custodiados AS ja, qtd_faltantes AS falt, qtd_hash_divergente AS hash_div, qtd_bronze AS bronze, \
	          seg_total, mensagem FROM ctrl.v_lote ORDER BY lote_id DESC LIMIT 15"

teste-bronze: ## marco 3 (D003): a bronze é reconstruída do raw com a mesma contagem e o mesmo conteúdo?
	$(AF) python -m pipelines.carga.reconstruir_bronze

db-bootstrap: ## reaplica o DDL em um banco existente (idempotente)
	$(PG) bash /docker-entrypoint-initdb.d/00_bootstrap.sh

psql: ## abre o psql no banco do projeto
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

test: ## testes locais (pip install -r requirements-dev.txt)
	pytest

lint: ## ruff
	ruff check .
