"""Regras de uso do conector: cadastro, prévia, emissão, consulta, cancelamento e acompanhamento.

Cada função devolve texto em português, pronto para o Claude mostrar ao usuário.
"""

from __future__ import annotations

import os
import secrets
import shutil
from contextlib import nullcontext
from dataclasses import asdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from pynfse_nacional.exceptions import NFSeError

from . import certificado as cert
from .acesso import exigir_admin, modo_servidor, pessoa_atual
from .config import (
    AMBIENTE_TEXTO,
    REGIME_ESPECIAL_TEXTO,
    REGIME_TEXTO,
    Empresa,
    ServicoPadrao,
    achar_empresa,
    carregar_empresas,
    CHAVE_COFRE_ESCRITORIO,
    carregar_escritorio,
    salvar_escritorio,
    formatar_cnpj,
    gravar_json,
    incrementar_notas_teste,
    ler_json,
    normalizar,
    pasta_dados,
    reais,
    salvar_empresa,
    so_digitos,
    trava,
)
from .dps import (
    MOTIVOS_CANCELAMENTO,
    DadosNota,
    ErroNota,
    Tomador,
    codigos_nbs,
    competencia_para_data,
    conferir_regras,
    dec,
    id_dps,
    montar_cancelamento,
    montar_dps,
    municipios,
    nome_municipio,
    servicos_nacionais,
    validar_documento,
    validar_xsd,
)
from .notas import (
    _preparar_xml_nota,
    aplicar_lote,
    atualizar_nota,
    carregar_indice,
    caminho_pdf,
    filtrar,
    guardar_nota,
    ler_historico,
    registrar_historico,
    resumo_evento,
    somar,
)
from .sefin import ClienteSefin

VALIDADE_RASCUNHO = timedelta(hours=2)


def _fabrica_padrao(empresa: Empresa, senha: str) -> ClienteSefin:
    return ClienteSefin(
        str(empresa.caminho_certificado()),
        senha,
        empresa.ambiente,
        cnpj_consulta=empresa.cnpj if empresa.usa_procuracao else None,
    )


# Os testes trocam esta fábrica por uma versão falsa, sem internet.
fabrica_cliente: Callable[[Empresa, str], ClienteSefin] = _fabrica_padrao


def _cliente(empresa: Empresa, ambiente: Optional[str] = None) -> ClienteSefin:
    senha = cert.ler_senha(empresa.chave_cofre())
    if not senha:
        dono = "do escritório" if empresa.usa_procuracao else f"de {empresa.nome}"
        raise ErroNota(f"A senha do certificado {dono} não está guardada no computador do conector. Cadastre o certificado de novo.")
    if ambiente and ambiente != empresa.ambiente:
        empresa = empresa.model_copy(update={"ambiente": ambiente})
    return fabrica_cliente(empresa, senha)


def _sessao(cliente):
    return cliente if hasattr(cliente, "__enter__") else nullcontext(cliente)


def _chave_50(chave: str) -> str:
    d = so_digitos(chave)
    if len(d) != 50:
        raise ErroNota("A chave de acesso da NFS-e tem 50 dígitos.")
    return d


def validade_certificado(e: Empresa) -> Optional[str]:
    if e.usa_procuracao:
        esc = carregar_escritorio()
        return esc.get("validade") if esc else None
    return e.certificado_validade


def _so_no_computador_central() -> None:
    if modo_servidor.get():
        raise ErroNota(
            "Certificados são cadastrados no computador central, para a senha nunca trafegar pela rede. "
            "Lá, rode: uv run emissor-nfse-admin certificado <arquivo ou pasta>"
        )


def _erro_api(e: Exception) -> str:
    codigo = getattr(e, "code", None) or getattr(e, "codigo", None)
    texto = str(e)
    return f"{texto} (código {codigo})" if codigo and str(codigo) not in texto else texto


# ---------------------------------------------------------------- cadastro

def cadastrar_certificado(caminho: str, apelido: Optional[str] = None, senha: Optional[str] = None) -> str:
    _so_no_computador_central()
    exigir_admin("Cadastrar certificado")
    origem = Path(os.path.expanduser(caminho.strip().strip("'\"")))
    if not origem.exists():
        raise ErroNota(f"Não encontrei o arquivo {origem}.")
    if origem.suffix.lower() not in (".pfx", ".p12"):
        raise ErroNota("O certificado A1 é um arquivo .pfx ou .p12.")
    conteudo = origem.read_bytes()
    if senha is None:
        senha = cert.pedir_senha_em_janela(f"Senha do certificado {origem.name}")
        if senha is None:
            raise ErroNota(
                "A janela de senha foi cancelada ou não abriu. Se preferir, rode no terminal: "
                "uv run emissor-nfse-cadastro"
            )
    info = cert.ler_certificado(conteudo, senha)

    destino = pasta_dados() / "certificados" / f"{info.cnpj}.pfx"
    destino.parent.mkdir(parents=True, exist_ok=True)
    if origem.resolve() != destino.resolve():
        shutil.copyfile(origem, destino)
    os.chmod(destino, 0o600)
    cert.guardar_senha(info.cnpj, senha)

    existentes = carregar_empresas()
    if info.cnpj in existentes:
        empresa = existentes[info.cnpj]
        empresa.certificado_arquivo = f"certificados/{info.cnpj}.pfx"
        empresa.certificado_validade = info.validade.date().isoformat()
        if apelido:
            empresa.apelido = apelido
        acao = "Certificado atualizado"
    else:
        empresa = Empresa(
            cnpj=info.cnpj,
            razao_social=info.razao_social,
            apelido=apelido,
            certificado_arquivo=f"certificados/{info.cnpj}.pfx",
            certificado_validade=info.validade.date().isoformat(),
        )
        acao = "Empresa cadastrada"
    salvar_empresa(empresa)
    registrar_historico("cadastro_certificado", cnpj=info.cnpj, validade=empresa.certificado_validade)

    linhas = [
        f"{acao}: {empresa.razao_social} ({formatar_cnpj(empresa.cnpj)}).",
        f"Certificado válido até {info.validade.strftime('%d/%m/%Y')} ({info.dias_restantes} dias). Senha guardada no cofre do computador.",
        f"Ambiente: {AMBIENTE_TEXTO[empresa.ambiente]}.",
    ]
    falta = empresa.faltando()
    if falta:
        linhas.append(f"Para emitir ainda falta: {', '.join(falta)}. Use configurar_empresa.")
    return "\n".join(linhas)


def importar_pasta_certificados(pasta: str) -> str:
    _so_no_computador_central()
    base = Path(os.path.expanduser(pasta.strip().strip("'\"")))
    if not base.is_dir():
        raise ErroNota(f"{base} não é uma pasta.")
    arquivos = sorted(p for p in base.iterdir() if p.is_file() and p.suffix.lower() in (".pfx", ".p12"))
    if not arquivos:
        return "Nenhum arquivo .pfx ou .p12 nessa pasta."
    resultados = []
    for arq in arquivos:
        try:
            resultados.append(cadastrar_certificado(str(arq)).splitlines()[0])
        except (ErroNota, cert.CertificadoInvalido) as e:
            resultados.append(f"{arq.name}: não cadastrado ({e})")
    return "\n".join(resultados)


def cadastrar_certificado_escritorio(caminho: str, senha: Optional[str] = None) -> str:
    """Certificado A1 do próprio escritório, usado para os clientes que deram procuração eletrônica."""
    _so_no_computador_central()
    exigir_admin("Cadastrar o certificado do escritório")
    origem = Path(os.path.expanduser(caminho.strip().strip("'\"")))
    if not origem.exists():
        raise ErroNota(f"Não encontrei o arquivo {origem}.")
    if senha is None:
        senha = cert.pedir_senha_em_janela(f"Senha do certificado do ESCRITÓRIO ({origem.name})")
        if senha is None:
            raise ErroNota("A janela de senha foi cancelada ou não abriu.")
    info = cert.ler_certificado(origem.read_bytes(), senha)
    destino = pasta_dados() / "certificados" / "escritorio.pfx"
    destino.parent.mkdir(parents=True, exist_ok=True)
    if origem.resolve() != destino.resolve():
        shutil.copyfile(origem, destino)
    os.chmod(destino, 0o600)
    cert.guardar_senha(CHAVE_COFRE_ESCRITORIO, senha)
    salvar_escritorio(
        {
            "cnpj": info.cnpj,
            "razao_social": info.razao_social,
            "certificado_arquivo": "certificados/escritorio.pfx",
            "validade": info.validade.date().isoformat(),
        }
    )
    registrar_historico("cadastro_certificado_escritorio", cnpj=info.cnpj, validade=info.validade.date().isoformat())
    return (
        f"Certificado do escritório cadastrado: {info.razao_social} ({formatar_cnpj(info.cnpj)}), "
        f"válido até {info.validade.strftime('%d/%m/%Y')}. Ele passa a valer para todos os clientes marcados como procuração."
    )


def cadastrar_cliente_procuracao(cnpj: str, razao_social: str, apelido: Optional[str] = None) -> str:
    """Cliente que deu procuração eletrônica ao escritório: não precisa do certificado dele."""
    exigir_admin("Cadastrar cliente por procuração")
    doc = so_digitos(cnpj)
    if len(doc) != 14:
        raise ErroNota("Informe o CNPJ do cliente (14 dígitos).")
    validar_documento(doc)
    existentes = carregar_empresas()
    if doc in existentes:
        e = existentes[doc]
        e.usa_procuracao = True
        if apelido:
            e.apelido = apelido
        acao = "Cliente passou a emitir por procuração"
    else:
        e = Empresa(cnpj=doc, razao_social=razao_social.strip(), apelido=apelido, usa_procuracao=True)
        acao = "Cliente cadastrado por procuração"
    salvar_empresa(e)
    registrar_historico("cadastro_procuracao", cnpj=doc)
    linhas = [f"{acao}: {e.razao_social} ({formatar_cnpj(doc)})."]
    if not carregar_escritorio():
        linhas.append("Atenção: o certificado do escritório ainda não está cadastrado no computador central.")
    linhas.append(
        "Lembrete: o cliente precisa ter dado a procuração no portal da Receita (servicos.receitafederal.gov.br, "
        "Controle de Acesso, só o serviço NFS-e) e o escritório precisa ter confirmado em Recebidas."
    )
    falta = e.faltando()
    if falta:
        linhas.append(f"Para emitir ainda falta: {', '.join(falta)}.")
    return "\n".join(linhas)


def _resolver_municipio(valor) -> int:
    texto = str(valor).strip()
    if so_digitos(texto) == texto and len(texto) == 7:
        return int(texto)
    uf = None
    for sep in ("/", " - ", "-", ","):
        if sep in texto:
            nome, _, possivel_uf = texto.rpartition(sep)
            if len(possivel_uf.strip()) == 2:
                texto, uf = nome, possivel_uf.strip().upper()
            break
    alvo = normalizar(texto)
    achados = [m for m in municipios() if normalizar(m["nome"]) == alvo and (uf is None or m["uf"] == uf)]
    if len(achados) == 1:
        return achados[0]["codigo"]
    if not achados:
        raise ErroNota(f"Não achei o município '{valor}'. Use buscar_codigo com tipo municipio.")
    opcoes = ", ".join(f"{m['nome']}/{m['uf']}" for m in achados)
    raise ErroNota(f"Há mais de um município com esse nome: {opcoes}. Informe com a UF.")


CAMPOS_CONFIGURAVEIS = {
    "apelido",
    "municipio",
    "municipio_ibge",
    "inscricao_municipal",
    "regime",
    "regime_especial",
    "aliquota_simples_total",
    "aliquota_iss_simples",
    "tributos_aproximados_federal",
    "tributos_aproximados_estadual",
    "tributos_aproximados_municipal",
    "cst_pis_cofins",
    "serie",
    "usa_procuracao",
    "codigo_servico",
    "codigo_municipal",
    "nbs",
    "descricao_padrao",
}


def configurar_empresa(empresa_ref: str, campos: dict) -> str:
    exigir_admin("Alterar o cadastro fiscal de uma empresa")
    empresa = achar_empresa(empresa_ref)
    desconhecidos = set(campos) - CAMPOS_CONFIGURAVEIS
    if desconhecidos:
        raise ErroNota(
            f"Campos que não posso alterar por aqui: {', '.join(sorted(desconhecidos))}. "
            f"Campos aceitos: {', '.join(sorted(CAMPOS_CONFIGURAVEIS))}."
        )
    dados = empresa.model_dump()
    servico = dict(dados.get("servico_padrao") or {})
    try:
        for chave, valor in campos.items():
            if valor == "":
                valor = None
            if chave == "municipio":
                dados["municipio_ibge"] = _resolver_municipio(valor) if valor is not None else None
            elif chave == "codigo_servico":
                servico["codigo_nacional"] = valor
            elif chave == "codigo_municipal":
                servico["codigo_municipal"] = valor
            elif chave == "nbs":
                servico["nbs"] = valor
            elif chave == "descricao_padrao":
                servico["descricao"] = valor
            elif chave in ("aliquota_simples_total", "aliquota_iss_simples") or chave.startswith("tributos_aproximados"):
                dados[chave] = dec(valor) if valor is not None else None
            else:
                dados[chave] = valor
        dados["servico_padrao"] = ServicoPadrao(**servico)
        nova = Empresa(**dados)
    except ErroNota:
        raise
    except Exception as e:
        raise ErroNota(f"Não salvei: {e}") from e
    cod = nova.servico_padrao.codigo_nacional
    if cod and cod not in servicos_nacionais():
        raise ErroNota(f"O código de serviço {cod} não existe na lista nacional.")
    salvar_empresa(nova)
    registrar_historico("configuracao", cnpj=nova.cnpj, campos=list(campos))
    return "Cadastro atualizado.\n\n" + ver_empresa(nova.cnpj)


def ver_empresa(empresa_ref: str) -> str:
    e = achar_empresa(empresa_ref)
    sp = e.servico_padrao
    linhas = [
        f"{e.razao_social}" + (f" ({e.apelido})" if e.apelido else ""),
        f"CNPJ: {formatar_cnpj(e.cnpj)}",
        f"Ambiente: {AMBIENTE_TEXTO[e.ambiente]}",
        f"Município: {nome_municipio(e.municipio_ibge)}" + (f" (IBGE {e.municipio_ibge})" if e.municipio_ibge else ""),
        f"Inscrição municipal: {e.inscricao_municipal or 'não informada'}",
        f"Regime: {REGIME_TEXTO.get(e.regime, 'não informado') if e.regime else 'não informado'}",
        f"Regime especial: {REGIME_ESPECIAL_TEXTO[e.regime_especial]}",
    ]
    if e.regime and e.regime.startswith("simples"):
        linhas.append(f"Tributos aproximados do Simples: {e.aliquota_simples_total if e.aliquota_simples_total is not None else 'não informado'}%")
        linhas.append(f"Alíquota de ISS do Simples (só para ISS retido): {e.aliquota_iss_simples if e.aliquota_iss_simples is not None else 'não informada'}%")
    if sp.codigo_nacional:
        linhas.append(f"Serviço padrão: {sp.codigo_nacional} {servicos_nacionais().get(sp.codigo_nacional, '')}")
    if sp.descricao:
        linhas.append(f"Descrição padrão: {sp.descricao}")
    if sp.codigo_municipal:
        linhas.append(f"Código municipal: {sp.codigo_municipal}")
    if sp.nbs:
        linhas.append(f"NBS: {sp.nbs}")
    linhas.append(f"Série das notas pelo Claude: {e.serie}")
    validade = validade_certificado(e)
    origem = "do escritório, por procuração" if e.usa_procuracao else "A1 da própria empresa"
    if validade:
        dias = (date.fromisoformat(validade) - date.today()).days
        linhas.append(f"Certificado {origem}, válido até {date.fromisoformat(validade).strftime('%d/%m/%Y')} ({dias} dias)")
    else:
        linhas.append(f"Certificado {origem}: não cadastrado")
    falta = e.faltando()
    linhas.append("Pronta para emitir." if not falta else f"Falta para emitir: {', '.join(falta)}")
    return "\n".join(linhas)


def listar_empresas() -> str:
    empresas = carregar_empresas()
    if not empresas:
        return "Nenhuma empresa cadastrada. Comece com cadastrar_certificado (ou importar_pasta_certificados)."
    linhas = [f"{len(empresas)} empresa(s):"]
    for e in sorted(empresas.values(), key=lambda x: x.nome.lower()):
        dias = ""
        validade = validade_certificado(e)
        if validade:
            d = (date.fromisoformat(validade) - date.today()).days
            dias = f", certificado {d} dias" if d >= 0 else ", CERTIFICADO VENCIDO"
        if e.usa_procuracao:
            dias += ", por procuração"
        estado = "pronta" if not e.faltando() else "cadastro incompleto"
        amb = "produção" if e.ambiente == "producao" else "teste"
        linhas.append(f"- {e.nome} ({formatar_cnpj(e.cnpj)}): {estado}, {amb}{dias}")
    return "\n".join(linhas)


def alterar_ambiente(empresa_ref: str, ambiente: str) -> str:
    exigir_admin("Mudar uma empresa entre teste e produção")
    e = achar_empresa(empresa_ref)
    if ambiente not in ("homologacao", "producao"):
        raise ErroNota("Ambiente deve ser 'homologacao' (teste) ou 'producao'.")
    if ambiente == "producao" and e.notas_teste_ok < 1:
        raise ErroNota(
            f"{e.nome} ainda não tem nenhuma nota de teste aceita pelo governo. "
            "Emita ao menos uma nota no ambiente de teste antes de liberar a produção."
        )
    e.ambiente = ambiente
    salvar_empresa(e)
    registrar_historico("ambiente", cnpj=e.cnpj, ambiente=ambiente)
    return f"{e.nome} agora está em {AMBIENTE_TEXTO[ambiente]}."


def buscar_codigo(texto: str, tipo: str = "servico") -> str:
    palavras = [p for p in normalizar(texto).split() if len(p) > 1]
    if tipo == "municipio":
        itens = [(f"{m['codigo']}", f"{m['nome']}/{m['uf']}") for m in municipios()]
    elif tipo == "nbs":
        itens = list(codigos_nbs().items())
    else:
        itens = list(servicos_nacionais().items())
    dig = so_digitos(texto)
    achados = []
    for codigo, descricao in itens:
        alvo = normalizar(descricao)
        if dig and codigo.startswith(dig):
            achados.append((0, codigo, descricao))
        elif palavras and all(p in alvo for p in palavras):
            achados.append((1, codigo, descricao))
    if not achados and palavras:
        for codigo, descricao in itens:
            alvo = normalizar(descricao)
            pontos = sum(p in alvo for p in palavras)
            if pontos:
                achados.append((10 - pontos, codigo, descricao))
    achados.sort()
    if not achados:
        return "Nada encontrado. Tente outras palavras."
    return "\n".join(f"{c}  {d}" for _, c, d in achados[:15])


# ---------------------------------------------------------------- rascunhos

def _pasta_rascunhos() -> Path:
    p = pasta_dados() / "rascunhos"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _novo_id(prefixo: str) -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return prefixo + "".join(secrets.choice(alfabeto) for _ in range(6))


def _salvar_rascunho(r: dict) -> None:
    gravar_json(_pasta_rascunhos() / f"{r['id']}.json", r)


def _ler_rascunho(rid: str) -> dict:
    r = ler_json(_pasta_rascunhos() / f"{rid.strip().upper()}.json", None)
    if not r:
        raise ErroNota(f"Não achei a prévia {rid}. Prepare a nota de novo.")
    return r


def _tomadores() -> dict:
    return ler_json(pasta_dados() / "tomadores.json", {})


def _lembrar_tomador(cnpj_empresa: str, tomador: Tomador) -> None:
    with trava("tomadores"):
        dados = _tomadores()
        dados.setdefault(cnpj_empresa, {})[tomador.documento] = {
            "nome": tomador.nome,
            "email": tomador.email,
            "ultimo_uso": date.today().isoformat(),
        }
        gravar_json(pasta_dados() / "tomadores.json", dados)


def buscar_tomador(empresa_ref: str, texto: str) -> str:
    e = achar_empresa(empresa_ref)
    lista = _tomadores().get(e.cnpj, {})
    alvo = normalizar(texto)
    dig = so_digitos(texto)
    achados = [
        (doc, t)
        for doc, t in lista.items()
        if (dig and doc.startswith(dig)) or (alvo and alvo in normalizar(t.get("nome", "")))
    ]
    if not achados:
        return f"Nenhum tomador parecido já usado por {e.nome}. Peça o CPF ou CNPJ e o nome."
    return "\n".join(f"{formatar_cnpj(doc)}  {t['nome']}" for doc, t in achados[:10])


def _dados_nota_de(r: dict) -> DadosNota:
    d = r["dados"]
    return DadosNota(
        tomador=Tomador(**d["tomador"]),
        valor=Decimal(d["valor"]),
        descricao=d["descricao"],
        competencia=date.fromisoformat(d["competencia"]),
        codigo_nacional=d["codigo_nacional"],
        codigo_municipal=d.get("codigo_municipal"),
        nbs=d.get("nbs"),
        municipio_prestacao=d.get("municipio_prestacao"),
        iss_retido=d.get("iss_retido", False),
        retencoes={k: Decimal(v) for k, v in (d.get("retencoes") or {}).items()},
        observacao=d.get("observacao"),
    )


# ---------------------------------------------------------------- emissão

def preparar_nota(
    empresa_ref: str,
    tomador_documento: str,
    valor,
    tomador_nome: Optional[str] = None,
    descricao: Optional[str] = None,
    competencia: Optional[str] = None,
    codigo_servico: Optional[str] = None,
    codigo_municipal: Optional[str] = None,
    nbs: Optional[str] = None,
    municipio_prestacao: Optional[str] = None,
    iss_retido: bool = False,
    retencoes: Optional[dict] = None,
    tomador_email: Optional[str] = None,
    observacao: Optional[str] = None,
) -> str:
    e = achar_empresa(empresa_ref)
    doc = validar_documento(tomador_documento)
    conhecido = _tomadores().get(e.cnpj, {}).get(doc, {})
    nome = (tomador_nome or conhecido.get("nome") or "").strip()
    if not nome:
        raise ErroNota("Preciso do nome ou razão social do tomador (primeira nota para esse documento).")
    sp = e.servico_padrao
    codigo = so_digitos(codigo_servico) if codigo_servico else sp.codigo_nacional
    if not codigo:
        raise ErroNota(
            f"Qual o código do serviço? {e.nome} não tem serviço padrão. Use buscar_codigo para achar o código nacional."
        )
    desc = (descricao or sp.descricao or "").strip()
    if not desc:
        raise ErroNota("Preciso da descrição do serviço (o texto que sai na nota).")
    try:
        valor_dec = dec(valor)
    except Exception as ex:
        raise ErroNota(f"Valor não reconhecido: {valor}") from ex

    retencoes_dec: dict[str, Decimal] = {}
    for k, v in (retencoes or {}).items():
        if v in (None, "", 0, "0"):
            continue
        try:
            retencoes_dec[k.lower()] = dec(v)
        except Exception as ex:
            raise ErroNota(f"Percentual de {k.upper()} não reconhecido: {v}") from ex

    dados = DadosNota(
        tomador=Tomador(documento=doc, nome=nome, email=tomador_email or conhecido.get("email")),
        valor=valor_dec,
        descricao=desc,
        competencia=competencia_para_data(competencia),
        codigo_nacional=codigo,
        codigo_municipal=codigo_municipal or (sp.codigo_municipal if not codigo_servico else None),
        nbs=so_digitos(nbs) if nbs else (sp.nbs if not codigo_servico else None),
        municipio_prestacao=_resolver_municipio(municipio_prestacao) if municipio_prestacao else None,
        iss_retido=bool(iss_retido),
        retencoes=retencoes_dec,
        observacao=observacao,
    )
    calculo = conferir_regras(e, dados)

    # Ensaio: monta o XML com um número qualquer e confere contra o esquema oficial.
    erros = validar_xsd(montar_dps(e, dados, 1, e.ambiente))
    if erros:
        raise ErroNota("A nota ficou fora do padrão oficial: " + "; ".join(erros[:3]))

    aliquota_prefeitura = None
    if e.ambiente == "producao" and e.regime in ("normal", "simples_iss_fora", "simples_tudo_fora"):
        try:
            aliquota_prefeitura = _cliente(e).aliquota_municipal(
                dados.municipio_prestacao or e.municipio_ibge, dados.codigo_nacional
            )
        except Exception:
            aliquota_prefeitura = None

    rid = _novo_id("N")
    dados_json = asdict(dados)
    dados_json["valor"] = str(dados.valor)
    dados_json["competencia"] = dados.competencia.isoformat()
    dados_json["retencoes"] = {k: str(v) for k, v in dados.retencoes.items()}
    _salvar_rascunho(
        {
            "id": rid,
            "tipo": "nota",
            "cnpj": e.cnpj,
            "ambiente": e.ambiente,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "status": "pendente",
            "dados": dados_json,
        }
    )

    l = []
    if e.ambiente == "homologacao":
        l.append("AMBIENTE DE TESTE: esta nota não tem valor fiscal.")
    else:
        l.append("PRODUÇÃO: esta nota é real e sai em nome da empresa.")
    l.append(f"Prévia {rid}")
    l.append(f"Prestador: {e.razao_social} ({formatar_cnpj(e.cnpj)})")
    l.append(f"Tomador: {nome} ({formatar_cnpj(doc)})")
    l.append(f"Serviço: {codigo} {servicos_nacionais()[codigo]}")
    l.append(f"Descrição na nota: {desc}")
    l.append(f"Competência: {dados.competencia.strftime('%d/%m/%Y')}")
    l.append(f"Local da prestação: {nome_municipio(dados.municipio_prestacao or e.municipio_ibge)}")
    l.append(f"Valor do serviço: {reais(dados.valor)}")
    if dados.iss_retido:
        if calculo.valor_iss_estimado is not None:
            l.append(f"ISS retido pelo tomador: {reais(calculo.valor_iss_estimado)} ({calculo.aliquota_iss}%)")
        else:
            l.append("ISS retido pelo tomador (valor calculado pelo sistema nacional)")
    elif e.regime == "simples":
        l.append("ISS: recolhido no DAS do Simples, não aparece destacado na nota")
    elif e.regime == "mei":
        l.append("ISS: recolhido no DAS do MEI")
    else:
        txt = f"{aliquota_prefeitura}% (cadastro da prefeitura)" if aliquota_prefeitura else "pela alíquota cadastrada pela prefeitura"
        l.append(f"ISS: calculado pelo sistema nacional, {txt}")
    for nome_ret, v in calculo.retencoes_valor.items():
        l.append(f"Retenção de {nome_ret.upper()}: {reais(v)} ({dados.retencoes[nome_ret]}%)")
    if calculo.retencoes_valor or calculo.valor_iss_estimado:
        l.append(f"Valor líquido estimado: {reais(calculo.valor_liquido_estimado)}")
    if observacao:
        l.append(f"Observação: {observacao}")
    l.extend(calculo.avisos)
    l.append("")
    l.append("Confira tomador, serviço e valor. A nota só será emitida depois do seu OK.")
    return "\n".join(l)


def emitir_nota(rascunho_id: str) -> str:
    r0 = _ler_rascunho(rascunho_id)
    if r0.get("tipo") != "nota":
        raise ErroNota("Esse código é de um cancelamento. Use cancelar_nota.")

    with trava(f"emissao-{r0['cnpj']}", espera_max=120):
        r = _ler_rascunho(rascunho_id)
        if r["status"] == "emitida":
            res = r.get("resultado", {})
            return f"Essa prévia já virou a nota {res.get('numero')} (chave {res.get('chave')}). Nada foi emitido de novo."
        if r["status"] in ("enviando", "desconhecido"):
            return _verificar_envio_travado(rascunho_id)
        if r["status"] != "pendente":
            raise ErroNota(f"Essa prévia já foi finalizada com situação '{r['status']}'. Prepare a nota de novo.")
        if datetime.now() - datetime.fromisoformat(r["criado_em"]) > VALIDADE_RASCUNHO:
            raise ErroNota("Essa prévia tem mais de 2 horas. Prepare a nota de novo para conferir os dados atualizados.")

        e = achar_empresa(r["cnpj"])
        if e.ambiente != r["ambiente"]:
            raise ErroNota("O ambiente da empresa mudou depois da prévia. Prepare a nota de novo.")
        dados = _dados_nota_de(r)
        cliente = _cliente(e)

        with _sessao(cliente):
            arquivo_num = pasta_dados() / "numeracao.json"
            chave_num = f"{e.cnpj}|{e.ambiente}|{e.serie}"
            with trava("numeracao"):
                numeracao = ler_json(arquivo_num, {})
                numero = int(numeracao.get(chave_num, 0)) + 1
            for _ in range(10):
                try:
                    ja_existe = cliente.has_nfse_by_dps(id_dps(e, numero))
                except Exception:
                    ja_existe = False
                if not ja_existe:
                    break
                numero += 1
            with trava("numeracao"):
                numeracao = ler_json(arquivo_num, {})
                numeracao[chave_num] = max(int(numeracao.get(chave_num, 0)), numero)
                gravar_json(arquivo_num, numeracao)

            def liberar_numero():
                with trava("numeracao"):
                    atual = ler_json(arquivo_num, {})
                    if int(atual.get(chave_num, 0)) == numero:
                        atual[chave_num] = numero - 1
                        gravar_json(arquivo_num, atual)

            try:
                xml = montar_dps(e, dados, numero, e.ambiente)
                assinado = cliente.assinar(xml)
            except Exception:
                liberar_numero()
                raise
            erros = validar_xsd(assinado)
            if erros:
                liberar_numero()
                raise ErroNota("A nota assinada ficou fora do padrão oficial: " + "; ".join(erros[:3]))

            identificador = id_dps(e, numero)
            r.update(status="enviando", numero_dps=numero, id_dps=identificador)
            _salvar_rascunho(r)

            try:
                resposta = cliente.enviar_dps(assinado)
            except NFSeError as ex:
                r["erro_envio"] = _erro_api(ex)
                r["status"] = "desconhecido"
                _salvar_rascunho(r)
                registrar_historico("emissao_sem_resposta", cnpj=e.cnpj, ambiente=e.ambiente, id_dps=identificador, erro=r["erro_envio"])
                return _verificar_envio_travado(rascunho_id, cliente=cliente)

            if not resposta.success:
                liberar_numero()
                r.update(status="recusada", erro=f"{resposta.error_code}: {resposta.error_message}")
                _salvar_rascunho(r)
                registrar_historico(
                    "emissao_recusada", cnpj=e.cnpj, ambiente=e.ambiente, codigo=resposta.error_code, mensagem=resposta.error_message
                )
                return (
                    "O sistema nacional recusou a nota. Nada foi emitido.\n"
                    f"Motivo: {resposta.error_message} (código {resposta.error_code})\n"
                    "Corrija o dado indicado e prepare a nota de novo."
                )

            return _concluir_emissao(e, r, resposta.chave_acesso, resposta.xml_nfse, resposta.nfse_number, cliente, dados)


def _concluir_emissao(e: Empresa, r: dict, chave: str, xml_nfse: Optional[str], numero, cliente, dados: DadosNota) -> str:
    ambiente = r.get("ambiente") or e.ambiente
    resumo = {}
    if xml_nfse:
        resumo = guardar_nota(e.cnpj, ambiente, xml_nfse, origem="claude")
    else:
        atualizar_nota(e.cnpj, ambiente, chave, numero=numero, situacao="autorizada", origem="claude")
    numero = resumo.get("numero") or numero
    r.update(status="emitida", resultado={"chave": chave, "numero": numero})
    _salvar_rascunho(r)
    _lembrar_tomador(e.cnpj, dados.tomador)
    if ambiente == "homologacao":
        incrementar_notas_teste(e.cnpj)
    registrar_historico(
        "emissao", cnpj=e.cnpj, ambiente=ambiente, numero=numero, chave=chave, valor=str(dados.valor), tomador=dados.tomador.documento
    )
    pdf = None
    try:
        if ambiente != e.ambiente:
            e = e.model_copy(update={"ambiente": ambiente})
        pdf = _obter_pdf(e, chave, cliente)
    except Exception:
        pass

    l = [("Nota de TESTE emitida" if ambiente == "homologacao" else "Nota emitida") + f": número {numero}"]
    l.append(f"Pedido por: {pessoa_atual.get().nome}")
    l.append(f"Chave de acesso: {chave}")
    if resumo.get("valor_iss"):
        l.append(f"ISS: {reais(resumo['valor_iss'])}" + (f" ({resumo['aliquota_iss']}%)" if resumo.get("aliquota_iss") else ""))
    if resumo.get("valor_liquido"):
        l.append(f"Valor líquido: {reais(resumo['valor_liquido'])}")
    if pdf:
        l.append(f"PDF (DANFSe): {pdf}")
    else:
        l.append("O PDF ainda não ficou disponível. Peça baixar_pdf daqui a pouco.")
    return "\n".join(l)


def _verificar_envio_travado(rascunho_id: str, cliente=None) -> str:
    r = _ler_rascunho(rascunho_id)
    if r["status"] == "emitida":
        res = r.get("resultado", {})
        return f"Nota {res.get('numero')} já emitida (chave {res.get('chave')})."
    if r["status"] not in ("enviando", "desconhecido"):
        return f"Essa prévia está com situação '{r['status']}'."
    e = achar_empresa(r["cnpj"])
    ambiente = r.get("ambiente") or e.ambiente
    if cliente is None:
        cliente = _cliente(e, ambiente=ambiente)
    with _sessao(cliente):
        resultado = cliente.recover_nfse_by_dps(r["id_dps"])
        if resultado.status == "success":
            q = resultado.result
            return _concluir_emissao(e, r, q.chave_acesso, q.xml_nfse, q.nfse_number, cliente, _dados_nota_de(r))
    if resultado.status == "processing":
        return (
            "O governo ainda não confirma a nota dessa prévia. Não emita de novo. "
            f"Peça para verificar o envio {r['id']} em alguns minutos."
        )
    return (
        "Não consegui confirmar com o governo se a nota foi gerada "
        f"({_erro_api(resultado.error) if resultado.error else 'sem detalhe'}). Não emita de novo; "
        f"tente verificar o envio {r['id']} mais tarde."
    )


def verificar_envio(rascunho_id: str) -> str:
    """Para quando o envio ficou sem resposta: pergunta ao governo se a nota foi gerada."""
    r = _ler_rascunho(rascunho_id)
    with trava(f"emissao-{r['cnpj']}", espera_max=120):
        return _verificar_envio_travado(rascunho_id)


# ---------------------------------------------------------------- consulta e PDF

def _obter_pdf(e: Empresa, chave: str, cliente=None) -> Optional[str]:
    destino = caminho_pdf(e.cnpj, e.ambiente, chave)
    if destino.exists():
        return str(destino)
    cliente = cliente or _cliente(e)
    conteudo = None
    try:
        conteudo = cliente.download_danfse(chave)
        if not conteudo.startswith(b"%PDF"):
            conteudo = None
    except Exception:
        conteudo = None
    if conteudo is None:
        item = carregar_indice(e.cnpj, e.ambiente).get(chave, {})
        if item.get("arquivo_xml"):
            from pynfse_nacional.pdf_generator import generate_danfse_from_xml

            xml = (pasta_dados() / item["arquivo_xml"]).read_text(encoding="utf-8")
            conteudo = generate_danfse_from_xml(xml_content=xml)
    if not conteudo:
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)
    atualizar_nota(e.cnpj, e.ambiente, chave, arquivo_pdf=str(destino.relative_to(pasta_dados())))
    return str(destino)


def baixar_pdf(empresa_ref: str, chave: str) -> str:
    e = achar_empresa(empresa_ref)
    caminho = _obter_pdf(e, _chave_50(chave))
    if not caminho:
        return "O governo não entregou o PDF agora e não tenho o XML dessa nota para gerar localmente. Tente mais tarde."
    return f"PDF salvo em: {caminho}"


def consultar_nota(empresa_ref: str, chave: str) -> str:
    e = achar_empresa(empresa_ref)
    chave = _chave_50(chave)
    cliente = _cliente(e)
    try:
        with _sessao(cliente):
            q = cliente.query_nfse(chave)
        cancelada = bool(q.status and "cancel" in q.status.lower())
        if q.xml_nfse:
            origem = carregar_indice(e.cnpj, e.ambiente).get(chave, {}).get("origem", "consulta")
            guardar_nota(e.cnpj, e.ambiente, q.xml_nfse, origem=origem)
            if cancelada:
                atualizar_nota(e.cnpj, e.ambiente, chave, situacao="cancelada")
        else:
            local = carregar_indice(e.cnpj, e.ambiente).get(chave)
            situacao = "cancelada" if cancelada else (q.status or "autorizada")
            if local:
                atualizar_nota(e.cnpj, e.ambiente, chave, situacao=situacao)
            else:
                emitida = q.data_emissao.isoformat() if hasattr(q.data_emissao, "isoformat") else str(q.data_emissao)
                atualizar_nota(
                    e.cnpj,
                    e.ambiente,
                    chave,
                    numero=q.nfse_number,
                    situacao=situacao,
                    emitida_em=emitida,
                    valor=str(q.valor_servicos),
                    prestador_cnpj=q.prestador_cnpj,
                    tomador_documento=q.tomador_documento,
                    papel="emitida" if q.prestador_cnpj == e.cnpj else "recebida",
                    origem="consulta",
                )
    except NFSeError as ex:
        local = carregar_indice(e.cnpj, e.ambiente).get(chave)
        if not local:
            raise ErroNota(f"Não consegui consultar a nota: {_erro_api(ex)}") from ex
    item = carregar_indice(e.cnpj, e.ambiente).get(chave, {})
    return _descrever_nota(item)


def _descrever_nota(item: dict) -> str:
    l = [f"Nota {item.get('numero') or '?'} ({item.get('situacao', 'autorizada')})"]
    if item.get("emitida_em"):
        l.append(f"Emitida em: {item['emitida_em'][:16].replace('T', ' ')}")
    if item.get("competencia"):
        l.append(f"Competência: {item['competencia']}")
    if item.get("prestador_nome") or item.get("prestador_cnpj"):
        l.append(f"Prestador: {item.get('prestador_nome') or ''} ({formatar_cnpj(item.get('prestador_cnpj') or '')})")
    l.append(f"Tomador: {item.get('tomador_nome') or '-'} ({formatar_cnpj(item.get('tomador_documento') or '')})")
    if item.get("servico_descricao"):
        l.append(f"Serviço: {item.get('servico_codigo') or ''} {item['servico_descricao'][:200]}")
    l.append(f"Valor: {reais(item.get('valor'))}")
    if item.get("valor_iss"):
        l.append(f"ISS: {reais(item['valor_iss'])}" + (" (retido)" if item.get("iss_retido") else ""))
    if item.get("valor_liquido"):
        l.append(f"Líquido: {reais(item['valor_liquido'])}")
    l.append(f"Chave: {item.get('chave')}")
    return "\n".join(l)


# ---------------------------------------------------------------- cancelamento

def preparar_cancelamento(empresa_ref: str, chave: str, motivo_codigo: int, justificativa: str) -> str:
    e = achar_empresa(empresa_ref)
    chave = so_digitos(chave)
    montar_cancelamento(e, chave, int(motivo_codigo), justificativa, e.ambiente)  # valida
    item = carregar_indice(e.cnpj, e.ambiente).get(chave)
    if item and item.get("situacao") == "cancelada":
        raise ErroNota("Essa nota já consta como cancelada.")
    rid = _novo_id("C")
    _salvar_rascunho(
        {
            "id": rid,
            "tipo": "cancelamento",
            "cnpj": e.cnpj,
            "ambiente": e.ambiente,
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "status": "pendente",
            "chave": chave,
            "motivo_codigo": int(motivo_codigo),
            "justificativa": justificativa.strip(),
        }
    )
    l = [("AMBIENTE DE TESTE. " if e.ambiente == "homologacao" else "PRODUÇÃO. ") + f"Prévia de cancelamento {rid}"]
    l.append(f"Empresa: {e.razao_social} ({formatar_cnpj(e.cnpj)})")
    if item:
        l.append(f"Nota {item.get('numero')}: {item.get('tomador_nome')} , {reais(item.get('valor'))}, competência {item.get('competencia')}")
    l.append(f"Chave: {chave}")
    l.append(f"Motivo: {MOTIVOS_CANCELAMENTO[int(motivo_codigo)]}. Justificativa: {justificativa.strip()}")
    l.append("")
    l.append("O cancelamento não pode ser desfeito. Só cancelo depois do seu OK.")
    return "\n".join(l)


def cancelar_nota(rascunho_id: str) -> str:
    r0 = _ler_rascunho(rascunho_id)
    if r0.get("tipo") != "cancelamento":
        raise ErroNota("Esse código é de uma nota, não de um cancelamento.")
    with trava(f"cancelamento-{r0['id']}", espera_max=120):
        r = _ler_rascunho(rascunho_id)
        if r["status"] == "cancelada":
            return "Esse cancelamento já foi feito."
        if datetime.now() - datetime.fromisoformat(r["criado_em"]) > VALIDADE_RASCUNHO:
            raise ErroNota("Essa prévia tem mais de 2 horas. Prepare o cancelamento de novo.")
        e = achar_empresa(r["cnpj"])
        if e.ambiente != r["ambiente"]:
            raise ErroNota("O ambiente da empresa mudou depois da prévia. Prepare de novo.")
        cliente = _cliente(e)
        xml = montar_cancelamento(e, r["chave"], r["motivo_codigo"], r["justificativa"], e.ambiente)
        assinado = cliente.assinar(xml)
        erros = validar_xsd(assinado, tipo="evento")
        if erros:
            raise ErroNota("O pedido de cancelamento ficou fora do padrão oficial: " + "; ".join(erros[:3]))
        try:
            resposta = cliente.registrar_evento(r["chave"], assinado)
        except NFSeError as ex:
            raise ErroNota(f"Sem resposta do governo ao cancelar ({_erro_api(ex)}). Consulte a nota antes de tentar de novo.") from ex
        if not resposta.success:
            registrar_historico("cancelamento_recusado", cnpj=e.cnpj, chave=r["chave"], codigo=resposta.error_code, mensagem=resposta.error_message)
            return f"O governo recusou o cancelamento: {resposta.error_message} (código {resposta.error_code})."
        r["status"] = "cancelada"
        _salvar_rascunho(r)
        atualizar_nota(e.cnpj, e.ambiente, r["chave"], situacao="cancelada", cancelada_em=datetime.now().isoformat(timespec="seconds"))
        registrar_historico("cancelamento", cnpj=e.cnpj, ambiente=e.ambiente, chave=r["chave"], protocolo=resposta.protocolo)
        return f"Nota cancelada. Protocolo: {resposta.protocolo or 'registrado'}."


# ---------------------------------------------------------------- notas do período

def sincronizar(e: Empresa, max_paginas: int = 30) -> tuple[int, list[str]]:
    """Traz do Ambiente de Dados Nacional as notas emitidas e recebidas (inclusive as feitas no site)."""
    arquivo = pasta_dados() / "adn_nsu.json"
    chave_nsu = f"{e.cnpj}|{e.ambiente}"
    cliente = _cliente(e)
    novos = 0
    avisos: list[str] = []
    with trava(f"sinc-{e.cnpj}", espera_max=120):
        with trava("adn_nsu"):
            ultimo = int(ler_json(arquivo, {}).get(chave_nsu, 0))
        with _sessao(cliente):
            for _ in range(max_paginas):
                situacao, docs, mensagens = cliente.distribuicao(ultimo)
                avisos.extend(mensagens)
                if situacao != "ok":
                    if situacao == "erro":
                        avisos.append("Não consegui atualizar a lista de notas pelo ambiente nacional agora.")
                    break
                progresso = False
                acoes: list[tuple[str, dict]] = []
                for doc in sorted(docs, key=lambda d: d.nsu):
                    if doc.nsu <= ultimo:
                        continue
                    progresso = True
                    ultimo = doc.nsu
                    if not doc.xml:
                        continue
                    try:
                        if "infNFSe" in doc.xml:
                            acoes.append(("nota", _preparar_xml_nota(e.cnpj, e.ambiente, doc.xml, origem="nacional")))
                            novos += 1
                        elif "infEvento" in doc.xml or "pedRegEvento" in doc.xml:
                            ev = resumo_evento(doc.xml)
                            if ev["cancelamento"] and ev["chave"]:
                                acoes.append(("cancelamento", ev))
                    except Exception as ex:
                        avisos.append(f"Documento NSU {doc.nsu} não pôde ser lido ({ex}).")
                aplicar_lote(e.cnpj, e.ambiente, acoes)
                with trava("adn_nsu"):
                    dados = ler_json(arquivo, {})
                    dados[chave_nsu] = ultimo
                    gravar_json(arquivo, dados)
                if not progresso:
                    break
    return novos, avisos


def listar_notas(
    empresa_ref: Optional[str] = None,
    de: Optional[str] = None,
    ate: Optional[str] = None,
    tipo: str = "emitidas",
    atualizar: bool = True,
) -> str:
    if tipo not in ("emitidas", "recebidas", "todas"):
        raise ErroNota("Tipo deve ser 'emitidas', 'recebidas' ou 'todas'.")
    hoje = date.today()
    de = de or hoje.replace(day=1).isoformat()
    ate = ate or hoje.isoformat()
    try:
        de_dt = date.fromisoformat(de)
        ate_dt = date.fromisoformat(ate)
    except ValueError as ex:
        raise ErroNota("Use as datas no formato AAAA-MM-DD (ex.: 2026-11-01).") from ex
    if empresa_ref:
        alvos = [achar_empresa(empresa_ref)]
    else:
        alvos = sorted(carregar_empresas().values(), key=lambda x: x.nome.lower())
        if len(alvos) > 10:
            return "São mais de 10 empresas. Diga quais (até 10 por pergunta) ou consulte uma de cada vez."
    papel = {"emitidas": "emitida", "recebidas": "recebida"}.get(tipo)
    blocos = []
    total_geral = Decimal("0")
    for e in alvos:
        avisos = []
        if atualizar:
            try:
                _, avisos = sincronizar(e)
            except Exception as ex:
                avisos = [f"Sem atualização do ambiente nacional ({ex}). Mostrando o que já estava salvo."]
        itens = filtrar(e.cnpj, e.ambiente, de, ate, papel)
        validas = [i for i in itens if i.get("situacao") != "cancelada"]
        canceladas = len(itens) - len(validas)
        total = somar(itens)
        total_geral += total
        cab = f"{e.nome}: {len(validas)} nota(s) {tipo}, total {reais(total)}"
        iss = somar(itens, "valor_iss")
        if iss:
            cab += f", ISS {reais(iss)}"
        if canceladas:
            cab += f", {canceladas} cancelada(s)"
        if e.ambiente == "homologacao":
            cab += " [ambiente de teste]"
        linhas = [cab]
        for i in itens[:40]:
            marca = " CANCELADA" if i.get("situacao") == "cancelada" else ""
            outro = i.get("tomador_nome") if i.get("papel") == "emitida" else i.get("prestador_nome")
            linhas.append(
                f"  nº {i.get('numero') or '?'}  {(i.get('competencia') or '')[:10]}  {reais(i.get('valor'))}  {outro or ''}{marca}"
            )
        if len(itens) > 40:
            linhas.append(f"  ... e mais {len(itens) - 40}")
        linhas.extend(f"  aviso: {a}" for a in avisos[:3])
        blocos.append("\n".join(linhas))
    periodo = f"Período: {de_dt.strftime('%d/%m/%Y')} a {ate_dt.strftime('%d/%m/%Y')}"
    rodape = f"\nTotal de todas as empresas: {reais(total_geral)}" if len(alvos) > 1 else ""
    return periodo + "\n\n" + "\n\n".join(blocos) + rodape


def pendencias() -> str:
    empresas = carregar_empresas()
    if not empresas:
        return "Nenhuma empresa cadastrada."
    itens = []
    for e in sorted(empresas.values(), key=lambda x: x.nome.lower()):
        if e.certificado_validade and not e.usa_procuracao:
            dias = (date.fromisoformat(e.certificado_validade) - date.today()).days
            if dias < 0:
                itens.append(f"{e.nome}: certificado VENCIDO há {-dias} dias. Nenhuma nota sai até renovar.")
            elif dias <= 30:
                itens.append(f"{e.nome}: certificado vence em {dias} dias ({e.certificado_validade}).")
        falta = e.faltando()
        if falta:
            itens.append(f"{e.nome}: cadastro incompleto ({', '.join(falta)}).")
        if not e.usa_procuracao and e.certificado_arquivo and not cert.ler_senha(e.cnpj):
            itens.append(f"{e.nome}: senha do certificado não está no computador do conector.")
    esc = carregar_escritorio()
    if any(e.usa_procuracao for e in empresas.values()):
        if not esc:
            itens.insert(0, "Há clientes por procuração, mas o certificado do escritório não está cadastrado.")
        else:
            dias = (date.fromisoformat(esc["validade"]) - date.today()).days
            if dias < 0:
                itens.insert(0, f"Certificado do ESCRITÓRIO vencido há {-dias} dias: nenhum cliente por procuração emite.")
            elif dias <= 30:
                itens.insert(0, f"Certificado do escritório vence em {dias} dias. Ele atende todos os clientes por procuração.")
    for arq in sorted((pasta_dados() / "rascunhos").glob("*.json")) if (pasta_dados() / "rascunhos").exists() else []:
        if arq.name.startswith(".tmp-"):
            continue
        r = ler_json(arq, {})
        if r.get("status") in ("enviando", "desconhecido") and r.get("id"):
            nome = empresas.get(r.get("cnpj"), None)
            itens.append(
                f"{nome.nome if nome else r.get('cnpj')}: envio {r['id']} sem confirmação do governo. Use verificar_envio antes de emitir de novo."
            )
    limite = (datetime.now() - timedelta(days=7)).isoformat()
    for h in ler_historico(300):
        if h.get("acao") in ("emissao_recusada", "cancelamento_recusado") and h.get("quando", "") >= limite:
            nome = empresas.get(h.get("cnpj"))
            itens.append(f"{nome.nome if nome else h.get('cnpj')}: recusa em {h['quando'][:10]}: {h.get('mensagem')}")
    if not itens:
        return "Nenhuma pendência. Certificados em dia e nenhum envio sem confirmação."
    return "Pendências:\n" + "\n".join(f"- {i}" for i in itens)
