"""Vários clientes: senhas independentes no cofre e equipe sem acesso a elas."""

from __future__ import annotations

import json

import keyring
import pytest

from emissor_nfse.acesso import Pessoa, SemPermissao, pessoa_atual
from emissor_nfse.certificado import SERVICO_COFRE, ler_senha

from .conftest import CNPJ_TESTE, gerar_pfx


def test_senhas_por_cliente_nao_vazam_para_cadastro_ou_historico(ambiente):
    op = ambiente["op"]
    outro_cnpj = "22333444000181"
    outro = ambiente["tmp"] / "outro.pfx"
    outro.write_bytes(gerar_pfx(outro_cnpj, "OUTRA EMPRESA TESTE", "senha-segunda-empresa"))
    op.cadastrar_certificado(str(ambiente["pfx"]), senha="1234")
    op.cadastrar_certificado(str(outro), senha="senha-segunda-empresa")
    assert ler_senha(CNPJ_TESTE) == "1234"
    assert ler_senha(outro_cnpj) == "senha-segunda-empresa"
    cofre = keyring.get_keyring()
    assert set(cofre.dados) == {(SERVICO_COFRE, CNPJ_TESTE), (SERVICO_COFRE, outro_cnpj)}
    for arquivo in (ambiente["tmp"] / "dados").rglob("*"):
        if arquivo.suffix in {".json", ".jsonl", ".log"}:
            texto = arquivo.read_text()
            assert "senha-segunda-empresa" not in texto
            if arquivo.suffix == ".json":
                assert '"senha"' not in json.dumps(json.loads(texto))


def test_equipe_nao_pode_importar_certificado_nem_trocar_senha(ambiente):
    marca = pessoa_atual.set(Pessoa("Pessoa da equipe", admin=False))
    try:
        with pytest.raises(SemPermissao):
            ambiente["op"].cadastrar_certificado(str(ambiente["pfx"]), senha="1234")
    finally:
        pessoa_atual.reset(marca)
    assert ler_senha(CNPJ_TESTE) is None
