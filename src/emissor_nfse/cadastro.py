"""Cadastro de certificados pelo terminal (alternativa à janela de senha).

Uso: uv run emissor-nfse-cadastro [arquivo.pfx ou pasta]
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

from .certificado import CertificadoInvalido
from .dps import ErroNota
from .operacoes import cadastrar_certificado


def _cadastrar(arquivo: Path) -> None:
    senha = getpass.getpass(f"Senha do certificado {arquivo.name}: ")
    try:
        print(cadastrar_certificado(str(arquivo), senha=senha))
    except (ErroNota, CertificadoInvalido) as e:
        print(f"Não cadastrado: {e}")
    print()


def main() -> None:
    alvo = sys.argv[1] if len(sys.argv) > 1 else input("Arraste aqui o arquivo .pfx (ou uma pasta) e tecle Enter: ")
    caminho = Path(alvo.strip().strip("'\"")).expanduser()
    if caminho.is_dir():
        arquivos = sorted(p for p in caminho.iterdir() if p.suffix.lower() in (".pfx", ".p12"))
        if not arquivos:
            print("Nenhum .pfx ou .p12 nessa pasta.")
        for arq in arquivos:
            _cadastrar(arq)
    elif caminho.exists():
        _cadastrar(caminho)
    else:
        print(f"Não encontrei {caminho}")


if __name__ == "__main__":
    main()
