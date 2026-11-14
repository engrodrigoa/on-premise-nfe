# Handoff — estado em 13/11/2026

Leia primeiro o `CLAUDE.md` (regras), depois este arquivo. O porquê das escolhas está em `docs/decisoes.md` (D001–D018).

## Onde paramos

| Marco | Status | PR |
|---|---|---|
| 0. Planejamento | concluído | — |
| 1. Fundação (compose, Airflow 3.4.0, Silo, Postgres, Grafana) | concluído | #1 |
| 2. Origem (gerador com estado, lote diário, romaneio sha256) | concluído | #2 |
| 3. Raw e bronze (`dag_nfe_saida`) | concluído | #3 (entrou via #2) |
| 3a. Painel de saúde da carga (Grafana) | concluído | #4, #5 |
| **4. Silver (dbt)** | **próximo** | — |
| 5. Gold + `ctrl.reconciliacao` · 6. Página de negócio · 7. Operação | a fazer | — |

`main` em `c71dd8d`. Nenhum PR aberto. A branch remota `feat/painel-saude-carga` sobrou e pode ser apagada.

## Validado no ambiente do dono (WSL2 + Docker Desktop, máquina `odyssey`, repo em `~/lambda4/xis_nfe`)

- Stack sobe; `make smoke` OK; `make teste-custodia` 4 × PASSOU: **object lock funciona no Silo em nó único**.
- 4 lotes carregados: 01-02, 01-03, 01-04 `CARREGADO`; 01-05 `INCOMPLETO` (5 faltantes). `make teste-bronze` 3 × PASSOU.
- Silo aceita PUT com `ChecksumSHA256` em bucket com lock. Encadeamento `proximo_lote` funciona no scheduler.
- Painel "NF-e · Saúde da carga" renderizado no Grafana 12 conforme esperado.

## Fluxo atual

```
inbox/lote_X ─► aguardar (sensor) ─► reservar ─► custodiar ─► carregar_bronze ─► concluir ─► há mais? ─► próximo run
                                    RECEBIDO    CUSTODIADO    CARREGADO|INCOMPLETO   processados/
```

Lógica em `pipelines/carga/lote.py`; DAG só encadeia. Falha final → `ERRO` (callback); a próxima execução retoma o que ficou em `processando/`.

## Marco 4 — o que fazer

1. Modelos dbt `silver`: nota, item, evento, situação da nota (cancelamento que chega depois muda a situação), a partir de `bronze.documento_xml` com `XMLTABLE`.
2. Quarentena: só o que não é documento íntegro do grupo (D007). Inconsistência fiscal = aviso em `ctrl.dq`, nota segue.
3. Regras fiscais em seed com vigência, "a validar" (D008). Nunca no código.
4. Task(s) dbt na `dag_nfe_saida` depois de `carregar_bronze` (BashOperator, venv isolado, D012).
5. Critério de pronto: bronze = válidos + quarentena; gabarito do gerador 100%; cancelamento tardio muda a situação.
6. Primeiro commit: marcar marcos 1–3a como "concluído" no `docs/roadmap.md`.

## Pendências registradas (`docs/roadmap.md`)

- Agenda `*/10` gera ~140 execuções vazias/dia → trocar no marco 4 (horária ou disparo por chegada).
- Como a origem completa um lote `INCOMPLETO` (a 2ª entrega do mesmo lote é recusada, D016) → runbook, marco 7.
- Painel mostra `NUNCA VERIFICADA` até a `dag_custodia_integridade` (marco 7).

## Armadilhas aprendidas

- **Porta 9001 reservada pelo Windows** na máquina do dono: `.env` usa `MINIO_CONSOLE_PORT=9002` (D015).
- **Trocar de branch quebra os bind mounts** no Docker Desktop/WSL (o Git recria as pastas): depois de `git switch`, `docker compose restart airflow-dag-processor airflow-scheduler airflow-triggerer airflow-apiserver grafana`.
- **Volume existente não recebe DDL novo**: após mudar `infra/postgres/init/`, rodar `make db-bootstrap` (idempotente).
- **PRs: um por vez, direto contra a `main`.** Empilhar quebrou: `gh pr merge --delete-branch` apaga a base e o GitHub fecha o PR de cima. Só dizer "pode fazer o merge" depois do último commit (o #4 foi mergeado pela metade).
- Proxy do ambiente de nuvem: GraphQL bloqueado (usar `gh api` REST), sem escrita em refs/settings via API, sem Docker nem download do Grafana. Validação visual e do stack real é sempre no WSL.
- Testes com banco: `DB_HOST=... pytest`; o `pyproject` usa `-q` duplo, então o resumo final não aparece (só os pontos).

## Comandos do dia a dia

```bash
make up | make down | make ps
make gerar DIAS=1 [ARGS="--incompleto 5"] | make gerar-sujo DIAS=3
make carregar        # liga e dispara a dag_nfe_saida (encadeia até esvaziar a inbox)
make carregar-um     # um lote, no terminal, sem encadear
make lotes           # ctrl.v_lote
make teste-bronze    # reconstrução da bronze a partir do raw
make db-bootstrap    # reaplica o DDL
```

## Preferências do dono para a sessão

Português do Brasil, direto, sem bajulação; mostrar o passo a passo e o raciocínio; mapas em caixas/faixas; contrapor quando a sugestão desvia (como na D018). Commits e PRs **sem link de sessão do Claude**. Ele faz os merges.
