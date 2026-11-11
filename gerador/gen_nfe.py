#!/usr/bin/env python3
"""
Sistema de origem simulado: o ERP do Grupo Xis emitindo NF-e (modelo 55, leiaute 4.00 + IBS/CBS).

Entrega um LOTE POR DIA na pasta de rede, como um ERP faria:

    <raiz>/inbox/lote_2026-01-02/
        <chave>-procNFe.xml                  nota autorizada (NF-e + protocolo)
        <chave>-110111-01-procEventoNFe.xml  evento de cancelamento
        controle_lote.json                   o que a origem declara ter enviado (escrito por último)
    <raiz>/gabarito/lote_2026-01-02.csv      anomalias injetadas (o pipeline NÃO lê esta pasta)
    <raiz>/gerador_estado.json               numeração, vendas devolvíveis e pendências entre lotes

O gerador tem estado: cada execução continua do dia seguinte ao último lote. É isso que permite
numeração sequencial por CNPJ, devolução que referencia uma venda que existe e cancelamento que chega
em um lote posterior ao da nota.

Base: gerador do databricks-nfe-lakehouse (visão SEFAZ), reescrito para a visão do emitente
(docs/03_cenario_dados.md). A estrutura da nota tributada é a mesma, que foi validada no XSD
leiauteNFe_v4.00. As regras fiscais são hipóteses a validar (gerador/referencias.py).

Uso:
    python -m gerador.gen_nfe --dias 5                 próximos 5 dias, sem anomalias
    python -m gerador.gen_nfe --dias 5 --sujo          + anomalias nas taxas padrão
    python -m gerador.gen_nfe --ate 2026-03-31 --sujo  do próximo dia até a data
    python -m gerador.gen_nfe --dias 1 --incompleto 40 último lote entregue com 40 arquivos a menos

Dados 100% fictícios.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import random
import string
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from gerador.referencias import (
    BAIRROS, CATALOGO, CST_IBSCBS_SEM_INCIDENCIA, CST_IBSCBS_TRIBUTADA, DS, EMPRESAS, ESTABELECIMENTOS,
    JUSTIFICATIVAS, LOGR, MIX, NOMES_PJ, NS, OPERACOES, P_CBS, P_IBSMUN, P_IBSUF, P_ICMS_INTER_IMPORTADO,
    PRENOMES, SOBRENOMES, SUFIXOS_PJ, UFS,
)

D = Decimal
TZ = "-03:00"
VERSAO_ESTADO = 1
# taxas usadas com --sujo. Uma taxa passada na linha de comando prevalece.
ANOMALIAS_PADRAO = {
    "defect_rate": 0.02,     # inconsistência de valores ou de classificação (aviso)
    "dup_rate": 0.01,        # reenvio idêntico do mesmo arquivo
    "corrupt_rate": 0.005,   # arquivo truncado no transporte
    "externo_rate": 0.003,   # nota de emitente que não é do grupo
    "conteudo_rate": 0.003,  # mesma chave reenviada com conteúdo diferente
    "salto_rate": 0.002,     # buraco na numeração
    "ref_rate": 0.05,        # das devoluções: refNFe para nota que não existe
    "tarde_rate": 0.10,      # dos cancelamentos: fora do prazo de 24 horas
    "orfao_rate": 0.10,      # dos cancelamentos: o evento chega antes da nota (nota atrasada 2 lotes)
}
P_CANCELAMENTO = 0.015       # das vendas. Não é anomalia: é a operação normal


# ------------------------------------------------------------------------------ utilidades
def q(x, casas=2) -> D:
    return D(x).quantize(D(1).scaleb(-casas), rounding=ROUND_HALF_UP)


def fmt(x, casas=2) -> str:
    return f"{q(x, casas):f}"


def dv_mod11(corpo: str, pesos_max: int = 9) -> str:
    """Módulo 11, pesos 2..pesos_max da direita para a esquerda (valor do caractere = ASCII - 48)."""
    soma, p = 0, 2
    for ch in reversed(corpo):
        soma += (ord(ch) - 48) * p
        p = 2 if p == pesos_max else p + 1
    r = soma % 11
    return "0" if r < 2 else str(11 - r)


def cnpj_de(raiz: str, ordem: int) -> str:
    corpo = f"{raiz}{ordem:04d}"
    d1 = dv_mod11(corpo)
    return corpo + d1 + dv_mod11(corpo + d1)


def gera_raiz(rng: random.Random, alfanumerico: bool = False) -> str:
    alfabeto = string.digits + string.ascii_uppercase if alfanumerico else string.digits
    return "".join(rng.choice(alfabeto) for _ in range(8))


def gera_cpf(rng: random.Random) -> str:
    n = [rng.randint(0, 9) for _ in range(9)]
    for _ in range(2):
        s = sum(a * b for a, b in zip(n, range(len(n) + 1, 1, -1)))
        n.append(0 if s % 11 < 2 else 11 - s % 11)
    return "".join(map(str, n))


def monta_chave(cuf: str, dt: datetime, cnpj: str, serie: int, nnf: int, cnf: str) -> str:
    c43 = f"{cuf}{dt:%y%m}{cnpj}55{serie:03d}{nnf:09d}1{cnf}"
    return c43 + dv_mod11(c43)


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def sub(parent, tag, text=None, ns=NS, **attrs):
    e = ET.SubElement(parent, f"{{{ns}}}{tag}", {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def subs(parent, pares):
    for t, v in pares:
        if v is not None:
            sub(parent, t, v)


def dh(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + TZ


# ------------------------------------------------------------------------------ cadastro
@dataclass(frozen=True)
class Participante:
    tipo: str            # CNPJ | CPF
    doc: str
    nome: str
    uf: str
    cmun: str
    xmun: str
    lgr: str
    nro: str
    bairro: str
    cep: str
    ie: str | None


@dataclass(frozen=True)
class Estabelecimento:
    idx: int
    empresa: str         # importadora | comercio
    part: Participante
    peso: int
    nnf_inicial: int
    serie: int = 1


@dataclass(frozen=True)
class ClientePJ:
    base: Participante
    mudanca_em: date | None = None      # alteração cadastral datada (alimenta o histórico da dimensão)
    depois: Participante | None = None

    def em(self, d: date) -> Participante:
        return self.depois if self.mudanca_em and d >= self.mudanca_em else self.base


def _endereco(rng, uf, i_mun=None):
    municipios = UFS[uf][3]
    cmun, xmun = municipios[i_mun] if i_mun is not None else rng.choice(municipios)
    return dict(uf=uf, cmun=cmun, xmun=xmun, lgr=f"{rng.choice(LOGR)} {rng.randint(1, 99)}",
                nro=rng.choice(["SN", str(rng.randint(1, 3000)), str(rng.randint(1, 3000))]),
                bairro=rng.choice(BAIRROS), cep=f"{rng.randint(10000000, 99999999)}")


def _pj(rng, uf, alfanumerico=False) -> Participante:
    nome = f"{rng.choice(NOMES_PJ)} {rng.choice(SUFIXOS_PJ)} {rng.choice(['LTDA', 'S.A.', 'LTDA', 'LTDA'])}"
    return Participante("CNPJ", cnpj_de(gera_raiz(rng, alfanumerico), 1), nome,
                        ie=str(rng.randint(10**8, 10**9 - 1)), **_endereco(rng, uf))


def _pf(rng, uf) -> Participante:
    nome = f"{rng.choice(PRENOMES)} {rng.choice(SOBRENOMES)} {rng.choice(SOBRENOMES)}"
    return Participante("CPF", gera_cpf(rng), nome, ie=None, **_endereco(rng, uf))


class Cadastro:
    """Determinístico pela seed: a entidade nº k é sempre a mesma, em qualquer execução."""

    def __init__(self, seed: int, n_pj: int = 400, n_pf: int = 900, pct_alfa: float = 0.05, pct_mudanca: float = 0.10):
        def r(papel: str, k) -> random.Random:
            return random.Random(f"{seed}-{papel}-{k}")

        raizes = {emp: gera_raiz(r("raiz", emp)) for emp in EMPRESAS}
        self.estabelecimentos: list[Estabelecimento] = []
        for idx, (emp, ordem, uf, i_mun, peso, nnf0) in enumerate(ESTABELECIMENTOS):
            rng = r("estab", idx)
            part = Participante("CNPJ", cnpj_de(raizes[emp], ordem), EMPRESAS[emp],
                                ie=str(rng.randint(10**8, 10**9 - 1)), **_endereco(rng, uf, i_mun))
            self.estabelecimentos.append(Estabelecimento(idx, emp, part, peso, nnf0))
        self.cnpjs_grupo = {e.part.doc for e in self.estabelecimentos}

        ufs, pesos = list(UFS), [UFS[u][2] for u in UFS]
        self.clientes_pj: list[ClientePJ] = []
        for k in range(n_pj):
            rng = r("pj", k)
            uf = rng.choices(ufs, weights=pesos)[0]
            base = _pj(rng, uf, rng.random() < pct_alfa)
            if rng.random() < pct_mudanca:
                quando = date(2026, 1, 1) + timedelta(days=rng.randint(30, 300))
                depois = (replace(base, nome=f"{base.nome.rsplit(' ', 1)[0]} {rng.choice(SUFIXOS_PJ)} LTDA")
                          if rng.random() < 0.5 else replace(base, **_endereco(rng, uf)))
                self.clientes_pj.append(ClientePJ(base, quando, depois))
            else:
                self.clientes_pj.append(ClientePJ(base))
        self.clientes_pf = [_pf(r("pf", k), r("pf-uf", k).choices(ufs, weights=pesos)[0]) for k in range(n_pf)]
        # um operador logístico por UF de estabelecimento
        self.operadores = {uf: replace(_pj(r("oplog", uf), uf), nome=f"OPERADOR LOGISTICO {uf} ARMAZENS GERAIS LTDA")
                           for uf in sorted({e.part.uf for e in self.estabelecimentos})}
        self._pesos_estab = [e.peso for e in self.estabelecimentos]

    def estabelecimento(self, rng) -> Estabelecimento:
        return rng.choices(self.estabelecimentos, weights=self._pesos_estab)[0]


# ------------------------------------------------------------------------------ XML
def _endereco_xml(parent, tag, p: Participante):
    e = sub(parent, tag)
    subs(e, [("xLgr", p.lgr), ("nro", p.nro), ("xBairro", p.bairro), ("cMun", p.cmun), ("xMun", p.xmun),
             ("UF", p.uf), ("CEP", p.cep), ("cPais", "1058"), ("xPais", "BRASIL")])


def _assinatura(parent, rng, uri: str):
    """Assinatura com estrutura real e conteúdo aleatório (não verificável)."""
    def b64(k):
        return base64.b64encode(rng.randbytes(k)).decode()

    sig = sub(parent, "Signature", ns=DS)
    si = sub(sig, "SignedInfo", ns=DS)
    sub(si, "CanonicalizationMethod", ns=DS, Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    sub(si, "SignatureMethod", ns=DS, Algorithm=f"{DS}rsa-sha1")
    ref = sub(si, "Reference", ns=DS, URI=f"#{uri}")
    trs = sub(ref, "Transforms", ns=DS)
    sub(trs, "Transform", ns=DS, Algorithm=f"{DS}enveloped-signature")
    sub(trs, "Transform", ns=DS, Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    sub(ref, "DigestMethod", ns=DS, Algorithm=f"{DS}sha1")
    sub(ref, "DigestValue", b64(20), ns=DS)
    sub(sig, "SignatureValue", b64(256), ns=DS)
    sub(sub(sub(sig, "KeyInfo", ns=DS), "X509Data", ns=DS), "X509Certificate", b64(600), ns=DS)


def serializar(root) -> str:
    xml = ET.tostring(root, encoding="unicode")
    # Signature com xmlns default próprio (sem prefixo), como no XML real
    xml = (xml.replace(f' xmlns:ds="{DS}"', "").replace("<ds:Signature>", f'<Signature xmlns="{DS}">')
              .replace("<ds:", "<").replace("</ds:", "</"))
    return '<?xml version="1.0" encoding="UTF-8"?>' + xml


def sortear_itens(rng: random.Random, operacao: str) -> list[tuple[int, str, str]]:
    """[(índice no catálogo, quantidade, valor unitário)]. Perfil por operação."""
    if operacao == "venda_pf":
        n, faixa_qtd = rng.choices([1, 2, 3], weights=[60, 30, 10])[0], (1, 3)
    elif operacao in ("transferencia", "remessa_oplog"):
        n, faixa_qtd = rng.randint(3, 10), (20, 200)
    else:
        n, faixa_qtd = rng.choices([1, 2, 3, 4, 5, 8], weights=[25, 25, 20, 15, 10, 5])[0], (2, 60)
    itens = []
    for i in rng.sample(range(len(CATALOGO)), k=min(n, len(CATALOGO))):
        lo, hi = CATALOGO[i][4]
        itens.append((i, str(rng.randint(*faixa_qtd)), fmt(D(rng.uniform(lo, hi)), 4)))
    return itens


def gerar_nota(rng: random.Random, emit: Participante, empresa: str, serie: int, nnf: int, operacao: str,
               dest: Participante, dt: datetime, itens: list[tuple[int, str, str]], *, ref_chave: str | None = None,
               defeito: str = "", obs: str = "") -> dict:
    """Monta um procNFe. Devolve chave, xml (texto), vnf, nprot e os totais usados na conferência."""
    natop, tpnf, finnfe, cfops, tributada, fatura = OPERACOES[operacao]
    interestadual = emit.uf != dest.uf
    consumidor_final = dest.tipo == "CPF"
    importadora = empresa == "importadora"
    saida = tpnf == "1"
    casas_aliq = rng.choice([2, 4])        # emissores variam as casas decimais das alíquotas
    cuf = UFS[emit.uf][0]
    cnf = f"{rng.randint(0, 99_999_999):08d}"
    chave = monta_chave(cuf, dt, emit.doc, serie, nnf, cnf)
    cfop = cfops[1] if interestadual else cfops[0]

    nfe = ET.Element(f"{{{NS}}}NFe")
    inf = sub(nfe, "infNFe", Id=f"NFe{chave}", versao="4.00")
    ide = sub(inf, "ide")
    subs(ide, [("cUF", cuf), ("cNF", cnf), ("natOp", natop), ("mod", "55"), ("serie", serie), ("nNF", nnf),
               ("dhEmi", dh(dt)), ("dhSaiEnt", dh(dt)), ("tpNF", tpnf), ("idDest", "2" if interestadual else "1"),
               ("cMunFG", emit.cmun), ("tpImp", "1"), ("tpEmis", "1"), ("cDV", chave[-1]), ("tpAmb", "2"),
               ("finNFe", finnfe), ("indFinal", "1" if consumidor_final else "0"),
               ("indPres", "2" if consumidor_final else "9"), ("indIntermed", "0"),
               ("procEmi", "0"), ("verProc", "ERP XIS 7.3")])
    if ref_chave:
        sub(sub(ide, "NFref"), "refNFe", ref_chave)

    em = sub(inf, "emit")
    subs(em, [("CNPJ", emit.doc), ("xNome", emit.nome), ("xFant", emit.nome)])
    _endereco_xml(em, "enderEmit", emit)
    subs(em, [("IE", emit.ie), ("CRT", "3")])
    de = sub(inf, "dest")
    subs(de, [(dest.tipo, dest.doc), ("xNome", dest.nome)])
    _endereco_xml(de, "enderDest", dest)
    subs(de, [("indIEDest", "1" if dest.ie else "9"), ("IE", dest.ie)])

    orig = "1" if importadora else "2"     # 1 importação direta; 2 estrangeira adquirida no mercado interno
    p_icms = P_ICMS_INTER_IMPORTADO if interestadual else UFS[emit.uf][1]
    cst_ibs, cclass_ok = CST_IBSCBS_TRIBUTADA if tributada else CST_IBSCBS_SEM_INCIDENCIA
    tot = {k: D(0) for k in ["vBC", "vICMS", "vProd", "vIPI", "vPIS", "vCOFINS", "vBCIBSCBS", "vIBSUF", "vIBSMun", "vCBS"]}

    for n, (i_prod, qtd_txt, vun_txt) in enumerate(itens, 1):
        cprod, xprod, ncm, ucom, _, p_ipi = CATALOGO[i_prod]
        qtd, vun = D(qtd_txt), D(vun_txt)
        vprod = q(qtd * vun)
        det = sub(inf, "det", nItem=n)
        ean = f"789{(i_prod + 1) * 7919 % 10**10:010d}"
        subs(sub(det, "prod"),
             [("cProd", cprod), ("cEAN", ean), ("xProd", xprod), ("NCM", ncm), ("CFOP", cfop), ("uCom", ucom),
              ("qCom", fmt(qtd, 4)), ("vUnCom", fmt(vun, 10)), ("vProd", fmt(vprod)), ("cEANTrib", ean),
              ("uTrib", ucom), ("qTrib", fmt(qtd, 4)), ("vUnTrib", fmt(vun, 10)), ("indTot", "1")])
        imp = sub(det, "imposto")
        vbc_icms = vicms = vipi = vpis = vcof = D(0)

        icms = sub(imp, "ICMS")
        if tributada:
            vbc_icms = vprod
            vicms = q(vbc_icms * p_icms / 100)
            subs(sub(icms, "ICMS00"), [("orig", orig), ("CST", "00"), ("modBC", "3"), ("vBC", fmt(vbc_icms)),
                                       ("pICMS", fmt(p_icms, 4)), ("vICMS", fmt(vicms))])
        else:
            subs(sub(icms, "ICMS40"), [("orig", orig), ("CST", "41")])

        ipi = sub(imp, "IPI")
        sub(ipi, "cEnq", "999")
        if tributada and saida and importadora:      # importador equiparado a industrial
            vipi = q(vprod * p_ipi / 100)
            subs(sub(ipi, "IPITrib"), [("CST", "50"), ("vBC", fmt(vprod)), ("pIPI", fmt(p_ipi, 4)), ("vIPI", fmt(vipi))])
        else:
            sub(sub(ipi, "IPINT"), "CST", ("53" if tributada else "55") if saida else "03")

        for trib, aliq in (("PIS", D("1.65")), ("COFINS", D("7.60"))):
            g = sub(imp, trib)
            if not tributada:
                sub(sub(g, f"{trib}NT"), "CST", "08")
                continue
            vb = vprod - vicms
            v = q(vb * aliq / 100)
            subs(sub(g, f"{trib}Aliq" if saida else f"{trib}Outr"),
                 [("CST", "01" if saida else "98"), ("vBC", fmt(vb)), (f"p{trib}", fmt(aliq, 4)), (f"v{trib}", fmt(v))])
            if trib == "PIS":
                vpis = v
            else:
                vcof = v

        if tributada and interestadual and consumidor_final and saida:      # DIFAL
            subs(sub(imp, "ICMSUFDest"),
                 [("vBCUFDest", fmt(vprod)), ("vBCFCPUFDest", fmt(vprod)), ("pFCPUFDest", "0.0000"),
                  ("pICMSUFDest", fmt(UFS[dest.uf][1], 4)), ("pICMSInter", fmt(p_icms)),
                  ("pICMSInterPart", "100"), ("vFCPUFDest", "0.00"), ("vICMSUFDest", "0.00"), ("vICMSUFRemet", "0.00")])

        cclass = cclass_ok
        if defeito == "cclasstrib_incoerente" and n == 1:
            cclass = "200038" if cst_ibs == "000" else "000001"
        ibs = sub(imp, "IBSCBS")
        subs(ibs, [("CST", cst_ibs), ("cClassTrib", cclass)])
        vbc = vibsuf = vibsmun = vcbs = D(0)
        if tributada:
            vbc = vprod - vicms - vpis - vcof
            vibsuf, vibsmun, vcbs = q(vbc * P_IBSUF / 100), q(vbc * P_IBSMUN / 100), q(vbc * P_CBS / 100)
            gi = sub(ibs, "gIBSCBS")
            sub(gi, "vBC", fmt(vbc))
            subs(sub(gi, "gIBSUF"), [("pIBSUF", fmt(P_IBSUF, casas_aliq)), ("vIBSUF", fmt(vibsuf))])
            subs(sub(gi, "gIBSMun"), [("pIBSMun", fmt(P_IBSMUN, casas_aliq)), ("vIBSMun", fmt(vibsmun))])
            sub(gi, "vIBS", fmt(vibsuf + vibsmun))
            subs(sub(gi, "gCBS"), [("pCBS", fmt(P_CBS, casas_aliq)), ("vCBS", fmt(vcbs))])
        if not saida:
            idv = sub(det, "impostoDevol")
            sub(idv, "pDevol", "100.00")
            sub(sub(idv, "IPI"), "vIPIDevol", "0.00")
        for k, v in (("vBC", vbc_icms), ("vICMS", vicms), ("vProd", vprod), ("vIPI", vipi), ("vPIS", vpis),
                     ("vCOFINS", vcof), ("vBCIBSCBS", vbc), ("vIBSUF", vibsuf), ("vIBSMun", vibsmun), ("vCBS", vcbs)):
            tot[k] += v

    vnf = tot["vProd"] + tot["vIPI"]
    declarado = dict(tot)                   # o que vai escrito no grupo de totais (pode divergir dos itens)
    if defeito == "tot_cbs_divergente":
        declarado["vCBS"] += D("0.05")
    elif defeito == "vbc_ibscbs_divergente":
        declarado["vBCIBSCBS"] += D("1.00")

    total = sub(inf, "total")
    subs(sub(total, "ICMSTot"),
         [("vBC", fmt(tot["vBC"])), ("vICMS", fmt(tot["vICMS"])), ("vICMSDeson", "0.00"), ("vFCP", "0.00"),
          ("vBCST", "0.00"), ("vST", "0.00"), ("vFCPST", "0.00"), ("vFCPSTRet", "0.00"), ("vProd", fmt(tot["vProd"])),
          ("vFrete", "0.00"), ("vSeg", "0.00"), ("vDesc", "0.00"), ("vII", "0.00"), ("vIPI", fmt(tot["vIPI"])),
          ("vIPIDevol", "0.00"), ("vPIS", fmt(tot["vPIS"])), ("vCOFINS", fmt(tot["vCOFINS"])), ("vOutro", "0.00"),
          ("vNF", fmt(vnf))])
    it = sub(total, "IBSCBSTot")
    sub(it, "vBCIBSCBS", fmt(declarado["vBCIBSCBS"]))
    gibs = sub(it, "gIBS")
    subs(sub(gibs, "gIBSUF"), [("vDif", "0.00"), ("vDevTrib", "0.00"), ("vIBSUF", fmt(declarado["vIBSUF"]))])
    subs(sub(gibs, "gIBSMun"), [("vDif", "0.00"), ("vDevTrib", "0.00"), ("vIBSMun", fmt(declarado["vIBSMun"]))])
    subs(gibs, [("vIBS", fmt(declarado["vIBSUF"] + declarado["vIBSMun"])), ("vCredPres", "0.00"),
                ("vCredPresCondSus", "0.00")])
    subs(sub(it, "gCBS"), [("vDif", "0.00"), ("vDevTrib", "0.00"), ("vCBS", fmt(declarado["vCBS"])),
                           ("vCredPres", "0.00"), ("vCredPresCondSus", "0.00")])
    sub(total, "vNFTot", fmt(vnf))

    sub(sub(inf, "transp"), "modFrete", "9" if consumidor_final else rng.choice(["0", "1"]))
    if fatura:
        cobr = sub(inf, "cobr")
        subs(sub(cobr, "fat"), [("nFat", str(nnf)), ("vOrig", fmt(vnf)), ("vDesc", "0.00"), ("vLiq", fmt(vnf))])
        n_dup = 1 if consumidor_final else rng.choice([1, 2, 3])
        parcelas = [q(vnf / n_dup)] * (n_dup - 1)
        parcelas.append(vnf - sum(parcelas))
        for k, vp in enumerate(parcelas, 1):
            subs(sub(cobr, "dup"), [("nDup", f"{k:03d}"), ("dVenc", f"{dt + timedelta(days=30 * k):%Y-%m-%d}"),
                                    ("vDup", fmt(vp))])
    dp = sub(sub(inf, "pag"), "detPag")
    if fatura:
        subs(dp, [("indPag", "0" if consumidor_final else "1"),
                  ("tPag", rng.choice(["03", "17"]) if consumidor_final else "15"), ("vPag", fmt(vnf))])
    else:
        subs(dp, [("tPag", "90"), ("vPag", "0.00")])
    cpl = "DOCUMENTO SINTETICO PARA TESTES - SEM VALOR FISCAL."
    if ref_chave:
        cpl += f" DEVOLUCAO REF. NF-E CHAVE {ref_chave}."
    if obs:
        cpl += f" {obs}"
    sub(sub(inf, "infAdic"), "infCpl", cpl)
    _assinatura(nfe, rng, f"NFe{chave}")

    root = ET.Element(f"{{{NS}}}nfeProc", versao="4.00")
    root.append(nfe)
    nprot = f"1{cuf}{rng.randint(10**12, 10**13 - 1)}"
    subs(sub(sub(root, "protNFe", versao="4.00"), "infProt"),
         [("tpAmb", "2"), ("verAplic", f"{emit.uf}4.0"), ("chNFe", chave),
          ("dhRecbto", dh(dt + timedelta(seconds=rng.randint(1, 30)))), ("nProt", nprot),
          ("digVal", base64.b64encode(rng.randbytes(20)).decode()), ("cStat", "100"),
          ("xMotivo", "Autorizado o uso da NF-e")])
    return {"chave": chave, "xml": serializar(root), "vnf": vnf, "nprot": nprot, "nfe": nfe,
            "vcbs": tot["vCBS"], "vibs": tot["vIBSUF"] + tot["vIBSMun"]}


def gerar_cancelamento(rng: random.Random, chave: str, emit: Participante, dest: Participante, nprot_nota: str,
                       quando: datetime) -> str:
    """procEventoNFe de cancelamento (tpEvento 110111), no formato de distribuição."""
    cuf = UFS[emit.uf][0]
    id_evento = f"ID110111{chave}01"
    root = ET.Element(f"{{{NS}}}procEventoNFe", versao="1.00")
    evento = sub(root, "evento", versao="1.00")
    inf = sub(evento, "infEvento", Id=id_evento)
    subs(inf, [("cOrgao", cuf), ("tpAmb", "2"), ("CNPJ", emit.doc), ("chNFe", chave), ("dhEvento", dh(quando)),
               ("tpEvento", "110111"), ("nSeqEvento", "1"), ("verEvento", "1.00")])
    subs(sub(inf, "detEvento", versao="1.00"),
         [("descEvento", "Cancelamento"), ("nProt", nprot_nota), ("xJust", rng.choice(JUSTIFICATIVAS))])
    _assinatura(evento, rng, id_evento)
    ret = sub(sub(root, "retEvento", versao="1.00"), "infEvento")
    subs(ret, [("tpAmb", "2"), ("verAplic", f"{emit.uf}4.0"), ("cOrgao", cuf), ("cStat", "135"),
               ("xMotivo", "Evento registrado e vinculado a NF-e"), ("chNFe", chave), ("tpEvento", "110111"),
               ("xEvento", "Cancelamento registrado"), ("nSeqEvento", "1"),
               ("CNPJDest" if dest.tipo == "CNPJ" else "CPFDest", dest.doc),
               ("dhRegEvento", dh(quando + timedelta(seconds=rng.randint(1, 20)))),
               ("nProt", f"1{cuf}{rng.randint(10**12, 10**13 - 1)}")])
    return serializar(root)


# ------------------------------------------------------------------------------ estado entre lotes
def estado_inicial(seed: int, inicio: date, cad: Cadastro) -> dict:
    return {"versao": VERSAO_ESTADO, "seed": seed, "proximo_dia": inicio.isoformat(),
            "nnf": {e.part.doc: e.nnf_inicial for e in cad.estabelecimentos},
            "vendas_pf": [],        # vendas a pessoa física que ainda podem ser devolvidas
            "pendentes": [],        # arquivos já gerados que só são entregues em um lote futuro
            "lotes": 0}


def carregar_estado(caminho: Path) -> dict | None:
    if not caminho.exists():
        return None
    estado = json.loads(caminho.read_text(encoding="utf-8"))
    if estado.get("versao") != VERSAO_ESTADO:
        raise SystemExit(f"{caminho}: versão de estado {estado.get('versao')} não suportada")
    return estado


def escrever_atomico(destino: Path, conteudo: str) -> None:
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_text(conteudo, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, destino)


# ------------------------------------------------------------------------------ um lote = um dia
def nome_lote(dia: date) -> str:
    return f"lote_{dia.isoformat()}"


def gerar_dia(dia: date, cad: Cadastro, estado: dict, taxas: dict, notas_dia: int, schema=None) -> dict:
    """Gera os arquivos do dia. Devolve {'arquivos': [...], 'gabarito': [...]} e atualiza o estado."""
    rng = random.Random(f"{estado['seed']}-{dia.isoformat()}")
    lote = nome_lote(dia)
    arquivos: list[dict] = []      # {arquivo, tipo, chave, vnf, xml, conteudo}  conteudo = o que vai para o disco
    gabarito: list[tuple] = []     # (lote, arquivo, chave, anomalia, onde_deve_aparecer)

    def anota(arquivo, chave, anomalia, onde):
        gabarito.append((lote, arquivo, chave, anomalia, onde))

    def entrega(arquivo, tipo, chave, vnf, xml, conteudo=None):
        arquivos.append({"arquivo": arquivo, "tipo": tipo, "chave": chave,
                         "vnf": None if vnf is None else fmt(vnf), "xml": xml,
                         "conteudo": xml if conteudo is None else conteudo})

    # 1. pendências de lotes anteriores que vencem hoje (cancelamento posterior, nota atrasada)
    ficam = []
    for p in estado["pendentes"]:
        if p["dia"] <= dia.isoformat():
            entrega(p["arquivo"], p["tipo"], p["chave"], None if p["vnf"] is None else D(p["vnf"]), p["xml"])
            for anomalia, onde in p.get("anomalias", []):
                anota(p["arquivo"], p["chave"], anomalia, onde)
        else:
            ficam.append(p)
    estado["pendentes"] = ficam

    def pendura(quando: date, arquivo, tipo, chave, vnf, xml, anomalias=()):
        estado["pendentes"].append({"dia": quando.isoformat(), "arquivo": arquivo, "tipo": tipo, "chave": chave,
                                    "vnf": None if vnf is None else fmt(vnf), "xml": xml,
                                    "anomalias": [list(a) for a in anomalias]})

    # 2. emissões do dia, em ordem de horário (a numeração acompanha o relógio)
    fator = 1.0 if dia.weekday() < 5 else 0.22
    n = max(1, round(notas_dia * fator * rng.uniform(0.85, 1.15)))
    base = datetime.combine(dia, datetime.min.time()) + timedelta(hours=7)
    horarios = sorted(base + timedelta(seconds=rng.randint(0, 12 * 3600)) for _ in range(n))
    limite_devolucao = (dia - timedelta(days=45)).isoformat()
    estado["vendas_pf"] = [v for v in estado["vendas_pf"] if v["dia"] >= limite_devolucao]

    for dt in horarios:
        est = cad.estabelecimento(rng)
        emit = est.part
        ops = MIX[est.empresa]
        operacao = rng.choices(list(ops), weights=list(ops.values()))[0]

        # nota de emitente que não é do grupo: não consome numeração do grupo
        if rng.random() < taxas["externo_rate"]:
            estranho = _pj(rng, rng.choice(list(UFS)))
            dest = cad.clientes_pj[rng.randrange(len(cad.clientes_pj))].em(dia)
            nota = gerar_nota(rng, estranho, "comercio", 1, rng.randint(1, 900000), "venda_pj", dest, dt,
                              sortear_itens(rng, "venda_pj"))
            arq = f"{nota['chave']}-procNFe.xml"
            entrega(arq, "nfe", nota["chave"], nota["vnf"], nota["xml"])
            anota(arq, nota["chave"], "emitente_fora_do_grupo", "quarentena")
            continue

        ref_chave, itens, origem_devolucao, ref_falsa = None, None, None, False
        if operacao == "devolucao_pf":
            elegiveis = [v for v in estado["vendas_pf"] if v["estab"] == est.idx
                         and v["dia"] <= (dia - timedelta(days=3)).isoformat()]
            if not elegiveis:
                operacao = "venda_pf"                   # ainda não há venda para devolver
            else:
                origem_devolucao = rng.choice(elegiveis)
                estado["vendas_pf"].remove(origem_devolucao)
                dest = cad.clientes_pf[origem_devolucao["cliente"]]
                itens = [tuple(i) for i in origem_devolucao["itens"]]
                ref_chave = origem_devolucao["chave"]
                if rng.random() < taxas["ref_rate"]:    # referência para uma nota que não existe
                    ref_falsa = True
                    ref_chave = monta_chave(UFS[emit.uf][0], dt - timedelta(days=20), emit.doc, est.serie,
                                            rng.randint(1, 9000), f"{rng.randint(0, 99_999_999):08d}")
        i_cliente = None
        if operacao == "venda_pj":
            dest = cad.clientes_pj[rng.randrange(len(cad.clientes_pj))].em(dia)
        elif operacao == "venda_pf":
            i_cliente = rng.randrange(len(cad.clientes_pf))
            dest = cad.clientes_pf[i_cliente]
        elif operacao == "venda_grupo":
            dest = rng.choice([e for e in cad.estabelecimentos if e.empresa == "comercio"]).part
        elif operacao == "transferencia":
            dest = rng.choice([e for e in cad.estabelecimentos if e.empresa == est.empresa and e.idx != est.idx]).part
        elif operacao == "remessa_oplog":
            dest = cad.operadores[emit.uf]
        itens = itens or sortear_itens(rng, operacao)

        defeito = ""
        if rng.random() < taxas["defect_rate"] and OPERACOES[operacao][4]:
            defeito = rng.choice(["tot_cbs_divergente", "vbc_ibscbs_divergente", "cclasstrib_incoerente"])

        salto = rng.random() < taxas["salto_rate"]
        if salto:
            estado["nnf"][emit.doc] += 1               # um número fica sem nota
        nnf = estado["nnf"][emit.doc]
        estado["nnf"][emit.doc] = nnf + 1

        nota = gerar_nota(rng, emit, est.empresa, est.serie, nnf, operacao, dest, dt, itens,
                          ref_chave=ref_chave, defeito=defeito)
        chave, xml = nota["chave"], nota["xml"]
        if schema is not None:
            from lxml import etree
            if not schema.validate(etree.fromstring(serializar(nota["nfe"]).encode())):
                raise SystemExit(f"XSD inválido ({chave}): {schema.error_log.last_error}")
        arq = f"{chave}-procNFe.xml"
        marcas = []
        if defeito:
            marcas.append((defeito, "ctrl.dq"))
        if salto:
            marcas.append(("numeracao_com_salto", "ctrl.dq"))
        if ref_falsa:
            marcas.append(("devolucao_ref_inexistente", "ctrl.dq"))

        # cancelamento: evento sobre uma venda já autorizada
        corrompe = rng.random() < taxas["corrupt_rate"]   # decidido antes: nota ilegível não recebe evento
        cancelada = operacao.startswith("venda") and not corrompe and rng.random() < P_CANCELAMENTO
        atrasar_nota = False
        if cancelada:
            tarde = rng.random() < taxas["tarde_rate"]
            atrasar_nota = rng.random() < taxas["orfao_rate"]
            espera = timedelta(hours=rng.uniform(26, 96)) if tarde else timedelta(minutes=rng.randint(5, 600))
            quando = dt + espera
            xml_ev = gerar_cancelamento(rng, chave, emit, dest, nota["nprot"], quando)
            arq_ev = f"{chave}-110111-01-procEventoNFe.xml"
            marcas_ev = []
            if tarde:
                marcas_ev.append(("cancelamento_fora_do_prazo", "ctrl.dq"))
            if atrasar_nota:
                marcas_ev.append(("evento_sem_nota", "quarentena"))
            if quando.date() <= dia:
                entrega(arq_ev, "evento", chave, None, xml_ev)
                for anomalia, onde in marcas_ev:
                    anota(arq_ev, chave, anomalia, onde)
            else:
                pendura(quando.date(), arq_ev, "evento", chave, None, xml_ev, marcas_ev)
        elif operacao == "venda_pf":
            estado["vendas_pf"].append({"chave": chave, "dia": dia.isoformat(), "estab": est.idx,
                                        "cliente": i_cliente, "itens": [list(i) for i in itens]})

        if atrasar_nota:                                # a nota só chega dois lotes depois do evento
            entrega_em = max(dia, quando.date()) + timedelta(days=2)
            pendura(entrega_em, arq, "nfe", chave, nota["vnf"], xml,
                    [("nota_atrasada", "info")] + marcas)
            continue

        if corrompe:                                    # truncado no transporte: o hash declarado é o do original
            entrega(arq, "nfe", chave, nota["vnf"], xml, conteudo=xml[: rng.randint(200, len(xml) // 2)])
            anota(arq, chave, "xml_corrompido", "quarentena")
            continue                                    # ilegível: as demais marcas não são observáveis
        entrega(arq, "nfe", chave, nota["vnf"], xml)
        for anomalia, onde in marcas:
            anota(arq, chave, anomalia, onde)
        if rng.random() < taxas["dup_rate"]:            # reenvio idêntico, com outro nome de arquivo
            dup = f"{chave}-procNFe-reenvio.xml"
            entrega(dup, "nfe", chave, nota["vnf"], xml)
            anota(dup, chave, "reenvio_identico", "ctrl.lote")
        elif rng.random() < taxas["conteudo_rate"]:     # mesma chave, conteúdo diferente
            alterado = xml.replace("SEM VALOR FISCAL.", "SEM VALOR FISCAL. SEGUNDA VIA.", 1)
            alt = f"{chave}-procNFe-v2.xml"
            entrega(alt, "nfe", chave, nota["vnf"], alterado)
            anota(alt, chave, "mesma_chave_conteudo_diferente", "custodia")

    estado["proximo_dia"] = (dia + timedelta(days=1)).isoformat()
    estado["lotes"] += 1
    return {"lote": lote, "dia": dia, "arquivos": arquivos, "gabarito": gabarito}


def controle_do_lote(lote: dict, seed: int) -> dict:
    """O que a origem DECLARA ter enviado. É o ponto de partida da conferência (decisão D005)."""
    arquivos = lote["arquivos"]
    nfes = [a for a in arquivos if a["tipo"] == "nfe"]
    return {
        "lote": lote["lote"],
        "data_referencia": lote["dia"].isoformat(),
        "gerado_em": f"{lote['dia'].isoformat()}T23:59:00{TZ}",
        "seed": seed,
        "arquivos": len(arquivos),
        "nfe": len(nfes),
        "eventos": len(arquivos) - len(nfes),
        "soma_vnf": fmt(sum((D(a["vnf"]) for a in nfes), D(0))),
        # romaneio: um registro por arquivo, com o sha256 do conteúdo na saída do ERP
        "itens": [{"arquivo": a["arquivo"], "tipo": a["tipo"], "chave": a["chave"], "vnf": a["vnf"],
                   "sha256": sha256(a["xml"])} for a in sorted(arquivos, key=lambda a: a["arquivo"])],
    }


def escrever_lote(raiz: Path, lote: dict, seed: int, faltando: int = 0) -> Path:
    """Grava o lote em uma pasta temporária e só então o publica na inbox (o sensor nunca vê lote pela metade).

    faltando > 0 simula entrega incompleta: o controle declara tudo, mas N arquivos não chegam.
    """
    inbox = raiz / "inbox"
    destino = inbox / lote["lote"]
    for pasta in ("inbox", "processando", "processados"):
        if (raiz / pasta / lote["lote"]).exists():
            raise SystemExit(f"{raiz / pasta / lote['lote']} já existe: cada lote é uma entrega única")
    parcial = inbox / f"_parcial_{lote['lote']}"
    parcial.mkdir(parents=True, exist_ok=False)

    ordenados = sorted(lote["arquivos"], key=lambda a: a["arquivo"])
    omitidos = {a["arquivo"] for a in ordenados[len(ordenados) - faltando:]} if faltando else set()
    for a in ordenados:
        if a["arquivo"] not in omitidos:
            escrever_atomico(parcial / a["arquivo"], a["conteudo"])
    for nome in sorted(omitidos):
        lote["gabarito"].append((lote["lote"], nome, "", "arquivo_nao_entregue", "ctrl.lote"))
    # arquivo de controle por último: é o "fim de transmissão" do lote
    escrever_atomico(parcial / "controle_lote.json",
                     json.dumps(controle_do_lote(lote, seed), ensure_ascii=False, indent=1))
    os.replace(parcial, destino)

    pasta_gabarito = raiz / "gabarito"
    pasta_gabarito.mkdir(parents=True, exist_ok=True)
    with open(pasta_gabarito / f"{lote['lote']}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lote", "arquivo", "chave", "anomalia", "onde_deve_aparecer"])
        w.writerows(lote["gabarito"])
    return destino


# ------------------------------------------------------------------------------ linha de comando
def taxas_de(args) -> dict:
    return {k: (getattr(args, k) if getattr(args, k) is not None else (v if args.sujo else 0.0))
            for k, v in ANOMALIAS_PADRAO.items()}


def executar(raiz: Path, *, dias: int | None = None, ate: date | None = None, seed: int = 42,
             inicio: date = date(2026, 1, 2), notas_dia: int = 90, taxas: dict | None = None,
             incompleto: int = 0, xsd: Path | None = None) -> list[dict]:
    """Gera os próximos lotes. Devolve um resumo por lote."""
    taxas = {**{k: 0.0 for k in ANOMALIAS_PADRAO}, **(taxas or {})}
    raiz.mkdir(parents=True, exist_ok=True)
    caminho_estado = raiz / "gerador_estado.json"
    estado = carregar_estado(caminho_estado)
    cad = Cadastro(estado["seed"] if estado else seed)
    if estado is None:
        estado = estado_inicial(seed, inicio, cad)
    primeiro = date.fromisoformat(estado["proximo_dia"])
    if ate is not None:
        dias = (ate - primeiro).days + 1
    if not dias or dias < 1:
        raise SystemExit(f"nada a gerar: o próximo dia é {primeiro} (use --dias N ou --ate AAAA-MM-DD)")

    schema = None
    if xsd:
        from lxml import etree
        schema = etree.XMLSchema(etree.parse(str(xsd)))
    ET.register_namespace("", NS)
    ET.register_namespace("ds", DS)

    resumos = []
    for k in range(dias):
        dia = primeiro + timedelta(days=k)
        lote = gerar_dia(dia, cad, estado, taxas, notas_dia, schema)
        faltando = incompleto if k == dias - 1 else 0
        if faltando >= len(lote["arquivos"]):
            raise SystemExit(f"--incompleto {faltando}: o lote só tem {len(lote['arquivos'])} arquivos")
        destino = escrever_lote(raiz, lote, estado["seed"], faltando)
        escrever_atomico(caminho_estado, json.dumps(estado, ensure_ascii=False))
        ctrl = controle_do_lote(lote, estado["seed"])
        resumos.append({"lote": lote["lote"], "pasta": str(destino), "arquivos": ctrl["arquivos"], "nfe": ctrl["nfe"],
                        "eventos": ctrl["eventos"], "soma_vnf": ctrl["soma_vnf"], "nao_entregues": faltando,
                        "anomalias": len(lote["gabarito"])})
    return resumos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raiz", type=Path, default=Path(os.environ.get("NFE_DIR_ORIGEM", "datasource/nfe")),
                    help="pasta de rede simulada (padrão: NFE_DIR_ORIGEM ou datasource/nfe)")
    ap.add_argument("--dias", type=int, help="quantos dias (lotes) gerar a partir do próximo dia")
    ap.add_argument("--ate", type=date.fromisoformat, help="gerar do próximo dia até esta data (AAAA-MM-DD)")
    ap.add_argument("--inicio", type=date.fromisoformat, default=date(2026, 1, 2),
                    help="primeiro dia (só vale na primeira execução)")
    ap.add_argument("--seed", type=int, default=42, help="seed do cenário (só vale na primeira execução)")
    ap.add_argument("--notas-dia", type=int, default=90, help="emissões em dia útil (fim de semana: 22%%)")
    ap.add_argument("--sujo", action="store_true", help="liga as anomalias nas taxas padrão")
    for nome, padrao in ANOMALIAS_PADRAO.items():
        ap.add_argument(f"--{nome.replace('_', '-')}", type=float, help=f"padrão com --sujo: {padrao}")
    ap.add_argument("--incompleto", type=int, default=0, metavar="N",
                    help="o último lote é entregue com N arquivos a menos do que o controle declara")
    ap.add_argument("--xsd", type=Path, help="nfe_v4.00.xsd: valida cada NF-e gerada (requer lxml)")
    a = ap.parse_args(argv)
    if a.dias is None and a.ate is None:
        a.dias = 1

    for r in executar(a.raiz, dias=a.dias, ate=a.ate, seed=a.seed, inicio=a.inicio, notas_dia=a.notas_dia,
                      taxas=taxas_de(a), incompleto=a.incompleto, xsd=a.xsd):
        extra = f"  NÃO ENTREGUES={r['nao_entregues']}" if r["nao_entregues"] else ""
        print(f"{r['lote']}: {r['arquivos']} arquivos ({r['nfe']} NF-e, {r['eventos']} eventos)  "
              f"soma vNF={r['soma_vnf']}  anomalias={r['anomalias']}{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
