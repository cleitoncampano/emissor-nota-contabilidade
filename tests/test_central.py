"""Servidor central: chave por pessoa, permissões, procuração e o caminho completo ponte -> rede -> servidor."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import sys
import threading
import time

import pytest
from cryptography import x509

from emissor_nfse import acesso
from emissor_nfse.acesso import Pessoa, SemPermissao, pessoa_atual
from emissor_nfse.notas import ler_historico

from .conftest import gerar_pfx
from .test_fluxo import GovernoFalso

CNPJ_ESCRITORIO = "11444777000161"
CNPJ_CLIENTE_PROC = "22333444000181"  # dado fictício, sem acesso ao governo


@pytest.fixture()
def governo(ambiente):
    op = ambiente["op"]
    GovernoFalso.enviados = []
    GovernoFalso.modo = "ok"
    GovernoFalso.distribuidos = []
    usados = []

    def fabrica(empresa, senha):
        cli = GovernoFalso(str(empresa.caminho_certificado()), senha, empresa.ambiente,
                           cnpj_consulta=empresa.cnpj if empresa.usa_procuracao else None)
        usados.append(cli)
        return cli

    op.fabrica_cliente = fabrica
    ambiente["clientes_usados"] = usados
    yield ambiente
    acesso.modo_servidor.set(False)


def test_chaves_por_pessoa(ambiente):
    chave = acesso.criar_pessoa("Maria")
    assert acesso.identificar(f"Bearer {chave}") == Pessoa("Maria", False)
    assert acesso.identificar("Bearer outra") is None
    assert acesso.identificar(None) is None
    nova = acesso.criar_pessoa("maria", admin=True)  # recriar invalida a chave antiga
    assert acesso.identificar(f"Bearer {chave}") is None
    assert acesso.identificar(f"Bearer {nova}").admin
    assert acesso.remover_pessoa("Maria")
    assert acesso.identificar(f"Bearer {nova}") is None


def test_quem_nao_e_admin_nao_altera_cadastro(governo):
    op = governo["op"]
    op.cadastrar_certificado(str(governo["pfx"]), apelido="Teste", senha="1234")
    marca = pessoa_atual.set(Pessoa("Estagiário", admin=False))
    try:
        with pytest.raises(SemPermissao):
            op.configurar_empresa("Teste", {"regime": "mei"})
        with pytest.raises(SemPermissao):
            op.alterar_ambiente("Teste", "producao")
        assert "Teste" in op.listar_empresas()  # consultar pode
    finally:
        pessoa_atual.reset(marca)


def test_procuracao_usa_certificado_do_escritorio(governo):
    op = governo["op"]
    pfx_esc = governo["tmp"] / "escritorio.pfx"
    pfx_esc.write_bytes(gerar_pfx(CNPJ_ESCRITORIO, "ESCRITORIO TESTE LTDA", "senhaesc"))
    assert "ESCRITORIO TESTE" in op.cadastrar_certificado_escritorio(str(pfx_esc), senha="senhaesc")
    texto = op.cadastrar_cliente_procuracao(CNPJ_CLIENTE_PROC, "Cliente Por Procuracao Ltda", apelido="Proc")
    assert "procuração" in texto
    op.configurar_empresa("Proc", {"municipio": "3550308", "regime": "normal", "codigo_servico": "171901", "descricao_padrao": "Serviços contábeis"})
    previa = op.preparar_nota("Proc", "11222333000181", "800", tomador_nome="Empresa Teste")
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))

    xml = GovernoFalso.enviados[0]
    assert f"<prest><CNPJ>{CNPJ_CLIENTE_PROC}</CNPJ>" in xml  # o emitente continua sendo o cliente
    der = base64.b64decode(re.search(r"<X509Certificate>([^<]+)</X509Certificate>", xml).group(1))
    assinante = x509.load_der_x509_certificate(der).subject.rfc4514_string()
    assert CNPJ_ESCRITORIO in assinante  # quem assina é o escritório
    assert governo["clientes_usados"][-1].cnpj_consulta == CNPJ_CLIENTE_PROC
    assert "por procuração" in op.listar_empresas()


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_rede_ponte_ate_servidor(governo, tmp_path):
    """Sobe o servidor central de verdade (HTTPS) e fala com ele pela ponte, como o Claude do funcionário faria."""
    import anyio
    import uvicorn
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from emissor_nfse.central import criar_app, garantir_certificado_tls

    op = governo["op"]
    op.cadastrar_certificado(str(governo["pfx"]), apelido="Teste", senha="1234")
    op.configurar_empresa("Teste", {"municipio": "Campinas/SP", "regime": "mei", "codigo_servico": "170101", "descricao_padrao": "Consultoria"})

    cert, chave_tls = garantir_certificado_tls()
    porta = _porta_livre()
    servidor = uvicorn.Server(
        uvicorn.Config(criar_app(), host="127.0.0.1", port=porta, ssl_certfile=str(cert), ssl_keyfile=str(chave_tls), log_level="warning")
    )
    t = threading.Thread(target=servidor.run, daemon=True)
    t.start()
    for _ in range(100):
        if servidor.started:
            break
        time.sleep(0.05)

    def convite(nome, admin=False, chave=None):
        c = {
            "nome": nome,
            "endereco": f"https://127.0.0.1:{porta}/mcp",
            "chave": chave or acesso.criar_pessoa(nome, admin=admin),
            "certificado_tls": cert.read_text(),
        }
        arq = tmp_path / f"convite-{nome}.json"
        arq.write_text(json.dumps(c))
        return arq

    async def usar(arquivo_convite, chamadas):
        env = {**os.environ, "EMISSOR_NFSE_CONVITE": str(arquivo_convite)}
        params = StdioServerParameters(command=sys.executable, args=["-m", "emissor_nfse.ponte"], env=env)
        saidas = []
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                nomes = [t.name for t in (await s.list_tools()).tools]
                saidas.append(nomes)
                for nome, args in chamadas:
                    res = await s.call_tool(nome, args)
                    saidas.append(res.content[0].text)
        return saidas

    try:
        # Maria (equipe): consulta, prepara e emite; não pode mudar cadastro.
        maria = convite("Maria")
        r = anyio.run(usar, maria, [("situacao_conexao", {}), ("listar_empresas", {}), ("configurar_empresa", {"empresa": "Teste", "campos": {"regime": "normal"}}), ("preparar_nota", {"empresa": "Teste", "tomador_documento": "52998224725", "tomador_nome": "Maria da Silva", "valor": "100"})])
        assert "emitir_nota" in r[0] and "situacao_conexao" in r[0]
        assert "como Maria" in r[1]
        assert "Teste" in r[2]
        assert "só para administradores" in r[3]
        codigo = re.search(r"Prévia (N\w+)", r[4]).group(1)
        r = anyio.run(usar, maria, [("emitir_nota", {"codigo_previa": codigo})])
        assert "Pedido por: Maria" in r[1]
        emissoes = [h for h in ler_historico() if h["acao"] == "emissao"]
        assert emissoes[-1]["quem"] == "Maria"

        # Chave errada: o servidor recusa e a ponte explica.
        intruso = convite("Intruso", chave="nfse_chave_falsa")
        r = anyio.run(usar, intruso, [("situacao_conexao", {})])
        assert r[0] == ["situacao_conexao"]
        assert "recusou a chave" in r[1]

        # Cadastro de certificado pela rede é bloqueado (senha não trafega).
        ana = convite("Ana", admin=True)
        r = anyio.run(usar, ana, [("cadastrar_certificado", {"caminho_arquivo": str(governo["pfx"])})])
        assert "computador central" in r[1]

        # Acesso removido deixa de funcionar na hora.
        acesso.remover_pessoa("Maria")
        r = anyio.run(usar, maria, [("situacao_conexao", {})])
        assert "recusou a chave" in r[1]
    finally:
        servidor.should_exit = True
        t.join(timeout=5)
