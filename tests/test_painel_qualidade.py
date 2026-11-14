"""Painel "NF-e · Qualidade do conteúdo" (grafana/dashboards/qualidade_conteudo.json).

Sem stack: estrutura, leiaute e fontes permitidas (só ctrl, D009). Com DB_HOST: cada consulta roda com a role
do Grafana (grafana_ro) sobre um cenário plantado na transação do teste, e o painel tem de ler a situação MAIS
RECENTE de cada lote x regra em ctrl.dq (o histórico fica, mas não soma duas vezes).
"""
import json
import re
from pathlib import Path

import pytest

PAINEL = Path(__file__).resolve().parents[1] / "grafana" / "dashboards" / "qualidade_conteudo.json"


@pytest.fixture(scope="module")
def painel() -> dict:
    return json.loads(PAINEL.read_text(encoding="utf-8"))


def _sql(painel: dict, titulo: str) -> str:
    p = next(p for p in painel["panels"] if p["title"] == titulo)
    sql = p["targets"][0]["rawSql"]
    return re.sub(r"\$__timeFilter\(([^)]+)\)", r"(\1 BETWEEN now() - interval '30 days' AND now() + interval '1 minute')", sql)


def test_estrutura(painel):
    assert painel["uid"] == "nfe-qualidade"
    assert len({p["id"] for p in painel["panels"]}) == len(painel["panels"])
    for p in painel["panels"]:
        assert p["datasource"]["uid"] == "nfe" and p["targets"][0]["datasource"]["uid"] == "nfe"
        assert p.get("description"), f"painel sem descrição: {p['title']}"
    assert [p["title"] for p in painel["panels"] if p["gridPos"]["y"] == 0] == [
        "Arquivos em quarentena", "Avisos fiscais", "Avisos por 100 notas", "Silver em dia?"]


@pytest.mark.parametrize("arquivo", ["saude_carga.json", "qualidade_conteudo.json"])
def test_stat_de_texto_aponta_o_campo(arquivo):
    """O stat do Grafana só reduz colunas numéricas quando `fields` fica vazio: um veredito em texto (SIM, OK,
    NUNCA VERIFICADA) some e aparece "sem dados". Pego na validação do marco 4 no "Silver em dia?"."""
    dados = json.loads((PAINEL.parent / arquivo).read_text(encoding="utf-8"))
    for p in dados["panels"]:
        if p["type"] != "stat":
            continue
        textos = [k for m in p["fieldConfig"]["defaults"].get("mappings", []) if m["type"] == "value"
                  for k in m["options"] if not k.replace(".", "").isdigit()]
        if textos:
            campo = p["options"]["reduceOptions"]["fields"]
            assert campo.startswith("/^") and campo.endswith("$/"), f"{arquivo}: {p['title']} sem o campo de texto"
            alias = campo[2:-2]
            assert re.search(rf"\bAS {alias}\b", p["targets"][0]["rawSql"]), f"{p['title']}: alias {alias} ausente"


def test_leiaute_sem_sobreposicao(painel):
    ocupado = set()
    for p in painel["panels"]:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24, p["title"]
        celulas = {(x, y) for x in range(g["x"], g["x"] + g["w"]) for y in range(g["y"], g["y"] + g["h"])}
        assert not celulas & ocupado, f"sobreposição em {p['title']}"
        ocupado |= celulas


def test_so_le_ctrl(painel):
    """D009: o painel não lê silver nem bronze; a quarentena chega por ctrl.dq (registrar.py)."""
    for p in painel["panels"]:
        sql = p["targets"][0]["rawSql"]
        esquemas = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)\.", sql, flags=re.IGNORECASE))
        assert esquemas == {"ctrl"}, f"{p['title']}: {esquemas}"


def test_provisionado_na_mesma_pasta():
    """O provider do Grafana carrega todo JSON de grafana/dashboards; os dois painéis ficam na pasta NF-e."""
    assert PAINEL.parent.name == "dashboards"


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


def _lote(cur, nome, **col) -> int:
    col = {"nome_lote": nome, "status": "CARREGADO", "carregado_em": "now", **col}
    cur.execute(f"INSERT INTO ctrl.lote ({', '.join(col)}) VALUES ({', '.join(['%s'] * len(col))}) RETURNING lote_id",
                list(col.values()))
    return cur.fetchone()[0]


def _dq(cur, lote, regra, severidade, avaliados, falhas, quando):
    cur.execute("""INSERT INTO ctrl.dq (lote_id, regra, severidade, qtd_avaliados, qtd_falhas, executado_em)
                   VALUES (%s, %s, %s, %s, %s, now() - %s::interval)""",
                (lote, regra, severidade, avaliados, falhas, quando))


def test_quarentena_le_so_a_situacao_mais_recente(painel, conexao_db):
    cur = conexao_db.cursor()
    antes = _valor(cur, painel, "Arquivos em quarentena")
    motivos_antes = {m: n for m, n, *_ in _como_grafana(cur, _sql(painel, "Quarentena por motivo"))}
    lote = _lote(cur, "lote_8886-01-01")
    _dq(cur, lote, "evento_sem_nota", "erro", 50, 1, "2 hours")       # o evento chegou antes da nota...
    _dq(cur, lote, "evento_sem_nota", "erro", 50, 0, "1 hour")        # ...e a nota chegou depois
    _dq(cur, lote, "xml_corrompido", "erro", 50, 2, "1 hour")
    _dq(cur, lote, "tot_cbs_divergente", "aviso", 40, 3, "1 hour")
    assert _valor(cur, painel, "Arquivos em quarentena") == antes + 2

    motivos = {m: n for m, n, *_ in _como_grafana(cur, _sql(painel, "Quarentena por motivo"))}
    assert motivos.get("evento_sem_nota", 0) == motivos_antes.get("evento_sem_nota", 0)    # a linha antiga não soma
    assert motivos["xml_corrompido"] == motivos_antes.get("xml_corrompido", 0) + 2


def test_silver_em_dia(painel, conexao_db):
    cur = conexao_db.cursor()
    _lote(cur, "lote_8886-02-01", erro_transformacao="transformar_silver: exit 1")
    assert _valor(cur, painel, "Silver em dia?").startswith("NÃO")


def test_todas_as_consultas_rodam_como_grafana(painel, conexao_db):
    cur = conexao_db.cursor()
    for p in painel["panels"]:
        _como_grafana(cur, _sql(painel, p["title"]))
