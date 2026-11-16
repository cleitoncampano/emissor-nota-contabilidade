"""Leitura do certificado A1 (.pfx) e guarda da senha no cofre do sistema."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import keyring
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

SERVICO_COFRE = "emissor-nfse"
OID_CNPJ_ICP = "2.16.76.1.3.3"


class CertificadoInvalido(Exception):
    pass


@dataclass
class InfoCertificado:
    cnpj: str
    razao_social: str
    validade: datetime

    @property
    def dias_restantes(self) -> int:
        return (self.validade - datetime.now(timezone.utc)).days


def _cnpj_do_certificado(cert: x509.Certificate) -> tuple[Optional[str], str]:
    cn = ""
    try:
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    except IndexError:
        pass
    razao = cn
    cnpj = None
    if ":" in cn:
        nome, _, numero = cn.rpartition(":")
        if re.fullmatch(r"\d{14}", numero.strip()):
            cnpj, razao = numero.strip(), nome.strip()
    if cnpj is None:
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            for nome in san:
                if isinstance(nome, x509.OtherName) and nome.type_id.dotted_string == OID_CNPJ_ICP:
                    achado = re.search(rb"\d{14}", nome.value)
                    if achado:
                        cnpj = achado.group().decode()
                        break
        except x509.ExtensionNotFound:
            pass
    return cnpj, razao


def ler_certificado(conteudo: bytes, senha: str) -> InfoCertificado:
    try:
        _chave, cert, _cadeia = pkcs12.load_key_and_certificates(conteudo, senha.encode())
    except ValueError as e:
        raise CertificadoInvalido("Senha incorreta ou arquivo de certificado inválido.") from e
    if cert is None:
        raise CertificadoInvalido("O arquivo não contém um certificado.")
    cnpj, razao = _cnpj_do_certificado(cert)
    if not cnpj:
        raise CertificadoInvalido(
            "Não encontrei CNPJ neste certificado. O conector aceita certificado A1 de pessoa jurídica (e-CNPJ)."
        )
    validade = cert.not_valid_after_utc
    return InfoCertificado(cnpj=cnpj, razao_social=razao or cnpj, validade=validade)


def ler_certificado_arquivo(caminho: Path, senha: str) -> InfoCertificado:
    return ler_certificado(Path(caminho).read_bytes(), senha)


def guardar_senha(cnpj: str, senha: str) -> None:
    keyring.set_password(SERVICO_COFRE, cnpj, senha)


def ler_senha(cnpj: str) -> Optional[str]:
    return keyring.get_password(SERVICO_COFRE, cnpj)


def apagar_senha(cnpj: str) -> None:
    try:
        keyring.delete_password(SERVICO_COFRE, cnpj)
    except keyring.errors.PasswordDeleteError:
        pass


def _escapar_applescript(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace('"', '\\"')


def pedir_senha_em_janela(mensagem: str) -> Optional[str]:
    """Abre uma janela do sistema pedindo a senha, para ela nunca passar pela conversa."""
    sistema = platform.system()
    if sistema == "Darwin" and shutil.which("osascript"):
        script = (
            f'text returned of (display dialog "{_escapar_applescript(mensagem)}" '
            'default answer "" with hidden answer with title "Emissor NFS-e" '
            'buttons {"Cancelar", "OK"} default button "OK" cancel button "Cancelar" giving up after 300)'
        )
        resultado = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if resultado.returncode != 0:
            return None
        senha = resultado.stdout.rstrip("\n")
        return senha or None
    try:
        import tkinter
        from tkinter import simpledialog

        raiz = tkinter.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        senha = simpledialog.askstring("Emissor NFS-e", mensagem, show="*", parent=raiz)
        raiz.destroy()
        return senha or None
    except Exception:
        return None
