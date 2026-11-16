"""Conversa com o Sistema Nacional NFS-e (Sefin Nacional e ADN) usando o certificado A1 da empresa.

O transporte (conexão com certificado, assinatura, compactação e leitura das respostas)
vem da biblioteca pynfse-nacional. A DPS é montada por nós em dps.py.
"""

from __future__ import annotations

import base64
import gzip
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from pynfse_nacional import NFSeClient
from pynfse_nacional.constants import ENDPOINTS
from pynfse_nacional.error_codes import ErrorCode
from pynfse_nacional.exceptions import NFSeAPIError
from pynfse_nacional.models import EventResponse, NFSeResponse
from pynfse_nacional.utils import compress_encode

ADN_CONTRIBUINTES = {
    "homologacao": "https://adn.producaorestrita.nfse.gov.br/contribuintes",
    "producao": "https://adn.nfse.gov.br/contribuintes",
}


@dataclass
class XmlAssinado:
    xml: str


@dataclass
class DocumentoDistribuido:
    nsu: int
    chave: Optional[str]
    tipo: str
    xml: Optional[str]


def _descompactar(b64: str) -> str:
    dados = base64.b64decode(b64)
    try:
        dados = gzip.decompress(dados)
    except OSError:
        pass
    return dados.decode("utf-8")


def _pegar(dic: dict, *nomes: str) -> Any:
    """Lê uma chave ignorando maiúsculas/minúsculas (o ADN não é consistente)."""
    if not isinstance(dic, dict):
        return None
    baixo = {k.lower(): v for k, v in dic.items()}
    for nome in nomes:
        if nome.lower() in baixo:
            return baixo[nome.lower()]
    return None


class ClienteSefin(NFSeClient):
    def __init__(
        self,
        cert_path: str,
        cert_password: str,
        ambiente: str,
        timeout: float = 40.0,
        cnpj_consulta: Optional[str] = None,
    ):
        super().__init__(cert_path=cert_path, cert_password=cert_password, ambiente=ambiente, timeout=timeout)
        self.ambiente_nome = ambiente
        # Com procuração, o certificado é do escritório e as consultas precisam dizer de qual cliente são.
        self.cnpj_consulta = cnpj_consulta
        self._profundidade_sessao = 0
        self._ctx_sessao = None
        self._cliente_sessao: Optional[httpx.Client] = None

    def __enter__(self) -> ClienteSefin:
        self._profundidade_sessao += 1
        return self

    def __exit__(self, *exc) -> None:
        self._profundidade_sessao = max(0, self._profundidade_sessao - 1)
        if self._profundidade_sessao == 0 and self._ctx_sessao is not None:
            ctx = self._ctx_sessao
            self._ctx_sessao = None
            self._cliente_sessao = None
            ctx.__exit__(*exc)

    @contextmanager
    def _get_client(self):
        if self._profundidade_sessao <= 0:
            with super()._get_client() as cliente:
                yield cliente
            return
        if self._cliente_sessao is None:
            ctx = super()._get_client()
            cliente = ctx.__enter__()
            self._ctx_sessao = ctx
            self._cliente_sessao = cliente
        yield self._cliente_sessao

    def assinar(self, xml: str) -> str:
        return self._xml_signer.sign(xml)

    def _build_submit_request(self, dps):  # type: ignore[override]
        if isinstance(dps, XmlAssinado):
            return (
                f"{self.base_url}{ENDPOINTS['submit_dps']}",
                {"dpsXmlGZipB64": compress_encode(dps.xml)},
            )
        return super()._build_submit_request(dps)

    def enviar_dps(self, xml_assinado: str) -> NFSeResponse:
        return self.submit_dps(XmlAssinado(xml_assinado))

    def registrar_evento(self, chave: str, xml_assinado: str) -> EventResponse:
        url = f"{self.base_url}{ENDPOINTS['events'].format(chave=chave)}"
        payload = {"pedidoRegistroEventoXmlGZipB64": compress_encode(xml_assinado)}
        try:
            with self._get_client() as cliente:
                resposta = cliente.post(url, json=payload)
                return self._parse_event_response(resposta)
        except httpx.TimeoutException as exc:
            raise NFSeAPIError(
                "Tempo esgotado ao registrar evento na SEFIN.",
                code=ErrorCode.COMMUNICATION_TIMEOUT,
            ) from exc
        except httpx.RequestError as exc:
            raise NFSeAPIError(
                f"Erro de comunicação ao registrar evento ({type(exc).__name__}).",
                code=ErrorCode.COMMUNICATION_ERROR,
            ) from exc

    def distribuicao(self, nsu: int) -> tuple[str, list[DocumentoDistribuido], list[str]]:
        """Busca documentos (notas emitidas, recebidas e eventos) a partir de um NSU.

        Devolve (situação, documentos, mensagens). A situação é 'ok', 'vazio' ou 'erro'.
        """
        base = ADN_CONTRIBUINTES[self.ambiente_nome]
        url = f"{base}/DFe/{int(nsu)}"
        extra = {"cnpjConsulta": self.cnpj_consulta} if self.cnpj_consulta else {}
        try:
            with self._get_client() as cliente:
                resposta = cliente.get(url, params={"lote": "true", **extra})
                if resposta.status_code == 400:
                    resposta = cliente.get(url, params=extra)
        except httpx.TimeoutException:
            return "erro", [], ["Tempo esgotado ao consultar o Ambiente de Dados Nacional."]
        except httpx.RequestError as exc:
            return "erro", [], [f"Erro de comunicação com o Ambiente de Dados Nacional ({type(exc).__name__})."]
        if resposta.status_code == 404:
            return "vazio", [], []
        try:
            dados = resposta.json()
        except ValueError:
            return "erro", [], [f"Resposta inesperada do ADN (HTTP {resposta.status_code})."]

        mensagens = []
        for chave_lista in ("Erros", "erro", "Alertas"):
            for item in _pegar(dados, chave_lista) or []:
                if isinstance(item, dict):
                    texto = " ".join(
                        str(v) for v in (_pegar(item, "Codigo"), _pegar(item, "Descricao"), _pegar(item, "Complemento")) if v
                    )
                    if texto:
                        mensagens.append(texto)

        lote = _pegar(dados, "LoteDFe", "loteDfe", "documentos") or []
        documentos = []
        for item in lote:
            arquivo = _pegar(item, "ArquivoXml", "arquivoXml", "xml")
            documentos.append(
                DocumentoDistribuido(
                    nsu=int(_pegar(item, "NSU", "nsu") or 0),
                    chave=_pegar(item, "ChaveAcesso", "chaveAcesso"),
                    tipo=str(_pegar(item, "TipoDocumento", "tipoDocumento") or ""),
                    xml=_descompactar(arquivo) if arquivo else None,
                )
            )
        situacao = str(_pegar(dados, "StatusProcessamento") or "")
        if not documentos:
            if resposta.status_code >= 400 and "NENHUM" not in situacao.upper():
                return "erro", [], mensagens or [f"ADN recusou a consulta (HTTP {resposta.status_code})."]
            return "vazio", [], mensagens
        return "ok", documentos, mensagens

    def aliquota_municipal(self, codigo_municipio: int, codigo_servico: str) -> Optional[str]:
        """Tenta descobrir a alíquota de ISS cadastrada pela prefeitura. Melhor esforço: pode voltar None."""
        url = f"{self.parametrizacao_url}/{int(codigo_municipio):07d}/{codigo_servico}"
        try:
            with self._get_client() as cliente:
                resposta = cliente.get(url, timeout=8.0)
            if resposta.status_code != 200:
                return None
            dados = resposta.json()
        except Exception:
            return None

        def procurar(no):
            if isinstance(no, dict):
                for k, v in no.items():
                    if "aliq" in k.lower() and isinstance(v, (int, float, str)) and str(v).replace(".", "", 1).isdigit():
                        return str(v)
                    achado = procurar(v)
                    if achado:
                        return achado
            if isinstance(no, list):
                for v in no:
                    achado = procurar(v)
                    if achado:
                        return achado
            return None

        return procurar(dados)
