# Decisões de arquitetura

Formato curto: contexto → decisão → consequência. Nova decisão = nova entrada; as antigas não são reescritas.

## D001 — Repositório independente, stack igual ao do datalabs

**Contexto:** o pipeline de NF-e já existe no datalabs (Postgres, dbt, Airflow) e no
databricks-nfe-lakehouse. Este projeto muda a perspectiva (empresa emitente, e não fisco) e o foco (custódia e
conferência).
**Decisão:** repositório próprio, com `docker compose` próprio, usando as mesmas imagens e versões fixadas do
datalabs. Nome de projeto e portas diferentes, para os dois poderem coexistir na mesma máquina.
*(Portas e versão do Airflow revistas na D011 e na D012.)*
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

## D011 — Portas padrão; o stack não roda junto com o datalabs

**Contexto:** a D001 previa portas diferentes para os dois stacks coexistirem. O dono do projeto decidiu isolar
este projeto e nunca rodar os dois ao mesmo tempo (a máquina tem 8 GB para o WSL).
**Decisão:** portas padrão: Postgres 5432, Airflow 8080, Grafana 3000, object store 9000 e 9001. Os containers
não têm nome fixo; o compose gera os nomes com o prefixo do projeto (`nfe_onprem`).
**Consequência:** endereços fáceis de lembrar. Com o datalabs de pé, `make up` falha por porta ocupada, e a
correção é `make down` no datalabs. Nomes de container nunca colidem, mesmo com o datalabs parado.

## D012 — Airflow 3.4.0, com o dbt chamado por linha de comando (sem Cosmos)

**Contexto:** o datalabs está no Airflow 3.3.2 com astronomer-cosmos 1.15.1. O dono do projeto quer avaliar o
3.4.0, publicado em 06/11/2026. A imagem do 3.4 mudou de base (Debian 13), e a compatibilidade do Cosmos com
o 3.4 não foi verificada neste projeto.
**Decisão:** imagem `apache/airflow:3.4.0-python3.12`, dependências resolvidas com as constraints oficiais do
3.4.0. O dbt roda em venv isolado e é chamado por `BashOperator` (`dbt build --select ...`), uma task por
camada.
**Consequência:** uma dependência a menos para acompanhar em uma versão recém-lançada. Perde-se a visão de
um nó por modelo na UI do Airflow; o detalhe por modelo fica no log da task e nos artefatos do dbt. Se o
Cosmos fizer falta, entra depois sem mudar os modelos. O job `airflow-dags` do CI carrega as DAGs no
Airflow 3.4.0 real a cada PR.

## D013 — Bronze em uma tabela só, e arquivo malformado entra com conteúdo nulo

**Contexto:** o desenho inicial previa duas tabelas na bronze (nota e evento). Mas o tipo de um arquivo
corrompido não é conhecido, e a conferência "bronze = válidos + quarentena" exige que todo arquivo recebido
tenha uma linha na bronze.
**Decisão:** uma tabela, `bronze.documento_xml`, com `tipo_documento` em `nfe`, `evento` ou `invalido`. O
arquivo que não é XML bem formado entra com `conteudo` nulo, a mensagem do parser e o início do texto. Na
custódia ele é guardado do mesmo jeito, sob o prefixo `invalido/`.
**Consequência:** nenhum arquivo some entre a pasta e a bronze, e a contagem fecha sem tabela de rejeitados à
parte. Quem decide o destino do arquivo malformado é a silver (quarentena, motivo `xml_corrompido`).

## D014 — Gerador com estado, um lote por dia, e romaneio com sha256 no arquivo de controle

**Contexto:** a origem precisa produzir o que um ERP real produz: numeração sem buracos, devolução que aponta
para uma venda existente e cancelamento que chega dias depois. Um gerador sem memória não consegue. Além
disso, contar arquivos e somar `vNF` (D005) não prova que cada arquivo chegou como saiu.
**Decisão:** o gerador guarda estado em `gerador_estado.json` e entrega um lote por dia, em pasta própria,
publicada na inbox só quando completa. O `controle_lote.json` traz, além dos totais, um romaneio com o sha256
de cada arquivo na saída do ERP. O gabarito das anomalias fica em outra pasta, que o pipeline não lê.
**Consequência:** a custódia compara o sha256 recebido com o declarado e detecta arquivo truncado antes de
qualquer parse. O estado torna a geração sequencial: perder o arquivo de estado exige recomeçar a massa (o
gerador recusa gravar por cima de um lote existente).

## D015 — Portas do object store configuráveis no host

**Contexto:** na primeira subida na máquina do dono (Windows + Docker Desktop + WSL2), o `make up` falhou ao
publicar a 9001: o Windows reservava essa porta (`netsh interface ipv4 show excludedportrange protocol=tcp`
listava 9001-9001) e o redirecionador de portas do Docker Desktop devolveu erro 500. O erro vem do
sistema operacional, e a D011 (portas padrão) assumia que elas estariam livres.
**Decisão:** o lado do host das portas 9000 e 9001 vem de `MINIO_API_PORT` e `MINIO_CONSOLE_PORT` no `.env`, com
os padrões de antes. A D011 continua valendo como padrão; a variável é a saída quando o Windows reserva a porta.
**Consequência:** nada muda dentro da rede do compose (`S3_ENDPOINT` segue `http://minio:9000`). Quem já tem
`.env` precisa acrescentar a variável. O `S3_ENDPOINT` do `.env` (usado só fora do compose) deve acompanhar
`MINIO_API_PORT` se ela mudar.

## D016 — Objeto endereçado pelo conteúdo, recebimento por arquivo e bronze lida do raw

**Contexto:** o inventário (`custodia.documento`) tem uma linha por objeto. O reenvio idêntico precisa
aparecer na contagem do lote sem duplicar a custódia, a bronze tem uma linha por arquivo recebido e precisa ser
reconstruível a partir do raw. Com só o inventário, um arquivo reenviado não teria de onde ser reconstruído.
**Decisão:** a chave do objeto é derivada do conteúdo: `nfe/AAAA/MM/<chave>/<sha256>.xml` (e `evento/...`,
`invalido/<sha256>.xml` para o ilegível). `custodia.recebimento` registra cada arquivo entregue em cada lote e
aponta para o objeto, com o sha256 recebido e o declarado no romaneio. A bronze é carregada a partir do
recebimento, lendo do bucket pela versão registrada e conferindo o hash, e não da pasta de rede. A conferência
contra o romaneio fica em `ctrl.lote_divergencia` (FALTANTE, NAO_DECLARADO, HASH_DIVERGENTE). Arquivo faltante
ou fora do romaneio deixa o lote `INCOMPLETO`; hash divergente não, porque o arquivo chegou e a quarentena
da silver decide o destino dele (D007). A segunda entrega de um lote já carregado é recusada e vai para
`rejeitados/`.
**Consequência:** reenvio idêntico = um objeto, dois recebimentos. Mesma chave com conteúdo diferente = dois
objetos na mesma pasta da chave. Toda carga da bronze lê o raw, então a reconstrução é o caminho de sempre e
não um procedimento de exceção; `make teste-bronze` prova isso comparando uma bronze paralela com a real.
O PUT envia o sha256 (`x-amz-checksum-sha256`) e o object store recusa o objeto se o conteúdo chegar diferente.

