# Decisões de arquitetura

Formato curto: contexto → decisão → consequência. Nova decisão = nova entrada; as antigas não são reescritas.

## D001 — Repositório independente, stack igual ao do datalabs

**Contexto:** o pipeline de NF-e já existe no datalabs (Postgres, dbt, Airflow) e no
databricks-nfe-lakehouse. Este projeto muda a perspectiva (empresa emitente, e não fisco) e o foco (custódia e
conferência).
**Decisão:** repositório próprio, com `docker compose` próprio, usando as mesmas imagens e versões fixadas do
datalabs. Nome de projeto e portas diferentes, para os dois poderem coexistir na mesma máquina.
**Consequência:** um clone novo funciona sozinho. Código do datalabs é copiado e adaptado, não importado; os
dois projetos evoluem separados. Custo: dois stacks com Airflow consomem memória, então em máquina pequena sobe
um por vez.

## D002 — Perspectiva do emitente: só documentos emitidos pelo grupo

**Contexto:** uma empresa lida com notas emitidas (ela gera o XML) e recebidas (o XML vem do fornecedor ou do
serviço de distribuição). São fluxos de ingestão diferentes.
**Decisão:** o MVP trata só o que os 5 CNPJs do grupo emitem: NF-e de saída, NF-e de entrada emitida pelo
próprio grupo (devolução de não contribuinte) e eventos de cancelamento. Devolução emitida pelo cliente e
notas de compra ficam fora.
**Consequência:** o conjunto de emitentes válidos é fechado, o que permite a regra "emitente fora do grupo" e
o controle de numeração. A venda líquida do painel desconta só as devoluções que o grupo emitiu; a limitação
fica escrita na página.

## D003 — Raw no object store é a custódia; bronze no Postgres é cópia de trabalho

**Contexto:** no datalabs a bronze (XML numa tabela Postgres) é a camada mais bruta. Para guarda legal, uma
tabela que o dono do banco pode alterar não prova integridade.
**Decisão:** o arquivo vai primeiro para um bucket versionado, com retenção, e recebe um sha256 registrado em
inventário. A bronze é carregada depois e pode ser apagada e reconstruída a partir do raw.
**Consequência:** duas cópias do XML, com papéis diferentes. Se o bloqueio de objeto não funcionar no Silo em
nó único (teste do marco 1), a imutabilidade passa a ser garantida por versionamento, credencial de escrita sem
permissão de exclusão e verificação periódica de hash.

## D004 — Ingestão em Python; parse continua em SQL

**Contexto:** o datalabs ingere com `pg_read_file`, em SQL puro (ADR 0006 de lá), porque o Postgres lê a pasta
diretamente. Aqui o arquivo precisa passar pelo object store e receber hash antes de chegar ao banco.
**Decisão:** a etapa pasta → raw → bronze é um script Python (boto3 e psycopg). O parse do XML continua em dbt,
com `XMLTABLE` e contrato, reaproveitando os modelos do datalabs.
**Consequência:** o Postgres não precisa montar a pasta de rede nem de permissão de leitura de arquivos do
servidor. A regra "Python orquestra e move, SQL transforma" fica preservada.

## D005 — O lote é a unidade de entrega, e a origem declara totais

**Contexto:** contar arquivos prova que nada sumiu, mas não prova que o valor está certo.
**Decisão:** cada lote traz um `controle_lote.json` com quantidade de arquivos por tipo e soma de `vNF`. O
pipeline confere esses totais em cada fronteira e grava em `ctrl.reconciliacao`.
**Consequência:** a auditoria dos números começa na origem, e não na primeira tabela do pipeline. Em um
cliente real, esse arquivo é o primeiro pedido a fazer ao time do ERP.

## D006 — Evento é documento próprio; a situação da nota é derivada

**Contexto:** o cancelamento chega como outro XML, que pode vir antes, junto ou depois da nota.
**Decisão:** eventos têm custódia, bronze e silver próprias. A situação da nota (autorizada, cancelada) é
calculada em `nfe_situacao` a partir dos eventos. Evento sem nota fica em quarentena e é reavaliado nas cargas
seguintes.
**Consequência:** a nota cancelada nunca é apagada nem alterada; ela muda de situação. A ordem de chegada dos
arquivos deixa de importar.

## D007 — Erro barra, aviso sinaliza

**Contexto:** uma NF-e autorizada é um fato jurídico, mesmo com inconsistência fiscal.
**Decisão:** só vai para a quarentena o que não é um documento íntegro do grupo (XML malformado, chave
inválida, sem autorização, emitente estranho, evento sem nota). Inconsistência fiscal vira aviso em `ctrl.dq`,
e a nota segue para a gold.
**Consequência:** os números do painel incluem notas com aviso. O fiscal decide o que fazer com elas no ERP; o
pipeline não corrige.

## D008 — Regra fiscal é tabela com vigência, mantida pelo especialista

**Contexto:** alíquotas e classificações mudam por nota técnica durante a transição da reforma, e o autor do
projeto é engenheiro de dados, não tributarista.
**Decisão:** alíquotas de teste, combinações CST × cClassTrib e CFOP por operação ficam em seeds do dbt com
data de início e fim de vigência, marcadas "a validar".
**Consequência:** mudar uma regra é alterar uma linha de tabela e reprocessar. O que é opinião fiscal fica
separado do que é mecanismo.

## D009 — Painel em Grafana, como código, lendo só gold e controle

**Contexto:** o pedido é uma página de comprovação, e o Grafana já está no stack.
**Decisão:** painel gerado por script e versionado, com todas as consultas cobertas por teste. Nenhuma regra de
negócio no painel.
**Consequência:** a gold fica independente de ferramenta; trocar por Power BI é refazer a página, não o modelo.
Pergunta em aberto no escopo: se o usuário fiscal pede outra ferramenta.

## D010 — Dados sintéticos e cliente fictício

**Decisão:** toda a massa vem do gerador. O Grupo Xis não representa nenhuma empresa real. Só códigos públicos
(IBGE, NCM, CFOP, CST) são reais.
**Consequência:** assinatura digital e protocolo têm estrutura real e conteúdo aleatório; validação de
assinatura e consulta de situação na SEFAZ ficam fora do escopo.
