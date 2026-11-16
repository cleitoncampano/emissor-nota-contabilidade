"""Fluxo completo com o governo simulado: cadastro, prévia, emissão, recusa, envio sem resposta, cancelamento e listagem."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest
from pynfse_nacional.exceptions import NFSeAPIError
from pynfse_nacional.models import EventResponse, NFSeQueryResult, NFSeResponse

from emissor_nfse.config import achar_empresa, ler_json, pasta_dados
from emissor_nfse.dps import ErroNota, competencia_para_data, validar_xsd
from emissor_nfse.sefin import ClienteSefin, DocumentoDistribuido

from .conftest import CNPJ_TESTE

NS = "http://www.sped.fazenda.gov.br/nfse"


def nfse_falsa(dps_assinada: str, numero: int, chave: str, iss: str = "30.00") -> str:
    dps = dps_assinada.split("?>", 1)[1] if dps_assinada.startswith("<?xml") else dps_assinada
    cnpj = re.search(r"<CNPJ>(\d{14})</CNPJ>", dps).group(1)
    vserv = re.search(r"<vServ>([\d.]+)</vServ>", dps).group(1)
    return (
        f'<NFSe xmlns="{NS}" versao="1.01"><infNFSe Id="NFS{chave}"><nNFSe>{numero}</nNFSe>'
        f"<dhProc>2026-11-16T10:00:00-03:00</dhProc><ambGer>2</ambGer>"
        f"<emit><CNPJ>{cnpj}</CNPJ><xNome>EMPRESA TESTE LTDA</xNome></emit>"
        f"<valores><vBC>{vserv}</vBC><pAliqAplic>2.00</pAliqAplic><vISSQN>{iss}</vISSQN><vLiq>{vserv}</vLiq></valores>"
        f"{dps}</infNFSe></NFSe>"
    )


class GovernoFalso(ClienteSefin):
    """Assina de verdade (com o certificado de teste) mas não sai para a internet."""

    enviados: list = []
    modo = "ok"
    distribuidos: list = []

    def has_nfse_by_dps(self, id_dps):
        return False

    def enviar_dps(self, xml_assinado):
        GovernoFalso.enviados.append(xml_assinado)
        erros = validar_xsd(xml_assinado)
        assert not erros, erros
        if GovernoFalso.modo == "recusa":
            return NFSeResponse(success=False, error_code="E0160", error_message="Opção do Simples não confere com o cadastro.")
        if GovernoFalso.modo == "sem_resposta":
            raise NFSeAPIError("Tempo esgotado ao comunicar com a SEFIN.", code="COMMUNICATION_TIMEOUT")
        n = len(GovernoFalso.enviados)
        chave = f"{n:050d}"
        return NFSeResponse(success=True, chave_acesso=chave, nfse_number=str(n), xml_nfse=nfse_falsa(xml_assinado, n, chave))

    def recover_nfse_by_dps(self, id_dps):
        from pynfse_nacional.client import RecoveryOutcome

        xml = GovernoFalso.enviados[-1]
        chave = "9" * 50
        return RecoveryOutcome(
            status="success",
            result=NFSeQueryResult(
                chave_acesso=chave,
                nfse_number="77",
                status="emitida",
                data_emissao="2026-11-16T10:00:00",
                valor_servicos=Decimal("100"),
                prestador_cnpj=CNPJ_TESTE,
                xml_nfse=nfse_falsa(xml, 77, chave),
            ),
        )

    def download_danfse(self, chave):
        return b"%PDF-1.4 falso"

    def registrar_evento(self, chave, xml_assinado):
        erros = validar_xsd(xml_assinado, tipo="evento")
        assert not erros, erros
        return EventResponse(success=True, protocolo="PROTOCOLO123")

    def distribuicao(self, nsu):
        docs = [d for d in GovernoFalso.distribuidos if d.nsu > nsu]
        return ("ok", docs, []) if docs else ("vazio", [], [])


@pytest.fixture()
def pronto(ambiente):
    op = ambiente["op"]
    GovernoFalso.enviados = []
    GovernoFalso.modo = "ok"
    GovernoFalso.distribuidos = []
    op.fabrica_cliente = lambda empresa, senha: GovernoFalso(str(empresa.caminho_certificado()), senha, empresa.ambiente)
    texto = op.cadastrar_certificado(str(ambiente["pfx"]), apelido="Teste", senha="1234")
    assert "EMPRESA TESTE LTDA" in texto
    op.configurar_empresa(
        "Teste",
        {
            "municipio": "Campinas/SP",
            "regime": "simples",
            "aliquota_simples_total": "6",
            "codigo_servico": "170101",
            "descricao_padrao": "Consultoria em gestão",
        },
    )
    return op


def test_cadastro_le_certificado(pronto):
    e = achar_empresa("teste")
    assert e.cnpj == CNPJ_TESTE
    assert e.municipio_ibge == 3509502
    assert e.ambiente == "homologacao"
    assert not e.faltando()


def test_senha_errada(ambiente):
    op = ambiente["op"]
    from emissor_nfse.certificado import CertificadoInvalido

    with pytest.raises(CertificadoInvalido):
        op.cadastrar_certificado(str(ambiente["pfx"]), senha="errada")


def test_previa_e_emissao_simples(pronto):
    op = pronto
    previa = op.preparar_nota("Teste", "11.444.777/0001-61", "1.500,00", tomador_nome="Alfa Tecnologia Ltda", competencia="10/2026")
    assert "AMBIENTE DE TESTE" in previa
    assert "R$ 1.500,00" in previa
    assert "31/10/2026" in previa
    codigo = re.search(r"Prévia (N\w+)", previa).group(1)
    assert GovernoFalso.enviados == []  # a prévia não envia nada

    resultado = op.emitir_nota(codigo)
    assert "Nota de TESTE emitida: número 1" in resultado
    assert "PDF" in resultado
    xml = GovernoFalso.enviados[0]
    assert "<pTotTribSN>6.00</pTotTribSN>" in xml
    assert "<opSimpNac>3</opSimpNac><regApTribSN>1</regApTribSN><regEspTrib>0</regEspTrib>" in xml
    assert "<serie>900</serie><nDPS>1</nDPS><dCompet>2026-10-31</dCompet>" in xml
    assert "<Signature" in xml
    assert "pAliq>" not in xml

    # repetir a confirmação não emite de novo
    assert "Nada foi emitido de novo" in op.emitir_nota(codigo)
    assert len(GovernoFalso.enviados) == 1

    # segunda nota usa o próximo número e o tomador fica lembrado
    previa2 = op.preparar_nota("Teste", "11444777000161", "200")
    assert "Alfa Tecnologia Ltda" in previa2
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa2).group(1))
    assert "<nDPS>2</nDPS>" in GovernoFalso.enviados[1]
    assert achar_empresa("teste").notas_teste_ok == 2


def test_normal_com_retencoes(pronto):
    op = pronto
    op.configurar_empresa("Teste", {"regime": "normal"})
    previa = op.preparar_nota(
        "Teste",
        "11444777000161",
        "10000",
        tomador_nome="Alfa",
        retencoes={"irrf": 1.5, "pis": 0.65, "cofins": 3, "csll": 1},
    )
    assert "Retenção de IRRF: R$ 150,00" in previa
    assert "Valor líquido estimado: R$ 9.385,00" in previa
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))
    xml = GovernoFalso.enviados[0]
    assert "<tpRetPisCofins>3</tpRetPisCofins>" in xml
    assert "<vPis>65.00</vPis><vCofins>300.00</vCofins>" in xml
    assert "<vRetIRRF>150.00</vRetIRRF><vRetCSLL>100.00</vRetCSLL>" in xml
    assert "<pTotTrib>" in xml and "<opSimpNac>1</opSimpNac>" in xml


def test_simples_iss_retido_exige_aliquota(pronto):
    op = pronto
    with pytest.raises(ErroNota, match="aliquota_iss_simples"):
        op.preparar_nota("Teste", "11444777000161", "1000", tomador_nome="Alfa", iss_retido=True)
    op.configurar_empresa("Teste", {"aliquota_iss_simples": "2.79"})
    previa = op.preparar_nota("Teste", "11444777000161", "1000", tomador_nome="Alfa", iss_retido=True)
    assert "ISS retido pelo tomador: R$ 27,90 (2.79%)" in previa
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))
    assert "<tpRetISSQN>2</tpRetISSQN><pAliq>2.79</pAliq>" in GovernoFalso.enviados[0]


def test_mei(pronto):
    op = pronto
    op.configurar_empresa("Teste", {"regime": "mei"})
    with pytest.raises(ErroNota, match="MEI"):
        op.preparar_nota("Teste", "11444777000161", "100", tomador_nome="Alfa", iss_retido=True)
    with pytest.raises(ErroNota, match="MEI"):
        op.preparar_nota("Teste", "11444777000161", "100", tomador_nome="Alfa", retencoes={"irrf": 1.5})
    previa = op.preparar_nota("Teste", "52998224725", "100", tomador_nome="Maria da Silva")
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))
    xml = GovernoFalso.enviados[0]
    assert "<indTotTrib>0</indTotTrib>" in xml and "<CPF>52998224725</CPF>" in xml
    assert "<regEspTrib>0</regEspTrib>" in xml


def test_recusa_libera_numero(pronto):
    op = pronto
    GovernoFalso.modo = "recusa"
    previa = op.preparar_nota("Teste", "11444777000161", "100", tomador_nome="Alfa")
    resp = op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))
    assert "recusou" in resp and "E0160" in resp
    numeracao = ler_json(pasta_dados() / "numeracao.json", {})
    assert numeracao[f"{CNPJ_TESTE}|homologacao|900"] == 0
    assert "recusa" in op.pendencias()


def test_envio_sem_resposta_e_recuperado(pronto):
    op = pronto
    GovernoFalso.modo = "sem_resposta"
    previa = op.preparar_nota("Teste", "11444777000161", "100", tomador_nome="Alfa")
    codigo = re.search(r"Prévia (N\w+)", previa).group(1)
    resp = op.emitir_nota(codigo)
    # sem resposta, o conector pergunta ao governo pelo identificador da DPS e recupera a nota
    assert "número 77" in resp
    assert "Nada foi emitido de novo" in op.emitir_nota(codigo)


def test_cancelamento(pronto):
    op = pronto
    previa = op.preparar_nota("Teste", "11444777000161", "100", tomador_nome="Alfa")
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))
    chave = f"{1:050d}"
    with pytest.raises(ErroNota):
        op.preparar_cancelamento("Teste", chave, 1, "curto")
    pc = op.preparar_cancelamento("Teste", chave, 1, "Valor informado errado na emissão")
    assert "não pode ser desfeito" in pc
    codigo = re.search(r"cancelamento (C\w+)", pc).group(1)
    assert "Protocolo: PROTOCOLO123" in op.cancelar_nota(codigo)
    from emissor_nfse.notas import carregar_indice

    assert carregar_indice(CNPJ_TESTE, "homologacao")[chave]["situacao"] == "cancelada"
    assert "já foi feito" in op.cancelar_nota(codigo)


def test_producao_so_depois_de_teste(pronto):
    op = pronto
    with pytest.raises(ErroNota, match="nota de teste"):
        op.alterar_ambiente("Teste", "producao")
    previa = op.preparar_nota("Teste", "11444777000161", "100", tomador_nome="Alfa")
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))
    assert "PRODUÇÃO" in op.alterar_ambiente("Teste", "producao")
    previa = op.preparar_nota("Teste", "11444777000161", "100", tomador_nome="Alfa")
    assert "PRODUÇÃO: esta nota é real" in previa


def test_listar_notas_com_distribuicao(pronto):
    op = pronto
    previa = op.preparar_nota("Teste", "11444777000161", "1500", tomador_nome="Alfa", competencia=date.today().strftime("%m/%Y"))
    op.emitir_nota(re.search(r"Prévia (N\w+)", previa).group(1))
    dps_site = GovernoFalso.enviados[0].replace("<nDPS>1</nDPS>", "<nDPS>5</nDPS>")
    nota_site = nfse_falsa(dps_site, 50, "5" * 50).replace("<vServ>1500.00</vServ>", "<vServ>500.00</vServ>")
    evento = (
        f'<evento xmlns="{NS}"><infEvento><dhProc>2026-11-16T11:00:00-03:00</dhProc>'
        f"<pedRegEvento><infPedReg><chNFSe>{'5' * 50}</chNFSe><e101101><xDesc>Cancelamento de NFS-e</xDesc></e101101>"
        "</infPedReg></pedRegEvento></infEvento></evento>"
    )
    nota_site2 = nfse_falsa(dps_site.replace("<nDPS>5</nDPS>", "<nDPS>6</nDPS>"), 51, "6" * 50).replace(
        "<vServ>1500.00</vServ>", "<vServ>700.00</vServ>"
    )
    GovernoFalso.distribuidos = [
        DocumentoDistribuido(nsu=1, chave="5" * 50, tipo="NFSE", xml=nota_site),
        DocumentoDistribuido(nsu=2, chave="5" * 50, tipo="EVENTO", xml=evento),
        DocumentoDistribuido(nsu=3, chave="6" * 50, tipo="NFSE", xml=nota_site2),
    ]
    texto = op.listar_notas("Teste")
    assert "2 nota(s) emitidas, total R$ 2.200,00" in texto
    assert "1 cancelada(s)" in texto
    assert "CANCELADA" in texto


def test_competencia():
    hoje = date(2026, 11, 16)
    assert competencia_para_data("10/2026", hoje) == date(2026, 10, 31)
    assert competencia_para_data("2026-11", hoje) == date(2026, 11, 16)
    assert competencia_para_data("05/11/2026", hoje) == date(2026, 11, 5)
    with pytest.raises(ErroNota):
        competencia_para_data("12/2026", hoje)


def test_busca_codigos(ambiente):
    op = ambiente["op"]
    assert "171901" in op.buscar_codigo("contabilidade")
    assert "3509502" in op.buscar_codigo("campinas", "municipio")
