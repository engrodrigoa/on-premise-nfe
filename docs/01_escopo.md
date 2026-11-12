# Escopo de consultoria

Documento de abertura do projeto, escrito como seria entregue ao cliente antes de qualquer linha de código.

## 1. Contexto

O Grupo Xis (fictício) importa produtos e revende no mercado nacional. Emite NF-e por 5 CNPJs, em GO, SP e PR,
para clientes em 15 UFs. A operação mistura venda para empresas, venda para pessoa física, transferência entre
estabelecimentos do grupo, remessa para operador logístico, devoluções e cancelamentos.

O XML de cada nota é gerado pelo ERP e fica em uma pasta de rede. A área de BI já mede vendas a partir do ERP,
mas ninguém responde pelo documento fiscal em si.

## 2. Por que este projeto existe

| Motivo | O que está em jogo |
|---|---|
| **Guarda obrigatória** | emitente e destinatário devem manter a NF-e em arquivo digital pelo prazo da legislação tributária (Ajuste SINIEF 07/05). O documento é o XML autorizado, com assinatura e protocolo. O DANFE e a tabela do ERP não o substituem |
| **Prova de integridade** | o ERP guarda o arquivo, mas não prova que ele não mudou. Troca de fornecedor, limpeza de banco ou pasta sobrescrita deixam a empresa sem defesa numa fiscalização |
| **Crédito do cliente** | IBS e CBS são não cumulativos. Nota de venda emitida com CST, cClassTrib ou base errados vira crédito glosado no comprador, ou seja, atrito comercial |
| **Leiaute em transição** | os grupos da reforma entram na NF-e por notas técnicas sucessivas. Só quem tem o bruto preservado e um parser versionado reprocessa o histórico |
| **Fonte única** | fiscal, financeiro e contábil conferem números em planilhas diferentes. Uma camada conferida com o XML encerra a discussão sobre qual número vale |

## 3. Objetivo

> Garantir a guarda íntegra e a disponibilização analítica das NF-e emitidas pelos 5 CNPJs do Grupo Xis, com
> rastreabilidade do XML bruto até o número do painel, preparada para as mudanças de leiaute da reforma
> tributária (IBS, CBS e IS).

Três resultados mensuráveis:

1. **Custódia:** 100% dos XMLs recebidos estão no object store, com hash, e a verificação periódica de
   integridade não encontra divergência.
2. **Conferência:** quantidade de notas, valor total e tributos são iguais na origem, na silver, na gold e no
   painel. Toda diferença tem explicação arquivo a arquivo (quarentena).
3. **Disponibilidade:** um lote entregue na pasta está no painel em até 30 minutos (meta inicial, a calibrar no
   marco 3).

## 4. Escopo

### Dentro (MVP)

| Item | Detalhe |
|---|---|
| Documentos | NF-e modelo 55 **emitida** pelos 5 CNPJs do grupo, no formato de distribuição (`procNFe`) |
| Operações | venda a CNPJ, venda a CPF, transferência entre estabelecimentos, remessa para operador logístico, devolução de venda a não contribuinte (nota de entrada emitida pelo próprio grupo) |
| Eventos | cancelamento (`procEventoNFe`, tipo 110111) |
| Tributos | ICMS, IPI, PIS, COFINS e os grupos IBS, CBS e IS |
| Linha 1 | pasta de rede → custódia → bronze → silver → gold → dashboard |
| Linha 2 | controle por lote, completude em cada fronteira, frescor, verificação de integridade da custódia, teste de restauração |
| Linha 3 | quarentena, regras de aviso, auditoria de totais da origem ao painel, gabarito das anomalias |
| Linha 4 | dashboard de 1 página: quantidade de notas, valor total, total de tributos, abertura por nota e por produto |
| Operação | runbook, política de retenção, tratamento de dado pessoal |

### Fora (registrado para fases seguintes)

| Item | Por que fica fora |
|---|---|
| **Notas recebidas** (compras, devolução emitida pelo cliente), via `NFeDistribuicaoDFe` | é onde está o crédito de IBS/CBS, e por isso é a fase 2 natural. Exige outro fluxo de ingestão |
| CC-e, inutilização de numeração, manifestação do destinatário | não mudam valor. Entram junto com as recebidas |
| NFC-e, NFS-e, CT-e | outros documentos, outros leiautes |
| Conciliação com EFD e com a apuração | depende das recebidas |
| Validação da assinatura digital e consulta de situação na SEFAZ | os dados são sintéticos. A assinatura tem estrutura real e conteúdo aleatório |
| Cálculo ou correção de imposto | o pipeline confere e sinaliza. Quem corrige é o fiscal, no ERP |
| Nuvem | o cliente definiu on-premise. A migração é outro projeto |

## 5. Quem usa

| Papel | O que precisa | Onde encontra |
|---|---|---|
| **Fiscal** | quantas notas emiti, quanto de cada tributo destaquei, quais foram canceladas, quais têm inconsistência | dashboard e `ctrl.dq` |
| **Financeiro** | valor faturado líquido de cancelamentos e devoluções, por CNPJ e período | dashboard e gold |
| **Jurídico / auditoria** | o XML original de uma chave, com prova de que não foi alterado | custódia (`custodia.documento` + object store) |
| **TI do cliente** | saber se a carga está saudável e o que fazer quando não está | `ctrl.lote`, runbook |

## 6. Premissas

1. O ERP entrega os XMLs por **lote**, numa pasta de rede, com um arquivo de controle que declara quantos
   arquivos enviou e o valor total das notas.
2. A pasta de rede é só passagem. A cópia que vale fica no object store.
3. Uma NF-e autorizada é um fato jurídico. O pipeline **nunca descarta** uma nota autorizada por regra fiscal:
   ele sinaliza. Só é barrado o que não é uma NF-e íntegra do grupo.
4. As regras fiscais ficam em tabelas com vigência, mantidas pelo fiscal do cliente.
5. Quem opera depois da entrega é a TI do cliente. Menos peças e um runbook claro valem mais que sofisticação.

## 7. Riscos

| Risco | Efeito | Tratamento |
|---|---|---|
| Regra fiscal modelada errada (CFOP, cClassTrib, alíquota de importado) | cenário pouco crível para quem é da área | regras em seeds marcadas "a validar"; revisão por tributarista antes de apresentar |
| Bloqueio de objeto (object lock) não funcionar no object store escolhido em nó único | custódia sem imutabilidade técnica | teste dirigido no marco 1; alternativa: versionamento + política de acesso só de escrita + verificação de hash |
| Dois stacks Docker na mesma máquina (este e o datalabs) | falta de memória, conflito de porta | os dois usam as portas padrão e não rodam juntos (D011): parar um antes de subir o outro |
| Escopo crescer para as notas recebidas antes de fechar o MVP | projeto não termina | recebidas só depois do marco 7 |
| Dado pessoal (CPF e endereço de consumidor) em camada analítica | exposição desnecessária | bruto guardado por obrigação legal; na gold o CPF é mascarado |

## 8. Entregáveis

| # | Entregável | Marco |
|---|---|---|
| 1 | Este escopo, o mapa das quatro linhas e as decisões | 0 |
| 2 | Ambiente `docker compose` e custódia com inventário | 1 |
| 3 | Gerador do cenário Grupo Xis, com gabarito e totais de controle | 2 |
| 4 | Pipeline raw → bronze → silver → gold, orquestrado | 3 a 5 |
| 5 | Dashboard de 1 página com selo de conferência | 6 |
| 6 | Runbook, política de retenção, teste de restauração e roteiro de demonstração de incidentes | 7 |

## 9. Critérios de aceite

1. Um lote novo na pasta é processado sem intervenção e fica `PROCESSADO` em `ctrl.lote`.
2. Um lote entregue pela metade fica `INCOMPLETO`, e não `PROCESSADO`.
3. Toda anomalia injetada pelo gerador aparece na quarentena ou em `ctrl.dq` (gabarito 100%).
4. Quantidade de notas e valor total são iguais na origem e no painel, descontada a quarentena listada arquivo
   a arquivo.
5. Nota cancelada continua consultável e não entra no valor faturado.
6. Um arquivo alterado no object store é detectado pela verificação de integridade.
7. A bronze apagada é reconstruída a partir do raw e a gold volta aos mesmos totais.

## 10. Decisões de escopo

Perguntas levantadas no planejamento e respondidas pelo dono do projeto em 11/11/2026:

| # | Pergunta | Decisão |
|---|---|---|
| 1 | Estrutura societária | 2 empresas (Xis Importadora e Xis Comércio), 5 estabelecimentos. Separa transferência (mesma empresa) de venda entre empresas do grupo |
| 2 | Linhas de produto | três famílias de importados: utensílios de cozinha, ferramentas elétricas e acessórios eletrônicos. Sem medicamento e cosmético, que têm regimes próprios |
| 3 | Período da massa | só 2026, com as alíquotas de teste (CBS 0,9% e IBS 0,1%). 2027 fica para depois do MVP |
| 4 | Volume | cerca de 25 mil notas no ano, em lotes diários de cerca de 100 |
| 5 | Dashboard | Grafana |
| 6 | Onde roda | máquina Windows com WSL2 e Docker Desktop, portas padrão, sem rodar junto com o datalabs (D011) |
| 7 | Versão do Airflow | 3.4.0 (D012) |
