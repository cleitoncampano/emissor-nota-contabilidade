"""Registra o conector no Claude Desktop deste computador (com cópia de segurança da configuração).

Uso: uv run python -m emissor_nfse.instalar [--modo ponte|local] [--uv caminho-do-uv]
  ponte: computador da equipe, ligado ao conector central (padrão)
  local: conector completo rodando neste computador, sem servidor central
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
from datetime import datetime
from pathlib import Path

from .config import RAIZ_PROJETO


def arquivo_config_desktop() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if platform.system() == "Windows":
        return Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def registrar(modo: str, uv: str) -> Path:
    arquivo = arquivo_config_desktop()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    config = {}
    if arquivo.exists():
        texto = arquivo.read_text(encoding="utf-8").strip()
        config = json.loads(texto) if texto else {}
        copia = arquivo.with_name(f"claude_desktop_config.backup-{datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copyfile(arquivo, copia)
        print(f"Cópia de segurança: {copia}")
    comando = "emissor-nfse-ponte" if modo == "ponte" else "emissor-nfse-mcp"
    config.setdefault("mcpServers", {})["emissor-nfse"] = {
        "command": str(Path(uv).resolve()),
        "args": ["run", "--directory", str(RAIZ_PROJETO), comando],
    }
    arquivo.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return arquivo


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--modo", choices=["ponte", "local"], default="ponte")
    p.add_argument("--uv", default=shutil.which("uv"))
    a = p.parse_args()
    if not a.uv:
        print("Não achei o uv. Instale em https://docs.astral.sh/uv/ e rode de novo.")
        raise SystemExit(1)
    arquivo = registrar(a.modo, a.uv)
    print(f"Conector registrado no Claude Desktop ({arquivo}).")


if __name__ == "__main__":
    main()
