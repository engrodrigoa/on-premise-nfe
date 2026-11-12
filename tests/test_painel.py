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
    """Critério de desenho (D017, D018): veredito no topo (5), contexto logo abaixo (4), o resto é detalhe."""
    assert len(painel["panels"]) <= 13
    stats = sorted((p["gridPos"]["y"], p["title"]) for p in painel["panels"] if p["type"] == "stat")
    assert [y for y, _ in stats].count(0) == 5        # a linha do veredito
    assert len(stats) == 9


def test_leiaute_sem_sobreposicao(painel):
    ocupado = set()
    for p in painel["panels"]:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24, p["title"]
        celulas = {(x, y) for x in range(g["x"], g["x"] + g["w"]) for y in range(g["y"], g["y"] + g["h"])}
        assert not celulas & ocupado, f"sobreposição em {p['title']}"
        ocupado |= celulas
    # veredito, contexto, ação e séries cabem numa tela; só a tabela de detalhe fica abaixo da dobra
    fim = {p["title"]: p["gridPos"]["y"] + p["gridPos"]["h"] for p in painel["panels"]}
    assert max(v for k, v in fim.items() if k != "Divergências arquivo a arquivo") <= 22


def test_so_le_ctrl_e_custodia(painel):
    """D009: o Grafana não lê bronze nem silver (a role nem tem acesso)."""
    for p in painel["panels"]:
        sql = p["targets"][0]["rawSql"]
        esquemas = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)\.", sql, flags=re.IGNORECASE))
        assert esquemas and esquemas <= {"ctrl", "custodia"}, f"{p['title']}: {esquemas}"


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


def test_volume_fora_do_padrao(painel, conexao_db):
    """Último lote 60% acima da média do mesmo dia da semana: o painel mostra +60."""
    cur = conexao_db.cursor()
    # sextas-feiras de um ano distante, para ficarem por último na ordem dos lotes
    for dia, qtd in (("9999-10-29", 100), ("9999-11-05", 100), ("9999-11-12", 100), ("9999-11-19", 100),
                     ("9999-11-25", 10), ("9999-11-26", 160)):
        _lote(cur, f"lote_{dia}", "CARREGADO", declarado_arquivos=qtd, qtd_arquivos=qtd, qtd_bronze=qtd,
              carregado_em="now")
    assert _valor(cur, painel, "Volume do último lote") == 60


def test_volume_sem_historico(painel, conexao_db):
    cur = conexao_db.cursor()
    _lote(cur, "lote_9999-12-31", "CARREGADO", declarado_arquivos=50, qtd_arquivos=50, qtd_bronze=50,
          carregado_em="now")
    assert _valor(cur, painel, "Volume do último lote") is None       # o painel mostra "histórico curto"

