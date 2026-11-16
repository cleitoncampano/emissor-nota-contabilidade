"""Servidor central do escritório: o conector rodando num computador da rede, com conexão criptografada
e chave de acesso por pessoa. Os computadores da equipe se ligam a ele pela ponte (ponte.py)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .acesso import identificar, modo_servidor
from .config import pasta_dados

PORTA_PADRAO = 8443


def pasta_servidor() -> Path:
    p = pasta_dados() / "servidor"
    p.mkdir(parents=True, exist_ok=True)
    return p


def nome_na_rede() -> str:
    nome = socket.gethostname().split(".")[0]
    return f"{nome}.local"


def _ips_locais() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def garantir_certificado_tls() -> tuple[Path, Path]:
    """Cria (uma vez) o certificado que criptografa a conexão entre a equipe e o servidor central."""
    pasta = pasta_servidor()
    cert_pem, chave_pem = pasta / "tls-cert.pem", pasta / "tls-chave.pem"
    if cert_pem.exists() and chave_pem.exists():
        return cert_pem, chave_pem
    chave = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Emissor NFS-e do escritorio")])
    alternativos = [x509.DNSName("localhost"), x509.DNSName(nome_na_rede()), x509.DNSName(socket.gethostname())]
    alternativos += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in _ips_locais()]
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - dt.timedelta(days=1))
        .not_valid_after(agora + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(alternativos), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(chave, hashes.SHA256())
    )
    chave_pem.write_bytes(
        chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    os.chmod(chave_pem, 0o600)
    cert_pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_pem, chave_pem


class Portaria:
    """Barra na porta quem não tem chave de acesso válida, antes de qualquer coisa do conector."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            cabecalhos = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
            if identificar(cabecalhos.get("authorization")) is None:
                corpo = json.dumps({"erro": "chave de acesso ausente ou inválida"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(corpo)).encode())],
                    }
                )
                await send({"type": "http.response.body", "body": corpo})
                return
        await self.app(scope, receive, send)


def criar_app():
    from mcp.server.transport_security import TransportSecuritySettings

    from .server import servidor

    modo_servidor.set(True)
    app = servidor.streamable_http_app(
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
        # A proteção contra "DNS rebinding" é para servidores sem senha no navegador; aqui cada chamada exige chave.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return Portaria(app)


def rodar(porta: int = PORTA_PADRAO) -> None:
    import uvicorn

    cert_pem, chave_pem = garantir_certificado_tls()
    print(f"Conector central no ar em https://{nome_na_rede()}:{porta}/mcp")
    uvicorn.run(
        criar_app(),
        host="0.0.0.0",
        port=porta,
        ssl_certfile=str(cert_pem),
        ssl_keyfile=str(chave_pem),
        log_level="warning",
    )
