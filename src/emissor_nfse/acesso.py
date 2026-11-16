"""Quem está usando: pessoas da equipe, chaves de acesso e permissões.

No servidor central, cada pessoa tem uma chave própria (guardada aqui só como resumo criptográfico).
Rodando sozinho no computador (modo local), quem usa é o dono do computador, com permissão de administrador.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import gravar_json, ler_json, normalizar, pasta_dados, trava


@dataclass(frozen=True)
class Pessoa:
    nome: str
    admin: bool


_local = Pessoa(nome=f"{getpass.getuser()} (este computador)", admin=True)
pessoa_atual: ContextVar[Pessoa] = ContextVar("pessoa_atual", default=_local)


class _Chave:
    """Liga/desliga global (vale para todas as requisições do processo)."""

    def __init__(self) -> None:
        self._valor = False

    def get(self) -> bool:
        return self._valor

    def set(self, valor: bool) -> None:
        self._valor = bool(valor)


modo_servidor = _Chave()


class SemPermissao(Exception):
    pass


def exigir_admin(acao: str) -> None:
    p = pessoa_atual.get()
    if not p.admin:
        raise SemPermissao(f"{acao} é só para administradores do conector. Peça a quem administra.")


def _arquivo():
    return pasta_dados() / "servidor" / "pessoas.json"


def _resumo(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


def criar_pessoa(nome: str, admin: bool = False) -> str:
    """Cria (ou recria) o acesso de uma pessoa e devolve a chave, que só aparece esta vez."""
    nome = nome.strip()
    if not nome:
        raise ValueError("Informe o nome da pessoa.")
    chave = "nfse_" + secrets.token_urlsafe(32)
    with trava("pessoas"):
        dados = ler_json(_arquivo(), {})
        dados = {k: v for k, v in dados.items() if normalizar(v["nome"]) != normalizar(nome)}
        dados[_resumo(chave)] = {"nome": nome, "admin": admin, "criado_em": datetime.now().isoformat(timespec="seconds")}
        gravar_json(_arquivo(), dados)
    return chave


def remover_pessoa(nome: str) -> bool:
    with trava("pessoas"):
        dados = ler_json(_arquivo(), {})
        restantes = {k: v for k, v in dados.items() if normalizar(v["nome"]) != normalizar(nome)}
        gravar_json(_arquivo(), restantes)
    return len(restantes) < len(dados)


def listar_pessoas() -> list[dict]:
    return sorted(ler_json(_arquivo(), {}).values(), key=lambda p: normalizar(p["nome"]))


def identificar(cabecalho_authorization: Optional[str]) -> Optional[Pessoa]:
    if not cabecalho_authorization or not cabecalho_authorization.lower().startswith("bearer "):
        return None
    resumo = _resumo(cabecalho_authorization[7:].strip())
    dados = ler_json(_arquivo(), {})
    for k, v in dados.items():
        if hmac.compare_digest(k, resumo):
            return Pessoa(nome=v["nome"], admin=bool(v.get("admin")))
    return None
