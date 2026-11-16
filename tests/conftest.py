"""Ambiente de teste sem internet: certificado autoassinado, cofre em memória e governo falso."""

from __future__ import annotations

import datetime as dt
import importlib

import keyring
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from keyring.backend import KeyringBackend

CNPJ_TESTE = "11222333000181"


class CofreMemoria(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.dados = {}

    def set_password(self, servico, usuario, senha):
        self.dados[(servico, usuario)] = senha

    def get_password(self, servico, usuario):
        return self.dados.get((servico, usuario))

    def delete_password(self, servico, usuario):
        self.dados.pop((servico, usuario), None)


def gerar_pfx(cnpj: str, razao: str, senha: str, dias: int = 365) -> bytes:
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{razao}:{cnpj}")])
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(days=1))
        .not_valid_after(agora + dt.timedelta(days=dias))
        .sign(chave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"teste", chave, cert, None, serialization.BestAvailableEncryption(senha.encode())
    )


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("EMISSOR_NFSE_DADOS", str(tmp_path / "dados"))
    keyring.set_keyring(CofreMemoria())
    from emissor_nfse import operacoes

    importlib.reload(operacoes)
    pfx = tmp_path / "empresa.pfx"
    pfx.write_bytes(gerar_pfx(CNPJ_TESTE, "EMPRESA TESTE LTDA", "1234"))
    return {"tmp": tmp_path, "pfx": pfx, "op": operacoes}
