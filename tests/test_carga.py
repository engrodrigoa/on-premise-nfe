"""Linha de carga do marco 3: pasta de rede -> custódia -> bronze.

Sem stack: funções puras. Com DB_HOST: o lote de ponta a ponta contra o Postgres e um S3 emulado (moto),
comparando o resultado com o gabarito do gerador.
"""
import csv
import json
import random
import uuid
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from gerador import gen_nfe
from pipelines.carga import lote as L

NFE = (b'<?xml version="1.0" encoding="UTF-8"?><nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">'
       b'<NFe><infNFe Id="NFe52260111281919000117550010000000011000000014"><emit><CNPJ>11281919000117</CNPJ>'
       b'</emit></infNFe></NFe><protNFe><infProt><chNFe>52260111281919000117550010000000011000000014</chNFe>'
       b'</infProt></protNFe></nfeProc>')
EVENTO = (b'<procEventoNFe xmlns="http://www.portalfiscal.inf.br/nfe"><evento><infEvento><CNPJ>11281919000117'
          b'</CNPJ><chNFe>52260111281919000117550010000000011000000014</chNFe></infEvento></evento></procEventoNFe>')


# ------------------------------------------------------------------------------------------- sem stack
def test_identifica_nfe_evento_e_ilegivel():
    assert L.identificar(NFE) == L.Identidade("nfe", "52260111281919000117550010000000011000000014", "11281919000117")
    assert L.identificar(EVENTO).tipo == "evento"
    assert L.identificar(EVENTO).chave == "52260111281919000117550010000000011000000014"
    truncado = L.identificar(NFE[:150])
    assert truncado.tipo == "invalido" and truncado.chave is None and "malformado" in truncado.erro
    assert L.identificar(b"<html/>").tipo == "invalido"


def test_nfe_sem_protocolo_usa_o_id_da_infnfe():
    sem_prot = NFE.split(b"<protNFe>")[0] + b"</nfeProc>"
    assert L.identificar(sem_prot).chave == "52260111281919000117550010000000011000000014"


def test_objeto_e_enderecado_pelo_conteudo():
    ident = L.identificar(NFE)
    h = L.sha256(NFE)
    assert L.chave_objeto(ident, h) == f"nfe/2026/01/{ident.chave}/{h}.xml"
    # mesma chave, outro conteúdo: outro objeto, na mesma pasta
    outro = L.chave_objeto(ident, L.sha256(NFE + b" "))
    assert outro != L.chave_objeto(ident, h) and outro.rsplit("/", 1)[0] == f"nfe/2026/01/{ident.chave}"
    assert L.chave_objeto(L.identificar(b"lixo"), h) == f"invalido/{h[:2]}/{h}.xml"


def test_conferencia_contra_o_romaneio():
    romaneio = {"a.xml": "1", "b.xml": "2", "c.xml": "3"}
    recebidos = {"a.xml": "1", "b.xml": "X", "d.xml": "4"}
    assert L.conferir(romaneio, recebidos) == [
        ("c.xml", "FALTANTE", "3", None),
        ("d.xml", "NAO_DECLARADO", None, "4"),
        ("b.xml", "HASH_DIVERGENTE", "2", "X"),
    ]
    assert L.conferir(None, recebidos) == []


def test_status_final():
    assert L.status_final(10, 0, 0) == "CARREGADO"
    assert L.status_final(10, 3, 0) == "INCOMPLETO"
    assert L.status_final(10, 0, 1) == "INCOMPLETO"
    assert L.status_final(None, 0, 0) == "INCOMPLETO"      # sem arquivo de controle, não há como provar


def test_sensor_ignora_lote_parcial(tmp_path, env_projeto, monkeypatch):
    monkeypatch.setenv("NFE_DIR_ORIGEM", str(tmp_path))
    for nome in ("lote_2026-01-03", "_parcial_lote_2026-01-04", "lote_2026-01-02", "outra_pasta"):
        (tmp_path / "inbox" / nome).mkdir(parents=True)
    from pipelines.commons.config import carregar
    assert L.lotes_prontos(carregar()) == ["lote_2026-01-02", "lote_2026-01-03"]
    # lote que ficou em processando (execução com falha) é retomado antes da inbox
    (tmp_path / "processando" / "lote_2026-01-01").mkdir(parents=True)
    assert L.lotes_prontos(carregar())[0] == "lote_2026-01-01"


# ------------------------------------------------------------------------------------------- com banco
@pytest.fixture
def ambiente(tmp_path, monkeypatch, conexao_db):
    """Pasta de rede com 2 lotes sujos (o 2º incompleto), banco real e bucket novo no moto.

    O pipeline faz commit, então cada execução usa nomes de lote e de bucket próprios para não colidir com
    dados de execuções anteriores no mesmo banco.
    """
    from pipelines.commons.config import carregar
    from pipelines.custodia.setup_bucket import preparar

    raiz = tmp_path / "nfe"
    taxas = {**gen_nfe.ANOMALIAS_PADRAO, "corrupt_rate": 0.05, "dup_rate": 0.05}
    gen_nfe.executar(raiz, dias=1, taxas=taxas)
    gen_nfe.executar(raiz, dias=1, taxas=taxas, incompleto=3)

    ano = random.randint(2100, 9999)
    nomes = []
    for i, pasta in enumerate(sorted((raiz / "inbox").iterdir())):
        novo = f"lote_{ano}-01-0{i + 1}"
        pasta.rename(raiz / "inbox" / novo)
        (raiz / "gabarito" / f"{pasta.name}.csv").rename(raiz / "gabarito" / f"{novo}.csv")
        nomes.append(novo)

    monkeypatch.setenv("NFE_DIR_ORIGEM", str(raiz))
    for var in ("S3_ACCESS_KEY", "S3_SECRET_KEY"):        # o moto aceita qualquer credencial
        monkeypatch.setenv(var, "teste")
    monkeypatch.setenv("NFE_BUCKET_RAW", f"nfe-raw-{uuid.uuid4().hex[:12]}")
    with mock_aws():
        cfg = carregar()
        preparar(boto3.client("s3", region_name="us-east-1"), cfg)
        monkeypatch.setattr(L, "cliente", lambda _cfg=None: boto3.client("s3", region_name="us-east-1"))
        yield cfg, raiz, nomes


def _gabarito(raiz: Path, nome: str) -> dict[str, int]:
    with open(raiz / "gabarito" / f"{nome}.csv", encoding="utf-8") as f:
        contagem: dict[str, int] = {}
        for linha in csv.DictReader(f):
            contagem[linha["anomalia"]] = contagem.get(linha["anomalia"], 0) + 1
        return contagem


def _carregar(cfg, nome):
    lote_id = L.reservar(cfg, nome, "pytest")
    custodia = L.custodiar(cfg, lote_id, nome)
    bronze = L.carregar_bronze(cfg, lote_id)
    L.concluir(cfg, nome)
    return lote_id, custodia, bronze


def test_lote_de_ponta_a_ponta_confere_com_o_gabarito(ambiente, conexao_db):
    cfg, raiz, nomes = ambiente
    for nome in nomes:
        controle = json.loads((raiz / "inbox" / nome / "controle_lote.json").read_text(encoding="utf-8"))
        gab = _gabarito(raiz, nome)
        _, custodia, bronze = _carregar(cfg, nome)

        assert custodia["faltantes"] == gab.get("arquivo_nao_entregue", 0)
        assert custodia["hash_divergente"] == gab.get("xml_corrompido", 0)
        assert custodia["ja_custodiados"] == gab.get("reenvio_identico", 0)
        assert custodia["arquivos"] == controle["arquivos"] - custodia["faltantes"]
        assert bronze["bronze"] == custodia["arquivos"]                      # ingeriu tudo
        assert bronze["ilegiveis"] == gab.get("xml_corrompido", 0)
        assert bronze["status"] == ("INCOMPLETO" if custodia["faltantes"] else "CARREGADO")
        assert (raiz / "processados" / nome).is_dir() and not (raiz / "processando" / nome).exists()

    assert gab.get("arquivo_nao_entregue") == 3          # o 2º lote é o incompleto
    # seg_total de lote terminado é a duração da carga e não cresce com o relógio
    with conexao_db.cursor() as cur:
        cur.execute("SELECT seg_total, extract(epoch FROM carregado_em - recebido_em)::int FROM ctrl.v_lote "
                    "WHERE nome_lote = ANY(%s)", (nomes,))
        for seg_total, duracao in cur.fetchall():
            assert seg_total == duracao


def test_retry_nao_duplica_nada(ambiente, conexao_db):
    cfg, _, nomes = ambiente
    nome = nomes[0]
    lote_id = L.reservar(cfg, nome, "pytest")
    primeira = L.custodiar(cfg, lote_id, nome)
    L.carregar_bronze(cfg, lote_id)
    # a task de custódia e a de bronze falham depois de gravar e o Airflow repete as duas
    assert L.custodiar(cfg, lote_id, nome) == primeira
    assert L.carregar_bronze(cfg, lote_id)["bronze"] == primeira["arquivos"]


def test_lote_com_erro_e_retomado_pela_proxima_execucao(ambiente, conexao_db):
    cfg, raiz, nomes = ambiente
    lote_id = L.reservar(cfg, nomes[0], "run-1")
    L.marcar_erro(cfg, nomes[0], "custodiar_lote: object store fora do ar")      # o que o callback faz
    assert L.lotes_prontos(cfg)[0] == nomes[0]                                     # parado em processando
    assert L.reservar(cfg, nomes[0], "run-2") == lote_id                           # mesma linha em ctrl.lote
    L.custodiar(cfg, lote_id, nomes[0])
    assert L.carregar_bronze(cfg, lote_id)["status"] == "CARREGADO"


def test_segunda_entrega_do_mesmo_lote_vai_para_rejeitados(ambiente, conexao_db):
    import shutil

    cfg, raiz, nomes = ambiente
    _carregar(cfg, nomes[0])
    shutil.copytree(raiz / "processados" / nomes[0], raiz / "inbox" / nomes[0])
    with pytest.raises(RuntimeError, match="já foi carregado"):
        L.reservar(cfg, nomes[0], "pytest")
    assert (raiz / "rejeitados" / nomes[0]).is_dir()
    assert nomes[0] not in L.lotes_prontos(cfg)


def test_bronze_reconstruida_do_raw(ambiente, conexao_db, monkeypatch):
    from pipelines.carga import reconstruir_bronze

    cfg, _, nomes = ambiente
    _carregar(cfg, nomes[0])
    monkeypatch.setattr(reconstruir_bronze, "cliente", L.cliente)
    r = reconstruir_bronze.verificar(cfg, nomes[0])
    assert r["bronze"] == r["reconstruidas"] > 0
    assert r["sem_par"] == 0 and r["diferentes"] == 0
