"""Montagem da DPS (leiaute 1.01 do Sistema Nacional NFS-e) e do pedido de cancelamento.

Regras seguidas (Anexo I, RN DPS_NFS-e, v1.01 de 09/02/2026):
- série 1 a 49999 para emissão por aplicativo próprio (E0010);
- dCompet <= dhEmi (E0015) e dhEmi <= hora de processamento (E0008);
- pAliq só para ME/EPP com ISS dentro do Simples e ISS retido (E0625/E0627);
  proibido para não optante, MEI, regime especial e ISS por fora com convênio ativo;
- MEI e regime especial não podem ter ISS retido (E0583, E0588);
- MEI não informa tributos federais (E0676);
- totTrib: não optante usa pTotTrib (E0713), ME/EPP usa pTotTribSN (E0712), MEI usa indTotTrib (E0710).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from typing import Optional
from xml.etree import ElementTree as ET

from lxml import etree

from .config import RECURSOS, REGIMES, Empresa, so_digitos

NAMESPACE = "http://www.sped.fazenda.gov.br/nfse"
VERSAO_LEIAUTE = "1.01"
VER_APLIC = "emissor-nfse-0.1"
FUSO_BR = timezone(timedelta(hours=-3))

RETENCOES_ACEITAS = ("irrf", "pis", "cofins", "csll", "inss")

# (pis, cofins, csll) retidos -> tpRetPisCofins
TIPO_RET_PIS_COFINS = {
    (True, True, True): "3",
    (True, True, False): "4",
    (True, False, False): "5",
    (False, True, False): "6",
    (False, True, True): "7",
    (False, False, True): "8",
    (True, False, True): "9",
}

MOTIVOS_CANCELAMENTO = {1: "Erro na emissão", 2: "Serviço não prestado", 9: "Outros"}


class ErroNota(Exception):
    """Problema nos dados da nota, explicado em português para o usuário."""


def q2(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dec(valor) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    return Decimal(texto)


@lru_cache(maxsize=1)
def servicos_nacionais() -> dict[str, str]:
    lista = json.loads((RECURSOS / "servicos_nacionais.json").read_text(encoding="utf-8"))
    return {item["codigo"]: item["descricao"] for item in lista}


@lru_cache(maxsize=1)
def codigos_nbs() -> dict[str, str]:
    lista = json.loads((RECURSOS / "nbs.json").read_text(encoding="utf-8"))
    return {item["codigo"]: item["descricao"] for item in lista}


@lru_cache(maxsize=1)
def municipios() -> list[dict]:
    return json.loads((RECURSOS / "municipios.json").read_text(encoding="utf-8"))


def nome_municipio(codigo: int | None) -> str:
    if not codigo:
        return "-"
    for m in municipios():
        if m["codigo"] == int(codigo):
            return f"{m['nome']}/{m['uf']}"
    return str(codigo)


def validar_documento(doc: str) -> str:
    d = so_digitos(doc)
    if len(d) == 11:
        pesos1, pesos2 = range(10, 1, -1), range(11, 1, -1)
        dv1 = (sum(int(a) * b for a, b in zip(d[:9], pesos1)) * 10) % 11 % 10
        dv2 = (sum(int(a) * b for a, b in zip(d[:10], pesos2)) * 10) % 11 % 10
        if d == d[0] * 11 or d[-2:] != f"{dv1}{dv2}":
            raise ErroNota(f"CPF do tomador inválido: {doc}")
        return d
    if len(d) == 14:
        def dv(base: str) -> int:
            pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):]
            resto = sum(int(a) * b for a, b in zip(base, pesos)) % 11
            return 0 if resto < 2 else 11 - resto

        if d == d[0] * 14 or d[-2:] != f"{dv(d[:12])}{dv(d[:13])}":
            raise ErroNota(f"CNPJ do tomador inválido: {doc}")
        return d
    raise ErroNota(f"Documento do tomador precisa ser CPF (11 dígitos) ou CNPJ (14 dígitos): {doc}")


def competencia_para_data(texto: Optional[str], hoje: Optional[date] = None) -> date:
    """Aceita '2026-10', '10/2026', '2026-10-15' ou '15/10/2026'. Mês sem dia vira o último dia do mês (limitado a hoje)."""
    hoje = hoje or datetime.now(FUSO_BR).date()
    if not texto:
        return hoje
    t = texto.strip()
    try:
        if len(t) == 7 and t[4] == "-":
            ano, mes = int(t[:4]), int(t[5:])
        elif len(t) == 7 and t[2] == "/":
            mes, ano = int(t[:2]), int(t[3:])
        elif len(t) == 10 and t[4] == "-":
            d = date.fromisoformat(t)
            ano, mes = None, None
        elif len(t) == 10 and t[2] == "/":
            d = date(int(t[6:]), int(t[3:5]), int(t[:2]))
            ano, mes = None, None
        else:
            raise ValueError
        if ano is not None:
            if date(ano, mes, 1) > hoje:
                raise ErroNota("A competência não pode ser um mês futuro (regra E0015 do governo).")
            proximo = date(ano + (mes == 12), mes % 12 + 1, 1)
            d = min(proximo - timedelta(days=1), hoje)
    except ValueError as e:
        raise ErroNota(f"Competência não reconhecida: '{texto}'. Use mês/ano, por exemplo 10/2026.") from e
    if d > hoje:
        raise ErroNota("A competência não pode ser depois da data de hoje (regra E0015 do governo).")
    return d


@dataclass
class Tomador:
    documento: str
    nome: str
    email: Optional[str] = None


@dataclass
class DadosNota:
    tomador: Tomador
    valor: Decimal
    descricao: str
    competencia: date
    codigo_nacional: str
    codigo_municipal: Optional[str] = None
    nbs: Optional[str] = None
    municipio_prestacao: Optional[int] = None
    iss_retido: bool = False
    retencoes: dict[str, Decimal] = field(default_factory=dict)
    observacao: Optional[str] = None


@dataclass
class Calculo:
    retencoes_valor: dict[str, Decimal]
    aliquota_iss: Optional[Decimal]
    valor_iss_estimado: Optional[Decimal]
    total_retido_federal: Decimal
    valor_liquido_estimado: Decimal
    avisos: list[str]


def conferir_regras(empresa: Empresa, dados: DadosNota) -> Calculo:
    faltando = empresa.faltando()
    if faltando:
        raise ErroNota(
            f"O cadastro de {empresa.nome} está incompleto: falta {', '.join(faltando)}. "
            "Complete com configurar_empresa antes de emitir."
        )
    if dados.valor <= 0:
        raise ErroNota("O valor do serviço precisa ser maior que zero.")
    if not dados.descricao or len(dados.descricao) > 2000:
        raise ErroNota("A descrição do serviço é obrigatória e tem no máximo 2000 caracteres.")
    if dados.codigo_nacional not in servicos_nacionais():
        raise ErroNota(
            f"O código de serviço {dados.codigo_nacional} não existe na lista nacional. Use buscar_codigo para achar o certo."
        )
    if dados.nbs and dados.nbs not in codigos_nbs():
        raise ErroNota(f"O código NBS {dados.nbs} não existe na tabela oficial.")
    if dados.observacao and len(dados.observacao) > 2000:
        raise ErroNota("A observação tem no máximo 2000 caracteres.")

    regime = empresa.regime
    op_simp, reg_ap = REGIMES[regime]
    avisos: list[str] = []

    if dados.iss_retido and regime == "mei":
        raise ErroNota("MEI não pode ter ISS retido pelo tomador (regra E0583 do governo).")
    if dados.iss_retido and empresa.regime_especial != 0:
        raise ErroNota("Empresa com regime especial de tributação não pode ter ISS retido (regra E0588).")

    desconhecidas = set(dados.retencoes) - set(RETENCOES_ACEITAS)
    if desconhecidas:
        raise ErroNota(f"Retenções aceitas: {', '.join(RETENCOES_ACEITAS)}. Não reconheci: {', '.join(sorted(desconhecidas))}.")
    if dados.retencoes and regime == "mei":
        raise ErroNota("MEI não informa retenções federais na nota (regra E0676).")

    retencoes_valor: dict[str, Decimal] = {}
    for nome, percentual in dados.retencoes.items():
        p = dec(percentual)
        if p <= 0 or p >= 100:
            raise ErroNota(f"Percentual de {nome.upper()} inválido: {percentual}.")
        retencoes_valor[nome] = q2(dados.valor * p / 100)
    total_fed = sum(retencoes_valor.values(), Decimal("0"))
    if total_fed >= dados.valor:
        raise ErroNota("As retenções somam mais que o valor do serviço.")

    aliquota = None
    iss_estimado = None
    if dados.iss_retido and op_simp == "3" and reg_ap == "1":
        if empresa.aliquota_iss_simples is None:
            raise ErroNota(
                f"Com ISS retido, a nota de empresa do Simples precisa da alíquota de ISS da faixa atual. "
                f"Informe aliquota_iss_simples no cadastro de {empresa.nome}."
            )
        aliquota = dec(empresa.aliquota_iss_simples)
        if aliquota > 5:
            raise ErroNota("Alíquota de ISS acima de 5% não é aceita (regra E0595).")
        iss_estimado = q2(dados.valor * aliquota / 100)
    elif dados.iss_retido:
        avisos.append("ISS retido: o valor é calculado pelo sistema nacional com a alíquota cadastrada pela prefeitura.")

    liquido = dados.valor - total_fed - (iss_estimado or Decimal("0"))
    return Calculo(
        retencoes_valor=retencoes_valor,
        aliquota_iss=aliquota,
        valor_iss_estimado=iss_estimado,
        total_retido_federal=total_fed,
        valor_liquido_estimado=q2(liquido),
        avisos=avisos,
    )


def id_dps(empresa: Empresa, numero: int) -> str:
    return f"DPS{int(empresa.municipio_ibge):07d}2{empresa.cnpj}{int(empresa.serie):05d}{int(numero):015d}"


def agora_emissao() -> datetime:
    # Um minuto para trás evita a rejeição E0008 por diferença de relógio.
    return datetime.now(FUSO_BR).replace(microsecond=0) - timedelta(minutes=1)


def _sub(pai: ET.Element, nome: str, texto=None) -> ET.Element:
    el = ET.SubElement(pai, nome)
    if texto is not None:
        el.text = str(texto)
    return el


def _fmt(valor: Decimal) -> str:
    return f"{q2(valor):.2f}"


def montar_dps(
    empresa: Empresa,
    dados: DadosNota,
    numero: int,
    ambiente: str,
    dh_emissao: Optional[datetime] = None,
) -> str:
    calculo = conferir_regras(empresa, dados)
    dh = dh_emissao or agora_emissao()
    if dados.competencia > dh.date():
        raise ErroNota("A competência não pode ser depois da data de emissão.")
    op_simp, reg_ap = REGIMES[empresa.regime]

    raiz = ET.Element("DPS", {"versao": VERSAO_LEIAUTE, "xmlns": NAMESPACE})
    inf = ET.SubElement(raiz, "infDPS", {"Id": id_dps(empresa, numero)})
    _sub(inf, "tpAmb", "1" if ambiente == "producao" else "2")
    _sub(inf, "dhEmi", dh.strftime("%Y-%m-%dT%H:%M:%S-03:00"))
    _sub(inf, "verAplic", VER_APLIC)
    _sub(inf, "serie", empresa.serie)
    _sub(inf, "nDPS", int(numero))
    _sub(inf, "dCompet", dados.competencia.isoformat())
    _sub(inf, "tpEmit", "1")
    _sub(inf, "cLocEmi", f"{int(empresa.municipio_ibge):07d}")

    prest = _sub(inf, "prest")
    _sub(prest, "CNPJ", empresa.cnpj)
    if empresa.inscricao_municipal:
        im = empresa.inscricao_municipal.strip()
        _sub(prest, "IM", im.zfill(15) if im.isdigit() else im)
    reg = _sub(prest, "regTrib")
    _sub(reg, "opSimpNac", op_simp)
    if reg_ap:
        _sub(reg, "regApTribSN", reg_ap)
    _sub(reg, "regEspTrib", empresa.regime_especial)

    toma = _sub(inf, "toma")
    doc = validar_documento(dados.tomador.documento)
    _sub(toma, "CPF" if len(doc) == 11 else "CNPJ", doc)
    _sub(toma, "xNome", dados.tomador.nome.strip()[:300])
    if dados.tomador.email:
        _sub(toma, "email", dados.tomador.email.strip()[:80])

    serv = _sub(inf, "serv")
    loc = _sub(serv, "locPrest")
    _sub(loc, "cLocPrestacao", f"{int(dados.municipio_prestacao or empresa.municipio_ibge):07d}")
    cserv = _sub(serv, "cServ")
    _sub(cserv, "cTribNac", dados.codigo_nacional)
    if dados.codigo_municipal:
        _sub(cserv, "cTribMun", dados.codigo_municipal)
    _sub(cserv, "xDescServ", dados.descricao.strip())
    if dados.nbs:
        _sub(cserv, "cNBS", dados.nbs)
    if dados.observacao:
        compl = _sub(serv, "infoCompl")
        _sub(compl, "xInfComp", dados.observacao.strip())

    valores = _sub(inf, "valores")
    vserv = _sub(valores, "vServPrest")
    _sub(vserv, "vServ", _fmt(dados.valor))
    trib = _sub(valores, "trib")
    tmun = _sub(trib, "tribMun")
    _sub(tmun, "tribISSQN", "1")
    _sub(tmun, "tpRetISSQN", "2" if dados.iss_retido else "1")
    if calculo.aliquota_iss is not None:
        _sub(tmun, "pAliq", _fmt(calculo.aliquota_iss))

    r = calculo.retencoes_valor
    if r:
        tfed = _sub(trib, "tribFed")
        pis, cofins, csll = "pis" in r, "cofins" in r, "csll" in r
        if pis or cofins or csll:
            pc = _sub(tfed, "piscofins")
            _sub(pc, "CST", empresa.cst_pis_cofins)
            if pis or cofins:
                _sub(pc, "vBCPisCofins", _fmt(dados.valor))
            if pis:
                _sub(pc, "pAliqPis", _fmt(dec(dados.retencoes["pis"])))
            if cofins:
                _sub(pc, "pAliqCofins", _fmt(dec(dados.retencoes["cofins"])))
            if pis:
                _sub(pc, "vPis", _fmt(r["pis"]))
            if cofins:
                _sub(pc, "vCofins", _fmt(r["cofins"]))
            _sub(pc, "tpRetPisCofins", TIPO_RET_PIS_COFINS[(pis, cofins, csll)])
        if "inss" in r:
            _sub(tfed, "vRetCP", _fmt(r["inss"]))
        if "irrf" in r:
            _sub(tfed, "vRetIRRF", _fmt(r["irrf"]))
        if csll:
            _sub(tfed, "vRetCSLL", _fmt(r["csll"]))

    tot = _sub(trib, "totTrib")
    if op_simp == "1":
        p = _sub(tot, "pTotTrib")
        _sub(p, "pTotTribFed", _fmt(empresa.tributos_aproximados_federal))
        _sub(p, "pTotTribEst", _fmt(empresa.tributos_aproximados_estadual))
        _sub(p, "pTotTribMun", _fmt(empresa.tributos_aproximados_municipal))
    elif op_simp == "2":
        _sub(tot, "indTotTrib", "0")
    else:
        _sub(tot, "pTotTribSN", _fmt(dec(empresa.aliquota_simples_total)))

    return ET.tostring(raiz, encoding="unicode", xml_declaration=False)


def montar_cancelamento(empresa: Empresa, chave: str, codigo: int, motivo: str, ambiente: str) -> str:
    chave = so_digitos(chave)
    if len(chave) != 50:
        raise ErroNota("A chave de acesso da NFS-e tem 50 dígitos.")
    if codigo not in MOTIVOS_CANCELAMENTO:
        raise ErroNota("Motivo do cancelamento: 1 (erro na emissão), 2 (serviço não prestado) ou 9 (outros).")
    motivo = (motivo or "").strip()
    if not 15 <= len(motivo) <= 255:
        raise ErroNota("A justificativa do cancelamento precisa ter entre 15 e 255 caracteres.")
    raiz = ET.Element("pedRegEvento", {"versao": VERSAO_LEIAUTE, "xmlns": NAMESPACE})
    inf = ET.SubElement(raiz, "infPedReg", {"Id": f"PRE{chave}101101"})
    _sub(inf, "tpAmb", "1" if ambiente == "producao" else "2")
    _sub(inf, "verAplic", VER_APLIC)
    _sub(inf, "dhEvento", agora_emissao().strftime("%Y-%m-%dT%H:%M:%S-03:00"))
    _sub(inf, "CNPJAutor", empresa.cnpj)
    _sub(inf, "chNFSe", chave)
    ev = _sub(inf, "e101101")
    _sub(ev, "xDesc", "Cancelamento de NFS-e")
    _sub(ev, "cMotivo", codigo)
    _sub(ev, "xMotivo", motivo)
    return ET.tostring(raiz, encoding="unicode", xml_declaration=False)


@lru_cache(maxsize=2)
def _esquema(nome: str) -> etree.XMLSchema:
    caminho = RECURSOS / "xsd" / nome
    return etree.XMLSchema(etree.parse(str(caminho)))


def validar_xsd(xml: str, tipo: str = "dps") -> list[str]:
    """Confere o XML contra o esquema oficial. Devolve a lista de erros (vazia se estiver ok)."""
    esquema = _esquema("DPS_v1.01.xsd" if tipo == "dps" else "pedRegEvento_v1.01.xsd")
    dados = xml.encode("utf-8") if isinstance(xml, str) else xml
    if dados.startswith(b"<?xml"):
        dados = dados.split(b"?>", 1)[1].lstrip()
    doc = etree.fromstring(dados, etree.XMLParser(resolve_entities=False, no_network=True))
    if esquema.validate(doc):
        return []
    return [f"{e.message} (linha {e.line})" for e in esquema.error_log]
