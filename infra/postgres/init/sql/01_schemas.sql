-- Camadas do projeto (docs/04_arquitetura.md). Idempotente.
CREATE SCHEMA IF NOT EXISTS custodia;  -- inventário do que está no object store (raw)
CREATE SCHEMA IF NOT EXISTS bronze;    -- XML bruto, cópia de trabalho, reconstruível do raw
CREATE SCHEMA IF NOT EXISTS silver;    -- dbt: nota, item, evento, situação, quarentena
CREATE SCHEMA IF NOT EXISTS gold;      -- dbt: dimensões, fatos, agregados
CREATE SCHEMA IF NOT EXISTS ctrl;      -- o que as linhas 2 (observabilidade) e 3 (qualidade) gravam

COMMENT ON SCHEMA custodia IS 'Inventário do raw: 1 linha por objeto gravado no bucket da custódia';
COMMENT ON SCHEMA bronze   IS 'XML bruto, 1 linha por arquivo recebido, só INSERT';
COMMENT ON SCHEMA silver   IS 'Dados tipados com contrato (dbt)';
COMMENT ON SCHEMA gold     IS 'Modelo dimensional para consumo (dbt)';
COMMENT ON SCHEMA ctrl     IS 'Controle de carga, qualidade e conferência';
