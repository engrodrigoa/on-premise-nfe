"""Espera a carga terminar: usado pelo `make demo` depois de disparar a dag_nfe_saida.

Terminou quando a pasta de rede não tem lote na inbox nem em processando/, nenhum lote está no meio da carga
(RECEBIDO ou CUSTODIADO) e todo lote carregado já passou pela silver. Falha aberta (ERRO ou erro na silver)
encerra a espera com código 1, para o roteiro não seguir como se estivesse tudo certo.

    python -m pipelines.carga.aguardar [--timeout 1800]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pipelines.carga.lote import lotes_prontos
from pipelines.commons.config import Config, carregar
from pipelines.commons.db import conectar

SQL_SITUACAO = """
SELECT count(*) FILTER (WHERE status IN ('RECEBIDO', 'CUSTODIADO'))                          AS em_carga,
       count(*) FILTER (WHERE status IN ('CARREGADO', 'INCOMPLETO') AND transformado_em IS NULL) AS sem_silver,
       count(*) FILTER (WHERE status = 'ERRO' OR erro_transformacao IS NOT NULL)               AS com_falha,
       count(*) FILTER (WHERE transformado_em IS NOT NULL)                                     AS na_silver,
       count(*)                                                                                AS total
  FROM ctrl.lote
"""


def situacao(cfg: Config) -> dict:
    with conectar(cfg) as conn, conn.cursor() as cur:
        cur.execute(SQL_SITUACAO)
        colunas = [c.name for c in cur.description]
        s = dict(zip(colunas, cur.fetchone()))
    processando = Path(cfg.dir_processando)
    s["na_inbox"] = len(lotes_prontos(cfg))
    s["processando"] = sum(1 for p in processando.iterdir() if p.is_dir()) if processando.is_dir() else 0
    return s


def aguardar(cfg: Config, timeout: int, intervalo: int = 10) -> int:
    limite = time.monotonic() + timeout
    ultimo = None
    while True:
        s = situacao(cfg)
        linha = (f"inbox {s['na_inbox']} · processando {s['processando']} · em carga {s['em_carga']} · "
                 f"aguardando silver {s['sem_silver']} · na silver {s['na_silver']}/{s['total']}")
        if linha != ultimo:
            print(linha, flush=True)
            ultimo = linha
        if s["com_falha"]:
            print(f"FALHOU  {s['com_falha']} lote(s) com falha: veja `make lotes` e o painel de saúde da carga")
            return 1
        if not (s["na_inbox"] or s["processando"] or s["em_carga"] or s["sem_silver"]):
            print("PRONTO  carga terminada até a silver")
            return 0
        if time.monotonic() > limite:
            print(f"FALHOU  a carga não terminou em {timeout} s: veja a dag_nfe_saida no Airflow")
            return 1
        time.sleep(intervalo)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeout", type=int, default=1800, help="segundos (padrão: 1800)")
    return aguardar(carregar(), ap.parse_args().timeout)


if __name__ == "__main__":
    sys.exit(main())
