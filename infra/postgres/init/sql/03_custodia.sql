-- ============================================================================
-- Custódia (raw): inventário do que foi gravado no object store.
-- O arquivo com valor legal está no bucket; esta tabela prova QUAL arquivo é e de onde veio.
-- Só INSERT: uma linha nunca é alterada nem apagada.
-- ============================================================================
CREATE TABLE IF NOT EXISTS custodia.documento (
    documento_id     bigserial   PRIMARY KEY,
    tipo_documento   text        NOT NULL CHECK (tipo_documento IN ('nfe', 'evento', 'invalido')),
    chave_acesso     text,                    -- 44 caracteres; NULL quando o arquivo não é legível
    cnpj_emitente    text,                    -- 14 caracteres (CNPJ pode ser alfanumérico)
    sha256           char(64)    NOT NULL,    -- prova de integridade do conteúdo
    tamanho_bytes    bigint      NOT NULL,
    bucket           text        NOT NULL,
    objeto           text        NOT NULL,    -- chave do objeto no bucket (inclui parte do sha256)
    version_id       text,                    -- versão devolvida pelo object store
    lote_id          bigint      NOT NULL REFERENCES ctrl.lote (lote_id),
    arquivo_origem   text        NOT NULL,    -- nome do arquivo na pasta de rede
    custodiado_em    timestamptz NOT NULL DEFAULT now(),
    reter_ate        date        NOT NULL,    -- política de retenção (CUSTODIA_RETENCAO_DIAS)
    UNIQUE (bucket, objeto)
);
CREATE INDEX IF NOT EXISTS ix_documento_chave ON custodia.documento (chave_acesso);
CREATE INDEX IF NOT EXISTS ix_documento_lote  ON custodia.documento (lote_id);

COMMENT ON TABLE custodia.documento IS 'Inventário da custódia: 1 linha por objeto no bucket, com sha256 e origem';

-- Mesma chave de acesso com conteúdos diferentes: alerta de custódia (docs/03_cenario_dados.md)
CREATE OR REPLACE VIEW custodia.v_chave_com_conteudos_diferentes AS
SELECT tipo_documento, chave_acesso, count(DISTINCT sha256) AS qtd_conteudos,
       min(custodiado_em) AS primeiro_em, max(custodiado_em) AS ultimo_em
FROM custodia.documento
WHERE tipo_documento = 'nfe' AND chave_acesso IS NOT NULL
GROUP BY tipo_documento, chave_acesso
HAVING count(DISTINCT sha256) > 1;

-- ----------------------------------------------------------------------------
-- Linha 2: verificação periódica de integridade (dag_custodia_integridade, marco 7).
-- Recalcula o sha256 do objeto e compara com o inventário.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ctrl.integridade_custodia (
    verificacao_id     bigserial   PRIMARY KEY,
    documento_id       bigint      NOT NULL REFERENCES custodia.documento (documento_id),
    resultado          text        NOT NULL CHECK (resultado IN ('OK', 'DIVERGENTE', 'AUSENTE')),
    sha256_esperado    char(64)    NOT NULL,
    sha256_encontrado  char(64),
    version_id_lido    text,
    verificado_em      timestamptz NOT NULL DEFAULT now(),
    dag_run_id         text
);
CREATE INDEX IF NOT EXISTS ix_integridade_quando ON ctrl.integridade_custodia (verificado_em DESC);

COMMENT ON TABLE ctrl.integridade_custodia IS 'Linha 2: o objeto custodiado continua com o mesmo sha256?';
