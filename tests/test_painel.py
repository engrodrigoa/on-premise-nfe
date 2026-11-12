"""Painel "NF-e · Saúde da carga" (grafana/dashboards/saude_carga.json).

Sem stack: estrutura, leiaute e fontes permitidas. Com DB_HOST: cada consulta roda com a role do Grafana
(grafana_ro) sobre um cenário montado na transação do teste (desfeita no fim), e o painel tem de acusar
exatamente o que foi plantado.
"""
import json
import re
from pathlib import Path

import pytest

PAINEL = Path(__file__).resolve().parents[1] / "grafana" / "dashboards" / "saude_carga.json"


@pytest.fixture(scope="module")
def painel() -> dict:
    return json.loads(PAINEL.read_text(encoding="utf-8"))


def _sql(painel: dict, titulo: str) -> str:
    p = next(p for p in painel["panels"] if p["title"] == titulo)
    sql = p["targets"][0]["rawSql"]
    # macro do Grafana -> período de 30 dias (o padrão do painel)
    return re.sub(r"\$__timeFilter\(([^)]+)\)", r"(\1 BETWEEN now() - interval '30 days' AND now() + interval '1 minute')", sql)


# ------------------------------------------------------------------------------------------- sem stack
def test_estrutura(painel):
    assert painel["uid"] == "nfe-saude-carga"          # links e o selo do marco 6 apontam para este uid
    ids = [p["id"] for p in painel["panels"]]
    assert len(ids) == len(set(ids))
    for p in painel["panels"]:
        assert p["datasource"]["uid"] == "nfe"
        assert p["targets"][0]["datasource"]["uid"] == "nfe"
        assert p.get("description"), f"painel sem descrição: {p['title']}"


def test_painel_enxuto(painel):
    """Critério de desenho: poucos painéis, cada um respondendo uma pergunta. Mais que 8 é sinal de vaidade."""
    assert len(painel["panels"]) <= 8
    assert sum(p["type"] == "stat" for p in painel["panels"]) == 5


def test_leiaute_sem_sobreposicao(painel):
    ocupado = set()
    for p in painel["panels"]:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24, p["title"]
        celulas = {(x, y) for x in range(g["x"], g["x"] + g["w"]) for y in range(g["y"], g["y"] + g["h"])}
        assert not celulas & ocupado, f"sobreposição em {p['title']}"
        ocupado |= celulas
    assert max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in painel["panels"]) <= 22   # cabe numa tela


def test_so_le_ctrl_e_custodia(painel):
    """D009: o Grafana não lê bronze nem silver (a role nem tem acesso)."""
    for p in painel["panels"]:
        sql = p["targets"][0]["rawSql"]
        esquemas = set(re.findall(r"\b([a-z_]+)\.[a-z_]+\b", sql)) - {"l", "d", "r", "u", "a"}
        assert esquemas <= {"ctrl", "custodia"}, f"{p['title']}: {esquemas}"


# ------------------------------------------------------------------------------------------- com banco
def _como_grafana(cur, sql: str) -> list[tuple]:
    cur.execute("SET LOCAL ROLE grafana_ro")
    try:
        cur.execute(sql)
        return cur.fetchall()
    finally:
        cur.execute("RESET ROLE")


def _valor(cur, painel, titulo):
    return _como_grafana(cur, _sql(painel, titulo))[0][0]


def _lote(cur, nome, status, **col) -> int:
    col = {"status": status, "nome_lote": nome, **col}
    cur.execute(f"INSERT INTO ctrl.lote ({', '.join(col)}) VALUES ({', '.join(['%s'] * len(col))}) RETURNING lote_id",
                list(col.values()))
    return cur.fetchone()[0]


def _recebimentos(cur, lote_id, n, prefixo):
    for i in range(n):
        h = f"{prefixo}{i}".ljust(64, "0")
        cur.execute(
            """INSERT INTO custodia.documento (tipo_documento, chave_acesso, sha256, tamanho_bytes, bucket, objeto,
                                               lote_id, arquivo_origem, reter_ate)
               VALUES ('nfe', %s, %s, 1, 'teste-painel', %s, %s, %s, current_date) RETURNING documento_id""",
            ("5" * 43 + str(i), h, f"{prefixo}/{i}.xml", lote_id, f"{i}.xml"))
        cur.execute("INSERT INTO custodia.recebimento (lote_id, arquivo, documento_id, sha256, objeto_novo) "
                    "VALUES (%s, %s, %s, %s, true)", (lote_id, f"{i}.xml", cur.fetchone()[0], h))


def test_painel_acusa_o_cenario_plantado(painel, conexao_db):
    cur = conexao_db.cursor()
    antes = {t: _valor(cur, painel, t) for t in ("Falhas de processo", "Entregas incompletas",
                                                  "Arquivos perdidos no pipeline")}
    tag = "lote_8888-01-0"

    _lote(cur, tag + "1", "ERRO", mensagem="custodiar_lote: object store fora do ar")
    _lote(cur, tag + "2", "CUSTODIADO")
    cur.execute("UPDATE ctrl.lote SET recebido_em = now() - interval '2 hours' WHERE nome_lote = %s", (tag + "2",))
    incompleto = _lote(cur, tag + "3", "INCOMPLETO", declarado_arquivos=5, qtd_arquivos=3, qtd_bronze=3,
                       qtd_faltantes=2, carregado_em="now", mensagem="2 arquivo(s) declarado(s) e não entregue(s)")
    _recebimentos(cur, incompleto, 3, "aa")
    perdeu = _lote(cur, tag + "4", "CARREGADO", qtd_arquivos=2, qtd_bronze=1, carregado_em="now")
    _recebimentos(cur, perdeu, 2, "bb")
    cur.execute("""INSERT INTO ctrl.integridade_custodia (documento_id, resultado, sha256_esperado)
                   SELECT documento_id, 'DIVERGENTE', sha256 FROM custodia.documento WHERE objeto = 'bb/0.xml'""")

    assert _valor(cur, painel, "Falhas de processo") == antes["Falhas de processo"] + 2     # ERRO + preso
    assert _valor(cur, painel, "Entregas incompletas") == antes["Entregas incompletas"] + 1
    assert _valor(cur, painel, "Arquivos perdidos no pipeline") == antes["Arquivos perdidos no pipeline"] + 1
    assert _valor(cur, painel, "Horas sem carga") == 0
    assert _valor(cur, painel, "Custódia íntegra?").startswith("DIVERGENTE")

    acao = _como_grafana(cur, _sql(painel, "O que exige ação"))
    nossas = {(sev, quem, onde) for sev, quem, onde, _, _ in acao if onde.startswith(tag)}
    assert nossas == {("CRÍTICO", "Engenharia", tag + "1"), ("CRÍTICO", "Engenharia", tag + "2"),
                      ("ATENÇÃO", "TI do ERP", tag + "3")}
    assert acao[0][0] == "CRÍTICO"                       # o crítico vem primeiro


def test_todas_as_consultas_rodam_como_grafana(painel, conexao_db):
    cur = conexao_db.cursor()
    for p in painel["panels"]:
        _como_grafana(cur, _sql(painel, p["title"]))
