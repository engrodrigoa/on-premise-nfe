# Roadmap

Cada marco entrega uma etapa do fluxo **com as suas linhas juntas**: a carga, o controle de observabilidade e
as regras de qualidade daquela etapa. Um marco só fecha quando o critério de pronto é verificado com números.

| Marco | Entrega | Critério de pronto | Status |
|---|---|---|---|
| **0. Planejamento** | escopo, mapa das quatro linhas, cenário, arquitetura, decisões | documentos revisados e perguntas em aberto respondidas | concluído |
| **1. Fundação** | `docker compose` (Postgres, Silo, Airflow, Grafana); bucket `nfe-raw`; schemas e tabelas `ctrl` e `custodia` | stack sobe do zero com um comando; teste de versionamento e bloqueio de objeto registrado em D003 | em validação |
| **2. Origem** | gerador do Grupo Xis: 5 CNPJs, 15 UFs, 6 operações, cancelamentos, arquivo de controle com totais, manifesto | notas válidas no XSD; soma do controle = soma dos XMLs; manifesto lista toda anomalia injetada | em validação |
| **3. Raw e bronze** | `dag_nfe_saida` até a bronze; custódia com inventário; `ctrl.lote` | lote completo → `CARREGADO`; lote pela metade → `INCOMPLETO`; reenvio preservado; bronze reconstruída do raw com a mesma contagem | em validação |
| **3a. Saúde da carga** | painel de operação no Grafana sobre `ctrl` e `custodia` (D017) | cenário plantado no banco (erro, lote preso, incompleto, arquivo perdido, custódia divergente) acusado pelo painel com a role `grafana_ro`; consultas na suíte de testes | em validação |
| **4. Silver** | modelos dbt de nota, item, evento e situação; quarentena; regras de aviso em `ctrl.dq` | bronze = válidos + quarentena; gabarito 100%; cancelamento que chega depois muda a situação da nota | a fazer |
| **5. Gold** | dimensões, fatos, agregado mensal; `ctrl.reconciliacao` | quatro métricas iguais da origem à gold; cancelada fora do total; devolução com sinal negativo | a fazer |
| **6. Dashboard** | página única em Grafana, como código, com o selo | indicadores do topo iguais ao recálculo independente na silver; consultas na suíte de testes | a fazer |
| **7. Operação** | `dag_custodia_integridade`; teste de restauração; runbook; política de retenção e de dado pessoal; roteiro de demonstração de incidentes | arquivo alterado no bucket é detectado; os três incidentes do roteiro são apanhados pelo controle certo | a fazer |

## Roteiro de demonstração (marco 7)

Três incidentes injetados ao vivo, cada um apanhado por uma linha diferente:

| Incidente | Linha que apanha | Evidência |
|---|---|---|
| Lote entregue com 40 arquivos a menos (`make gerar ARGS="--incompleto 40"`) | 2 | `ctrl.lote` = `INCOMPLETO`; selo do painel |
| XML com total de CBS divergente e emitente estranho no mesmo lote | 3 | um aviso em `ctrl.dq`, um arquivo na quarentena; painel segue conferido |
| Objeto alterado no bucket depois da carga | 2 | `ctrl.integridade_custodia` acusa o hash; versão anterior restaurada |

## Pendências registradas

| Item | Origem | Onde resolver |
|---|---|---|
| Como a origem completa um lote `INCOMPLETO`: a segunda entrega do mesmo lote é recusada (D016), então os arquivos faltantes precisam de um caminho próprio | marco 3 | runbook (marco 7) |
| Agenda de 10 em 10 minutos gera cerca de 140 execuções vazias por dia; avaliar agenda horária ou disparo por chegada de lote | validação do marco 3 | marco 4 |
| Painel de saúde mostra `NUNCA VERIFICADA` até existir a `dag_custodia_integridade` | painel de saúde | marco 7 |

## Depois do MVP

1. Notas recebidas via distribuição de documentos, para tratar crédito de IBS e CBS.
2. Demais eventos (CC-e, inutilização, manifestação).
3. Massa de 2027, com a virada de alíquotas pela tabela de vigência.
4. Logs e métricas de plataforma (Loki, Prometheus) como perfil opcional.
