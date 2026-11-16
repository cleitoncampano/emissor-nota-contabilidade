#!/bin/bash
# Instala o CONECTOR CENTRAL de NFS-e no computador do escritório que vai ficar sempre ligado.
# Rode uma vez só, neste computador. Os demais usam instalar-mac.command com o convite de cada um.
set -e
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$HOME/EmissorNFSe"

echo "== Emissor NFS-e: instalação do computador central =="

UV="$(command -v uv || true)"
if [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ]; then UV="$HOME/.local/bin/uv"; fi
if [ -z "$UV" ]; then
  echo "Instalando o uv (gerenciador do Python usado pelo conector)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi

# O macOS não deixa serviços automáticos lerem Documentos/Mesa/Downloads: o conector vive em ~/EmissorNFSe.
if [ "$ORIGEM" != "$DESTINO" ]; then
  mkdir -p "$DESTINO"
  rsync -a --exclude .git --exclude .venv --exclude dados --exclude tests --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache "$ORIGEM/" "$DESTINO/"
fi
cd "$DESTINO"
echo "Preparando o conector..."
"$UV" sync --quiet
ADMIN="$UV run emissor-nfse-admin"

echo
read -r -p "Tem o certificado A1 do PRÓPRIO escritório para usar nas procurações? Arraste o .pfx (ou só Enter para pular): " ESC
ESC="${ESC%\"}"; ESC="${ESC#\"}"; ESC="${ESC%\'}"; ESC="${ESC#\'}"; ESC="${ESC% }"
if [ -n "$ESC" ]; then $ADMIN escritorio "$ESC"; fi

echo
read -r -p "Arraste a PASTA com os certificados A1 dos clientes (ou só Enter para pular): " PASTA
PASTA="${PASTA%\"}"; PASTA="${PASTA#\"}"; PASTA="${PASTA%\'}"; PASTA="${PASTA#\'}"; PASTA="${PASTA% }"
if [ -n "$PASTA" ]; then $ADMIN certificado "$PASTA"; fi

echo
$ADMIN instalar-servico

echo
read -r -p "Seu nome, para criar o seu acesso de administrador: " NOME
if [ -n "$NOME" ]; then $ADMIN convidar "$NOME" --admin; fi

echo
echo "Pronto. Para dar acesso a mais alguém, abra o Terminal e rode:"
echo "  cd ~/EmissorNFSe && uv run emissor-nfse-admin convidar \"Nome da pessoa\""
echo "Cada pessoa instala no próprio computador com instalar-mac.command e o convite dela."
echo "Se este computador também for usado para emitir, rode instalar-mac.command aqui mesmo com o seu convite."
read -r -p "Tecle Enter para fechar esta janela."
