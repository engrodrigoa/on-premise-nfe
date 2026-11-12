# Dashboards

Dois públicos, duas páginas (D017): a de **negócio** (linha 4, marco 6), descrita primeiro, e a de **operação**
(linha 2), no fim deste documento.

## Página de negócio (linha 4)

A comprovação final do projeto. Uma página, lida pelo fiscal e pelo financeiro, que fecha o assunto: estes são
os números das notas emitidas, e eles foram conferidos com os XMLs.

## Decisões de desenho

| Pergunta | Resposta | Consequência |
|---|---|---|
| Quem lê? | fiscal e financeiro do cliente | números de negócio em destaque; a saúde da carga aparece só como selo |
| O que precisa provar? | que o número exibido é o do XML | o selo lê `ctrl.reconciliacao`; se a conferência não fechou, a página avisa antes de mostrar valor |
| Quantas páginas? | uma | o que não cabe aqui é consulta na gold, não painel |
| De onde lê? | só gold e tabelas `ctrl` | nenhuma regra de negócio dentro do painel |

## Leiaute

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ SELO   números conferidos: SIM/NÃO · última carga: data e status · horas sem dado novo    │
│        documentos sob custódia: N · última verificação de integridade: data, resultado    │
├──────────────┬──────────────┬──────────────┬──────────────┬──────────────────────────────┤
│ notas        │ notas        │ valor total  │ total de     │ filtros: período · CNPJ       │
│ autorizadas  │ canceladas   │ das notas    │ tributos     │ emitente · UF destino ·       │
│              │              │ (líquido)    │              │ operação                      │
├──────────────┴──────────────┴──────────────┴──────────────┴──────────────────────────────┤
│ tributos por tipo e mês:  ICMS · IPI · PIS · COFINS   |   IBS · CBS · IS                  │
├─────────────────────────────────────────────┬────────────────────────────────────────────┤
│ por nota                                    │ por produto                                 │
│ chave · data · CNPJ · UF destino ·          │ produto · NCM · quantidade · valor ·        │
│ operação · situação · valor · tributos      │ ICMS · IBS · CBS                            │
└─────────────────────────────────────────────┴────────────────────────────────────────────┘
```

## Indicadores

| Indicador | Definição | Fonte |
|---|---|---|
| Notas autorizadas | notas com situação autorizada no período | `ft_nfe_documento` |
| Notas canceladas | notas com evento de cancelamento | `ft_nfe_documento` |
| Valor total (líquido) | soma de `vNF` das vendas autorizadas, menos as devoluções emitidas pelo grupo (D002); sem transferência e remessa | `ag_fiscal_mensal` |
| Total de tributos | soma dos tributos destacados nas notas autorizadas, por tipo | `ag_fiscal_mensal` |
| Por nota | uma linha por chave, com situação | `ft_nfe_documento` |
| Por produto | uma linha por produto, agregando itens | `ft_nfe_item` + `dm_produto` |

Tributos aparecem em dois grupos, lado a lado: os do regime atual (ICMS, IPI, PIS, COFINS) e os da reforma
(IBS, CBS, IS). Em 2026 os da reforma são valores de teste, e a página diz isso.

## O selo

| Campo | Regra |
|---|---|
| Números conferidos | `SIM` quando todas as linhas de `ctrl.reconciliacao` do último lote estão sem diferença |
| Última carga | data e status do último lote em `ctrl.lote` |
| Horas sem dado novo | agora menos o fim da última carga `PROCESSADO` |
| Custódia | quantidade de objetos em `custodia.documento` e resultado da última linha de `ctrl.integridade_custodia` |

Quando a conferência não fecha, o selo fica em destaque negativo e mostra qual fronteira e qual métrica
divergiram. Os valores continuam visíveis, marcados como não conferidos.

## Teste da página

Antes de considerar o marco 6 pronto, os quatro indicadores do topo são recalculados direto na silver, com SQL
escrito à parte, e comparados com o que a página mostra. Toda consulta do painel entra na suíte de testes, como
no datalabs.

## Página de operação: saúde da carga (linha 2)

`grafana/dashboards/saude_carga.json`, uid `nfe-saude-carga`. Quem lê é quem sustenta o pipeline (engenharia,
consultoria). A pergunta é uma só: **o pipeline está seguro?** Sim = os 5 indicadores verdes e a tabela
"O que exige ação" vazia.

Critério de desenho: cada painel responde uma pergunta de sim ou não e tem limite definido. Volume processado,
notas por dia e contagens que só crescem ficam de fora. Elas não mudam nenhuma decisão.

```
┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ Horas sem    │ Falhas de    │ Entregas     │ Arquivos     │ Custódia     │
│ carga        │ processo     │ incompletas  │ perdidos     │ íntegra?     │
├──────────────┴──────────────┴──────────────┴──────────────┴──────────────┤
│ O que exige ação: severidade · quem age · onde · o que aconteceu · desde  │
├────────────────────────────────────────────┬─────────────────────────────┤
│ Divergências arquivo a arquivo             │ Duração da carga por lote   │
└────────────────────────────────────────────┴─────────────────────────────┘
```

| Painel | Pergunta | Regra | Verde | Quem age |
|---|---|---|---|---|
| Horas sem carga | está rodando? | agora − último `carregado_em` | < 26 h (laranja até 74 h) | Engenharia ou TI do ERP |
| Falhas de processo | quebrou algo? | lotes `ERRO` + lotes em `RECEBIDO`/`CUSTODIADO` há mais de 30 min; ignora o período | 0 | Engenharia |
| Entregas incompletas | a origem entregou tudo? | lotes `INCOMPLETO` no período | 0 | TI do ERP |
| Arquivos perdidos no pipeline | perdemos algo entre a pasta e a bronze? | por lote terminado: pasta × `custodia.recebimento` × bronze | 0, sempre | Engenharia |
| Custódia íntegra? | o XML legal segue igual? | última verificação de `ctrl.integridade_custodia` (marco 7) | `OK` | Engenharia |
| O que exige ação | o que fazer agora? | uma linha por problema: erro, lote preso, incompleto, hash divergente, mesma chave com dois XMLs | vazia | a coluna diz |
| Divergências arquivo a arquivo | qual arquivo? | `ctrl.lote_divergencia` no período | vazia | TI do ERP |
| Duração da carga por lote | está degradando? | `seg_custodia` + `seg_bronze` por lote | bem abaixo de 600 s (a agenda) | Engenharia |

Teste: `tests/test_painel.py` planta um cenário no banco (erro, lote preso, incompleto, arquivo perdido,
custódia divergente) e confere que cada painel, rodando com a role `grafana_ro`, acusa o que foi plantado.
