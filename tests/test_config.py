import pytest

from pipelines.commons import config


def test_carrega_com_padroes(env_projeto):
    cfg = config.carregar()
    assert cfg.bucket_raw == "nfe-raw"
    assert cfg.retencao_dias == 2190
    assert cfg.dir_inbox.endswith("/inbox")


def test_variavel_obrigatoria_ausente(env_projeto, monkeypatch):
    monkeypatch.delenv("DB_PASSWORD")
    with pytest.raises(RuntimeError, match="DB_PASSWORD"):
        config.carregar()


@pytest.mark.parametrize("nome,valor", [("CUSTODIA_LOCK_MODE", "LIVRE"), ("CUSTODIA_RETENCAO_DIAS", "0")])
def test_configuracao_de_custodia_invalida(env_projeto, monkeypatch, nome, valor):
    monkeypatch.setenv(nome, valor)
    with pytest.raises(RuntimeError, match=nome):
        config.carregar()
