"""Configuração: pastas, cadastro das empresas e gravação segura de arquivos."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

RAIZ_PROJETO = Path(__file__).resolve().parents[2]
RECURSOS = Path(__file__).resolve().parent / "recursos"

Ambiente = Literal["homologacao", "producao"]
Regime = Literal["mei", "simples", "simples_iss_fora", "simples_tudo_fora", "normal"]

# regime -> (opSimpNac, regApTribSN)
REGIMES: dict[str, tuple[str, Optional[str]]] = {
    "mei": ("2", None),
    "simples": ("3", "1"),
    "simples_iss_fora": ("3", "2"),
    "simples_tudo_fora": ("3", "3"),
    "normal": ("1", None),
}

REGIME_TEXTO = {
    "mei": "MEI",
    "simples": "Simples Nacional (ISS dentro do Simples)",
    "simples_iss_fora": "Simples Nacional com ISS por fora (sublimite)",
    "simples_tudo_fora": "Simples Nacional com tributos por fora (limite excedido)",
    "normal": "Não optante do Simples (Lucro Presumido ou Real)",
}

REGIME_ESPECIAL_TEXTO = {
    0: "nenhum",
    1: "ato cooperado",
    2: "estimativa",
    3: "microempresa municipal",
    4: "notário ou registrador",
    5: "profissional autônomo",
    6: "sociedade de profissionais",
    9: "outros",
}

AMBIENTE_TEXTO = {
    "homologacao": "TESTE (produção restrita do governo, nota sem valor fiscal)",
    "producao": "PRODUÇÃO (nota real, com valor fiscal)",
}


def pasta_dados() -> Path:
    pasta = Path(os.environ.get("EMISSOR_NFSE_DADOS") or (RAIZ_PROJETO / "dados"))
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def so_digitos(texto: str) -> str:
    return "".join(c for c in str(texto) if c.isdigit())


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join(sem_acento.lower().split())


def formatar_cnpj(cnpj: str) -> str:
    d = so_digitos(cnpj)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return cnpj


def reais(valor: Decimal | float | int | str | None) -> str:
    if valor is None:
        return "-"
    v = Decimal(str(valor)).quantize(Decimal("0.01"))
    inteiro, _, centavos = f"{v:.2f}".partition(".")
    negativo = inteiro.startswith("-")
    inteiro = inteiro.lstrip("-")
    grupos = []
    while inteiro:
        grupos.insert(0, inteiro[-3:])
        inteiro = inteiro[:-3]
    return f"{'-' if negativo else ''}R$ {'.'.join(grupos)},{centavos}"


def ler_json(caminho: Path, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return padrao


def gravar_json(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=caminho.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, caminho)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def trava(nome: str, espera_max: float = 30.0):
    """Trava simples entre processos (Claude Desktop e Claude Code podem rodar juntos)."""
    caminho = pasta_dados() / f".{nome}.lock"
    inicio = time.monotonic()
    while True:
        try:
            fd = os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - caminho.stat().st_mtime > 120:
                    caminho.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() - inicio > espera_max:
                raise TimeoutError("Outra operação de nota está em andamento. Tente de novo em instantes.") from None
            time.sleep(0.1)
    try:
        yield
    finally:
        caminho.unlink(missing_ok=True)


class ServicoPadrao(BaseModel):
    codigo_nacional: Optional[str] = None
    codigo_municipal: Optional[str] = None
    nbs: Optional[str] = None
    descricao: Optional[str] = None

    @field_validator("codigo_nacional")
    @classmethod
    def _cod_nac(cls, v):
        if v is None:
            return v
        d = so_digitos(v)
        if len(d) != 6:
            raise ValueError("o código de tributação nacional tem 6 dígitos (ex.: 170101)")
        return d

    @field_validator("nbs")
    @classmethod
    def _nbs(cls, v):
        if v is None:
            return v
        d = so_digitos(v)
        if len(d) != 9:
            raise ValueError("o código NBS tem 9 dígitos")
        return d


class Empresa(BaseModel):
    cnpj: str
    razao_social: str
    apelido: Optional[str] = None
    certificado_arquivo: Optional[str] = None
    certificado_validade: Optional[str] = None
    usa_procuracao: bool = Field(
        False, description="Emite com o certificado do escritório, por procuração eletrônica do cliente na Receita"
    )
    ambiente: Ambiente = "homologacao"
    municipio_ibge: Optional[int] = None
    inscricao_municipal: Optional[str] = None
    regime: Optional[Regime] = None
    regime_especial: int = 0
    aliquota_simples_total: Optional[Decimal] = Field(
        None, description="Percentual aproximado de tributos do Simples (pTotTribSN)"
    )
    aliquota_iss_simples: Optional[Decimal] = Field(
        None, description="Alíquota de ISS do Simples, usada só quando o tomador retém o ISS"
    )
    tributos_aproximados_federal: Decimal = Decimal("0")
    tributos_aproximados_estadual: Decimal = Decimal("0")
    tributos_aproximados_municipal: Decimal = Decimal("0")
    cst_pis_cofins: str = "01"
    serie: str = "900"
    servico_padrao: ServicoPadrao = Field(default_factory=ServicoPadrao)
    notas_teste_ok: int = 0

    @field_validator("cnpj")
    @classmethod
    def _cnpj(cls, v):
        d = so_digitos(v)
        if len(d) != 14:
            raise ValueError("CNPJ precisa ter 14 dígitos")
        return d

    @field_validator("regime_especial")
    @classmethod
    def _reg_esp(cls, v):
        if v not in REGIME_ESPECIAL_TEXTO:
            raise ValueError("regime especial deve ser 0, 1, 2, 3, 4, 5, 6 ou 9")
        return v

    @field_validator("serie")
    @classmethod
    def _serie(cls, v):
        d = so_digitos(v)
        if not d or not (1 <= int(d) <= 49999):
            raise ValueError("a série de emissão por sistema próprio vai de 1 a 49999")
        return str(int(d))

    @property
    def nome(self) -> str:
        return self.apelido or self.razao_social

    def caminho_certificado(self) -> Optional[Path]:
        """Certificado usado na conexão e na assinatura: o do escritório (procuração) ou o da própria empresa."""
        if self.usa_procuracao:
            esc = carregar_escritorio()
            return pasta_dados() / esc["certificado_arquivo"] if esc else None
        return pasta_dados() / self.certificado_arquivo if self.certificado_arquivo else None

    def chave_cofre(self) -> str:
        """Nome da senha no cofre: a do escritório (procuração) ou a da empresa."""
        return CHAVE_COFRE_ESCRITORIO if self.usa_procuracao else self.cnpj

    def faltando(self) -> list[str]:
        falta = []
        if not self.municipio_ibge:
            falta.append("município (código IBGE)")
        if not self.regime:
            falta.append("regime tributário")
        if self.regime and self.regime.startswith("simples") and self.aliquota_simples_total is None:
            falta.append("percentual aproximado de tributos do Simples (aliquota_simples_total)")
        caminho = self.caminho_certificado()
        if not caminho or not caminho.exists():
            falta.append("certificado do escritório" if self.usa_procuracao else "certificado A1 da empresa")
        return falta


CHAVE_COFRE_ESCRITORIO = "escritorio"


def carregar_escritorio() -> Optional[dict]:
    """Certificado do próprio escritório, usado para emitir em nome dos clientes que deram procuração."""
    return ler_json(pasta_dados() / "escritorio.json", None)


def salvar_escritorio(dados: dict) -> None:
    gravar_json(pasta_dados() / "escritorio.json", dados)


def _arquivo_empresas() -> Path:
    return pasta_dados() / "empresas.json"


def carregar_empresas() -> dict[str, Empresa]:
    bruto = ler_json(_arquivo_empresas(), {})
    return {cnpj: Empresa(**dados) for cnpj, dados in bruto.items()}


def salvar_empresa(empresa: Empresa) -> None:
    with trava("empresas"):
        bruto = ler_json(_arquivo_empresas(), {})
        bruto[empresa.cnpj] = empresa.model_dump(mode="json")
        gravar_json(_arquivo_empresas(), bruto)


def remover_empresa(cnpj: str) -> None:
    with trava("empresas"):
        bruto = ler_json(_arquivo_empresas(), {})
        bruto.pop(cnpj, None)
        gravar_json(_arquivo_empresas(), bruto)


class EmpresaNaoEncontrada(Exception):
    pass


def achar_empresa(referencia: str) -> Empresa:
    empresas = carregar_empresas()
    if not empresas:
        raise EmpresaNaoEncontrada(
            "Nenhuma empresa cadastrada ainda. Cadastre o certificado A1 com a ferramenta cadastrar_certificado."
        )
    ref = (referencia or "").strip()
    dig = so_digitos(ref)
    if len(dig) == 14 and dig in empresas:
        return empresas[dig]
    alvo = normalizar(ref)
    exatos = [e for e in empresas.values() if alvo in (normalizar(e.apelido or ""), normalizar(e.razao_social))]
    if len(exatos) == 1:
        return exatos[0]
    parciais = [
        e
        for e in empresas.values()
        if alvo and (alvo in normalizar(e.razao_social) or alvo in normalizar(e.apelido or ""))
    ]
    if len(dig) >= 8:
        parciais += [e for e in empresas.values() if e.cnpj.startswith(dig) and e not in parciais]
    if len(parciais) == 1:
        return parciais[0]
    if not parciais:
        raise EmpresaNaoEncontrada(f"Não achei empresa cadastrada para '{referencia}'. Use listar_empresas para ver as opções.")
    nomes = "; ".join(f"{e.nome} ({formatar_cnpj(e.cnpj)})" for e in parciais[:10])
    raise EmpresaNaoEncontrada(f"Mais de uma empresa combina com '{referencia}': {nomes}. Diga qual.")
