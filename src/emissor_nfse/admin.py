"""Comandos de administração do conector central (rodar no computador central do escritório).

  uv run emissor-nfse-admin servidor                 liga o conector central (normalmente quem liga é o serviço)
  uv run emissor-nfse-admin convidar "Maria"         cria o acesso da Maria e gera o arquivo de convite dela
  uv run emissor-nfse-admin convidar "Ana" --admin   idem, com permissão de administradora
  uv run emissor-nfse-admin pessoas                  lista quem tem acesso
  uv run emissor-nfse-admin remover "Maria"          corta o acesso da Maria na hora
  uv run emissor-nfse-admin certificado <arquivo|pasta>   cadastra certificado A1 de cliente(s)
  uv run emissor-nfse-admin escritorio <arquivo>     cadastra o certificado A1 do escritório (procuração)
  uv run emissor-nfse-admin instalar-servico         deixa o conector central ligando sozinho com o Mac
  uv run emissor-nfse-admin desinstalar-servico
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import plistlib
import shutil
import subprocess
from pathlib import Path

from . import acesso
from .central import PORTA_PADRAO, garantir_certificado_tls, nome_na_rede, pasta_servidor, rodar
from .certificado import CertificadoInvalido
from .config import RAIZ_PROJETO, normalizar, pasta_dados
from .dps import ErroNota

ROTULO_SERVICO = "local.emissor-nfse"


def _convidar(nome: str, admin: bool, endereco: str | None, porta: int) -> None:
    cert_pem, _ = garantir_certificado_tls()
    chave = acesso.criar_pessoa(nome, admin=admin)
    host = endereco or nome_na_rede()
    convite = {
        "nome": nome,
        "endereco": f"https://{host}:{porta}/mcp",
        "chave": chave,
        "certificado_tls": cert_pem.read_text(),
    }
    pasta = pasta_servidor() / "convites"
    pasta.mkdir(exist_ok=True)
    arquivo = pasta / f"convite-{normalizar(nome).replace(' ', '-')}.json"
    arquivo.write_text(json.dumps(convite, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(arquivo, 0o600)
    print(f"Acesso criado para {nome}{' (administrador)' if admin else ''}.")
    print(f"Convite: {arquivo}")
    print("Entregue esse arquivo só para essa pessoa (pendrive ou rede interna; evite e-mail e WhatsApp).")
    print("Se a pessoa já tinha acesso, o convite antigo deixou de funcionar.")


def _certificado(caminho: str) -> None:
    from .operacoes import cadastrar_certificado

    alvo = Path(caminho).expanduser()
    arquivos = sorted(p for p in alvo.iterdir() if p.suffix.lower() in (".pfx", ".p12")) if alvo.is_dir() else [alvo]
    if not arquivos:
        print("Nenhum .pfx ou .p12 encontrado.")
    for arq in arquivos:
        senha = getpass.getpass(f"Senha do certificado {arq.name}: ")
        try:
            print(cadastrar_certificado(str(arq), senha=senha))
        except (ErroNota, CertificadoInvalido) as e:
            print(f"{arq.name}: não cadastrado ({e})")
        print()


def _escritorio(caminho: str) -> None:
    from .operacoes import cadastrar_certificado_escritorio

    senha = getpass.getpass("Senha do certificado do escritório: ")
    try:
        print(cadastrar_certificado_escritorio(caminho, senha=senha))
    except (ErroNota, CertificadoInvalido) as e:
        print(f"Não cadastrado: {e}")


def _arquivo_servico() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{ROTULO_SERVICO}.plist"


def _instalar_servico(porta: int) -> None:
    if platform.system() == "Windows":
        from .servico_windows import instalar

        instalar(porta)
        return
    if platform.system() != "Darwin":
        print("A inicialização automática está disponível para Windows e Mac.")
        return
    protegidas = [Path.home() / p for p in ("Documents", "Desktop", "Downloads")]
    if any(RAIZ_PROJETO.is_relative_to(p) for p in protegidas) or any(pasta_dados().is_relative_to(p) for p in protegidas):
        print(
            "O macOS não deixa serviços automáticos lerem Documentos, Mesa ou Downloads.\n"
            "Use o instalador do servidor (instalar-servidor-mac.command), que copia o conector para ~/EmissorNFSe."
        )
        return
    uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    log = pasta_servidor() / "servidor.log"
    plist = {
        "Label": ROTULO_SERVICO,
        "ProgramArguments": [uv, "run", "--directory", str(RAIZ_PROJETO), "emissor-nfse-admin", "servidor", "--porta", str(porta)],
        "EnvironmentVariables": {"EMISSOR_NFSE_DADOS": str(pasta_dados())},
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
    }
    arquivo = _arquivo_servico()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(arquivo)], capture_output=True)
    with open(arquivo, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "load", "-w", str(arquivo)], check=True)
    print(f"Serviço instalado. O conector central liga sozinho com o Mac (porta {porta}). Registro em {log}.")
    print("O Mac precisa ficar ligado e com o usuário logado (o cofre de senhas abre com o login).")


def _desinstalar_servico() -> None:
    if platform.system() == "Windows":
        from .servico_windows import desinstalar

        desinstalar()
        return
    arquivo = _arquivo_servico()
    if arquivo.exists():
        subprocess.run(["launchctl", "unload", str(arquivo)], capture_output=True)
        arquivo.unlink()
        print("Serviço removido.")
    else:
        print("Serviço não estava instalado.")


def main() -> None:
    p = argparse.ArgumentParser(prog="emissor-nfse-admin", description="Administração do conector central de NFS-e")
    sub = p.add_subparsers(dest="comando", required=True)
    s = sub.add_parser("servidor")
    s.add_argument("--porta", type=int, default=PORTA_PADRAO)
    c = sub.add_parser("convidar")
    c.add_argument("nome")
    c.add_argument("--admin", action="store_true")
    c.add_argument("--endereco", help="nome ou IP do computador central na rede (padrão: nome.local)")
    c.add_argument("--porta", type=int, default=PORTA_PADRAO)
    sub.add_parser("pessoas")
    r = sub.add_parser("remover")
    r.add_argument("nome")
    ce = sub.add_parser("certificado")
    ce.add_argument("caminho")
    es = sub.add_parser("escritorio")
    es.add_argument("caminho")
    i = sub.add_parser("instalar-servico")
    i.add_argument("--porta", type=int, default=PORTA_PADRAO)
    sub.add_parser("desinstalar-servico")
    a = p.parse_args()

    if a.comando == "servidor":
        rodar(a.porta)
    elif a.comando == "convidar":
        _convidar(a.nome, a.admin, a.endereco, a.porta)
    elif a.comando == "pessoas":
        pessoas = acesso.listar_pessoas()
        if not pessoas:
            print("Ninguém com acesso ainda. Use: convidar \"Nome\" --admin")
        for pe in pessoas:
            print(f"- {pe['nome']}{' (admin)' if pe.get('admin') else ''}, desde {pe['criado_em'][:10]}")
    elif a.comando == "remover":
        print("Acesso removido." if acesso.remover_pessoa(a.nome) else "Não achei essa pessoa.")
    elif a.comando == "certificado":
        _certificado(a.caminho)
    elif a.comando == "escritorio":
        _escritorio(a.caminho)
    elif a.comando == "instalar-servico":
        _instalar_servico(a.porta)
    elif a.comando == "desinstalar-servico":
        _desinstalar_servico()


if __name__ == "__main__":
    main()
