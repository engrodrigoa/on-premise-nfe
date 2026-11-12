"""Prepara o bucket da custódia: versionamento + bloqueio de objeto (object lock) com retenção padrão.

Roda no airflow-init a cada `make up`. É idempotente: em um bucket já pronto, só confere.

O bloqueio de objeto só pode ser ligado na CRIAÇÃO do bucket. Se o object store recusar (decisão D003),
o bucket é criado só com versionamento e o script avisa: a imutabilidade passa a depender de
versionamento + verificação periódica de hash, e isso precisa ficar registrado.

Uso:  python -m pipelines.custodia.setup_bucket
"""
from __future__ import annotations

import json
import sys

from botocore.exceptions import ClientError

from pipelines.commons.config import Config, carregar
from pipelines.commons.s3 import cliente


def _codigo(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _bucket_existe(s3, bucket: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        if _codigo(exc) in ("404", "NoSuchBucket", "NotFound"):
            return False
        raise


def estado(s3, bucket: str) -> dict:
    """Lê o estado real do bucket (não o que foi pedido)."""
    versionamento = s3.get_bucket_versioning(Bucket=bucket).get("Status", "Desligado")
    try:
        cfg_lock = s3.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
        regra = cfg_lock.get("Rule", {}).get("DefaultRetention", {})
        lock = {
            "ligado": cfg_lock.get("ObjectLockEnabled") == "Enabled",
            "modo": regra.get("Mode"),
            "dias": regra.get("Days"),
        }
    except ClientError as exc:
        if _codigo(exc) not in ("ObjectLockConfigurationNotFoundError", "NoSuchObjectLockConfiguration",
                                "InvalidRequest", "NotImplemented"):
            raise
        lock = {"ligado": False, "modo": None, "dias": None}
    return {"bucket": bucket, "versionamento": versionamento, "lock": lock}


def preparar(s3, cfg: Config) -> dict:
    """Cria o bucket se não existir e aplica a retenção padrão. Devolve o estado final."""
    bucket = cfg.bucket_raw
    criado = False
    lock_recusado = None

    if not _bucket_existe(s3, bucket):
        try:
            s3.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
        except ClientError as exc:
            if _codigo(exc) in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise
            # object store sem suporte a bloqueio: segue só com versionamento (D003)
            lock_recusado = f"{_codigo(exc)}: {exc.response.get('Error', {}).get('Message', '')}"
            s3.create_bucket(Bucket=bucket)
        criado = True

    atual = estado(s3, bucket)

    if atual["versionamento"] != "Enabled":
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})

    if atual["lock"]["ligado"]:
        desejado = {"Mode": cfg.lock_mode, "Days": cfg.retencao_dias}
        if (atual["lock"]["modo"], atual["lock"]["dias"]) != (desejado["Mode"], desejado["Days"]):
            s3.put_object_lock_configuration(
                Bucket=bucket,
                ObjectLockConfiguration={"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": desejado}},
            )

    final = estado(s3, bucket)
    final["criado_agora"] = criado
    final["lock_recusado"] = lock_recusado
    return final


def main() -> int:
    cfg = carregar()
    final = preparar(cliente(cfg), cfg)
    print("custodia.setup_bucket " + json.dumps(final, ensure_ascii=False))
    if final["versionamento"] != "Enabled":
        print("ERRO: o bucket da custódia está sem versionamento", file=sys.stderr)
        return 1
    if not final["lock"]["ligado"]:
        # não derruba o stack: é uma limitação conhecida e tratada na D003
        print("AVISO: bucket SEM bloqueio de objeto. A imutabilidade depende de versionamento + "
              "verificação de hash. Registre o resultado na decisão D003.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
