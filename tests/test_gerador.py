"""Sistema de origem simulado (gerador/gen_nfe.py): o cenário Grupo Xis sai como o desenho pede?"""
import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from gerador import gen_nfe
from gerador.referencias import UFS

N = {"n": "http://www.portalfiscal.inf.br/nfe"}
SUJO = dict(gen_nfe.ANOMALIAS_PADRAO)
# taxas altas para que toda anomalia apareça em poucos dias de massa
FORTE = {k: v * 6 for k, v in SUJO.items()} | {"ref_rate": 0.5, "tarde_rate": 0.4, "orfao_rate": 0.4}


def _ler(raiz):
    """Lê tudo o que foi entregue: {lote: {'controle', 'arquivos': {nome: texto}, 'gabarito': [dict]}}."""
    lotes = {}
    for pasta in sorted((raiz / "inbox").iterdir()):
        with open(raiz / "gabarito" / f"{pasta.name}.csv", encoding="utf-8") as f:
            gabarito = list(csv.DictReader(f))
        lotes[pasta.name] = {
            "controle": json.loads((pasta / "controle_lote.json").read_text(encoding="utf-8")),
            "arquivos": {a.name: a.read_text(encoding="utf-8") for a in sorted(pasta.glob("*.xml"))},
            "gabarito": gabarito,
        }
    return lotes


def _notas(lotes, incluir_anomalas=False):
    """Notas legíveis: [(lote, arquivo, raiz_xml)]. Por padrão sem as de emitente estranho."""
    fora = set()
    if not incluir_anomalas:
        fora = {(l, g["arquivo"]) for l, d in lotes.items() for g in d["gabarito"]
                if g["anomalia"] in ("emitente_fora_do_grupo", "xml_corrompido")}
    saida = []
    for l, d in lotes.items():
        for nome, texto in d["arquivos"].items():
            if "procNFe" not in nome or (l, nome) in fora:
                continue
            try:
                saida.append((l, nome, ET.fromstring(texto)))
            except ET.ParseError:
                pass
    return saida


def _t(raiz_xml, caminho):
    return raiz_xml.findtext(caminho, namespaces=N)


@pytest.fixture(scope="module")
def limpo(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("limpo")
    gen_nfe.executar(raiz, dias=21, seed=7)
    return raiz, _ler(raiz), gen_nfe.Cadastro(7)


@pytest.fixture(scope="module")
def sujo(tmp_path_factory):
    raiz = tmp_path_factory.mktemp("sujo")
    gen_nfe.executar(raiz, dias=21, seed=7, taxas=FORTE)
    return raiz, _ler(raiz), gen_nfe.Cadastro(7)


# ------------------------------------------------------------------ o grupo
def test_cadastro_tem_duas_empresas_cinco_estabelecimentos_em_go_sp_pr():
    cad = gen_nfe.Cadastro(42)
    assert len(cad.estabelecimentos) == 5
    assert len({e.part.doc[:8] for e in cad.estabelecimentos}) == 2          # duas raízes de CNPJ
    assert Counter(e.part.uf for e in cad.estabelecimentos) == {"GO": 2, "SP": 2, "PR": 1}
    for e in cad.estabelecimentos:                                             # dígitos verificadores válidos
        assert gen_nfe.cnpj_de(e.part.doc[:8], int(e.part.doc[8:12])) == e.part.doc
    assert cad.estabelecimentos == gen_nfe.Cadastro(42).estabelecimentos       # estável pela seed
    assert len(UFS) == 15 and {c.base.uf for c in cad.clientes_pj} == set(UFS)


def test_toda_nota_e_emitida_por_um_cnpj_do_grupo(limpo):
    _, lotes, cad = limpo
    emitentes = {_t(x, ".//n:emit/n:CNPJ") for _, _, x in _notas(lotes)}
    assert emitentes == cad.cnpjs_grupo


def test_chave_de_acesso_confere_com_o_conteudo(limpo):
    _, lotes, _ = limpo
    for _, nome, x in _notas(lotes):
        chave = x.find(".//n:infNFe", N).get("Id")[3:]
        assert len(chave) == 44 and gen_nfe.dv_mod11(chave[:43]) == chave[43]
        assert chave[6:20] == _t(x, ".//n:emit/n:CNPJ")
        assert int(chave[25:34]) == int(_t(x, ".//n:ide/n:nNF"))
        assert _t(x, ".//n:protNFe/n:infProt/n:chNFe") == chave and _t(x, ".//n:infProt/n:cStat") == "100"
        assert nome.startswith(chave)


def test_numeracao_sequencial_por_cnpj_sem_buraco(limpo):
    _, lotes, cad = limpo
    por_cnpj = defaultdict(list)
    for _, _, x in _notas(lotes):
        por_cnpj[_t(x, ".//n:emit/n:CNPJ")].append((_t(x, ".//n:ide/n:dhEmi"), int(_t(x, ".//n:ide/n:nNF"))))
    iniciais = {e.part.doc: e.nnf_inicial for e in cad.estabelecimentos}
    for cnpj, emissoes in por_cnpj.items():
        numeros = [n for _, n in sorted(emissoes)]                 # em ordem de horário de emissão
        assert numeros == list(range(iniciais[cnpj], iniciais[cnpj] + len(numeros))), cnpj


# ------------------------------------------------------------------ operações
def test_as_seis_operacoes_aparecem_com_o_cfop_esperado(limpo):
    _, lotes, cad = limpo
    cfops = Counter()
    for _, _, x in _notas(lotes):
        cfops[_t(x, ".//n:det/n:prod/n:CFOP")] += 1
    # transferência é sempre interestadual (os 3 estabelecimentos da importadora ficam em UFs diferentes)
    # e a remessa é sempre interna (o operador logístico fica na UF do estabelecimento)
    esperados = {"5102", "6102", "6108", "6152", "5905", "1202", "2202"}
    assert set(cfops) == esperados, f"CFOPs encontrados: {dict(cfops)}"


def test_transferencia_fica_dentro_da_mesma_empresa_e_sem_tributo(limpo):
    _, lotes, _ = limpo
    achou = 0
    for _, _, x in _notas(lotes):
        if _t(x, ".//n:det/n:prod/n:CFOP") not in ("5152", "6152"):
            continue
        achou += 1
        emit, dest = _t(x, ".//n:emit/n:CNPJ"), _t(x, ".//n:dest/n:CNPJ")
        assert emit[:8] == dest[:8] and emit != dest
        assert _t(x, ".//n:IBSCBS/n:CST") == "410" and x.find(".//n:IBSCBS/n:gIBSCBS", N) is None
        assert D(_t(x, ".//n:IBSCBSTot/n:gCBS/n:vCBS")) == 0 and D(_t(x, ".//n:ICMSTot/n:vICMS")) == 0
    assert achou > 0


def test_interestadual_de_importado_sai_a_4_por_cento(limpo):
    _, lotes, _ = limpo
    for _, _, x in _notas(lotes):
        if _t(x, ".//n:ide/n:idDest") == "2" and x.find(".//n:ICMS00", N) is not None:
            assert {D(e.text) for e in x.findall(".//n:ICMS00/n:pICMS", N)} == {D("4")}


def test_totais_da_nota_fecham_com_os_itens(limpo):
    _, lotes, _ = limpo
    for _, nome, x in _notas(lotes):
        soma = lambda tag: sum(D(e.text) for e in x.findall(f".//n:det//n:{tag}", N))   # noqa: E731
        assert D(_t(x, ".//n:ICMSTot/n:vProd")) == soma("vProd"), nome
        assert D(_t(x, ".//n:ICMSTot/n:vNF")) == soma("vProd") + soma("vIPI"), nome
        assert D(_t(x, ".//n:IBSCBSTot/n:gCBS/n:vCBS")) == soma("vCBS"), nome
        assert D(_t(x, ".//n:IBSCBSTot/n:vBCIBSCBS")) == sum(D(e.text) for e in x.findall(".//n:gIBSCBS/n:vBC", N))


def test_devolucao_referencia_venda_a_pessoa_fisica_que_existe(limpo):
    _, lotes, _ = limpo
    notas = _notas(lotes)
    por_chave = {x.find(".//n:infNFe", N).get("Id")[3:]: x for _, _, x in notas}
    canceladas = {nome[:44] for d in lotes.values() for nome in d["arquivos"] if "procEventoNFe" in nome}
    devolucoes = [x for _, _, x in notas if _t(x, ".//n:ide/n:finNFe") == "4"]
    assert len(devolucoes) >= 5
    for x in devolucoes:
        origem = por_chave[_t(x, ".//n:NFref/n:refNFe")]             # KeyError = referência inexistente
        assert _t(x, ".//n:ide/n:tpNF") == "0"
        assert _t(origem, ".//n:dest/n:CPF") == _t(x, ".//n:dest/n:CPF")
        assert _t(origem, ".//n:emit/n:CNPJ") == _t(x, ".//n:emit/n:CNPJ")
        assert _t(origem, ".//n:ide/n:dhEmi") < _t(x, ".//n:ide/n:dhEmi")
        assert _t(origem, ".//n:ICMSTot/n:vProd") == _t(x, ".//n:ICMSTot/n:vProd")
        assert _t(x, ".//n:NFref/n:refNFe") not in canceladas
    referencias = [_t(x, ".//n:NFref/n:refNFe") for x in devolucoes]
    assert len(referencias) == len(set(referencias))                 # uma venda é devolvida no máximo uma vez


# ------------------------------------------------------------------ eventos
def test_cancelamento_aponta_para_venda_do_grupo_com_o_protocolo_dela(limpo):
    _, lotes, _ = limpo
    por_chave = {x.find(".//n:infNFe", N).get("Id")[3:]: x for _, _, x in _notas(lotes)}
    eventos = [(nome, ET.fromstring(t)) for d in lotes.values() for nome, t in d["arquivos"].items()
               if "procEventoNFe" in nome]
    assert len(eventos) >= 5
    for nome, e in eventos:
        chave = _t(e, ".//n:evento/n:infEvento/n:chNFe")
        nota = por_chave[chave]
        assert _t(e, ".//n:evento/n:infEvento/n:tpEvento") == "110111"
        assert e.find(".//n:evento/n:infEvento", N).get("Id") == f"ID110111{chave}01"
        assert _t(e, ".//n:detEvento/n:nProt") == _t(nota, ".//n:protNFe/n:infProt/n:nProt")
        assert _t(e, ".//n:retEvento/n:infEvento/n:cStat") == "135"
        assert _t(nota, ".//n:ide/n:finNFe") == "1" and _t(nota, ".//n:det/n:prod/n:CFOP")[1:] in ("102", "108")
        emitida = datetime.fromisoformat(_t(nota, ".//n:ide/n:dhEmi"))
        cancelada = datetime.fromisoformat(_t(e, ".//n:evento/n:infEvento/n:dhEvento"))
        assert 0 < (cancelada - emitida).total_seconds() <= 24 * 3600      # sem anomalia: dentro do prazo


def test_evento_de_outro_dia_chega_no_lote_do_dia_do_evento(limpo):
    _, lotes, _ = limpo
    em_lote_posterior = 0
    for lote, d in lotes.items():
        for nome, texto in d["arquivos"].items():
            if "procEventoNFe" not in nome:
                continue
            dia_evento = _t(ET.fromstring(texto), ".//n:evento/n:infEvento/n:dhEvento")[:10]
            assert lote == f"lote_{dia_evento}"
            em_lote_posterior += f"{nome[:44]}-procNFe.xml" not in d["arquivos"]
    assert em_lote_posterior >= 1


# ------------------------------------------------------------------ controle do lote (linha 2 e 3)
def test_controle_declara_o_que_foi_entregue(limpo):
    _, lotes, _ = limpo
    for nome_lote, d in lotes.items():
        c = d["controle"]
        assert c["lote"] == nome_lote and c["arquivos"] == len(d["arquivos"]) == len(c["itens"])
        assert c["nfe"] + c["eventos"] == c["arquivos"]
        assert {i["arquivo"] for i in c["itens"]} == set(d["arquivos"])
        for item in c["itens"]:
            assert hashlib.sha256(d["arquivos"][item["arquivo"]].encode()).hexdigest() == item["sha256"]
        soma = sum(D(_t(ET.fromstring(t), ".//n:ICMSTot/n:vNF")) for n, t in d["arquivos"].items() if "procNFe" in n)
        assert D(c["soma_vnf"]) == soma == sum(D(i["vnf"]) for i in c["itens"] if i["tipo"] == "nfe")
        assert d["gabarito"] == []                                     # massa limpa não tem anomalia


def test_lote_incompleto_declara_mais_do_que_entrega(tmp_path):
    gen_nfe.executar(tmp_path, dias=2, seed=7, incompleto=12)
    lotes = _ler(tmp_path)
    primeiro, ultimo = lotes["lote_2026-01-02"], lotes["lote_2026-01-03"]
    assert primeiro["controle"]["arquivos"] == len(primeiro["arquivos"])
    assert ultimo["controle"]["arquivos"] - len(ultimo["arquivos"]) == 12
    faltando = {i["arquivo"] for i in ultimo["controle"]["itens"]} - set(ultimo["arquivos"])
    assert {g["arquivo"] for g in ultimo["gabarito"] if g["anomalia"] == "arquivo_nao_entregue"} == faltando


def test_lote_so_aparece_na_inbox_depois_de_completo(limpo):
    raiz, lotes, _ = limpo
    assert not list((raiz / "inbox").glob("_parcial_*")) and not list(raiz.rglob("*.tmp"))
    assert all((raiz / "inbox" / l / "controle_lote.json").exists() for l in lotes)


# ------------------------------------------------------------------ estado e reprodutibilidade
def test_gerar_em_duas_execucoes_da_o_mesmo_que_em_uma(tmp_path):
    gen_nfe.executar(tmp_path / "a", dias=6, seed=11, taxas=SUJO)
    gen_nfe.executar(tmp_path / "b", dias=2, seed=11, taxas=SUJO)
    gen_nfe.executar(tmp_path / "b", dias=4, seed=999, taxas=SUJO)     # a seed do estado prevalece
    a, b = _ler(tmp_path / "a"), _ler(tmp_path / "b")
    assert list(a) == list(b) == [f"lote_2026-01-0{d}" for d in range(2, 8)]
    assert all(a[l]["arquivos"] == b[l]["arquivos"] and a[l]["controle"] == b[l]["controle"] for l in a)


def test_nao_gera_por_cima_de_um_lote_existente(tmp_path):
    gen_nfe.executar(tmp_path, dias=1, seed=7)
    (tmp_path / "gerador_estado.json").unlink()                        # estado perdido: recomeçaria do dia 1
    with pytest.raises(SystemExit, match="entrega única"):
        gen_nfe.executar(tmp_path, dias=1, seed=7)


def test_ate_gera_do_proximo_dia_ate_a_data(tmp_path):
    resumos = gen_nfe.executar(tmp_path, ate=date(2026, 1, 5), seed=7)
    assert [r["lote"] for r in resumos] == [f"lote_2026-01-0{d}" for d in range(2, 6)]
    with pytest.raises(SystemExit, match="nada a gerar"):
        gen_nfe.executar(tmp_path, ate=date(2026, 1, 5), seed=7)


# ------------------------------------------------------------------ anomalias e gabarito
ESPERADAS = {"tot_cbs_divergente", "vbc_ibscbs_divergente", "cclasstrib_incoerente", "numeracao_com_salto",
             "devolucao_ref_inexistente", "cancelamento_fora_do_prazo", "evento_sem_nota", "nota_atrasada",
             "xml_corrompido", "reenvio_identico", "mesma_chave_conteudo_diferente", "emitente_fora_do_grupo"}


def test_toda_anomalia_do_desenho_aparece_no_gabarito(sujo):
    _, lotes, _ = sujo
    vistas = Counter(g["anomalia"] for d in lotes.values() for g in d["gabarito"])
    assert set(vistas) == ESPERADAS, set(vistas) ^ ESPERADAS
    for d in lotes.values():                                           # o gabarito só fala de arquivo entregue
        assert {g["arquivo"] for g in d["gabarito"]} <= set(d["arquivos"])


def test_cada_anomalia_e_observavel_no_arquivo(sujo):
    """O gabarito não é só rótulo: o defeito está mesmo no XML entregue."""
    _, lotes, cad = sujo
    todas = {(l, n): t for l, d in lotes.items() for n, t in d["arquivos"].items()}
    chaves_de_nota = {n[:44] for (_, n) in todas if "procNFe" in n}
    conferidas = Counter()
    for l, d in lotes.items():
        hashes = {i["arquivo"]: i["sha256"] for i in d["controle"]["itens"]}
        for g in d["gabarito"]:
            texto, tipo = d["arquivos"][g["arquivo"]], g["anomalia"]
            if tipo == "xml_corrompido":
                with pytest.raises(ET.ParseError):
                    ET.fromstring(texto)
                assert hashlib.sha256(texto.encode()).hexdigest() != hashes[g["arquivo"]]   # truncou no transporte
                conferidas[tipo] += 1
                continue
            x = ET.fromstring(texto)
            if tipo == "tot_cbs_divergente":
                assert D(_t(x, ".//n:IBSCBSTot/n:gCBS/n:vCBS")) - sum(
                    D(e.text) for e in x.findall(".//n:det//n:vCBS", N)) == D("0.05")
            elif tipo == "vbc_ibscbs_divergente":
                assert D(_t(x, ".//n:IBSCBSTot/n:vBCIBSCBS")) - sum(
                    D(e.text) for e in x.findall(".//n:gIBSCBS/n:vBC", N)) == D("1.00")
            elif tipo == "cclasstrib_incoerente":
                assert (_t(x, ".//n:det/n:imposto/n:IBSCBS/n:CST"),
                        _t(x, ".//n:det/n:imposto/n:IBSCBS/n:cClassTrib")) == ("000", "200038")
            elif tipo == "emitente_fora_do_grupo":
                assert _t(x, ".//n:emit/n:CNPJ") not in cad.cnpjs_grupo
            elif tipo == "devolucao_ref_inexistente":
                assert _t(x, ".//n:NFref/n:refNFe") not in chaves_de_nota
            elif tipo == "cancelamento_fora_do_prazo":
                nota = next(ET.fromstring(t) for (_, n), t in todas.items() if n == f"{g['chave']}-procNFe.xml")
                atraso = (datetime.fromisoformat(_t(x, ".//n:evento/n:infEvento/n:dhEvento"))
                          - datetime.fromisoformat(_t(nota, ".//n:ide/n:dhEmi")))
                assert atraso.total_seconds() > 24 * 3600
            elif tipo == "evento_sem_nota":
                antes = [ll for (ll, n) in todas if n == f"{g['chave']}-procNFe.xml" and ll <= l]
                assert antes == []                                    # até este lote a nota ainda não chegou
            elif tipo == "nota_atrasada":
                assert _t(x, ".//n:ide/n:dhEmi")[:10] < l.removeprefix("lote_")
            elif tipo == "reenvio_identico":
                assert texto == d["arquivos"][f"{g['chave']}-procNFe.xml"]
            elif tipo == "mesma_chave_conteudo_diferente":
                original = d["arquivos"][f"{g['chave']}-procNFe.xml"]
                assert texto != original and x.find(".//n:infNFe", N).get("Id") == f"NFe{g['chave']}"
            elif tipo == "numeracao_com_salto":
                assert int(_t(x, ".//n:ide/n:nNF")) > 0
            conferidas[tipo] += 1
    assert set(conferidas) == ESPERADAS


def test_salto_de_numeracao_deixa_exatamente_um_numero_sem_nota(sujo):
    _, lotes, cad = sujo
    saltos = {g["chave"] for d in lotes.values() for g in d["gabarito"] if g["anomalia"] == "numeracao_com_salto"}
    por_cnpj = defaultdict(set)
    for _, nome, x in _notas(lotes, incluir_anomalas=True):
        cnpj = _t(x, ".//n:emit/n:CNPJ")
        if cnpj in cad.cnpjs_grupo:
            por_cnpj[cnpj].add(int(_t(x, ".//n:ide/n:nNF")))
    esperados = Counter(c[6:20] for c in saltos)
    ilegiveis = Counter(g["chave"][6:20] for d in lotes.values() for g in d["gabarito"]
                        if g["anomalia"] == "xml_corrompido")
    for e in cad.estabelecimentos:
        numeros = por_cnpj[e.part.doc]
        buracos = max(numeros) - e.nnf_inicial + 1 - len(numeros)
        # buraco = salto injetado, nota ilegível, ou nota atrasada/ilegível ainda não vista no fim da massa
        assert esperados[e.part.doc] <= buracos <= esperados[e.part.doc] + ilegiveis[e.part.doc] + 3, e.part.doc


def test_massa_do_ano_fica_perto_de_25_mil_notas():
    """Decisão de escopo 4: cerca de 25 mil notas no ano, em lotes diários de cerca de 100 em dia útil."""
    uteis = sum(1 for d in range(364) if date.fromordinal(date(2026, 1, 2).toordinal() + d).weekday() < 5)
    estimativa = uteis * 90 + (364 - uteis) * 90 * 0.22
    assert 23_000 <= estimativa <= 27_000
