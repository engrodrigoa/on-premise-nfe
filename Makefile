# on-premise-nfe: tarefas comuns.  `make help`
SHELL := /bin/bash
COMPOSE := docker compose
AF := $(COMPOSE) exec -T airflow-scheduler
PG := $(COMPOSE) exec -T postgres

.PHONY: help env creds up down clean ps logs urls smoke teste-custodia gerar gerar-sujo carregar carregar-um lotes teste-bronze silver teste-silver demo aguardar-airflow db-bootstrap psql test lint

DIAS ?= 1
ARGS ?=
DIAS_DEMO ?= 7
DBT := "$$DBT_BIN" build --select +path:models/silver --project-dir "$$DBT_PROJECT_DIR" --profiles-dir "$$DBT_PROFILES_DIR"

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
	          qtd_validos AS validos, qtd_quarentena AS quar, qtd_reenvios AS reenv, silver_fechada AS fecha, \
	          seg_total, coalesce(erro_transformacao, mensagem) AS mensagem \
	     FROM ctrl.v_lote ORDER BY lote_id DESC LIMIT 15"

teste-bronze: ## marco 3 (D003): a bronze é reconstruída do raw com a mesma contagem e o mesmo conteúdo?
	$(AF) python -m pipelines.carga.reconstruir_bronze

silver: ## marco 4: roda a silver (dbt) e o registro em ctrl, sem a DAG (lotes já na bronze)
	$(AF) bash -c '$(DBT)'
	$(AF) python -m pipelines.qualidade.registrar

teste-silver: ## marco 4: confere a silver com o gabarito do gerador (PASSOU/FALHOU por critério)
	$(AF) python -m pipelines.qualidade.conferir_silver

demo: ## roteiro completo para quem clonou: sobe, gera DIAS_DEMO dias sujos, carrega pela DAG e confere
	@$(MAKE) --no-print-directory env
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory aguardar-airflow
	@$(MAKE) --no-print-directory gerar-sujo DIAS=$(DIAS_DEMO)
	@$(MAKE) --no-print-directory carregar
	$(AF) python -m pipelines.carga.aguardar --timeout 1800
	@$(MAKE) --no-print-directory lotes
	@$(MAKE) --no-print-directory teste-silver
	@echo "Painéis: Grafana > pasta NF-e > 'Saúde da carga' e 'Qualidade do conteúdo'"
	@$(MAKE) --no-print-directory creds

aguardar-airflow: # interno: scheduler saudável e dag_nfe_saida já registrada (até 5 min)
	@echo "aguardando o Airflow (o primeiro build leva alguns minutos)..."
	@for i in $$(seq 1 60); do \
	   [ "$$($(COMPOSE) ps --format '{{.Health}}' airflow-scheduler 2>/dev/null)" = healthy ] && \
	   $(AF) airflow dags details dag_nfe_saida >/dev/null 2>&1 && exit 0; sleep 5; done; \
	 echo "o Airflow não ficou pronto em 5 min: make ps e make logs"; exit 1

db-bootstrap: ## reaplica o DDL em um banco existente (idempotente)
	$(PG) bash /docker-entrypoint-initdb.d/00_bootstrap.sh

psql: ## abre o psql no banco do projeto
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

test: ## testes locais (pip install -r requirements-dev.txt)
	pytest

lint: ## ruff
	ruff check .
