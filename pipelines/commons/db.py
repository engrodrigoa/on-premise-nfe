"""Conexão com o Postgres do projeto (psycopg2). As DAGs e os scripts usam a mesma função."""
from __future__ import annotations

from contextlib import contextmanager

import psycopg2

from pipelines.commons.config import Config, carregar


@contextmanager
def conectar(cfg: Config | None = None):
    """Abre uma conexão, faz commit ao sair sem erro e rollback se houver exceção."""
    cfg = cfg or carregar()
    conn = psycopg2.connect(
        host=cfg.db_host, port=cfg.db_port, user=cfg.db_user, password=cfg.db_password, dbname=cfg.db_db
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
