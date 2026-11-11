# Cenário de dados: o Grupo Xis

O cliente é fictício. O gerador é o "sistema de origem": faz o papel do ERP que emite a nota e grava o XML na
pasta de rede. Tudo o que está aqui é **proposta para o marco 2** e depende das perguntas em aberto do
[escopo](01_escopo.md#10-perguntas-em-aberto).

## 1. O grupo

Proposta: duas empresas, cinco estabelecimentos emitentes.

| # | Empresa | Estabelecimento | UF | Papel |
|---|---|---|---|---|
| 1 | Xis Importadora Ltda | matriz (0001) | GO | importa e distribui |
| 2 | Xis Importadora Ltda | filial (0002) | SP | centro de distribuição |
| 3 | Xis Importadora Ltda | filial (0003) | PR | entrada de mercadoria e distribuição no Sul |
| 4 | Xis Comércio Ltda | matriz (0001) | GO | venda direta ao consumidor |
| 5 | Xis Comércio Ltda | filial (0002) | SP | venda direta ao consumidor |

Duas raízes de CNPJ criam a distinção que interessa: movimentação **entre estabelecimentos da mesma empresa**
é transferência; movimentação **entre as duas empresas** é venda. Os 5 CNPJs são fixos e conhecidos pelo
pipeline (dimensão `dm_estabelecimento`), e é isso que permite barrar uma nota cujo emitente não é do grupo.

Destinatários em 15 UFs: GO, DF, MT, MS, TO, SP, MG, RJ, ES, PR, SC, RS, BA, PE e CE.

## 2. Operações

Hipóteses de trabalho. A coluna CFOP é a que mais precisa de revisão por um tributarista.

| Operação | Participação | Quem recebe | CFOP (interna / interestadual) | Observação |
|---|---|---|---|---|
| Venda a empresa | ~65% | CNPJ contribuinte | 5102 / 6102 | operação principal |
| Venda a pessoa física | ~15% | CPF, não contribuinte | 5102 / 6108 | consumidor final; DIFAL no interestadual |
| Transferência | ~8% | outro estabelecimento da mesma empresa | 5152 / 6152 | sem pagamento |
| Remessa para operador logístico | ~5% | CNPJ do operador | 5905 / 6905 | não é faturamento |
| Venda entre empresas do grupo | ~4% | Xis Comércio | 5102 / 6102 | faturamento dentro do grupo |
| Devolução de venda a pessoa física | ~3% | o próprio cliente (CPF) | 1202 / 2202 | nota de **entrada** emitida pelo grupo, com `refNFe` da venda original |

Cancelamento não é uma operação, é um **evento** sobre uma nota já autorizada: cerca de 1,5% das vendas
recebem um `procEventoNFe` de cancelamento (tipo 110111), que chega no mesmo lote ou em um lote posterior.

Pontos fiscais que o gerador precisa representar e que ficam marcados "a validar":

- mercadoria importada: origem 1 ou 2 no ICMS e alíquota interestadual de 4% (Resolução do Senado 13/2012);
- IBS e CBS em 2026 com as alíquotas de teste (CBS 0,9% e IBS 0,1%), como o gerador já faz;
- transferência entre estabelecimentos do mesmo contribuinte sem incidência de IBS e CBS (LC 214/2025);
- remessa para operador logístico fora do valor faturado;
- prazo de cancelamento (regra geral de 24 horas): cancelamento fora do prazo vira aviso de qualidade.

## 3. O que muda no gerador

Base: `gerador/gen_nfe.py` do
[databricks-nfe-lakehouse](https://github.com/engrodrigoa/databricks-nfe-lakehouse), que simula a visão de uma
SEFAZ (centenas de emitentes, um estado).

| Aspecto | Hoje (visão SEFAZ-GO) | Neste projeto (visão do emitente) |
|---|---|---|
| Emitentes | 300, sorteados, concentrados em GO | 5 estabelecimentos fixos do grupo |
| Destinatários | 9 UFs, regra "ou emitente ou destinatário em GO" | 15 UFs, sem regra de estado |
| Catálogo | agro, alimentos, bebidas, eletro; origem nacional | produtos importados; origem 1 ou 2; 4% no interestadual |
| Tipos de nota | venda, venda de produção, devolução | as 6 operações da tabela acima |
| Devolução | `refNFe` aponta para uma chave inventada | `refNFe` aponta para uma venda que existe na massa |
| Numeração | avança de 1 a 3 por nota (com saltos) | sequencial por CNPJ e série; salto só como anomalia |
| Eventos | não existem | `procEventoNFe` de cancelamento |
| Arquivo de controle | lote, quantidade de arquivos, seed | + quantidade por tipo de documento e soma de `vNF` |
| Manifesto (gabarito) | 5 tipos de anomalia | + as anomalias novas da seção 4 |

O que é mantido sem mudança: estrutura validada contra o XSD `leiauteNFe_v4.00`, grupos IBS/CBS/IS, CNPJ
alfanumérico, cadastro estável por seed, alteração cadastral datada (para a dimensão de cliente com histórico),
escrita atômica e a regra "um lote é uma entrega única".

## 4. Anomalias e quem deve pegar

Cada anomalia injetada vai para o manifesto. O critério de aceite é detectar 100% delas.

| Anomalia | Já existe no gerador | Linha | Onde deve aparecer |
|---|---|---|---|
| XML truncado no transporte | sim | 3 | quarentena (`xml_corrompido`) |
| Reenvio do mesmo arquivo | sim | 3 | bronze guarda os dois; silver usa o mais recente; contagem em `ctrl.lote` |
| Total de CBS ≠ soma dos itens | sim | 3 | aviso em `ctrl.dq` |
| Base IBS/CBS total ≠ soma dos itens | sim | 3 | aviso em `ctrl.dq` |
| cClassTrib incompatível com o CST | sim | 3 | aviso em `ctrl.dq` |
| Emitente fora do grupo | **novo** | 3 | quarentena (`emitente_fora_do_grupo`) |
| Mesma chave, conteúdo diferente | **novo** | 3 | alerta de custódia: dois hashes para a mesma chave |
| Evento de cancelamento sem a nota | **novo** | 3 | quarentena de eventos (`evento_sem_nota`), revisto quando a nota chegar |
| Cancelamento fora do prazo | **novo** | 3 | aviso em `ctrl.dq` |
| Salto na numeração | **novo** | 3 | aviso em `ctrl.dq` (`numeracao_com_salto`) |
| Devolução que referencia nota inexistente | **novo** | 3 | aviso em `ctrl.dq` |
| Lote entregue pela metade | **novo** (o controle declara mais do que chegou) | 2 | `ctrl.lote` = `INCOMPLETO` |
| Arquivo alterado depois de custodiado | **novo** (injetado no object store, fora do gerador) | 2 | `ctrl.integridade_custodia` |

## 5. Dado pessoal

Venda a pessoa física coloca CPF, nome e endereço no XML.

| Camada | Tratamento |
|---|---|
| Raw e bronze | dado completo. A guarda do documento é obrigação legal, pelo prazo fiscal |
| Silver | dado completo, acesso restrito |
| Gold e dashboard | CPF mascarado (hash), sem nome e sem endereço de pessoa física. UF e município permanecem |

O prazo de retenção e o descarte ao fim dele ficam na política de retenção (marco 7).
