"""Referências do cenário Grupo Xis (docs/03_cenario_dados.md).

Códigos públicos (IBGE, NCM, CFOP, CST) são reais. Todo o resto é fictício.

REGRAS FISCAIS AQUI SÃO HIPÓTESES DE TRABALHO, A VALIDAR COM UM TRIBUTARISTA (decisão D008):
alíquotas internas de ICMS, 4% no interestadual de importado, IPI por família, CST/cClassTrib das
operações sem incidência. O objetivo do gerador é produzir XML com estrutura fiel e números coerentes
entre item e total, não reproduzir a apuração real de uma importadora.
"""
from __future__ import annotations

from decimal import Decimal as D

NS = "http://www.portalfiscal.inf.br/nfe"
DS = "http://www.w3.org/2000/09/xmldsig#"

# alíquotas de teste de 2026 (ano-teste da reforma)
P_IBSUF, P_IBSMUN, P_CBS = D("0.10"), D("0.00"), D("0.90")
# mercadoria importada: 4% nas operações interestaduais (Resolução do Senado 13/2012)
P_ICMS_INTER_IMPORTADO = D("4")

# UF -> (cUF IBGE, alíquota interna de ICMS, peso na carteira de clientes, [(cMun IBGE, nome)])
UFS = {
    "GO": ("52", D("19"),   18, [("5208707", "Goiania"), ("5201405", "Aparecida de Goiania"), ("5201108", "Anapolis"),
                                 ("5218805", "Rio Verde")]),
    "DF": ("53", D("20"),    6, [("5300108", "Brasilia")]),
    "MT": ("51", D("17"),    4, [("5103403", "Cuiaba"), ("5107602", "Rondonopolis")]),
    "MS": ("50", D("17"),    3, [("5002704", "Campo Grande")]),
    "TO": ("17", D("20"),    2, [("1721000", "Palmas")]),
    "SP": ("35", D("18"),   24, [("3550308", "Sao Paulo"), ("3509502", "Campinas"), ("3543402", "Ribeirao Preto")]),
    "MG": ("31", D("18"),    9, [("3106200", "Belo Horizonte"), ("3170206", "Uberlandia")]),
    "RJ": ("33", D("22"),    7, [("3304557", "Rio de Janeiro"), ("3303302", "Niteroi")]),
    "ES": ("32", D("17"),    2, [("3205309", "Vitoria"), ("3205200", "Vila Velha")]),
    "PR": ("41", D("19.5"),  8, [("4106902", "Curitiba"), ("4113700", "Londrina")]),
    "SC": ("42", D("17"),    5, [("4205407", "Florianopolis"), ("4209102", "Joinville")]),
    "RS": ("43", D("17"),    5, [("4314902", "Porto Alegre"), ("4305108", "Caxias do Sul")]),
    "BA": ("29", D("20.5"),  3, [("2927408", "Salvador")]),
    "PE": ("26", D("20.5"),  2, [("2611606", "Recife")]),
    "CE": ("23", D("20"),    2, [("2304400", "Fortaleza")]),
}

# ------------------------------------------------------------------ o grupo: 2 empresas, 5 estabelecimentos
# (empresa, razão social, ordem do estabelecimento, UF, índice do município em UFS, peso no volume, 1º nNF)
EMPRESAS = {"importadora": "XIS IMPORTADORA LTDA", "comercio": "XIS COMERCIO LTDA"}
ESTABELECIMENTOS = [
    ("importadora", 1, "GO", 0, 28, 48210),
    ("importadora", 2, "SP", 1, 27, 91530),
    ("importadora", 3, "PR", 0, 15, 20745),
    ("comercio",    1, "GO", 1, 16, 130480),
    ("comercio",    2, "SP", 0, 14, 77615),
]

# ------------------------------------------------------------------ catálogo: 3 famílias de importados
# (código, descrição, NCM, unidade, faixa de preço, % IPI ilustrativo)
CATALOGO = [
    ("UC001", "JOGO DE PANELAS ANTIADERENTE 5 PCS",  "76151000", "CJ", (140, 420),  D("6.5")),
    ("UC002", "FRIGIDEIRA ACO INOX 24 CM",           "73239300", "UN", (60, 190),   D("6.5")),
    ("UC003", "JOGO DE FACAS INOX 6 PCS",            "82119100", "CJ", (45, 220),   D("6.5")),
    ("UC004", "GARRAFA TERMICA INOX 1 L",            "96170010", "UN", (35, 140),   D("6.5")),
    ("UC005", "JOGO DE TALHERES INOX 24 PCS",        "82152000", "CJ", (50, 260),   D("6.5")),
    ("FE001", "FURADEIRA DE IMPACTO 650 W",          "84672100", "UN", (160, 480),  D("5.2")),
    ("FE002", "PARAFUSADEIRA A BATERIA 12 V",        "84672100", "UN", (140, 520),  D("5.2")),
    ("FE003", "SERRA CIRCULAR 1400 W",               "84672200", "UN", (280, 760),  D("5.2")),
    ("FE004", "ESMERILHADEIRA ANGULAR 850 W",        "84672992", "UN", (150, 430),  D("5.2")),
    ("FE005", "LIXADEIRA ORBITAL 300 W",             "84672991", "UN", (120, 380),  D("5.2")),
    ("AE001", "FONE DE OUVIDO BLUETOOTH",            "85183000", "UN", (40, 320),   D("9.75")),
    ("AE002", "CABO USB-C 1 M",                      "85444200", "UN", (8, 45),     D("9.75")),
    ("AE003", "CARREGADOR USB 20 W",                 "85044010", "UN", (25, 110),   D("9.75")),
    ("AE004", "CAIXA DE SOM BLUETOOTH",              "85182200", "UN", (70, 480),   D("9.75")),
    ("AE005", "POWER BANK 10000 MAH",                "85076000", "UN", (60, 240),   D("9.75")),
]

# ------------------------------------------------------------------ operações
# natOp, tpNF, finNFe, CFOP (interno, interestadual), tributada?, fatura?
OPERACOES = {
    "venda_pj":       ("VENDA DE MERCADORIA",                      "1", "1", ("5102", "6102"), True,  True),
    "venda_pf":       ("VENDA DE MERCADORIA A CONSUMIDOR",         "1", "1", ("5102", "6108"), True,  True),
    "venda_grupo":    ("VENDA DE MERCADORIA",                      "1", "1", ("5102", "6102"), True,  True),
    "transferencia":  ("TRANSFERENCIA DE MERCADORIA",              "1", "1", ("5152", "6152"), False, False),
    "remessa_oplog":  ("REMESSA PARA DEPOSITO EM OPERADOR LOGISTICO", "1", "1", ("5905", "6905"), False, False),
    "devolucao_pf":   ("DEVOLUCAO DE VENDA",                       "0", "4", ("1202", "2202"), True,  False),
}
# mix por empresa (pesos). A importadora distribui; o comércio vende ao consumidor.
MIX = {
    "importadora": {"venda_pj": 72, "transferencia": 12, "venda_grupo": 9, "remessa_oplog": 7},
    "comercio":    {"venda_pf": 60, "venda_pj": 25, "devolucao_pf": 12, "remessa_oplog": 3},
}
# operações sem incidência: ICMS CST 41, PIS/COFINS CST 08, IBS/CBS CST 410 (a validar)
CST_IBSCBS_TRIBUTADA = ("000", "000001")
CST_IBSCBS_SEM_INCIDENCIA = ("410", "410999")

NOMES_PJ = ["COMERCIAL", "DISTRIBUIDORA", "ATACADO", "MAGAZINE", "CASA", "FERRAGENS", "UTILIDADES", "ELETRO"]
SUFIXOS_PJ = ["ALFA", "BETA", "GAMA", "DELTA", "SIGMA", "OMEGA", "CERRADO", "PLANALTO", "HORIZONTE", "VALE",
              "ARAGUAIA", "PARANAIBA", "TOCANTINS", "PIRENEUS", "SERRA DOURADA", "LITORAL", "PAMPA", "SERTAO"]
PRENOMES = ["ANA", "JOAO", "MARIA", "PEDRO", "LUCAS", "JULIA", "CARLOS", "FERNANDA", "PAULO", "BEATRIZ",
            "RAFAEL", "CAMILA", "BRUNO", "LARISSA"]
SOBRENOMES = ["SILVA", "SOUZA", "OLIVEIRA", "PEREIRA", "COSTA", "ALMEIDA", "RIBEIRO", "MARTINS", "ROCHA", "BARROS"]
LOGR = ["RUA", "AVENIDA", "RODOVIA", "ALAMEDA", "TRAVESSA"]
BAIRROS = ["CENTRO", "SETOR INDUSTRIAL", "JARDIM AMERICA", "SETOR BUENO", "DISTRITO INDUSTRIAL", "VILA NOVA"]
JUSTIFICATIVAS = ["ERRO NA DIGITACAO DOS DADOS DO DESTINATARIO", "PEDIDO CANCELADO PELO CLIENTE ANTES DA SAIDA",
                  "MERCADORIA INDISPONIVEL EM ESTOQUE NO MOMENTO DA SEPARACAO", "NOTA EMITIDA EM DUPLICIDADE PELO SISTEMA"]
