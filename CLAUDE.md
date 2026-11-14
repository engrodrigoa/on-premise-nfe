# CLAUDE.md — on-premise-nfe

Contexto para desenvolvimento assistido por IA. Leia antes de alterar qualquer coisa. Estado atual e próximos passos: `docs/handoff.md`. O que o projeto é
está no `README.md`; o porquê de cada escolha está em `docs/decisoes.md`.

## O que é

Projeto de portfólio (dono: Rodrigo Arruda, engenheiro de dados): custódia e pipeline on-premise das NF-e
emitidas por uma empresa privada fictícia (Grupo Xis), escrito como o trabalho de um consultor. Dados 100%
sintéticos. Roda em uma máquina, com Docker Compose, no WSL2.

Todo o projeto é planejado em quatro linhas, e cada entrega traz as quatro juntas (`docs/02_fluxo.md`):

1. **Carga**: pasta de rede → raw (custódia) → bronze → silver → gold → dashboard
2. **Observabilidade**: a carga rodou e não perdeu nada? (`ctrl.lote`, `ctrl.integridade_custodia`)
3. **Qualidade**: o conteúdo é válido e os números fecham? (quarentena, `ctrl.dq`, `ctrl.reconciliacao`)
4. **Comprovação**: o painel mostra o número certo, com prova (selo lendo `ctrl.reconciliacao`)

## Stack (versões fixas; não atualizar como efeito colateral)

| Camada | Versão |
|---|---|
| Orquestração | Airflow 3.4.0, Python 3.12, LocalExecutor (api-server, scheduler, dag-processor, triggerer) |
| Transformação | dbt-core 1.11.15, dbt-postgres 1.11.0, venv isolado `/opt/airflow/dbt_venv`, chamado por `BashOperator` (D012) |
| Banco | Postgres 15 |
| Custódia | Silo (fork do MinIO), tag + digest, bucket `nfe-raw` com versionamento e bloqueio de objeto (D003) |
| Dashboard | Grafana 12.0.0, painel como código |

A versão do Airflow aparece em três lugares que precisam andar juntos: `Dockerfile` (FROM e ARG),
`docker-compose.yml` (tag da imagem) e `.github/workflows/ci.yml`. `tests/test_compose.py` trava isso.

## Mapa do repositório

- `airflow/dags/` — DAGs. Importam de `airflow.sdk` (Airflow 3). Sem lógica de negócio: chamam `pipelines/` ou o dbt
- `pipelines/commons/` — `config.py` (única leitura de variáveis de ambiente), `db.py`, `s3.py`
- `pipelines/custodia/` — bucket, inventário e testes da custódia
- `pipelines/qualidade/` — registro da silver em `ctrl` (`registrar.py`) e conferência com o gabarito (`conferir_silver.py`, o único código que lê o gabarito)
- `dbt/` — projeto dbt; perfil lê as variáveis `DB_*`. Silver recriada inteira a cada carga (D021); regras fiscais e catálogo de regras de qualidade em `dbt/seeds`; contratos em `models/silver/_silver.yml`
- `gerador/` — sistema de origem simulado: `gen_nfe.py` (um lote por dia, com estado) e `referencias.py` (cenário e regras fiscais a validar)
- `infra/postgres/init/` — bootstrap + DDL idempotente (fonte única das tabelas que não são do dbt)
- `grafana/` — provisionamento e painéis como código: saúde da carga (linha 2) e qualidade do conteúdo (linha 3)
- `tests/` — sem stack: `pytest`. Com banco: defina `DB_HOST`. Com Airflow instalado: `tests/test_dag_integrity.py`
- `docs/` — escopo, fluxo, cenário, arquitetura, dashboard, decisões, roadmap

## Regras

- **Idioma:** documentação, comentários, nomes de tabelas e colunas em português do Brasil. Termos técnicos
  consagrados ficam em inglês (lote, mas `bucket`, `object lock`, `merge`).
- **Decisões:** mudou um desenho? Nova entrada em `docs/decisoes.md` (D0NN). Entradas antigas não são reescritas;
  no máximo ganham uma nota apontando para a que as revê.
- **Marcos:** um PR por marco (`docs/roadmap.md`), com o critério de pronto verificado com números na descrição.
- **Custódia:** `custodia.documento` e o bucket são só INSERT/PUT. Nada no código apaga ou altera um objeto custodiado.
- **Bronze:** só INSERT, sem regra de negócio. Reconstruível a partir do raw.
- **Erro x aviso (D007):** só vai para a quarentena o que não é um documento íntegro do grupo. Inconsistência
  fiscal é aviso, e a nota segue.
- **Regra fiscal (D008):** nunca no código. Fica em seed do dbt, com vigência, marcada "a validar".
- **Configuração:** em `pipelines/` e nas DAGs, ninguém lê `os.environ` fora de `pipelines/commons/config.py`. O `gerador/` é um sistema externo simulado e não importa nada de `pipelines/`. Segredo só no `.env`.
- **DDL:** idempotente (`IF NOT EXISTS`, `CREATE OR REPLACE`), aplicado duas vezes no CI.
- **Dado pessoal:** CPF, nome e endereço de pessoa física não chegam à gold nem ao painel (CPF vira hash).
- **Commits e PRs:** sem link de sessão do Claude.

## Como verificar uma mudança

```bash
ruff check . && pytest                         # sem stack
DB_HOST=localhost DB_USER=... pytest tests/test_ddl.py   # com um Postgres com o bootstrap aplicado
make up && make smoke && make teste-custodia   # no stack
make silver && make teste-silver               # marco 4: silver e critério de pronto contra o gabarito
```
