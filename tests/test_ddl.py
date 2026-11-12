"""DDL de controle (infra/postgres/init). Precisa de um Postgres com o bootstrap aplicado."""
import psycopg2
import pytest

pytestmark = pytest.mark.db

TABELAS = ["ctrl.lote", "ctrl.dq", "ctrl.reconciliacao", "ctrl.integridade_custodia",
           "custodia.documento", "bronze.documento_xml",
           "custodia.recebimento", "ctrl.lote_divergencia"]


def _lote(cur, nome="lote_teste"):
    cur.execute("INSERT INTO ctrl.lote (nome_lote) VALUES (%s) RETURNING lote_id", (nome,))
    return cur.fetchone()[0]


@pytest.mark.parametrize("tabela", TABELAS + ["ctrl.v_lote", "custodia.v_chave_com_conteudos_diferentes"])
def test_objeto_existe(conexao_db, tabela):
    with conexao_db.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (tabela,))
        assert cur.fetchone()[0] is not None


def test_status_do_lote_e_restrito(conexao_db):
    with conexao_db.cursor() as cur:
        lote = _lote(cur)
        cur.execute("UPDATE ctrl.lote SET status = 'INCOMPLETO' WHERE lote_id = %s", (lote,))
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("UPDATE ctrl.lote SET status = 'QUASE' WHERE lote_id = %s", (lote,))


def test_entrega_completa_compara_declarado_com_encontrado(conexao_db):
    with conexao_db.cursor() as cur:
        lote = _lote(cur)
        cur.execute("UPDATE ctrl.lote SET declarado_arquivos = 100, qtd_arquivos = 60 WHERE lote_id = %s", (lote,))
        cur.execute("SELECT entrega_completa FROM ctrl.v_lote WHERE lote_id = %s", (lote,))
        assert cur.fetchone()[0] is False
        cur.execute("UPDATE ctrl.lote SET qtd_arquivos = 100 WHERE lote_id = %s", (lote,))
        cur.execute("SELECT entrega_completa FROM ctrl.v_lote WHERE lote_id = %s", (lote,))
        assert cur.fetchone()[0] is True


def test_reconciliacao_calcula_diferenca_e_confere(conexao_db):
    with conexao_db.cursor() as cur:
        lote = _lote(cur)
        cur.execute(
            """INSERT INTO ctrl.reconciliacao (escopo, lote_id, fronteira, metrica, esperado, encontrado)
               VALUES ('lote', %s, 'origem_raw', 'soma_vnf', 1000.00, 1000.00),
                      ('lote', %s, 'raw_bronze', 'soma_vnf', 1000.00,  990.50)
               RETURNING diferenca, confere""", (lote, lote))
        assert [(float(d), c) for d, c in cur.fetchall()] == [(0.0, True), (-9.5, False)]


def test_reconciliacao_de_lote_exige_lote(conexao_db):
    with conexao_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("""INSERT INTO ctrl.reconciliacao (escopo, fronteira, metrica, esperado, encontrado)
                           VALUES ('lote', 'origem_raw', 'soma_vnf', 1, 1)""")


def test_custodia_nao_aceita_o_mesmo_objeto_duas_vezes(conexao_db):
    sql = """INSERT INTO custodia.documento (tipo_documento, chave_acesso, cnpj_emitente, sha256, tamanho_bytes,
                                              bucket, objeto, lote_id, arquivo_origem, reter_ate)
             VALUES ('nfe', %s, '12345678000199', %s, 10, 'nfe-raw', %s, %s, 'a.xml', current_date + 2190)"""
    with conexao_db.cursor() as cur:
        lote = _lote(cur)
        cur.execute(sql, ("5" * 44, "a" * 64, "nfe/x-aaaa.xml", lote))
        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(sql, ("5" * 44, "a" * 64, "nfe/x-aaaa.xml", lote))


def test_mesma_chave_com_conteudos_diferentes_aparece_no_alerta(conexao_db):
    sql = """INSERT INTO custodia.documento (tipo_documento, chave_acesso, cnpj_emitente, sha256, tamanho_bytes,
                                              bucket, objeto, lote_id, arquivo_origem, reter_ate)
             VALUES ('nfe', %s, '12345678000199', %s, 10, 'nfe-raw', %s, %s, 'a.xml', current_date + 2190)"""
    chave = "7" * 44
    with conexao_db.cursor() as cur:
        lote = _lote(cur)
        cur.execute(sql, (chave, "a" * 64, "nfe/k-aaaa.xml", lote))
        cur.execute(sql, (chave, "b" * 64, "nfe/k-bbbb.xml", lote))
        cur.execute("SELECT qtd_conteudos FROM custodia.v_chave_com_conteudos_diferentes WHERE chave_acesso = %s",
                    (chave,))
        assert cur.fetchone()[0] == 2


def test_fn_xml_devolve_null_para_xml_malformado(conexao_db):
    with conexao_db.cursor() as cur:
        cur.execute("SELECT bronze.fn_xml('<a><b>1</b></a>') IS NOT NULL, bronze.fn_xml('<a><b>1</b') IS NULL")
        assert cur.fetchone() == (True, True)


def test_grafana_le_gold_e_controle_mas_nao_bronze_nem_silver(conexao_db):
    with conexao_db.cursor() as cur:
        cur.execute("""SELECT has_schema_privilege('grafana_ro', 'ctrl', 'USAGE'),
                              has_schema_privilege('grafana_ro', 'gold', 'USAGE'),
                              has_table_privilege('grafana_ro', 'ctrl.lote', 'SELECT'),
                              has_table_privilege('grafana_ro', 'ctrl.lote', 'INSERT'),
                              has_schema_privilege('grafana_ro', 'bronze', 'USAGE'),
                              has_schema_privilege('grafana_ro', 'silver', 'USAGE')""")
        assert cur.fetchone() == (True, True, True, False, False, False)
