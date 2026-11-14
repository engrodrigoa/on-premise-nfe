# Handoff — estado em 13/11/2026 (fim da sessão do marco 4)

Leia primeiro o `CLAUDE.md` (regras), depois este arquivo. O porquê das escolhas está em `docs/decisoes.md` (D001–D025).

## Onde paramos

| Marco | Status | PR |
|---|---|---|
| 0 a 3a | concluídos | #1 a #5 |
| **4. Silver (dbt)** | **implementado; falta a validação no WSL** | branch `feat/silver` |
| 5. Gold + `ctrl.reconciliacao` · 6. Página de negócio · 7. Operação | a fazer | — |

A branch remota `feat/painel-saude-carga` já não existia. PRs: um por vez, direto contra a `main`.

## O que o marco 4 entregou

```
bronze.documento_xml ─► stg_documento ─► documento ─┬─► valido ─► nfe_nota · nfe_item · nfe_evento ─► nfe_situacao
                        (identifica)     (destino)  │                                               └► nfe_aviso
                                                    ├─► quarentena (motivo)
                                                    └─► reenvio_descartado
                      dbt build (transformar_silver) ─► registrar_silver ─► ctrl.lote · ctrl.dq
```

- DAG: `… carregar_bronze → concluir_lote → transformar_silver → registrar_silver → ha_mais_lotes → proximo_lote`, `@hourly` (D024).
- Decisões novas: D019 (falha da silver em `erro_transformacao`), D020 (fechamento com reenvios), D021 (silver
  recriada a cada carga), D022 (mesma chave: vale o primeiro), D023 (INCOMPLETO segue), D024 (agenda), D025
  (página "Qualidade do conteúdo").
- Comandos novos: `make silver`, `make teste-silver`, `make demo`.

## Verificado nesta sessão (fora do Docker)

Postgres 16 local, S3 emulado (moto) e as funções reais de `pipelines/carga/lote.py`; dbt 1.11.15; Airflow 3.4.0
com as constraints oficiais.

- `dbt build` da silver: 39/39 (seeds, 8 modelos com contrato, testes de coluna e de fechamento).
- 18 lotes sujos com taxas altas: gabarito 120/120, zero falso positivo, 7/7 cancelamentos tardios; saltos
  temporários explicados por nota pendente e resolvidos quando ela chegou; histórico em `ctrl.dq` mostrando o
  `evento_sem_nota` saindo da quarentena.
- `make demo` reproduzido num banco zerado (7 dias, `--sujo` padrão): 484 = 474 + 2 + 8; 29/29; 1/1.
- `airflow dags test dag_nfe_saida`: cadeia completa verde; com `DBT_BIN=/bin/false`, lote fica `CARREGADO` com
  `erro_transformacao`; o registro recusa silver velha; o sucesso seguinte limpa.
- `ruff`, `pytest` com banco (todos) e `tests/test_dag_integrity.py` no Airflow 3.4.0 (6/6).

## Validação no WSL (13/11, primeira rodada)

- `make silver && make teste-silver` nos 4 lotes antigos: 39/39 no dbt; 200 = 197 + 1 + 2; gabarito 12/12;
  cancelamento tardio `INCONCLUSIVO` (nenhum caso ainda).
- A DAG rodou de ponta a ponta no Airflow real (`transformar_silver` ~8 s, run de ~20 s).
- Dois achados, já corrigidos na branch:
  1. `make carregar` só disparava a DAG; o `teste-silver` encadeado olhava a silver antiga. Agora `carregar`
     espera a carga chegar à silver, e a conferência devolve `INCONCLUSIVO` se houver carga em andamento.
  2. `FALHOU` com 1 `numeracao_com_salto` sem gabarito: o lote 05/01 (`--incompleto 5`) não entregou os nº
     48268 a 48272 da série GO-importadora; quando o 48273 chegou, o buraco apareceu. O aviso está certo (falta
     nota sob custódia); o gabarito só registra `arquivo_nao_entregue`. A conferência agora aceita salto cujo
     buraco é `FALTANTE` em `ctrl.lote_divergencia`, e o detalhe do aviso cita o lote e os números. Reproduzido
     aqui com a mesma sequência de comandos: mesmos lotes byte a byte, mesma chave, e 41/41 depois da correção.

## Falta (validação do dono, no WSL)

1. `git switch feat/silver`, depois `docker compose restart airflow-dag-processor airflow-scheduler airflow-triggerer airflow-apiserver grafana` (bind mounts).
2. `make db-bootstrap` (colunas novas em `ctrl.lote` e a view).
3. `git pull` na `feat/silver`, `make silver && make teste-silver` (deve passar com 1 salto explicado por arquivo não entregue); depois `make gerar-sujo DIAS=5 && make carregar && make teste-silver`.
4. Conferir as duas páginas do Grafana e o grafo da DAG; capturas em `docs/img/` (pontos marcados em comentário no README).
5. Números no PR; marco 4 → "concluído" no roadmap.

Os lotes antigos (carregados antes da silver) aparecem como "Falhas de processo" no painel de saúde até o
passo 3 (D019). Se a suíte com banco já rodou contra o banco do stack, há lotes com ano 2100-9999: viram reenvios
na silver e a conferência os ignora (pendência no roadmap).

## Marco 5 — o que fazer

1. Gold: dimensões, `ft_nfe_documento`, `ft_nfe_item`, `ag_fiscal_mensal` (cancelada fora do total, devolução com sinal negativo). CPF só como hash.
2. `ctrl.reconciliacao`: quatro métricas (qtd, `vNF`, CBS, IBS) em cada fronteira, da origem à gold.
3. Lote `PROCESSADO` e `processado_em` só depois da reconciliação.
4. Task(s) dbt da gold na DAG, no mesmo padrão da silver (BashOperator + registro em Python).

## Armadilhas aprendidas

- **Porta 9001 reservada pelo Windows**: `.env` usa `MINIO_CONSOLE_PORT=9002` (D015).
- **Trocar de branch quebra os bind mounts** no Docker Desktop/WSL: reiniciar os serviços (passo 1 acima).
- **Volume existente não recebe DDL novo**: `make db-bootstrap` (idempotente).
- **PRs: um por vez, direto contra a `main`.** Só dizer "pode fazer o merge" depois do último commit.
- `make -n` com alvos que chamam `$(MAKE)` executa o alvo recursivo de verdade (o `env` cria o `.env`).
- `docker compose up --wait` foi evitado no `make demo`: o `airflow-init` termina de propósito; a espera é pelo
  healthcheck do scheduler e pela DAG registrada (`make aguardar-airflow`).
- No Airflow 3.4, a agenda `@hourly` aparece como `CronTriggerTimetable` com `expression == "0 * * * *"`.
- Proxy do ambiente de nuvem: GraphQL bloqueado (usar `gh api` REST), sem Docker. Validação do stack real é sempre no WSL.

## Comandos do dia a dia

```bash
make up | make down | make ps
make demo                                   # roteiro completo para quem clonou
make gerar DIAS=1 [ARGS="--incompleto 5"] | make gerar-sujo DIAS=3
make carregar        # liga e dispara a dag_nfe_saida (encadeia até esvaziar a inbox)
make lotes           # ctrl.v_lote, com a silver
make silver          # dbt da silver + registro em ctrl, sem a DAG
make teste-bronze | make teste-silver
make db-bootstrap    # reaplica o DDL
```

## Preferências do dono para a sessão

Português do Brasil, direto, sem bajulação; mostrar o passo a passo e o raciocínio; mapas em caixas/faixas;
entrevista nos pontos de direção; contrapor quando a sugestão desvia. Código no Opus; ações no GitHub (push, PR)
num agente Sonnet. Commits e PRs **sem link de sessão do Claude**. Ele faz os merges.
