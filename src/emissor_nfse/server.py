"""Servidor MCP: as ferramentas que o Claude usa para emitir e consultar NFS-e no Emissor Nacional."""

from __future__ import annotations

import functools
import traceback
from typing import Annotated, Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import operacoes as op
from .acesso import SemPermissao, identificar, modo_servidor, pessoa_atual
from .certificado import CertificadoInvalido
from .config import EmpresaNaoEncontrada, pasta_dados
from .dps import ErroNota

INSTRUCOES = """
Conector do escritório com o Emissor Nacional da NFS-e (Sistema Nacional NFS-e do governo).
Cada empresa cliente emite com o próprio certificado A1 ou, se deu procuração eletrônica ao escritório,
com o certificado do escritório. Os certificados ficam só no computador central do conector.

Regras de uso:
1. Para emitir, SEMPRE chame preparar_nota primeiro e mostre a prévia inteira ao usuário.
   Só chame emitir_nota depois que o usuário responder que confirma aquela prévia (um "sim", "pode emitir", "ok").
   Nunca emita por iniciativa própria, nunca emita várias prévias de uma vez sem confirmação de cada uma
   (ou de uma lista explícita que o usuário aprovou), e nunca reaproveite confirmação antiga.
2. Cancelamento segue o mesmo caminho: preparar_cancelamento, mostrar, esperar o OK, cancelar_nota.
3. Se emitir_nota responder que o envio ficou sem confirmação, NÃO prepare a mesma nota de novo:
   use verificar_envio com o mesmo código.
4. Impostos e alíquotas são calculados pelo sistema nacional e pelo cadastro da empresa. Não invente alíquota.
   Se faltar informação fiscal no cadastro, pergunte ao usuário e grave com configurar_empresa.
5. Para achar o código do serviço ou do município, use buscar_codigo. Para tomador já usado, buscar_tomador.
6. Valores em reais. Competência em mês/ano (ex.: 10/2026) ou data. Retenções em percentual (ex.: irrf 1.5).
7. Empresas novas começam no ambiente de TESTE (nota sem valor fiscal). Só passe para produção com
   alterar_ambiente quando o usuário pedir, depois de uma nota de teste aceita.
8. Senhas de certificado nunca passam pela conversa. Certificados são cadastrados no computador central
   (comando emissor-nfse-admin). Mudanças de cadastro e de ambiente são só para administradores; se a ferramenta
   responder que falta permissão, explique isso ao usuário, sem tentar contornar.
Responda ao usuário em português, de forma curta, sem jargão técnico.
""".strip()

class IdentificarPessoa:
    """No servidor central, descobre quem chamou (pela chave pessoal) antes de cada ferramenta."""

    async def __call__(self, ctx, call_next):
        if not modo_servidor.get():
            return await call_next(ctx)
        cabecalhos = getattr(ctx.request, "headers", None) or {}
        pessoa = identificar(cabecalhos.get("authorization"))
        if pessoa is None:
            raise PermissionError("Chave de acesso inválida.")
        marca = pessoa_atual.set(pessoa)
        try:
            return await call_next(ctx)
        finally:
            pessoa_atual.reset(marca)


servidor = MCPServer(
    name="emissor-nfse",
    title="Emissor NFS-e Nacional",
    instructions=INSTRUCOES,
    version="0.2.0",
    middleware=[IdentificarPessoa()],
)

LEITURA = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
LEITURA_LOCAL = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
LOCAL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
SEGURA = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
DEFINITIVA = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)


def amigavel(func):
    @functools.wraps(func)
    def envolvida(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (ErroNota, CertificadoInvalido, EmpresaNaoEncontrada, SemPermissao, TimeoutError) as e:
            return f"Não foi possível: {e}"
        except Exception as e:  # noqa: BLE001
            with open(pasta_dados() / "erros.log", "a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")
            return f"Erro inesperado: {type(e).__name__}: {e}. Detalhes gravados em dados/erros.log."

    return envolvida


EmpresaRef = Annotated[str, Field(description="Nome, apelido ou CNPJ da empresa cliente (a prestadora do serviço)")]


@servidor.tool(annotations=LEITURA_LOCAL)
@amigavel
def listar_empresas() -> str:
    """Lista as empresas clientes cadastradas, com situação do cadastro, ambiente (teste ou produção) e validade do certificado."""
    return op.listar_empresas()


@servidor.tool(annotations=LEITURA_LOCAL)
@amigavel
def ver_empresa(empresa: EmpresaRef) -> str:
    """Mostra o cadastro fiscal de uma empresa: município, regime, serviço padrão, ambiente e o que falta para emitir."""
    return op.ver_empresa(empresa)


@servidor.tool(annotations=SEGURA)
@amigavel
def cadastrar_certificado(
    caminho_arquivo: Annotated[str, Field(description="Caminho do arquivo .pfx ou .p12 do certificado A1")],
    apelido: Annotated[Optional[str], Field(description="Nome curto para chamar a empresa (opcional)")] = None,
) -> str:
    """Cadastra (ou renova) o certificado A1 de uma empresa. Abre uma janela no computador para digitar a senha,
    que fica guardada no cofre do sistema e nunca passa pela conversa. Lê CNPJ, razão social e validade do próprio certificado."""
    return op.cadastrar_certificado(caminho_arquivo, apelido=apelido)


@servidor.tool(annotations=SEGURA)
@amigavel
def cadastrar_certificado_escritorio(
    caminho_arquivo: Annotated[str, Field(description="Caminho do .pfx do certificado A1 do próprio escritório (e-CNPJ)")],
) -> str:
    """Cadastra o certificado A1 do escritório, usado para emitir pelos clientes que deram procuração eletrônica.
    Só funciona no computador central; a senha é pedida numa janela."""
    return op.cadastrar_certificado_escritorio(caminho_arquivo)


@servidor.tool(annotations=LOCAL)
@amigavel
def cadastrar_cliente_procuracao(
    cnpj: Annotated[str, Field(description="CNPJ do cliente que deu procuração eletrônica ao escritório")],
    razao_social: Annotated[str, Field(description="Razão social do cliente")],
    apelido: Annotated[Optional[str], Field(description="Nome curto (opcional)")] = None,
) -> str:
    """Cadastra um cliente que vai emitir com o certificado do escritório (procuração eletrônica na Receita,
    serviço NFS-e). Também serve para passar um cliente já cadastrado para o modo procuração. Só administradores."""
    return op.cadastrar_cliente_procuracao(cnpj, razao_social, apelido)


@servidor.tool(annotations=SEGURA)
@amigavel
def importar_pasta_certificados(
    pasta: Annotated[str, Field(description="Pasta com vários arquivos .pfx/.p12")],
) -> str:
    """Cadastra todos os certificados A1 de uma pasta, pedindo a senha de cada um em janela própria."""
    return op.importar_pasta_certificados(pasta)


@servidor.tool(annotations=LOCAL)
@amigavel
def configurar_empresa(
    empresa: EmpresaRef,
    campos: Annotated[
        dict,
        Field(
            description=(
                "Campos a gravar. Aceitos: apelido; municipio (nome com UF, ex. 'Campinas/SP', ou código IBGE); "
                "inscricao_municipal; regime ('mei', 'simples', 'simples_iss_fora', 'simples_tudo_fora', 'normal'); "
                "regime_especial (0 nenhum, 1 cooperativa, 2 estimativa, 3 microempresa municipal, 4 notário, "
                "5 profissional autônomo, 6 sociedade de profissionais, 9 outros); "
                "aliquota_simples_total (percentual aproximado de tributos do Simples, sai na nota); "
                "aliquota_iss_simples (alíquota de ISS do Simples, usada só com ISS retido); "
                "tributos_aproximados_federal/estadual/municipal (percentuais, para não optantes); "
                "cst_pis_cofins (padrão '01'); serie (1 a 49999, padrão 900); "
                "usa_procuracao (true para emitir com o certificado do escritório); "
                "codigo_servico (código nacional de 6 dígitos); codigo_municipal; nbs (9 dígitos); descricao_padrao."
            )
        ),
    ],
) -> str:
    """Grava ou corrige dados fiscais do cadastro de uma empresa. Não altera o ambiente (use alterar_ambiente)."""
    return op.configurar_empresa(empresa, campos)


@servidor.tool(annotations=SEGURA)
@amigavel
def alterar_ambiente(
    empresa: EmpresaRef,
    ambiente: Annotated[str, Field(description="'homologacao' (teste, sem valor fiscal) ou 'producao' (notas reais)")],
) -> str:
    """Muda a empresa entre ambiente de teste e produção. Produção só é liberada depois de uma nota de teste aceita.
    Só use quando o usuário pedir explicitamente."""
    return op.alterar_ambiente(empresa, ambiente)


@servidor.tool(annotations=LEITURA_LOCAL)
@amigavel
def buscar_codigo(
    texto: Annotated[str, Field(description="Palavras ou início do código")],
    tipo: Annotated[str, Field(description="'servico' (lista nacional, padrão), 'nbs' ou 'municipio'")] = "servico",
) -> str:
    """Procura códigos oficiais: serviço da lista nacional (cTribNac, 6 dígitos), NBS ou município (IBGE)."""
    return op.buscar_codigo(texto, tipo)


@servidor.tool(annotations=LEITURA_LOCAL)
@amigavel
def buscar_tomador(
    empresa: EmpresaRef,
    texto: Annotated[str, Field(description="Parte do nome ou do CPF/CNPJ do tomador")],
) -> str:
    """Procura tomadores que já receberam nota dessa empresa por este conector, para reaproveitar CNPJ e nome."""
    return op.buscar_tomador(empresa, texto)


@servidor.tool(annotations=LOCAL)
@amigavel
def preparar_nota(
    empresa: EmpresaRef,
    tomador_documento: Annotated[str, Field(description="CPF ou CNPJ de quem contratou o serviço")],
    valor: Annotated[str, Field(description="Valor do serviço em reais, ex. '1500' ou '1.500,00'")],
    tomador_nome: Annotated[Optional[str], Field(description="Nome ou razão social do tomador (obrigatório na primeira nota para ele)")] = None,
    descricao: Annotated[Optional[str], Field(description="Texto que descreve o serviço na nota. Se vazio, usa a descrição padrão da empresa")] = None,
    competencia: Annotated[Optional[str], Field(description="Mês/ano ou data da prestação, ex. '10/2026'. Padrão: hoje")] = None,
    codigo_servico: Annotated[Optional[str], Field(description="Código nacional de 6 dígitos. Padrão: o serviço padrão da empresa")] = None,
    codigo_municipal: Annotated[Optional[str], Field(description="Código de tributação municipal, se a prefeitura usar")] = None,
    nbs: Annotated[Optional[str], Field(description="Código NBS de 9 dígitos (opcional)")] = None,
    municipio_prestacao: Annotated[Optional[str], Field(description="Município onde o serviço foi prestado, se diferente do da empresa")] = None,
    iss_retido: Annotated[bool, Field(description="true se o tomador vai reter o ISS")] = False,
    retencoes: Annotated[
        Optional[dict],
        Field(description="Retenções federais em percentual sobre o valor: irrf, pis, cofins, csll, inss. Ex.: {'irrf': 1.5, 'pis': 0.65, 'cofins': 3, 'csll': 1}"),
    ] = None,
    tomador_email: Annotated[Optional[str], Field(description="E-mail do tomador (opcional)")] = None,
    observacao: Annotated[Optional[str], Field(description="Informação complementar que sai na nota (opcional)")] = None,
) -> str:
    """Monta a PRÉVIA de uma NFS-e e confere tudo contra as regras e o esquema oficial, sem emitir nada.
    Devolve um código de prévia (começa com N). Mostre a prévia ao usuário e espere a confirmação antes de emitir_nota."""
    return op.preparar_nota(
        empresa,
        tomador_documento,
        valor,
        tomador_nome=tomador_nome,
        descricao=descricao,
        competencia=competencia,
        codigo_servico=codigo_servico,
        codigo_municipal=codigo_municipal,
        nbs=nbs,
        municipio_prestacao=municipio_prestacao,
        iss_retido=iss_retido,
        retencoes=retencoes,
        tomador_email=tomador_email,
        observacao=observacao,
    )


@servidor.tool(annotations=DEFINITIVA)
@amigavel
def emitir_nota(
    codigo_previa: Annotated[str, Field(description="Código da prévia devolvido por preparar_nota (ex. N7KQ2PZ)")],
) -> str:
    """EMITE a NFS-e de uma prévia no sistema nacional, em nome da empresa. Só chame depois que o usuário
    confirmou explicitamente aquela prévia. Salva o XML e o PDF da nota no computador."""
    return op.emitir_nota(codigo_previa)


@servidor.tool(annotations=SEGURA)
@amigavel
def verificar_envio(
    codigo_previa: Annotated[str, Field(description="Código da prévia cujo envio ficou sem confirmação")],
) -> str:
    """Pergunta ao governo se a nota de uma prévia foi gerada, quando o envio ficou sem resposta. Não emite nada novo."""
    return op.verificar_envio(codigo_previa)


@servidor.tool(annotations=LEITURA)
@amigavel
def consultar_nota(
    empresa: EmpresaRef,
    chave: Annotated[str, Field(description="Chave de acesso da NFS-e (50 dígitos)")],
) -> str:
    """Consulta uma NFS-e pela chave de acesso no sistema nacional e mostra situação, valores e tomador."""
    return op.consultar_nota(empresa, chave)


@servidor.tool(annotations=LEITURA)
@amigavel
def baixar_pdf(
    empresa: EmpresaRef,
    chave: Annotated[str, Field(description="Chave de acesso da NFS-e (50 dígitos)")],
) -> str:
    """Baixa o PDF oficial (DANFSe) da nota e devolve onde ele foi salvo. Se o governo não entregar, gera a partir do XML."""
    return op.baixar_pdf(empresa, chave)


@servidor.tool(annotations=LEITURA)
@amigavel
def listar_notas(
    empresa: Annotated[Optional[str], Field(description="Empresa (nome, apelido ou CNPJ). Vazio = todas, até 10")] = None,
    de: Annotated[Optional[str], Field(description="Data inicial AAAA-MM-DD. Padrão: dia 1 do mês atual")] = None,
    ate: Annotated[Optional[str], Field(description="Data final AAAA-MM-DD. Padrão: hoje")] = None,
    tipo: Annotated[str, Field(description="'emitidas' (padrão), 'recebidas' ou 'todas'")] = "emitidas",
    atualizar: Annotated[bool, Field(description="Buscar notas novas no ambiente nacional antes de listar (inclui as emitidas pelo site)")] = True,
) -> str:
    """Lista e soma as notas de um período por empresa: quantidade, total, ISS e canceladas.
    Inclui notas emitidas pelo site do Emissor Nacional e notas recebidas de fornecedores."""
    return op.listar_notas(empresa, de, ate, tipo, atualizar)


@servidor.tool(annotations=LOCAL)
@amigavel
def preparar_cancelamento(
    empresa: EmpresaRef,
    chave: Annotated[str, Field(description="Chave de acesso da NFS-e a cancelar (50 dígitos)")],
    motivo_codigo: Annotated[int, Field(description="1 erro na emissão, 2 serviço não prestado, 9 outros")],
    justificativa: Annotated[str, Field(description="Explicação do cancelamento, de 15 a 255 caracteres")],
) -> str:
    """Monta a PRÉVIA de um cancelamento, sem cancelar. Devolve um código (começa com C). Mostre e espere o OK."""
    return op.preparar_cancelamento(empresa, chave, motivo_codigo, justificativa)


@servidor.tool(annotations=DEFINITIVA)
@amigavel
def cancelar_nota(
    codigo_previa: Annotated[str, Field(description="Código da prévia de cancelamento (ex. C4HT9XA)")],
) -> str:
    """CANCELA a NFS-e da prévia no sistema nacional. Não tem volta. Só chame depois do OK explícito do usuário."""
    return op.cancelar_nota(codigo_previa)


@servidor.tool(annotations=LEITURA)
@amigavel
def pendencias() -> str:
    """Mostra o que precisa de atenção: certificados vencendo ou vencidos, cadastros incompletos,
    envios sem confirmação e notas recusadas nos últimos 7 dias."""
    return op.pendencias()


def main() -> None:
    servidor.run("stdio")


if __name__ == "__main__":
    main()
