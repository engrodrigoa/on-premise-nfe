"""Critério de pronto do marco 4: a silver acerta o gabarito do gerador?

Fica fora do pipeline, como `teste_lock` e `reconstruir_bronze`: é o único código que lê o gabarito e o estado
do ERP simulado (D014). Confere três coisas e imprime PASSOU/FALHOU em cada uma:

  1. fechamento        bronze = válidos + quarentena + reenvios descartados, em todo lote (D020)
  2. gabarito          toda anomalia injetada aparece onde deve (recall) e nada aparece sem ter sido injetado
                       (precisão). Exceção explicada: salto de numeração causado por nota que o ERP ainda não
                       entregou (está nos pendentes do gerador). Ela some quando a nota chegar.
                       Só lotes com gabarito entram: os da suíte de testes (tests/test_carga.py grava lotes
                       com ano 2100-9999 e sem gabarito na pasta) são contados e ignorados.
  3. cancelamento      todo cancelamento que chegou num lote depois da nota deixou a nota cancelada

    python -m pipelines.qualidade.conferir_silver            (ou: make teste-silver)

Código de saída: 0 tudo passou; 1 algo falhou; 2 inconclusivo (o período não tem o caso a provar).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

from pipelines.commons.config import Config, carregar
from pipelines.commons.db import conectar

# anomalia do gabarito -> como a silver deve mostrá-la
QUARENTENA = {
    "xml_corrompido": "xml_corrompido",
    "emitente_fora_do_grupo": "emitente_fora_do_grupo",
    "mesma_chave_conteudo_diferente": "chave_conteudo_divergente",   # D022: o segundo conteúdo vai para quarentena
    "evento_sem_nota": "evento_sem_nota",
}
AVISO = {"tot_cbs_divergente", "vbc_ibscbs_divergente", "cclasstrib_incoerente", "numeracao_com_salto",
         "devolucao_ref_inexistente", "cancelamento_fora_do_prazo"}
REENVIO = {"reenvio_identico"}
FORA_DA_SILVER = {"nota_atrasada", "arquivo_nao_entregue"}   # informativa / conferida no marco 3 (ctrl.lote)


def ler_gabarito(pasta: Path, lotes: set[str]) -> list[dict]:
    linhas = []
    for nome in sorted(lotes):
        arq = pasta / f"{nome}.csv"
        if arq.exists():
            with open(arq, newline="", encoding="utf-8") as f:
                linhas.extend(csv.DictReader(f))
    return linhas


def numeros_pendentes(caminho_estado: Path) -> set[tuple[str, int, int]]:
    """(cnpj, série, nNF) das NF-e que o ERP ainda vai entregar: explicam um salto temporário."""
    if not caminho_estado.exists():
        return set()
    estado = json.loads(caminho_estado.read_text(encoding="utf-8"))
    saida = set()
    for p in estado.get("pendentes", []):
        chave = p.get("chave") or ""
        if p.get("tipo") == "nfe" and len(chave) == 44:
            saida.add((chave[6:20], int(chave[22:25]), int(chave[25:34])))
    return saida


def _consulta(cur, sql: str, params=None) -> list[dict]:
    cur.execute(sql, params)
    colunas = [c.name for c in cur.description]
    return [dict(zip(colunas, linha)) for linha in cur.fetchall()]


def conferir(cfg: Config) -> dict:
    with conectar(cfg) as conn, conn.cursor() as cur:
        fechamento = _consulta(cur, """
            SELECT nome_lote, qtd_bronze, qtd_validos, qtd_quarentena, qtd_reenvios, silver_fechada
              FROM ctrl.v_lote WHERE qtd_bronze IS NOT NULL ORDER BY lote_id""")
        documentos = _consulta(cur, """
            SELECT l.nome_lote, d.arquivo, d.chave, d.destino, d.motivo
              FROM silver.documento d JOIN ctrl.lote l USING (lote_id)""")
        avisos = _consulta(cur, """
            SELECT l.nome_lote, a.chave, a.regra, n.emit_cnpj, n.serie, n.nnf
              FROM silver.nfe_aviso a JOIN silver.nfe_nota n USING (chave) JOIN ctrl.lote l ON l.lote_id = n.lote_id""")
        # números recebidos por série, para achar o buraco que cada salto acusa
        recebidos = _consulta(cur, """
            SELECT emit_cnpj, serie, nnf FROM silver.documento
             WHERE chave_valida AND (tipo_documento = 'nfe'
                   OR (tipo_documento = 'invalido' AND arquivo !~ 'procEventoNFe'))""")
        situacao = _consulta(cur, """
            SELECT chave, situacao, chegada_cancelamento FROM silver.nfe_situacao
             WHERE chegada_cancelamento IS NOT NULL""")

    lotes = {f["nome_lote"] for f in fechamento}
    gabarito = ler_gabarito(Path(cfg.dir_gabarito), lotes)
    com_gabarito = {nome for nome in lotes if (Path(cfg.dir_gabarito) / f"{nome}.csv").exists()}
    pendentes = numeros_pendentes(Path(cfg.arquivo_estado_gerador))

    por_arquivo = {(d["nome_lote"], d["arquivo"]): d for d in documentos}
    aviso_set = {(a["chave"], a["regra"]) for a in avisos}
    liberados = {s["chave"] for s in situacao if s["chegada_cancelamento"] == "antes_da_nota"}

    # --- recall: cada anomalia injetada está onde deve?
    esperado, achado, faltando = Counter(), Counter(), []
    esperados_quarentena, esperados_aviso, esperados_reenvio = set(), set(), set()
    for g in gabarito:
        anomalia = g["anomalia"]
        if anomalia in FORA_DA_SILVER:
            continue
        esperado[anomalia] += 1
        doc = por_arquivo.get((g["lote"], g["arquivo"]))
        if anomalia in QUARENTENA:
            motivo = QUARENTENA[anomalia]
            esperados_quarentena.add((g["lote"], g["arquivo"]))
            ok = doc is not None and doc["destino"] == "quarentena" and doc["motivo"] == motivo
            if not ok and anomalia == "evento_sem_nota" and doc is not None and doc["destino"] == "valido":
                ok = g["chave"] in liberados            # a nota chegou depois: saiu da quarentena, como deve
        elif anomalia in REENVIO:
            esperados_reenvio.add((g["lote"], g["arquivo"]))
            ok = doc is not None and doc["destino"] == "reenvio_descartado"
        elif anomalia in AVISO:
            esperados_aviso.add((g["chave"], anomalia))
            ok = (g["chave"], anomalia) in aviso_set
        else:
            ok = False
        if ok:
            achado[anomalia] += 1
        else:
            faltando.append(f"{anomalia}: {g['lote']}/{g['arquivo']}")

    # --- precisão: algo acusado sem ter sido injetado?
    por_serie: dict[tuple[str, int], list[int]] = {}
    for r in recebidos:
        por_serie.setdefault((r["emit_cnpj"], r["serie"]), []).append(r["nnf"])
    for numeros in por_serie.values():
        numeros.sort()

    sobrando, explicados = [], 0
    for d in documentos:
        if d["nome_lote"] not in com_gabarito:
            continue
        k = (d["nome_lote"], d["arquivo"])
        if d["destino"] == "quarentena" and k not in esperados_quarentena:
            sobrando.append(f"quarentena {d['motivo']}: {d['nome_lote']}/{d['arquivo']}")
        if d["destino"] == "reenvio_descartado" and k not in esperados_reenvio:
            sobrando.append(f"reenvio descartado: {d['nome_lote']}/{d['arquivo']}")
    for a in avisos:
        if a["nome_lote"] not in com_gabarito or (a["chave"], a["regra"]) in esperados_aviso:
            continue
        if a["regra"] == "numeracao_com_salto":
            serie = por_serie.get((a["emit_cnpj"], a["serie"]), [])
            anteriores = [n for n in serie if n < a["nnf"]]
            buraco = range(anteriores[-1] + 1, a["nnf"]) if anteriores else range(0)
            if buraco and all((a["emit_cnpj"], a["serie"], n) in pendentes for n in buraco):
                explicados += 1
                continue
        sobrando.append(f"aviso {a['regra']}: {a['chave']}")

    tardios = [s for s in situacao if s["chegada_cancelamento"] == "depois_da_nota"]
    return {
        "fechamento": fechamento,
        "esperado": esperado, "achado": achado, "faltando": faltando,
        "sobrando": sobrando, "explicados": explicados,
        "tardios": len(tardios), "tardios_cancelados": sum(s["situacao"] == "cancelada" for s in tardios),
        "lotes_gabarito": len(com_gabarito), "lotes": len(lotes),
    }


def imprimir(r: dict) -> int:
    abertos = [f for f in r["fechamento"] if not f["silver_fechada"]]
    total = sum(r["esperado"].values())
    acertos = sum(r["achado"].values())
    checagens = [
        ("PASSOU" if not abertos and r["fechamento"] else "FALHOU",
         "1. fechamento: bronze = válidos + quarentena + reenvios",
         f"{len(r['fechamento']) - len(abertos)}/{len(r['fechamento'])} lotes"),
        ("PASSOU" if acertos == total else "FALHOU",
         "2. gabarito: anomalia injetada encontrada onde devia", f"{acertos}/{total}"),
        ("PASSOU" if not r["sobrando"] else "FALHOU",
         "   gabarito: nada acusado sem ter sido injetado",
         f"{len(r['sobrando'])} sobrando" + (f", {r['explicados']} salto(s) por nota ainda não entregue"
                                             if r["explicados"] else "")),
    ]
    if r["tardios"] == 0:
        checagens.append(("INCONCLUSIVO", "3. cancelamento que chega depois muda a situação",
                          "nenhum caso no período: gere mais dias"))
    else:
        checagens.append(("PASSOU" if r["tardios_cancelados"] == r["tardios"] else "FALHOU",
                          "3. cancelamento que chega depois muda a situação",
                          f"{r['tardios_cancelados']}/{r['tardios']} notas canceladas"))

    largura = max(len(c[1]) for c in checagens)
    print(f"\nMarco 4 · conferência da silver ({r['lotes']} lotes, {r['lotes_gabarito']} com gabarito)\n")
    for status, texto, numero in checagens:
        print(f"  {status:<12} {texto:<{largura}}  {numero}")

    print(f"\n  {'anomalia':<32} {'injetadas':>9} {'encontradas':>11}")
    for anomalia in sorted(r["esperado"]):
        print(f"  {anomalia:<32} {r['esperado'][anomalia]:>9} {r['achado'][anomalia]:>11}")
    for titulo, lista in (("não encontradas", r["faltando"]), ("acusadas sem gabarito", r["sobrando"])):
        if lista:
            print(f"\n  {titulo}:")
            for item in lista[:20]:
                print(f"    - {item}")
            if len(lista) > 20:
                print(f"    ... e mais {len(lista) - 20}")
    print()

    estados = {c[0] for c in checagens}
    if "FALHOU" in estados:
        return 1
    return 2 if "INCONCLUSIVO" in estados else 0


def main() -> int:
    return imprimir(conferir(carregar()))


if __name__ == "__main__":
    sys.exit(main())
