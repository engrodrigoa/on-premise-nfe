-- ============================================================================
-- Linha 3 (qualidade) e linha 4 (comprovação).
-- ============================================================================

-- Resultado das regras por execução. Erro barra (quarentena); aviso sinaliza e a nota segue (decisão D007).
CREATE TABLE IF NOT EXISTS ctrl.dq (
    dq_id          bigserial   PRIMARY KEY,
    lote_id        bigint      REFERENCES ctrl.lote (lote_id),
    regra          text        NOT NULL,
    severidade     text        NOT NULL CHECK (severidade IN ('erro', 'aviso')),
    qtd_avaliados  int         NOT NULL,
    qtd_falhas     int         NOT NULL,
    executado_em   timestamptz NOT NULL DEFAULT now(),
    dag_run_id     text
);
CREATE INDEX IF NOT EXISTS ix_dq_lote ON ctrl.dq (lote_id, regra);

COMMENT ON TABLE ctrl.dq IS 'Linha 3: quantas notas caíram em cada regra, por execução';

-- Conferência dos números em cada fronteira. O painel lê esta tabela para o selo (decisão D009).
--   escopo 'lote'       confere o lote em trânsito   (origem -> raw -> bronze -> silver)
--   escopo 'acumulado'  confere a base inteira       (silver -> gold)
CREATE TABLE IF NOT EXISTS ctrl.reconciliacao (
    reconciliacao_id  bigserial     PRIMARY KEY,
    escopo            text          NOT NULL CHECK (escopo IN ('lote', 'acumulado')),
    lote_id           bigint        REFERENCES ctrl.lote (lote_id),
    fronteira         text          NOT NULL
                      CHECK (fronteira IN ('origem_raw', 'raw_bronze', 'bronze_silver', 'silver_gold')),
    metrica           text          NOT NULL
                      CHECK (metrica IN ('qtd_documentos', 'soma_vnf', 'soma_cbs', 'soma_ibs')),
    esperado          numeric(20,2) NOT NULL,
    encontrado        numeric(20,2) NOT NULL,
    diferenca         numeric(20,2) GENERATED ALWAYS AS (encontrado - esperado) STORED,
    confere           boolean       GENERATED ALWAYS AS (encontrado = esperado) STORED,
    executado_em      timestamptz   NOT NULL DEFAULT now(),
    dag_run_id        text,
    CHECK ((escopo = 'lote') = (lote_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_reconciliacao_lote   ON ctrl.reconciliacao (lote_id, fronteira, metrica);
CREATE INDEX IF NOT EXISTS ix_reconciliacao_quando ON ctrl.reconciliacao (executado_em DESC);

COMMENT ON TABLE ctrl.reconciliacao IS 'Linhas 3 e 4: valor esperado x encontrado em cada fronteira; base do selo do painel';
