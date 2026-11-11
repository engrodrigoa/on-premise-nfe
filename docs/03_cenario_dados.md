# Cenário de dados: o Grupo Xis

O cliente é fictício. O gerador é o "sistema de origem": faz o papel do ERP que emite a nota e grava o XML na
pasta de rede. Este é o desenho do marco 2, com as [decisões de escopo](01_escopo.md#10-decisões-de-escopo)
já tomadas.

## 1. O grupo

Duas empresas, cinco estabelecimentos emitentes.

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

Hipóteses de trabalho, a validar com um tributarista (decisão D008). A participação é a medida em uma massa
de um ano (25,4 mil notas legíveis).

| Operação | Participação | Quem emite | Quem recebe | CFOP | Tributação no XML |
|---|---|---|---|---|---|
| Venda a empresa | 58% | os 5 estabelecimentos | CNPJ contribuinte, 15 UFs | 5102 / 6102 | ICMS 00, IBS/CBS CST 000 |
| Venda a pessoa física | 18% | Xis Comércio | CPF, consumidor final | 5102 / 6108 | idem, com grupo de DIFAL no interestadual |
| Transferência | 8% | Xis Importadora | outro estabelecimento da mesma empresa | 6152 | sem incidência: ICMS 41, IBS/CBS CST 410 |
| Venda entre empresas do grupo | 6% | Xis Importadora | Xis Comércio | 5102 / 6102 | como venda a empresa |
| Remessa para operador logístico | 6% | os 5 | operador da mesma UF | 5905 | sem incidência; não é faturamento |
| Devolução de venda a pessoa física | 3,5% | Xis Comércio | o próprio cliente (CPF) | 1202 / 2202 | nota de **entrada**, `refNFe` da venda original |

A transferência é sempre interestadual porque os três estabelecimentos da importadora ficam em UFs diferentes.

**Cancelamento** não é operação, é **evento** sobre uma venda já autorizada: 1,5% das vendas recebem um
`procEventoNFe` de cancelamento (tipo 110111). O evento chega no lote do dia em que foi registrado, que pode
ser o da nota ou um posterior. Venda cancelada não pode ser devolvida.

Regras fiscais representadas, todas marcadas "a validar" em `gerador/referencias.py`:

- mercadoria importada: origem 1 (importadora) ou 2 (comércio) no ICMS, e 4% no interestadual (Resolução do
  Senado 13/2012);
- IPI destacado nas vendas da importadora, com alíquotas ilustrativas por família;
- IBS e CBS com as alíquotas de teste de 2026 (CBS 0,9% e IBS 0,1%); base = produto − ICMS − PIS − COFINS;
- transferência e remessa sem incidência de IBS e CBS (CST 410, `cClassTrib` 410999);
- prazo de cancelamento de 24 horas: fora dele, aviso de qualidade.

## 3. Como o gerador entrega

`gerador/gen_nfe.py` faz o papel do ERP. Entrega **um lote por dia**, em pasta própria:

```
datasource/nfe/
├── inbox/lote_2026-01-02/
│   ├── <chave>-procNFe.xml                   nota autorizada
│   ├── <chave>-110111-01-procEventoNFe.xml   cancelamento
│   └── controle_lote.json                    o que a origem declara ter enviado
├── gabarito/lote_2026-01-02.csv              anomalias injetadas (o pipeline não lê)
└── gerador_estado.json                       numeração, vendas devolvíveis, pendências
```

| Aspecto | Como é | Por quê |
|---|---|---|
| Lote atômico | a pasta é montada como `_parcial_lote_...` e renomeada no fim | o sensor nunca vê um lote pela metade |
| Arquivo de controle | quantidade de arquivos, de notas e de eventos, soma de `vNF` e um **romaneio**: nome, tipo, chave, `vNF` e sha256 de cada arquivo | é o ponto de partida da conferência (D005); o sha256 permite provar que o arquivo não mudou no transporte |
| Estado entre lotes | `gerador_estado.json` | numeração sequencial por CNPJ, devolução que referencia venda existente, cancelamento em lote posterior |
| Reprodutível | mesma seed = mesmos arquivos, byte a byte, em uma ou em várias execuções | um incidente pode ser reproduzido |
| Volume | 90 emissões em dia útil, 22% disso no fim de semana | cerca de 25,7 mil notas e 360 eventos no ano, em 364 lotes |

Diferenças em relação ao gerador de origem
([databricks-nfe-lakehouse](https://github.com/engrodrigoa/databricks-nfe-lakehouse), visão de uma SEFAZ):

| Aspecto | Gerador de origem | Aqui |
|---|---|---|
| Emitentes | 300, sorteados, concentrados em GO | 5 estabelecimentos fixos do grupo |
| Destinatários | 9 UFs, regra "emitente ou destinatário em GO" | 15 UFs, sem regra de estado |
| Catálogo | agro, alimentos, bebidas, eletro; origem nacional | 15 produtos importados em 3 famílias |
| Devolução | `refNFe` aponta para uma chave inventada | `refNFe` aponta para uma venda que existe na massa |
| Numeração | avança de 1 a 3 por nota | sequencial por CNPJ; salto só como anomalia |
| Eventos | não existem | `procEventoNFe` de cancelamento |
| Arquivo de controle | lote, quantidade, seed | + notas, eventos, soma de `vNF` e romaneio com sha256 |
| Estado | nenhum | arquivo de estado entre execuções |

Mantido: grupos IBS/CBS, CNPJ alfanumérico em parte dos clientes, cadastro estável pela seed, alteração
cadastral datada e escrita atômica. A estrutura da nota tributada é a do gerador de origem, que foi validada
no XSD `leiauteNFe_v4.00`. **O evento de cancelamento e os itens sem incidência (CST 410) são novos e ainda
não foram validados contra o XSD**; a opção `--xsd` do gerador faz essa validação quando o arquivo do XSD
estiver à mão.

## 4. Anomalias e quem deve pegar

Com `--sujo`, o gerador injeta as anomalias abaixo e registra cada uma no gabarito, com o lugar onde ela deve
aparecer. O critério de aceite é detectar 100% delas. A quantidade é a de uma massa de um ano.

| Anomalia (nome no gabarito) | No ano | Linha | Onde deve aparecer |
|---|---|---|---|
| `xml_corrompido`: arquivo truncado no transporte | 107 | 2 e 3 | sha256 diferente do romaneio; quarentena |
| `reenvio_identico`: mesmo arquivo, outro nome | 248 | 2 | contagem em `ctrl.lote`; um objeto só na custódia |
| `mesma_chave_conteudo_diferente` | 65 | 3 | alerta de custódia: dois sha256 para a mesma chave |
| `emitente_fora_do_grupo` | 82 | 3 | quarentena |
| `evento_sem_nota`: o cancelamento chega antes da nota | 43 | 3 | quarentena de eventos, revista quando a nota chegar |
| `nota_atrasada`: a nota do caso acima, 2 lotes depois | 43 | — | informativo: é o que resolve o evento sem nota |
| `tot_cbs_divergente`: total de CBS ≠ soma dos itens | 155 | 3 | aviso em `ctrl.dq` |
| `vbc_ibscbs_divergente`: base total ≠ soma dos itens | 159 | 3 | aviso em `ctrl.dq` |
| `cclasstrib_incoerente`: `cClassTrib` incompatível com o CST | 139 | 3 | aviso em `ctrl.dq` |
| `numeracao_com_salto`: um número sem nota | 51 | 3 | aviso em `ctrl.dq` |
| `devolucao_ref_inexistente` | 47 | 3 | aviso em `ctrl.dq` |
| `cancelamento_fora_do_prazo`: mais de 24 horas | 40 | 3 | aviso em `ctrl.dq` |
| `arquivo_nao_entregue` (`--incompleto N`) | sob demanda | 2 | `ctrl.lote` = `INCOMPLETO` |
| Arquivo alterado depois de custodiado | marco 7 | 2 | `ctrl.integridade_custodia` (injetado no object store, fora do gerador) |

## 5. Dado pessoal

Venda a pessoa física coloca CPF, nome e endereço no XML.

| Camada | Tratamento |
|---|---|
| Raw e bronze | dado completo. A guarda do documento é obrigação legal, pelo prazo fiscal |
| Silver | dado completo, acesso restrito |
| Gold e dashboard | CPF mascarado (hash), sem nome e sem endereço de pessoa física. UF e município permanecem |

O prazo de retenção e o descarte ao fim dele ficam na política de retenção (marco 7).
