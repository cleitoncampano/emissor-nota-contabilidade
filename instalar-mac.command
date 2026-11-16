#!/bin/bash
# Liga o Claude Desktop DESTE computador ao conector central de NFS-e do escritório.
# Precisa do arquivo de convite pessoal (convite-seunome.json) que quem administra gerou.
set -e
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$HOME/EmissorNFSe"

echo "== Emissor NFS-e: instalação no computador da equipe =="
read -r -p "Arraste aqui o seu arquivo de convite e tecle Enter: " CONVITE
CONVITE="${CONVITE%\"}"; CONVITE="${CONVITE#\"}"; CONVITE="${CONVITE%\'}"; CONVITE="${CONVITE#\'}"; CONVITE="${CONVITE% }"
if [ ! -f "$CONVITE" ]; then echo "Não achei o arquivo $CONVITE"; exit 1; fi

mkdir -p "$HOME/.emissor-nfse"
cp "$CONVITE" "$HOME/.emissor-nfse/convite.json"
chmod 600 "$HOME/.emissor-nfse/convite.json"

UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  echo "Instalando o uv (gerenciador do Python usado pelo conector)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi

if [ "$ORIGEM" != "$DESTINO" ]; then
  mkdir -p "$DESTINO"
  rsync -a --exclude .git --exclude .venv --exclude dados --exclude tests --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache "$ORIGEM/" "$DESTINO/"
fi
cd "$DESTINO"
echo "Preparando o conector..."
"$UV" sync --quiet
"$UV" run python -m emissor_nfse.instalar --modo ponte --uv "$UV"
echo
echo "Pronto. Feche o Claude Desktop por completo (Cmd+Q) e abra de novo."
echo "Para conferir, pergunte ao Claude: \"o conector de notas está conectado?\""
read -r -p "Tecle Enter para fechar esta janela."
