# Arquitetura

Desenho alvo. O ambiente e as tabelas de controle existem desde o marco 1; o restante é a especificação dos
marcos 2 a 7.

## 1. Ambiente

Um `docker compose` próprio (projeto `nfe_onprem`), com versões fixas e portas padrão. Não roda ao mesmo tempo
que o datalabs (decisões D001 e D011).

| Serviço | Versão | Porta | Papel |
|---|---|---|---|
| `postgres` | Postgres 15 | 5432 | bronze, silver, gold, controle e metadados do Airflow (banco `airflow`, separado) |
| `minio` | Silo, fixado por tag e digest (o mesmo do datalabs) | 9000 (API S3), 9001 (console) | custódia do XML bruto |
| `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor`, `airflow-triggerer` | Airflow 3.4.0, Python 3.12, LocalExecutor | 8080 | orquestração |
| `airflow-init` | mesma imagem | — | migra os metadados, cria o admin e prepara o bucket da custódia; roda e termina |
| `grafana` | Grafana 12.0.0 | 3000 | dashboard de 1 página |
| `adminer` (perfil `tools`) | 4.8.1 | 8081 | consulta ao banco pelo navegador |

O dbt (dbt-core 1.11.15, dbt-postgres 1.11.0) fica em um venv isolado dentro da imagem do Airflow e é chamado
por linha de comando (D012).

Loki, Promtail, Prometheus e statsd-exporter, que o datalabs usa para logs e métricas de plataforma, ficam
fora do MVP. As linhas 2 e 3 deste projeto são respondidas por tabelas de controle no Postgres. Entram depois,
como perfil opcional do compose, se a operação pedir.

## 2. Camadas

| Camada | Onde | Objetos | Regra |
|---|---|---|---|
| **Origem** | pasta `datasource/nfe/` | `inbox/`, `processando/<lote>/`, `processados/<lote>/` | passagem; nada é lido daqui depois da custódia |
| **Raw (custódia)** | object store | bucket `nfe-raw` | imutável; versionado; retenção; a cópia com valor legal |
| **Bronze** | Postgres `bronze` | `documento_xml` | XML bruto de notas e eventos, 1 linha por arquivo, só INSERT; reconstruível do raw (D013) |
| **Silver** | Postgres `silver` | `nfe_nota`, `nfe_item`, `nfe_evento`, `nfe_situacao`, `nfe_quarentena` | tipada, com contrato; 1 linha por chave |
| **Gold** | Postgres `gold` | dimensões, `ft_nfe_item`, `ft_nfe_documento`, `ag_fiscal_mensal` | modelo dimensional para consumo |
| **Controle** | Postgres `ctrl` e `custodia` | ver seção 4 | o que as linhas 2 e 3 gravam |

## 3. Custódia

A parte que não existe nos projetos anteriores e que justifica este.

**Chave do objeto.** `<tipo>/<cnpj_emitente>/<aaaa>/<mm>/<chave_acesso>-<sha256[:12]>.xml`, com `tipo` em
`nfe` ou `evento`. Arquivo ilegível, do qual não se extrai chave, vai para `invalido/<lote>/`. O trecho do hash no nome impede que um reenvio com conteúdo diferente sobrescreva o
anterior: são dois objetos, e o inventário mostra os dois.

**Inventário.** `custodia.documento`, 1 linha por objeto gravado:

| Coluna | Para quê |
|---|---|
| `chave_acesso`, `tipo_documento`, `cnpj_emitente` | localizar o documento de uma nota |
| `sha256`, `tamanho_bytes` | provar que o conteúdo é o mesmo que entrou |
| `bucket`, `objeto`, `version_id` | endereço exato no object store |
| `lote_id`, `arquivo_origem`, `custodiado_em` | de qual entrega veio |
| `reter_ate` | data calculada pela política de retenção |

**Imutabilidade.** Bucket com versionamento e bloqueio de objeto (object lock) em modo de retenção. O
comportamento do bloqueio no Silo em nó único é a primeira coisa a testar no marco 1; a alternativa, se não
funcionar, está em D003.

**Verificação de integridade.** Uma DAG separada (`dag_custodia_integridade`) sorteia objetos, baixa,
recalcula o sha256 e compara com o inventário. Resultado em `ctrl.integridade_custodia`. É o controle que
responde "o XML que eu tenho é o que foi autorizado?" ao longo dos anos, e não só no dia da carga.

**Restauração.** Dois testes documentados no runbook: reconstruir a bronze a partir do raw, e recuperar uma
versão anterior de um objeto.

## 4. Tabelas de controle

| Tabela | Grão | Linha | O que responde |
|---|---|---|---|
| `ctrl.lote` | lote × execução | 2 | o lote chegou inteiro, foi custodiado, ingerido e processado? quanto tempo levou? |
| `ctrl.integridade_custodia` | verificação | 2 | os objetos custodiados continuam com o mesmo hash? |
| `silver.nfe_quarentena` | arquivo barrado | 3 | o que não entrou, e por quê |
| `ctrl.dq` | execução × regra | 3 | quantas notas caíram em cada regra de aviso |
| `ctrl.reconciliacao` | lote × fronteira × métrica | 3 e 4 | quantidade e valores iguais de uma camada para a outra? |

Estados de `ctrl.lote`: `RECEBIDO → CUSTODIADO → CARREGADO → PROCESSADO`, com `INCOMPLETO` (chegou menos do
que o controle declara) e `ERRO` (falha de processo). É o controle por lote do datalabs, com um estado a mais
para a custódia.

`ctrl.reconciliacao` confere quatro métricas (quantidade de documentos, soma de `vNF`, soma de CBS e soma de
IBS) em cinco fronteiras:

```
origem (controle_lote.json) → raw (inventário) → bronze → silver + quarentena → gold
```

Cada linha guarda o valor esperado, o encontrado, a diferença e o status. O painel lê a última.

## 5. Orquestração

| DAG | Gatilho | Tarefas |
|---|---|---|
| `dag_nfe_saida` | sensor na `inbox/` | `aguardar_lote → reservar_lote → custodiar → carregar_bronze → dbt (silver, gold) → dq → reconciliar → fechar_lote → arquivar_lote` |
| `dag_custodia_integridade` | agenda diária | `sortear_objetos → verificar_hash → registrar` |

`fechar_lote` roda sempre, mesmo quando uma tarefa anterior falha, para que `ctrl.lote` registre o que
aconteceu. `reconciliar` fica antes dela e pode falhar de propósito: uma conferência que não fecha deixa o lote
fora do estado `PROCESSADO`.

## 6. Modelo dimensional (gold)

Grão da fato principal: **item da NF-e**.

| Objeto | Chave | Observação |
|---|---|---|
| `dm_estabelecimento` | CNPJ | os 5 emitentes do grupo; tabela fixa (seed) |
| `dm_cliente` | `sk_cliente` | destinatário, com histórico de alteração cadastral; CPF mascarado |
| `dm_produto` | código + NCM | catálogo do grupo |
| `dm_operacao` | `sk_operacao` | tipo da operação (venda, transferência, remessa, devolução), CFOP, sinal no faturamento |
| `dm_tributacao` | CST + cClassTrib | tabela de referência com vigência |
| `dm_tempo` | data | data de emissão |
| `ft_nfe_item` | chave + item | valores e tributos por item |
| `ft_nfe_documento` | chave | totais da nota e situação (autorizada, cancelada) |
| `ag_fiscal_mensal` | mês × CNPJ × UF destino × operação | o que o painel lê nos indicadores |

Duas regras de negócio moram na gold e são conferidas pela linha 3:

- nota **cancelada** permanece em `ft_nfe_documento`, com a situação, e não soma no faturamento nem nos
  tributos;
- **devolução** soma com sinal negativo; **transferência** e **remessa** não são faturamento.
