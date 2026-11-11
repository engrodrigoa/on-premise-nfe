"""Cliente S3 (boto3) do object store. Todo acesso ao bucket passa por aqui."""
from __future__ import annotations

import boto3
from botocore.config import Config as BotoConfig

from pipelines.commons.config import Config, carregar


def cliente(cfg: Config | None = None):
    cfg = cfg or carregar()
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name="us-east-1",
        config=BotoConfig(
            retries={"max_attempts": 5, "mode": "standard"},
            connect_timeout=10,
            read_timeout=120,
            s3={"addressing_style": "path"},
        ),
    )
