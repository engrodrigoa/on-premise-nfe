# on-premise-nfe

Custódia e pipeline **on-premise** das NF-e de saída de uma empresa privada, do XML bruto até a página que o
fiscal e o financeiro consultam, já com os grupos da reforma tributária (IBS, CBS e IS).

O projeto é escrito como o trabalho de um **consultor de engenharia de dados** dentro do cliente: primeiro o
escopo, depois o desenho das quatro linhas, depois a entrega por marcos.

> **Status: planejamento (marco 0).** Este commit contém só o planejamento. Nenhum pipeline foi implementado
> ainda. O que existe e o que falta está em [docs/roadmap.md](docs/roadmap.md).

Dados **100% sintéticos**. O cliente ("Grupo Xis") é fictício e nenhum documento, CNPJ ou CPF real entra neste
repositório.

## O problema do cliente

O Grupo Xis importa produtos e revende em todo o país: 5 CNPJs emitentes (GO, SP e PR), clientes em 15 UFs,
venda para empresas e para pessoas físicas, transferências entre os próprios estabelecimentos, remessas para
operador logístico, devoluções e cancelamentos.

Toda NF-e emitida gera um XML autorizado que a empresa é obrigada a guardar pelo prazo da legislação
tributária. Hoje esse XML vive no ERP e numa pasta de rede. Três perguntas ficam sem resposta:

1. **O XML que eu guardei é o mesmo que foi autorizado, e eu consigo provar isso daqui a cinco anos?**
2. **Os números que o fiscal vê (notas, valor, tributos) batem com os XMLs, nota a nota?**
3. **Quando o leiaute mudar de novo na transição da reforma, eu consigo reprocessar o histórico?**

## As quatro linhas

Cada etapa do pipeline é planejada respondendo quatro perguntas ao mesmo tempo:

| Linha | Pergunta | Registro |
|---|---|---|
| **1. Carga** | o que entra, o que sai, onde grava? | raw (custódia) → bronze → silver → gold |
| **2. Observabilidade** | a carga rodou, chegou ao fim e não perdeu nada? | `ctrl.lote` · `ctrl.integridade_custodia` |
| **3. Qualidade** | o conteúdo é válido e os números fecham? | quarentena · `ctrl.dq` · `ctrl.reconciliacao` |
| **4. Comprovação** | o usuário final vê o número certo, e dá para provar? | dashboard de 1 página com selo de conferência |

Mapa completo, etapa por etapa: **[docs/02_fluxo.md](docs/02_fluxo.md)**.

## Arquitetura

```
 ERP / emissor do cliente (simulado por gerador/gen_nfe.py)
        │ procNFe + procEventoNFe + controle_lote.json, por lote
        ▼
 Pasta de rede  datasource/nfe/inbox/
        │ dag_nfe_saida: sensor → reserva o lote
        ▼
 RAW (custódia)   object store · bucket versionado com retenção · sha256 por arquivo
        │         inventário: custodia.documento                      ◄── cópia com valor legal
        ▼
 BRONZE           Postgres · XML bruto, 1 linha por arquivo, só INSERT ◄── cópia de trabalho, reconstruível do raw
        │ dbt: XMLTABLE + contrato
        ▼
 SILVER           nfe_nota · nfe_item · nfe_evento · situação da nota │ quarentena (erro)
        │
        ▼
 GOLD             dimensões · ft_nfe_item · ft_nfe_documento · agregado fiscal mensal
        │
        ▼
 DASHBOARD        1 página: notas, valor, tributos, abertura por nota e por produto
                  + selo: "números conferidos com o XML" (ctrl.reconciliacao)
```

## Stack

A mesma do [datalabs](https://github.com/engrodrigoa/datalabs), com as mesmas versões fixadas, num
`docker compose` próprio (decisão D001):

| Camada | Ferramenta |
|---|---|
| Orquestração | Airflow 3.3.2 (api-server, scheduler, dag-processor, triggerer) + astronomer-cosmos 1.15.1 |
| Custódia (raw) | Silo (fork comunitário do MinIO, API S3), bucket com versionamento e retenção |
| Banco | Postgres 15 |
| Transformação | dbt-core 1.11 + dbt-postgres (parse com `XMLTABLE`) |
| Ingestão | Python (boto3 + psycopg): pasta → object store → bronze |
| Dashboard | Grafana 12, painel como código |

## De onde vem o código

| Origem | O que é reaproveitado |
|---|---|
| [databricks-nfe-lakehouse](https://github.com/engrodrigoa/databricks-nfe-lakehouse) | gerador `gen_nfe.py` (XML validado no XSD `leiauteNFe_v4.00`, anomalias com gabarito, arquivo de controle por lote) e o método das linhas |
| [datalabs](https://github.com/engrodrigoa/datalabs) | controle por lote, modelos dbt de NF-e (`XMLTABLE`, contrato, merge), padrão de DAG e de painel como código |

O que é **novo** aqui: a perspectiva do emitente (5 CNPJs de um mesmo grupo), a custódia com prova de
integridade, os eventos de cancelamento, a auditoria de totais da origem ao painel e os entregáveis de
consultoria. Detalhes em [docs/03_cenario_dados.md](docs/03_cenario_dados.md).

## Documentos

| Documento | Conteúdo |
|---|---|
| [01_escopo.md](docs/01_escopo.md) | escopo de consultoria: objetivo, dentro e fora, premissas, riscos, critérios de aceite, perguntas em aberto |
| [02_fluxo.md](docs/02_fluxo.md) | mapa das quatro linhas por etapa |
| [03_cenario_dados.md](docs/03_cenario_dados.md) | o cliente fictício, as operações, as anomalias e o que muda no gerador |
| [04_arquitetura.md](docs/04_arquitetura.md) | camadas, objetos, custódia e tabelas de controle |
| [05_dashboard.md](docs/05_dashboard.md) | a página única e o selo de conferência |
| [decisoes.md](docs/decisoes.md) | decisões de arquitetura (contexto → decisão → consequência) |
| [roadmap.md](docs/roadmap.md) | marcos e critério de pronto de cada um |

## Aviso

Este é um projeto de engenharia de dados. As regras fiscais usadas (CFOP, CST, cClassTrib, alíquotas, prazos)
são hipóteses de trabalho e ficam em tabelas parametrizadas, marcadas para validação por um tributarista. O
pipeline entrega o mecanismo; a regra é do especialista.
