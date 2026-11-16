"""Arquivo local das notas: leitura do XML da NFS-e, índice por empresa e histórico de ações."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from lxml import etree

from .config import gravar_json, ler_json, pasta_dados, so_digitos, trava


def _xp(raiz, caminho: str) -> Optional[str]:
    """Busca por nomes locais, ignorando o namespace. caminho no formato 'a/b/c' (descendente)."""
    partes = caminho.split("/")
    expr = ".//" + "/".join(f"*[local-name()='{p}']" for p in partes)
    achado = raiz.xpath(expr + "/text()")
    return achado[0].strip() if achado else None


def _parse(xml: str):
    dados = xml.encode("utf-8") if isinstance(xml, str) else xml
    if dados.lstrip().startswith(b"<?xml"):
        dados = dados.split(b"?>", 1)[1]
    return etree.fromstring(dados.strip(), etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))


def resumo_nfse(xml: str) -> dict:
    raiz = _parse(xml)
    inf = raiz.xpath(".//*[local-name()='infNFSe']")
    chave = None
    if inf:
        chave = so_digitos(inf[0].get("Id") or "") or None
    toma_doc = _xp(raiz, "toma/CNPJ") or _xp(raiz, "toma/CPF")
    return {
        "chave": chave,
        "numero": _xp(raiz, "infNFSe/nNFSe"),
        "emitida_em": _xp(raiz, "infNFSe/dhProc") or _xp(raiz, "infDPS/dhEmi"),
        "competencia": _xp(raiz, "infDPS/dCompet"),
        "prestador_cnpj": _xp(raiz, "emit/CNPJ") or _xp(raiz, "prest/CNPJ"),
        "prestador_nome": _xp(raiz, "emit/xNome"),
        "tomador_documento": toma_doc,
        "tomador_nome": _xp(raiz, "toma/xNome"),
        "servico_codigo": _xp(raiz, "cServ/cTribNac"),
        "servico_descricao": _xp(raiz, "cServ/xDescServ"),
        "valor": _xp(raiz, "vServPrest/vServ"),
        "base_calculo": _xp(raiz, "infNFSe/valores/vBC"),
        "aliquota_iss": _xp(raiz, "infNFSe/valores/pAliqAplic"),
        "valor_iss": _xp(raiz, "infNFSe/valores/vISSQN"),
        "total_retido": _xp(raiz, "infNFSe/valores/vTotalRet"),
        "valor_liquido": _xp(raiz, "infNFSe/valores/vLiq"),
        "iss_retido": _xp(raiz, "tribMun/tpRetISSQN") in ("2", "3"),
        "ambiente": "producao" if _xp(raiz, "infNFSe/ambGer") == "1" or _xp(raiz, "infDPS/tpAmb") == "1" else "homologacao",
    }


def resumo_evento(xml: str) -> dict:
    raiz = _parse(xml)
    cancelamento = bool(raiz.xpath(".//*[local-name()='e101101' or local-name()='e105102']"))
    return {
        "chave": _xp(raiz, "chNFSe"),
        "cancelamento": cancelamento,
        "data": _xp(raiz, "dhProc") or _xp(raiz, "dhEvento"),
    }


def pasta_notas(cnpj: str, ambiente: str) -> Path:
    p = pasta_dados() / "notas" / cnpj / ambiente
    p.mkdir(parents=True, exist_ok=True)
    return p


def _indice_arquivo(cnpj: str, ambiente: str) -> Path:
    return pasta_notas(cnpj, ambiente) / "indice.json"


def carregar_indice(cnpj: str, ambiente: str) -> dict[str, dict]:
    return ler_json(_indice_arquivo(cnpj, ambiente), {})


def guardar_nota(cnpj: str, ambiente: str, xml: str, origem: str) -> dict:
    resumo = resumo_nfse(xml)
    chave = resumo["chave"]
    if not chave:
        raise ValueError("XML da NFS-e sem chave de acesso.")
    resumo["papel"] = "emitida" if resumo.get("prestador_cnpj") == cnpj else "recebida"
    mes = (resumo.get("competencia") or resumo.get("emitida_em") or "0000-00")[:7]
    pasta = pasta_notas(cnpj, ambiente) / resumo["papel"] / mes
    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"{resumo.get('numero') or chave}-{chave[-8:]}"
    (pasta / f"{nome}.xml").write_text(xml, encoding="utf-8")
    resumo["arquivo_xml"] = str((pasta / f"{nome}.xml").relative_to(pasta_dados()))
    resumo["origem"] = origem
    with trava(f"indice-{cnpj}"):
        indice = carregar_indice(cnpj, ambiente)
        anterior = indice.get(chave, {})
        resumo["situacao"] = anterior.get("situacao", "autorizada")
        for campo in ("arquivo_pdf", "cancelada_em"):
            if anterior.get(campo):
                resumo[campo] = anterior[campo]
        indice[chave] = resumo
        gravar_json(_indice_arquivo(cnpj, ambiente), indice)
    return resumo


def atualizar_nota(cnpj: str, ambiente: str, chave: str, **campos) -> None:
    with trava(f"indice-{cnpj}"):
        indice = carregar_indice(cnpj, ambiente)
        item = indice.setdefault(chave, {"chave": chave})
        item.update(campos)
        gravar_json(_indice_arquivo(cnpj, ambiente), indice)


def caminho_pdf(cnpj: str, ambiente: str, chave: str) -> Path:
    item = carregar_indice(cnpj, ambiente).get(chave, {})
    if item.get("arquivo_xml"):
        return (pasta_dados() / item["arquivo_xml"]).with_suffix(".pdf")
    pasta = pasta_notas(cnpj, ambiente) / "pdf"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / f"{chave}.pdf"


def filtrar(
    cnpj: str, ambiente: str, de: Optional[str], ate: Optional[str], papel: Optional[str]
) -> list[dict]:
    itens = []
    for item in carregar_indice(cnpj, ambiente).values():
        data = (item.get("competencia") or item.get("emitida_em") or "")[:10]
        if de and data < de:
            continue
        if ate and data > ate:
            continue
        if papel and item.get("papel") != papel:
            continue
        itens.append(item)
    itens.sort(key=lambda i: (i.get("emitida_em") or "", i.get("numero") or ""))
    return itens


def somar(itens: list[dict], campo: str = "valor") -> Decimal:
    total = Decimal("0")
    for i in itens:
        if i.get("situacao") == "cancelada":
            continue
        try:
            total += Decimal(str(i.get(campo) or "0"))
        except Exception:
            pass
    return total


# Histórico de ações (auditoria): uma linha JSON por ação.

def registrar_historico(acao: str, **dados) -> None:
    from .acesso import pessoa_atual

    linha = {"quando": datetime.now().isoformat(timespec="seconds"), "acao": acao, "quem": pessoa_atual.get().nome, **dados}
    arquivo = pasta_dados() / "historico.jsonl"
    with trava("historico"):
        with open(arquivo, "a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")


def ler_historico(limite: int = 200) -> list[dict]:
    arquivo = pasta_dados() / "historico.jsonl"
    if not arquivo.exists():
        return []
    linhas = arquivo.read_text(encoding="utf-8").splitlines()[-limite:]
    saida = []
    for l in linhas:
        try:
            saida.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return saida
