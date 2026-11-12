"""Linha de carga de um lote: pasta de rede -> custódia (raw) -> bronze.

Cada função é uma task da `dag_nfe_saida` e pode ser repetida sem efeito duplicado (retry, reprocessamento):
quem já foi gravado é reconhecido pelo conteúdo (sha256) ou pela chave única da tabela.

    reservar       inbox/lote_X -> processando/lote_X; abre a linha em ctrl.lote (RECEBIDO)
    custodiar      sha256 de cada arquivo, PUT no bucket com bloqueio, inventário e conferência
                   contra o romaneio do controle_lote.json (CUSTODIADO)
    carregar_bronze  lê de volta do bucket e insere o XML bruto na bronze (CARREGADO ou INCOMPLETO)
    concluir       processando/lote_X -> processados/lote_X

Regras do CLAUDE.md que este módulo cumpre: custódia e bronze só INSERT; nenhuma regra fiscal; nenhuma
leitura de variável de ambiente (tudo vem de Config).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from botocore.exceptions import ClientError

from pipelines.commons.config import Config
from pipelines.commons.db import conectar
from pipelines.commons.s3 import cliente

PADRAO_LOTE = re.compile(r"^lote_\d{4}-\d{2}-\d{2}$")
ARQUIVO_CONTROLE = "controle_lote.json"
# lotes que terminam a carga neste estado; reservar um deles de novo é reenvio do lote inteiro
FINAIS = ("CARREGADO", "INCOMPLETO", "PROCESSADO")


# --------------------------------------------------------------------------------------------- funções puras
@dataclass(frozen=True)
class Identidade:
    tipo: str               # nfe | evento | invalido
    chave: str | None
    cnpj: str | None
    erro: str | None = None


def sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _primeiro(raiz: ET.Element, *caminho: str) -> str | None:
    """Primeiro texto no caminho de nomes locais (ignora o namespace do portal fiscal)."""
    atual = [raiz]
    for nome in caminho:
        atual = [f for e in atual for f in e.iter() if _local(f.tag) == nome]
        if not atual:
            return None
    texto = (atual[0].text or "").strip()
    return texto or None


def identificar(conteudo: bytes) -> Identidade:
    """Só o necessário para custodiar: tipo, chave de acesso e CNPJ do emitente. Nenhuma validação fiscal.

    Arquivo ilegível vira `invalido`: é guardado assim mesmo, porque o bruto é a evidência (D003).
    """
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError as exc:
        return Identidade("invalido", None, None, f"XML malformado: {exc}")
    nome = _local(raiz.tag)
    if nome == "nfeProc":
        chave = _primeiro(raiz, "infProt", "chNFe")
        if chave is None:                                   # sem protocolo: tenta o Id da infNFe
            inf = next((e for e in raiz.iter() if _local(e.tag) == "infNFe"), None)
            if inf is not None:
                chave = (inf.get("Id") or "")[3:] or None
        return Identidade("nfe", chave, _primeiro(raiz, "emit", "CNPJ"))
    if nome == "procEventoNFe":
        return Identidade("evento", _primeiro(raiz, "infEvento", "chNFe"), _primeiro(raiz, "infEvento", "CNPJ"))
    return Identidade("invalido", None, None, f"raiz inesperada: {nome}")


def chave_objeto(ident: Identidade, hash_conteudo: str) -> str:
    """Endereço do objeto no bucket, derivado do CONTEÚDO (D016).

    Mesmo conteúdo = mesmo objeto: o reenvio idêntico não duplica a custódia. Mesma chave com conteúdo
    diferente = dois objetos lado a lado, sob a mesma pasta da chave.
    """
    chave = ident.chave or ""
    if ident.tipo in ("nfe", "evento") and len(chave) == 44 and chave[2:6].isdigit():
        return f"{ident.tipo}/20{chave[2:4]}/{chave[4:6]}/{chave}/{hash_conteudo}.xml"
    return f"invalido/{hash_conteudo[:2]}/{hash_conteudo}.xml"


def conferir(romaneio: dict[str, str] | None, recebidos: dict[str, str]) -> list[tuple[str, str, str | None, str | None]]:
    """Compara o que chegou com o que a origem declarou. Devolve (arquivo, tipo, sha_declarado, sha_recebido).

    Sem romaneio (lote sem arquivo de controle) não há o que comparar: quem acusa é o status INCOMPLETO.
    """
    if romaneio is None:
        return []
    saida = []
    for arq in sorted(romaneio.keys() - recebidos.keys()):
        saida.append((arq, "FALTANTE", romaneio[arq], None))
    for arq in sorted(recebidos.keys() - romaneio.keys()):
        saida.append((arq, "NAO_DECLARADO", None, recebidos[arq]))
    for arq in sorted(romaneio.keys() & recebidos.keys()):
        if romaneio[arq] != recebidos[arq]:
            saida.append((arq, "HASH_DIVERGENTE", romaneio[arq], recebidos[arq]))
    return saida


def status_final(declarado_arquivos: int | None, faltantes: int, nao_declarados: int) -> str:
    """Hash divergente NÃO torna o lote incompleto: o arquivo chegou, e a quarentena da silver cuida dele (D007)."""
    if declarado_arquivos is None or faltantes or nao_declarados:
        return "INCOMPLETO"
    return "CARREGADO"


# ------------------------------------------------------------------------------------------- pasta de rede
def _lotes_em(pasta: Path) -> list[str]:
    if not pasta.is_dir():
        return []
    return sorted(p.name for p in pasta.iterdir() if p.is_dir() and PADRAO_LOTE.match(p.name))


def lotes_prontos(cfg: Config) -> list[str]:
    """Fila de trabalho, na ordem em que deve ser carregada.

    Primeiro o que ficou parado em `processando/` (execução que falhou: lote ERRO), depois a inbox, do mais
    antigo para o mais novo. Seguro porque a DAG roda uma execução por vez (max_active_runs=1).
    `_parcial_*` é lote ainda sendo escrito pela origem e nunca entra na fila.
    """
    parados = _lotes_em(Path(cfg.dir_processando))
    return parados + [n for n in _lotes_em(Path(cfg.dir_inbox)) if n not in parados]


def ler_controle(pasta: Path) -> dict | None:
    arq = pasta / ARQUIVO_CONTROLE
    if not arq.exists():
        return None
    return json.loads(arq.read_text(encoding="utf-8"))


def _xmls(pasta: Path) -> list[Path]:
    return sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".xml")


def _mover_sem_sobrescrever(origem: Path, pasta_destino: Path) -> Path:
    pasta_destino.mkdir(parents=True, exist_ok=True)
    destino = pasta_destino / origem.name
    if destino.exists():
        destino = pasta_destino / f"{origem.name}__{datetime.now():%Y%m%d%H%M%S}"
    os.replace(origem, destino)
    return destino


# ----------------------------------------------------------------------------------------------- reservar
def reservar(cfg: Config, nome_lote: str, dag_run_id: str | None = None) -> int:
    """Tira o lote da inbox (rename atômico) e registra o que a origem declarou. Devolve o lote_id."""
    origem = Path(cfg.dir_inbox) / nome_lote
    pasta = Path(cfg.dir_processando) / nome_lote
    if origem.is_dir():
        if pasta.exists():
            raise RuntimeError(f"{pasta} já existe: outra execução está com este lote")
        pasta.parent.mkdir(parents=True, exist_ok=True)
        os.replace(origem, pasta)
    elif not pasta.is_dir():
        raise RuntimeError(f"lote {nome_lote} não está nem na inbox nem em processando")

    controle = ler_controle(pasta)
    declarado = (controle or {})
    with conectar(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO ctrl.lote (nome_lote, declarado_arquivos, declarado_nfe, declarado_eventos,
                                      declarado_vnf, qtd_arquivos, dag_run_id, mensagem)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (nome_lote) DO UPDATE
                  SET status = 'RECEBIDO', dag_run_id = EXCLUDED.dag_run_id, mensagem = NULL
                WHERE ctrl.lote.status NOT IN %s
               RETURNING lote_id""",
            (nome_lote, declarado.get("arquivos"), declarado.get("nfe"), declarado.get("eventos"),
             declarado.get("soma_vnf"), len(_xmls(pasta)), dag_run_id,
             None if controle else "lote sem arquivo de controle", FINAIS),
        )
        linha = cur.fetchone()
    if linha is None:
        # o mesmo lote já foi carregado: não mistura a segunda entrega com a primeira
        destino = _mover_sem_sobrescrever(pasta, Path(cfg.dir_rejeitados))
        raise RuntimeError(f"{nome_lote} já foi carregado antes; a nova entrega foi para {destino}")
    return linha[0]


# ---------------------------------------------------------------------------------------------- custodiar
def _objeto_existente(s3, bucket: str, objeto: str) -> dict | None:
    try:
        return s3.head_object(Bucket=bucket, Key=objeto)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def custodiar(cfg: Config, lote_id: int, nome_lote: str) -> dict:
    """Grava cada arquivo no bucket (uma vez por conteúdo) e registra inventário, recebimento e divergências."""
    pasta = Path(cfg.dir_processando) / nome_lote
    controle = ler_controle(pasta)
    romaneio = {i["arquivo"]: i["sha256"] for i in controle["itens"]} if controle else None
    s3 = cliente(cfg)
    reter_ate = date.today() + timedelta(days=cfg.retencao_dias)
    recebidos: dict[str, str] = {}

    with conectar(cfg) as conn, conn.cursor() as cur:
        for caminho in _xmls(pasta):
            dados = caminho.read_bytes()
            h = sha256(dados)
            recebidos[caminho.name] = h
            ident = identificar(dados)
            objeto = chave_objeto(ident, h)

            existente = _objeto_existente(s3, cfg.bucket_raw, objeto)
            if existente is None:
                resp = s3.put_object(
                    Bucket=cfg.bucket_raw, Key=objeto, Body=dados, ContentType="application/xml",
                    # o object store recalcula e recusa o PUT se o conteúdo chegar diferente
                    # (bucket com object lock exige o cabeçalho do algoritmo de checksum)
                    ChecksumAlgorithm="SHA256", ChecksumSHA256=base64.b64encode(bytes.fromhex(h)).decode(),
                    Metadata={"sha256": h, "lote": nome_lote, "arquivo-origem": caminho.name},
                )
                version_id, novo = resp.get("VersionId"), True
            else:
                version_id, novo = existente.get("VersionId"), False

            cur.execute(
                """INSERT INTO custodia.documento (tipo_documento, chave_acesso, cnpj_emitente, sha256,
                          tamanho_bytes, bucket, objeto, version_id, lote_id, arquivo_origem, reter_ate)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (bucket, objeto) DO NOTHING""",
                (ident.tipo, ident.chave, ident.cnpj, h, len(dados), cfg.bucket_raw, objeto, version_id,
                 lote_id, caminho.name, reter_ate),
            )
            cur.execute("SELECT documento_id FROM custodia.documento WHERE bucket = %s AND objeto = %s",
                        (cfg.bucket_raw, objeto))
            documento_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO custodia.recebimento (lote_id, arquivo, documento_id, sha256, sha256_declarado,
                                                     objeto_novo)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (lote_id, arquivo) DO NOTHING""",
                (lote_id, caminho.name, documento_id, h, (romaneio or {}).get(caminho.name), novo),
            )

        divergencias = conferir(romaneio, recebidos)
        for arq, tipo, decl, rec in divergencias:
            cur.execute(
                """INSERT INTO ctrl.lote_divergencia (lote_id, arquivo, tipo, sha256_declarado, sha256_recebido)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT (lote_id, arquivo, tipo) DO NOTHING""",
                (lote_id, arq, tipo, decl, rec),
            )
        # contagens a partir das tabelas, não desta execução: um retry não conta duas vezes
        cur.execute(
            """UPDATE ctrl.lote l SET
                   status = 'CUSTODIADO', custodiado_em = now(), qtd_arquivos = %s,
                   qtd_custodiados    = (SELECT count(*) FILTER (WHERE objeto_novo)     FROM custodia.recebimento WHERE lote_id = l.lote_id),
                   qtd_ja_custodiados = (SELECT count(*) FILTER (WHERE NOT objeto_novo) FROM custodia.recebimento WHERE lote_id = l.lote_id),
                   qtd_faltantes       = (SELECT count(*) FILTER (WHERE tipo = 'FALTANTE')        FROM ctrl.lote_divergencia WHERE lote_id = l.lote_id),
                   qtd_nao_declarados  = (SELECT count(*) FILTER (WHERE tipo = 'NAO_DECLARADO')   FROM ctrl.lote_divergencia WHERE lote_id = l.lote_id),
                   qtd_hash_divergente = (SELECT count(*) FILTER (WHERE tipo = 'HASH_DIVERGENTE') FROM ctrl.lote_divergencia WHERE lote_id = l.lote_id)
               WHERE lote_id = %s
               RETURNING qtd_arquivos, qtd_custodiados, qtd_ja_custodiados, qtd_faltantes, qtd_nao_declarados,
                         qtd_hash_divergente""",
            (len(recebidos), lote_id),
        )
        nomes = ("arquivos", "custodiados", "ja_custodiados", "faltantes", "nao_declarados", "hash_divergente")
        return dict(zip(nomes, cur.fetchone()))


# ------------------------------------------------------------------------------------------------- bronze
def _texto_xml(dados: bytes) -> tuple[str | None, str | None, str | None]:
    """(texto para o Postgres, erro, início do conteúdo). Malformado: texto None, com o motivo."""
    try:
        ET.fromstring(dados)
        return dados.decode("utf-8"), None, None
    except (ET.ParseError, UnicodeDecodeError) as exc:
        return None, f"XML malformado: {exc}", dados[:200].decode("utf-8", errors="replace")


def ler_do_raw(s3, bucket: str, objeto: str, version_id: str | None, sha_esperado: str) -> bytes:
    """Lê a versão custodiada e confere o hash. Divergência aqui é incidente de custódia, não de dado."""
    args = {"Bucket": bucket, "Key": objeto}
    if version_id:
        args["VersionId"] = version_id
    dados = s3.get_object(**args)["Body"].read()
    if sha256(dados) != sha_esperado:
        raise RuntimeError(f"custódia divergente: {bucket}/{objeto} não tem mais o sha256 {sha_esperado}")
    return dados


SQL_INSERIR_BRONZE = """
INSERT INTO bronze.documento_xml (lote_id, arquivo, documento_id, tipo_documento, sha256, tamanho_bytes,
                                  conteudo, erro_leitura, inicio_conteudo)
VALUES (%(lote_id)s, %(arquivo)s, %(documento_id)s, %(tipo)s, %(sha256)s, %(tamanho)s,
        bronze.fn_xml(%(texto)s),
        CASE WHEN bronze.fn_xml(%(texto)s) IS NULL
             THEN coalesce(%(erro)s, 'XML recusado pelo XMLPARSE do Postgres') END,
        CASE WHEN bronze.fn_xml(%(texto)s) IS NULL THEN coalesce(%(inicio)s, left(%(texto)s, 200)) END)
ON CONFLICT (lote_id, arquivo) DO NOTHING
"""


def _pendentes_bronze(cur, lote_id: int) -> list[dict]:
    cur.execute(
        """SELECT r.arquivo, r.documento_id, d.tipo_documento, d.sha256, d.tamanho_bytes, d.bucket, d.objeto,
                  d.version_id
             FROM custodia.recebimento r JOIN custodia.documento d USING (documento_id)
            WHERE r.lote_id = %s
              AND NOT EXISTS (SELECT 1 FROM bronze.documento_xml b WHERE b.lote_id = r.lote_id AND b.arquivo = r.arquivo)
            ORDER BY r.arquivo""",
        (lote_id,),
    )
    colunas = [c.name for c in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def parametros_bronze(lote_id: int, rec: dict, dados: bytes) -> dict:
    texto, erro, inicio = _texto_xml(dados)
    return {"lote_id": lote_id, "arquivo": rec["arquivo"], "documento_id": rec["documento_id"],
            "tipo": rec["tipo_documento"], "sha256": rec["sha256"], "tamanho": rec["tamanho_bytes"],
            "texto": texto, "erro": erro, "inicio": inicio}


def carregar_bronze(cfg: Config, lote_id: int) -> dict:
    """1 linha por arquivo recebido, lida do RAW (não da pasta): prova a cada carga que a bronze sai da custódia."""
    s3 = cliente(cfg)
    with conectar(cfg) as conn, conn.cursor() as cur:
        for rec in _pendentes_bronze(cur, lote_id):
            dados = ler_do_raw(s3, rec["bucket"], rec["objeto"], rec["version_id"], rec["sha256"])
            cur.execute(SQL_INSERIR_BRONZE, parametros_bronze(lote_id, rec, dados))

        cur.execute(
            """SELECT (SELECT count(*) FROM custodia.recebimento WHERE lote_id = %(l)s),
                      (SELECT count(*) FROM bronze.documento_xml WHERE lote_id = %(l)s),
                      (SELECT count(*) FROM bronze.documento_xml WHERE lote_id = %(l)s AND conteudo IS NULL),
                      declarado_arquivos, qtd_faltantes, qtd_nao_declarados, qtd_hash_divergente
                 FROM ctrl.lote WHERE lote_id = %(l)s""",
            {"l": lote_id},
        )
        recebidos, na_bronze, ilegiveis, declarado, faltantes, nao_decl, divergentes = cur.fetchone()
        if na_bronze != recebidos:          # linha 2: ingeriu tudo?
            raise RuntimeError(f"lote {lote_id}: {recebidos} recebidos e {na_bronze} na bronze")

        status = status_final(declarado, faltantes or 0, nao_decl or 0)
        partes = []
        if declarado is None:
            partes.append("sem arquivo de controle")
        if faltantes:
            partes.append(f"{faltantes} arquivo(s) declarado(s) e não entregue(s)")
        if nao_decl:
            partes.append(f"{nao_decl} arquivo(s) fora do romaneio")
        if divergentes:
            partes.append(f"{divergentes} arquivo(s) com sha256 diferente do romaneio")
        if ilegiveis:
            partes.append(f"{ilegiveis} XML(s) ilegível(is) na bronze")
        cur.execute(
            """UPDATE ctrl.lote SET status = %s, qtd_bronze = %s, carregado_em = now(), mensagem = %s
                WHERE lote_id = %s""",
            (status, na_bronze, "; ".join(partes) or None, lote_id),
        )
    return {"status": status, "bronze": na_bronze, "ilegiveis": ilegiveis, "mensagem": "; ".join(partes)}


# ------------------------------------------------------------------------------------------ fim do lote
def concluir(cfg: Config, nome_lote: str) -> str:
    pasta = Path(cfg.dir_processando) / nome_lote
    if not pasta.is_dir():                        # retry depois de já ter movido
        return str(Path(cfg.dir_processados) / nome_lote)
    return str(_mover_sem_sobrescrever(pasta, Path(cfg.dir_processados)))


def marcar_erro(cfg: Config, nome_lote: str, mensagem: str) -> None:
    """Falha de processo (D007): o lote fica ERRO em ctrl.lote, com a mensagem, para o runbook."""
    with conectar(cfg) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE ctrl.lote SET status = 'ERRO', mensagem = %s WHERE nome_lote = %s AND status NOT IN %s",
            (mensagem[:2000], nome_lote, FINAIS),
        )
