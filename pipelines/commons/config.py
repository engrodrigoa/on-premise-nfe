"""Configuração do projeto: uma única leitura das variáveis de ambiente.

Dentro do compose os valores vêm do docker-compose.yml (DB_HOST=postgres, S3_ENDPOINT=http://minio:9000).
No host, vêm do .env (visão localhost). Nenhum outro módulo lê os.environ diretamente.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(nome: str, padrao: str | None = None) -> str:
    valor = os.environ.get(nome, padrao)
    if valor is None or valor == "":
        raise RuntimeError(f"variável de ambiente obrigatória ausente: {nome}")
    return valor


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_db: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    bucket_raw: str
    lock_mode: str
    retencao_dias: int
    dir_origem: str

    @property
    def dir_inbox(self) -> str:
        return f"{self.dir_origem}/inbox"

    @property
    def dir_processando(self) -> str:
        return f"{self.dir_origem}/processando"

    @property
    def dir_processados(self) -> str:
        return f"{self.dir_origem}/processados"


def carregar() -> Config:
    lock_mode = _env("CUSTODIA_LOCK_MODE", "GOVERNANCE").upper()
    if lock_mode not in ("GOVERNANCE", "COMPLIANCE"):
        raise RuntimeError(f"CUSTODIA_LOCK_MODE inválido: {lock_mode} (use GOVERNANCE ou COMPLIANCE)")
    retencao = int(_env("CUSTODIA_RETENCAO_DIAS", "2190"))
    if retencao < 1:
        raise RuntimeError("CUSTODIA_RETENCAO_DIAS precisa ser >= 1")
    return Config(
        db_host=_env("DB_HOST", "localhost"),
        db_port=int(_env("DB_PORT", "5432")),
        db_user=_env("DB_USER"),
        db_password=_env("DB_PASSWORD"),
        db_db=_env("DB_DB"),
        s3_endpoint=_env("S3_ENDPOINT", "http://localhost:9000"),
        s3_access_key=_env("S3_ACCESS_KEY"),
        s3_secret_key=_env("S3_SECRET_KEY"),
        bucket_raw=_env("NFE_BUCKET_RAW", "nfe-raw"),
        lock_mode=lock_mode,
        retencao_dias=retencao,
        dir_origem=_env("NFE_DIR_ORIGEM", "datasource/nfe"),
    )
