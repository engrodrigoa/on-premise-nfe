# Dashboard: a página única (linha 4)

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
