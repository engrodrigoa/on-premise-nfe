"""Teste dirigido da custódia (marco 1, decisão D003): o object store segura o que promete?

Usa um bucket TEMPORÁRIO (nunca o da custódia), com retenção de 1 dia em modo GOVERNANCE, e confere:

  1. versionamento  sobrescrever um objeto cria uma versão nova e a anterior continua legível
  2. bloqueio       apagar uma versão dentro da retenção é recusado
  3. exclusão comum um DELETE sem versão só cria um marcador; o conteúdo continua recuperável

No fim remove tudo usando o bypass de GOVERNANCE (por isso o teste não usa COMPLIANCE).

Uso:  python -m pipelines.custodia.teste_lock        (ou: make teste-custodia)
Saída: uma linha por verificação e código de saída 0 só se todas passarem.
"""
from __future__ import annotations

import sys
import uuid

from botocore.exceptions import ClientError

from pipelines.commons.s3 import cliente

OBJETO = "teste/documento.xml"


def executar(s3, bucket: str | None = None) -> list[tuple[str, bool, str]]:
    bucket = bucket or f"nfe-teste-lock-{uuid.uuid4().hex[:8]}"
    resultados: list[tuple[str, bool, str]] = []
    try:
        try:
            s3.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
            s3.put_object_lock_configuration(
                Bucket=bucket,
                ObjectLockConfiguration={
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}},
                },
            )
            resultados.append(("bucket com bloqueio de objeto", True, "criado com retenção GOVERNANCE de 1 dia"))
        except ClientError as exc:
            resultados.append(("bucket com bloqueio de objeto", False, str(exc)))
            return resultados

        v1 = s3.put_object(Bucket=bucket, Key=OBJETO, Body=b"<nfe>original</nfe>")["VersionId"]
        v2 = s3.put_object(Bucket=bucket, Key=OBJETO, Body=b"<nfe>adulterado</nfe>")["VersionId"]
        original = s3.get_object(Bucket=bucket, Key=OBJETO, VersionId=v1)["Body"].read()
        ok = v1 != v2 and original == b"<nfe>original</nfe>"
        resultados.append(("versionamento", ok, "sobrescrita criou versão nova; a original continua legível"
                           if ok else f"v1={v1} v2={v2} conteúdo={original!r}"))

        try:
            s3.delete_object(Bucket=bucket, Key=OBJETO, VersionId=v1)
            resultados.append(("bloqueio", False, "a versão dentro da retenção FOI apagada"))
        except ClientError as exc:
            resultados.append(("bloqueio", True, f"exclusão da versão recusada ({exc.response['Error']['Code']})"))

        s3.delete_object(Bucket=bucket, Key=OBJETO)  # sem versão: só cria um marcador de exclusão
        ainda = s3.get_object(Bucket=bucket, Key=OBJETO, VersionId=v1)["Body"].read()
        ok = ainda == b"<nfe>original</nfe>"
        resultados.append(("exclusão comum", ok, "virou marcador; a versão original continua recuperável"
                           if ok else "o conteúdo original não está mais acessível"))
    finally:
        _limpar(s3, bucket)
    return resultados


def _limpar(s3, bucket: str) -> None:
    try:
        versoes = s3.list_object_versions(Bucket=bucket)
        for item in versoes.get("Versions", []) + versoes.get("DeleteMarkers", []):
            s3.delete_object(Bucket=bucket, Key=item["Key"], VersionId=item["VersionId"],
                             BypassGovernanceRetention=True)
        s3.delete_bucket(Bucket=bucket)
    except ClientError as exc:
        print(f"AVISO: não consegui remover o bucket de teste {bucket}: {exc}", file=sys.stderr)


def main() -> int:
    resultados = executar(cliente())
    for nome, ok, detalhe in resultados:
        print(f"{'PASSOU' if ok else 'FALHOU'}  {nome}: {detalhe}")
    return 0 if resultados and all(ok for _, ok, _ in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
