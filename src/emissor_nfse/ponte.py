"""Ponte do computador do funcionário até o conector central.

O Claude (Desktop ou Code) conversa com esta ponte no próprio computador; a ponte repassa cada pedido
ao computador central do escritório, pela rede, com conexão criptografada e a chave pessoal de quem usa.
Aqui não fica certificado nem senha de certificado: só o convite (endereço, chave pessoal e o certificado
da conexão, para ter certeza de que está falando com o servidor do escritório e não com um impostor).
"""

from __future__ import annotations

import json
import os
import ssl
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server

PASTA_PADRAO = Path.home() / ".emissor-nfse"


def arquivo_convite() -> Path:
    return Path(os.environ.get("EMISSOR_NFSE_CONVITE") or (PASTA_PADRAO / "convite.json"))


def carregar_convite() -> dict:
    with open(arquivo_convite(), encoding="utf-8") as f:
        convite = json.load(f)
    for campo in ("endereco", "chave", "certificado_tls"):
        if not convite.get(campo):
            raise ValueError(f"Convite incompleto: falta {campo}.")
    return convite


@asynccontextmanager
async def sessao_central(convite: dict):
    contexto = ssl.create_default_context(cadata=convite["certificado_tls"])
    # A identidade do servidor é garantida pelo certificado fixado no convite, não pelo nome na rede
    # (o endereço do computador central pode mudar).
    contexto.check_hostname = False
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {convite['chave']}"},
        verify=contexto,
        timeout=httpx2.Timeout(30.0, read=180.0),
    ) as http:
        async with streamable_http_client(convite["endereco"], http_client=http, terminate_on_close=False) as streams:
            async with ClientSession(streams[0], streams[1]) as sessao:
                await sessao.initialize()
                yield sessao


FERRAMENTA_SITUACAO = types.Tool(
    name="situacao_conexao",
    description="Mostra se este computador consegue falar com o conector central de NFS-e do escritório.",
    input_schema={"type": "object", "properties": {}},
)


def _texto(msg: str, erro: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=msg)], is_error=erro)


def _erros_internos(e: BaseException) -> list[BaseException]:
    """Desembrulha grupos de erros (o kit de conexão junta vários num só)."""
    if isinstance(e, BaseExceptionGroup):
        saida = []
        for interno in e.exceptions:
            saida.extend(_erros_internos(interno))
        return saida
    return [e] + (_erros_internos(e.__cause__) if e.__cause__ else [])


async def _chave_recusada(convite: dict) -> bool:
    """Pergunta direto ao servidor se a chave foi recusada (o kit de conexão esconde o código HTTP)."""
    contexto = ssl.create_default_context(cadata=convite["certificado_tls"])
    contexto.check_hostname = False
    try:
        async with httpx2.AsyncClient(verify=contexto, timeout=10.0) as http:
            r = await http.post(
                convite["endereco"],
                headers={"Authorization": f"Bearer {convite['chave']}", "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
            )
            return r.status_code == 401
    except Exception:  # noqa: BLE001
        return False


def _explicar_falha(e: BaseException, recusada: bool = False) -> str:
    erros = _erros_internos(e)
    texto = " | ".join(f"{type(x).__name__}: {x}" for x in erros)
    recusado = any(getattr(getattr(x, "response", None), "status_code", None) == 401 for x in erros)
    if recusada or recusado or "401" in texto:
        return "O computador central recusou a chave deste computador. Peça um convite novo a quem administra."
    return (
        "Não consegui falar com o computador central do escritório. Confira se ele está ligado e se este "
        f"computador está na rede do escritório. Detalhe técnico: {texto[:300]}"
    )


def criar_ponte(convite: dict) -> Server:
    async def listar(ctx, params):
        try:
            async with sessao_central(convite) as s:
                resultado = await s.list_tools()
            resultado.tools.append(FERRAMENTA_SITUACAO)
            return resultado
        except Exception:  # noqa: BLE001
            return types.ListToolsResult(tools=[FERRAMENTA_SITUACAO])

    async def chamar(ctx, params):
        if params.name == "situacao_conexao":
            try:
                async with sessao_central(convite) as s:
                    await s.list_tools()
                return _texto(f"Conectado ao computador central ({convite['endereco']}) como {convite.get('nome', '?')}.")
            except Exception as e:  # noqa: BLE001
                return _texto(_explicar_falha(e, await _chave_recusada(convite)), erro=True)
        try:
            async with sessao_central(convite) as s:
                return await s.call_tool(params.name, params.arguments or {})
        except Exception as e:  # noqa: BLE001
            return _texto(_explicar_falha(e, await _chave_recusada(convite)), erro=True)

    from .server import INSTRUCOES

    return Server(
        "emissor-nfse",
        version="0.2.0",
        instructions=INSTRUCOES,
        on_list_tools=listar,
        on_call_tool=chamar,
    )


def main() -> None:
    import logging

    for nome in ("httpx", "httpx2", "mcp"):
        logging.getLogger(nome).setLevel(logging.WARNING)
    convite = carregar_convite()
    ponte = criar_ponte(convite)

    async def rodar():
        async with stdio_server() as (leitura, escrita):
            await ponte.run(leitura, escrita, ponte.create_initialization_options())

    anyio.run(rodar)


if __name__ == "__main__":
    main()
