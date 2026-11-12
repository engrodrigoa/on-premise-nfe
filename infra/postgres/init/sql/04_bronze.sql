-- ============================================================================
-- Bronze: cópia de trabalho do XML, 1 linha por ARQUIVO recebido, só INSERT.
-- Pode ser apagada e reconstruída a partir do raw (decisão D003).
-- Nenhuma regra de negócio aqui: arquivo malformado entra com conteudo NULL e o motivo;
-- quem decide o destino dele é a silver (quarentena).
-- ============================================================================
CREATE TABLE IF NOT EXISTS bronze.documento_xml (
    bronze_id        bigserial   PRIMARY KEY,
    lote_id          bigint      NOT NULL REFERENCES ctrl.lote (lote_id),
    arquivo          text        NOT NULL,
    documento_id     bigint      NOT NULL REFERENCES custodia.documento (documento_id),
    tipo_documento   text        NOT NULL CHECK (tipo_documento IN ('nfe', 'evento', 'invalido')),
    sha256           char(64)    NOT NULL,
    tamanho_bytes    bigint      NOT NULL,
    conteudo         xml,                     -- NULL quando o arquivo não é um XML bem formado
    erro_leitura     text,                    -- mensagem do parser quando conteudo é NULL
    inicio_conteudo  text,                    -- primeiros caracteres, para diagnóstico
    carregado_em     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lote_id, arquivo)
);
CREATE INDEX IF NOT EXISTS ix_bronze_lote ON bronze.documento_xml (lote_id);

COMMENT ON TABLE bronze.documento_xml IS 'XML bruto, 1 linha por arquivo recebido (reenvio = outra linha). Só INSERT';

-- Converte texto em xml sem derrubar a carga: malformado devolve NULL.
CREATE OR REPLACE FUNCTION bronze.fn_xml(p_texto text)
RETURNS xml
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    RETURN XMLPARSE(DOCUMENT p_texto);
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;
END $$;
