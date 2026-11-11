-- ============================================================================
-- Linha 2 (observabilidade): controle por LOTE. O lote é a unidade de entrega, de carga e de registro.
--
--   RECEBIDO -> CUSTODIADO -> CARREGADO -> PROCESSADO
--                                       -> INCOMPLETO   chegou menos (ou mais) do que a origem declarou
--   qualquer etapa                      -> ERRO         falha de processo
--
-- Execução verde no Airflow não é carga saudável: quem informa a saúde é esta tabela.
-- ============================================================================
CREATE TABLE IF NOT EXISTS ctrl.lote (
    lote_id              bigserial   PRIMARY KEY,
    nome_lote            text        NOT NULL UNIQUE,
    status               text        NOT NULL DEFAULT 'RECEBIDO'
                         CHECK (status IN ('RECEBIDO', 'CUSTODIADO', 'CARREGADO', 'PROCESSADO', 'INCOMPLETO', 'ERRO')),
    -- o que a ORIGEM declarou em controle_lote.json (NULL = lote veio sem arquivo de controle)
    declarado_arquivos   int,
    declarado_nfe        int,
    declarado_eventos    int,
    declarado_vnf        numeric(18,2),
    -- o que o pipeline contou em cada fronteira
    qtd_arquivos         int,           -- arquivos .xml encontrados na pasta do lote
    qtd_custodiados      int,           -- objetos novos gravados no bucket
    qtd_ja_custodiados   int,           -- reenvio idêntico: mesmo objeto já estava no bucket
    qtd_bronze           int,           -- linhas inseridas na bronze
    qtd_validos          int,           -- documentos que chegaram à silver
    qtd_quarentena       int,           -- documentos barrados, com motivo
    recebido_em          timestamptz NOT NULL DEFAULT now(),
    custodiado_em        timestamptz,
    carregado_em         timestamptz,
    processado_em        timestamptz,
    dag_run_id           text,
    mensagem             text
);
CREATE INDEX IF NOT EXISTS ix_lote_status ON ctrl.lote (status);

-- Marco 3: conferência arquivo a arquivo contra o romaneio (D014, D016)
ALTER TABLE ctrl.lote ADD COLUMN IF NOT EXISTS qtd_faltantes       int;  -- no romaneio, não chegou
ALTER TABLE ctrl.lote ADD COLUMN IF NOT EXISTS qtd_nao_declarados  int;  -- chegou, não está no romaneio
ALTER TABLE ctrl.lote ADD COLUMN IF NOT EXISTS qtd_hash_divergente int;  -- chegou diferente do que saiu do ERP

-- O detalhe: qual arquivo, e o que divergiu. 1 linha por divergência, só INSERT.
CREATE TABLE IF NOT EXISTS ctrl.lote_divergencia (
    divergencia_id    bigserial   PRIMARY KEY,
    lote_id           bigint      NOT NULL REFERENCES ctrl.lote (lote_id),
    arquivo           text        NOT NULL,
    tipo              text        NOT NULL CHECK (tipo IN ('FALTANTE', 'NAO_DECLARADO', 'HASH_DIVERGENTE')),
    sha256_declarado  char(64),
    sha256_recebido   char(64),
    registrado_em     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lote_id, arquivo, tipo)
);
COMMENT ON TABLE ctrl.lote_divergencia IS 'Linha 2: arquivo do lote que não confere com o romaneio da origem';

COMMENT ON TABLE  ctrl.lote IS 'Controle de carga por lote (linha 2). 1 linha por lote entregue pela origem';
COMMENT ON COLUMN ctrl.lote.declarado_vnf IS 'Soma de vNF declarada pela origem: ponto de partida da conferência dos números';

-- Leitura pronta para o painel e para o runbook
-- DROP + CREATE: a view usa l.*, e CREATE OR REPLACE falha quando a tabela ganha coluna
DROP VIEW IF EXISTS ctrl.v_lote;
CREATE VIEW ctrl.v_lote AS
SELECT l.*,
       (l.declarado_arquivos IS NOT NULL AND l.qtd_arquivos = l.declarado_arquivos) AS entrega_completa,
       extract(epoch FROM l.custodiado_em - l.recebido_em)::int                      AS seg_custodia,
       extract(epoch FROM l.carregado_em  - l.custodiado_em)::int                    AS seg_bronze,
       extract(epoch FROM l.processado_em - l.carregado_em)::int                     AS seg_transformacao,
       -- lote terminado: duração da carga até a última etapa concluída. Em andamento ou ERRO: há quanto tempo
       -- está aberto (cresce até alguém agir). Antes usava coalesce(processado_em, now()) e virava a idade do
       -- lote, porque processado_em só existe a partir da gold.
       extract(epoch FROM CASE WHEN l.status IN ('CARREGADO', 'INCOMPLETO', 'PROCESSADO')
                               THEN greatest(l.custodiado_em, l.carregado_em, l.processado_em)
                               ELSE now() END - l.recebido_em)::int                  AS seg_total
FROM ctrl.lote l;
