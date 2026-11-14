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

## D017 — Duas páginas no Grafana: negócio e operação

**Contexto:** o desenho original (`docs/05_dashboard.md`) previa uma página única para o fiscal e o
financeiro, com a saúde da carga resumida num selo. Na validação do marco 3 ficou claro que quem sustenta o
pipeline precisa ver a carga antes de existir gold, e que o selo não diz o que fazer quando algo falha.
**Decisão:** uma segunda página, "NF-e · Saúde da carga", para a operação, construída já sobre `ctrl` e
`custodia`. A página de negócio do marco 6 continua única para o seu público, e o selo dela passa a apontar
para esta. Entra no painel só o que responde uma pergunta de sim ou não com limite definido; métricas de volume
ficam de fora.
**Consequência:** a linha 2 (observabilidade) ganha tela própria e testada. A página de operação lê só `ctrl` e
`custodia` (D009 continua valendo). Os limites (26 h, 30 min, 600 s) ficam no JSON e na tabela do
`docs/05_dashboard.md`; mudar um limite é mudar os dois.

> Revista em parte pela D018: volume entra como contexto e como sinal de anomalia.
> Revista em parte pela D025: a linha 3 (qualidade do conteúdo) ganha uma terceira página.

## D018 — Volume no painel de operação: contexto e anomalia, não contador

**Contexto:** na primeira revisão do painel de saúde, o dono pediu números de volume (execuções, lotes, XML,
taxa de eficiência) e uma série diária. A D017 tinha deixado volume de fora por não mudar decisão.
**Decisão:** volume entra de três formas, numa linha menor abaixo do veredito: (1) **lotes e XML no período**,
como denominador dos indicadores de cima; (2) **% de lotes sem intervenção** no lugar da taxa de sucesso do
Airflow, que conta como sucesso as execuções em que o sensor não achou lote; (3) **volume do último lote
comparado ao mesmo dia da semana nas 4 semanas anteriores**, com faixa verde de ±50%. É o pilar "volume" da
observabilidade de dados: um dia com metade das notas é sinal, mesmo com o romaneio fechado. Também entra a série
diária de XML e lotes. Execuções e falhas do Airflow ficam de fora: são em maioria execuções vazias, estão no
banco do Airflow (fora do alcance da `grafana_ro`), e a falha que importa já vira `ERRO` em `ctrl.lote`.
**Consequência:** o painel passa de 8 para 13 elementos, mas mantém a hierarquia: veredito, contexto, ação,
séries, detalhe. Sem pelo menos 2 semanas de histórico, o indicador de volume mostra "histórico curto".

## D019 — Falha da silver aparece em `ctrl.lote` sem apagar o status da entrega

**Contexto:** `marcar_erro` não mexe em lote `CARREGADO`, `INCOMPLETO` ou `PROCESSADO` (os estados finais da
carga). Com o dbt depois de `carregar_bronze`, uma falha da silver ficaria verde em `ctrl.lote` e só apareceria
na cor da execução do Airflow, contra a regra "execução verde não é carga saudável". Trocar o status para `ERRO`
apagaria a informação de que a entrega veio incompleta.
**Decisão:** `ctrl.lote` ganha `transformado_em` (primeira vez que a silver incluiu o lote) e
`erro_transformacao`. As tasks da silver têm callback próprio, que grava a falha nessa coluna e deixa o status
como está; o próximo registro bem-sucedido limpa. Lote `CARREGADO` ou `INCOMPLETO` há mais de 30 min sem
`transformado_em` também é falha de processo no painel de saúde.
**Consequência:** o status continua dizendo como a entrega chegou, e a silver tem a sua própria evidência. Lotes
carregados antes do marco 4 aparecem como falha até a primeira execução da silver (`make silver` ou a próxima
carga).

## D020 — Fechamento da silver: bronze = válidos + quarentena + reenvios descartados

**Contexto:** o critério do roadmap era "bronze = válidos + quarentena". Mas o reenvio idêntico gera duas
linhas na bronze (D016: um objeto, dois recebimentos) e um documento só. A conta nunca fecharia, ou o reenvio
teria de ir para a quarentena, que é para documento que não é íntegro (D007).
**Decisão:** `silver.documento` dá um destino a cada linha da bronze: `valido`, `quarentena` (com motivo) ou
`reenvio_descartado` (aponta para o documento que vale). O fechamento é feito por lote no dbt (teste) e no
registro (`pipelines/qualidade/registrar.py`), que quebra a task se a silver não cobrir a bronze inteira.
**Consequência:** `ctrl.lote` ganha `qtd_reenvios` e `ctrl.v_lote` ganha `silver_fechada`. Nenhum arquivo some
entre a bronze e a silver sem deixar rastro, e reenvio não infla a quarentena.

## D021 — Silver recriada inteira a partir da bronze a cada carga

**Contexto:** o datalabs usa merge incremental por chave, filtrado pelos lotes pendentes. Aqui o evento pode
chegar antes da nota (fica em quarentena e precisa ser reavaliado) e o cancelamento pode chegar dias depois
(muda a situação de uma nota de outro lote). O volume é de ~25 mil notas por ano.
**Decisão:** todos os modelos da silver são `table`, recriados a cada execução. A bronze é a fonte e já é
reconstruível do raw (D003), então a silver é sempre função da bronze.
**Consequência:** reavaliação e situação saem sem lógica extra, e uma regra nova vale para o histórico na
próxima carga (útil na transição da reforma). Os números de `ctrl.lote` são a fotografia atual; o que se sabia
em cada momento fica em `ctrl.dq`, que só ganha linha quando o número muda. Gatilho para rever: `dbt build` da
silver acima de 5 minutos (16 lotes levaram cerca de 7 s no ambiente de teste).

## D022 — Mesma chave com outro conteúdo: vale o primeiro recebido

**Contexto:** a nota autorizada é um fato jurídico único; dois XMLs diferentes para a mesma chave significam que
um deles não é o autorizado. Sem validar assinatura (D010), o pipeline não sabe qual. O datalabs adota "vale o
mais recente", o que deixaria um reenvio adulterado substituir em silêncio uma nota já carregada.
**Decisão:** vale o primeiro recebido (menor lote). No mesmo lote, vale o arquivo com o nome no padrão da origem
(`<chave>-procNFe.xml`) e, depois, a ordem de chegada na bronze. O outro conteúdo vai para a quarentena com o
motivo `chave_conteudo_divergente`; conteúdo idêntico é `reenvio_descartado` (D020). A mesma regra vale para
eventos, pela chave, tipo e sequência.
**Consequência:** o número já publicado não muda por causa de um reenvio. O alerta de custódia
(`custodia.v_chave_com_conteudos_diferentes`) e a quarentena apontam o mesmo caso para o fiscal decidir.

## D023 — Lote `INCOMPLETO` também segue para a silver

**Contexto:** `INCOMPLETO` diz que faltou arquivo (ou chegou arquivo fora do romaneio). Os que chegaram são
notas válidas por si.
**Decisão:** a silver processa toda a bronze, sem filtrar por status do lote. A falha de entrega continua
acusada no status e no painel de saúde.
**Consequência:** o fiscal vê as notas que chegaram sem esperar o runbook de reenvio (marco 7). O faltante entra
quando a origem o entregar, e a silver recalcula o que depender dele (salto de numeração, evento sem nota).

## D024 — Agenda horária com sensor de 55 minutos

**Contexto:** a agenda de 10 em 10 minutos gerava cerca de 140 execuções vazias por dia (pendência do marco 3),
poluindo o histórico do Airflow. Disparo por chegada de arquivo exigiria um gatilho de evento que o Airflow 3.4
não traz pronto para pasta.
**Decisão:** `@hourly`, com o sensor em modo `reschedule` olhando a inbox a cada 60 s por até 55 minutos. Havendo
fila, a execução continua disparando a próxima no fim (`proximo_lote`).
**Consequência:** 24 execuções por dia, e o lote publicado entra em cerca de um minuto, como antes. O sensor em
`reschedule` não ocupa um slot de execução enquanto espera.

## D025 — Terceira página no Grafana: qualidade do conteúdo

**Contexto:** a D017 deixou a página de operação para a linha 2, com teste que limita o número de painéis e o
leiaute a uma tela. A quarentena e os avisos respondem a outra pergunta (o conteúdo é válido?), para outro
público (TI do ERP e fiscal).
**Decisão:** página própria, "NF-e · Qualidade do conteúdo" (uid `nfe-qualidade`), lendo só `ctrl.dq` e
`ctrl.v_lote`. O registro da silver grava em `ctrl.dq` também os motivos de quarentena (severidade `erro`), além
dos avisos, para o painel não precisar ler a silver (D009). Na página de operação entra só a falha da silver (D019).
**Consequência:** a role `grafana_ro` não muda, e o detalhe com dado pessoal continua só na silver. A página de
negócio do marco 6 pode apontar para esta como apontará para a de saúde.

