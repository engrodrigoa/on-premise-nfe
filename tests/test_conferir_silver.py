"""Conferência do marco 4 (pipelines/qualidade/conferir_silver.py): as partes que não precisam de banco."""
import json
import re
from pathlib import Path

from pipelines.qualidade import conferir_silver as c

CHAVE = "52260191170343000150550010000483241202611320"   # série 001, nNF 48324


def test_pendentes_viram_numeros_da_serie(tmp_path):
    estado = tmp_path / "gerador_estado.json"
    estado.write_text(json.dumps({"pendentes": [
        {"tipo": "nfe", "chave": CHAVE, "arquivo": f"{CHAVE}-procNFe.xml"},
        {"tipo": "evento", "chave": CHAVE, "arquivo": f"{CHAVE}-110111-01-procEventoNFe.xml"},
    ]}))
    assert c.numeros_pendentes(estado) == {("91170343000150", 1, 48324)}
    assert c.numeros_pendentes(tmp_path / "nao_existe.json") == set()


def test_gabarito_so_dos_lotes_carregados(tmp_path):
    for nome in ("lote_2026-01-02", "lote_2026-01-03"):
        (tmp_path / f"{nome}.csv").write_text(
            "lote,arquivo,chave,anomalia,onde_deve_aparecer\n"
            f"{nome},a.xml,{CHAVE},xml_corrompido,quarentena\n", encoding="utf-8")
    linhas = c.ler_gabarito(tmp_path, {"lote_2026-01-02", "lote_2026-01-09"})
    assert [g["lote"] for g in linhas] == ["lote_2026-01-02"]


def _resultado(**extra):
    base = {"fechamento": [{"silver_fechada": True}], "esperado": {"xml_corrompido": 1},
            "achado": {"xml_corrompido": 1}, "faltando": [], "sobrando": [], "explicados": 0,
            "tardios": 1, "tardios_cancelados": 1, "lotes_gabarito": 1, "lotes": 1}
    return {**base, **extra}


def test_codigo_de_saida(capsys):
    assert c.imprimir(_resultado()) == 0
    assert c.imprimir(_resultado(sobrando=["aviso x"])) == 1                      # falso positivo reprova
    assert c.imprimir(_resultado(achado={"xml_corrompido": 0}, faltando=["x"])) == 1
    assert c.imprimir(_resultado(fechamento=[{"silver_fechada": False}])) == 1
    assert c.imprimir(_resultado(tardios=0, tardios_cancelados=0)) == 2           # sem caso: inconclusivo
    assert "INCONCLUSIVO" in capsys.readouterr().out


def test_toda_anomalia_do_gerador_tem_destino_na_conferencia():
    """Anomalia nova no gerador sem lugar na conferência quebraria o gabarito em silêncio."""
    conhecidas = set(c.QUARENTENA) | c.AVISO | c.REENVIO | c.FORA_DA_SILVER
    gerador = (Path(__file__).resolve().parents[1] / "gerador" / "gen_nfe.py").read_text(encoding="utf-8")
    marcadas = set(re.findall(r'\(\s*"([a-z_]+)",\s*"(?:quarentena|ctrl\.dq|ctrl\.lote|custodia|info)"\)', gerador))
    marcadas |= set(re.findall(r'anota\([^,]+,\s*[^,]+,\s*"([a-z_]+)"', gerador))
    marcadas |= set(re.findall(r'"(tot_cbs_divergente|vbc_ibscbs_divergente|cclasstrib_incoerente)"', gerador))
    assert marcadas and marcadas <= conhecidas, marcadas - conhecidas
