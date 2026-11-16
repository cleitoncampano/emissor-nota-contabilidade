"""Agendamento sem senha de login e administração compatível com Windows."""

from __future__ import annotations

import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from emissor_nfse import admin, servico_windows

NS = {"t": servico_windows.NS}


def test_tarefa_usa_mesmo_usuario_sem_senha():
    pythonw = PureWindowsPath("C:/Users/Equipe Contábil/EmissorNFSe/.venv/Scripts/pythonw.exe")
    script = PureWindowsPath("C:/Users/Equipe Contábil/EmissorNFSe/dados/servidor/iniciar.pyw")
    xml = servico_windows.xml_tarefa("S-1-5-21-1000", pythonw, script)
    raiz = ET.fromstring(xml)
    assert raiz.findtext("t:Principals/t:Principal/t:UserId", namespaces=NS) == "S-1-5-21-1000"
    assert raiz.findtext("t:Triggers/t:LogonTrigger/t:UserId", namespaces=NS) == "S-1-5-21-1000"
    assert raiz.findtext("t:Principals/t:Principal/t:LogonType", namespaces=NS) == "InteractiveToken"
    assert raiz.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=NS) == "LeastPrivilege"
    assert raiz.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == str(pythonw)
    assert raiz.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == f'"{script}"'
    assert raiz.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=NS) == "PT0S"
    assert "Password" not in xml.decode("utf-16")


def test_instalar_windows_preserva_pasta_dados_e_registra_tarefa(tmp_path, monkeypatch):
    pasta = tmp_path / "dados privados" / "servidor"
    pasta.mkdir(parents=True)
    python = tmp_path / "python.exe"
    python.with_name("pythonw.exe").touch()
    monkeypatch.setattr(servico_windows.sys, "executable", str(python))
    monkeypatch.setattr(servico_windows, "pasta_servidor", lambda: pasta)
    monkeypatch.setattr(servico_windows, "pasta_dados", lambda: pasta.parent)
    chamadas = []

    def executar(args, **kwargs):
        chamadas.append(args)
        return SimpleNamespace(returncode=0, stdout='"DOMINIO\\operador","S-1-5-21-1000"\n')

    monkeypatch.setattr(servico_windows.subprocess, "run", executar)
    servico_windows.instalar(9443)
    assert chamadas[0][0] == "whoami"
    assert [args[1] for args in chamadas[1:]] == ["/Create", "/End", "/Run"]
    inicializador = (pasta / "iniciar-servidor.pyw").read_text()
    compile(inicializador, "iniciar-servidor.pyw", "exec")
    assert repr(str(pasta.parent)) in inicializador
    assert "'9443'" in inicializador
    assert "runpy.run_module" in inicializador
    raiz = ET.parse(pasta / "tarefa-windows.xml")
    assert raiz.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == str(python.with_name("pythonw.exe"))


def test_admin_encaminha_instalacao_e_remocao_windows(monkeypatch):
    chamadas = []
    monkeypatch.setattr(admin.platform, "system", lambda: "Windows")
    monkeypatch.setattr(servico_windows, "instalar", lambda porta: chamadas.append(porta))
    monkeypatch.setattr(servico_windows, "desinstalar", lambda: chamadas.append("remover"))
    admin._instalar_servico(8443)
    admin._desinstalar_servico()
    assert chamadas == [8443, "remover"]


@pytest.mark.skipif(sys.platform != "win32", reason="requer Agendador de Tarefas do Windows")
def test_agendador_windows_real(tmp_path, monkeypatch):
    """Registra, executa e remove uma tarefa temporária, sem ligar o servidor fiscal."""
    import csv
    import io

    nome = f"EmissorNFSe-Teste-{uuid.uuid4().hex}"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    assert pythonw.is_file()
    marcador = tmp_path / "executou.txt"
    script = tmp_path / "teste agendador.pyw"
    script.write_text(f"from pathlib import Path\nPath({str(marcador)!r}).write_text('ok')\n", encoding="utf-8")
    usuario = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], check=True, capture_output=True, text=True)
    sid = next(csv.reader(io.StringIO(usuario.stdout)))[1]
    arquivo = tmp_path / "tarefa.xml"
    arquivo.write_bytes(servico_windows.xml_tarefa(sid, pythonw, script))
    monkeypatch.setattr(servico_windows, "NOME_TAREFA", nome)
    try:
        subprocess.run(["schtasks", "/Create", "/TN", nome, "/XML", str(arquivo), "/F"], check=True, capture_output=True)
        subprocess.run(["schtasks", "/Run", "/TN", nome], check=True, capture_output=True)
        limite = time.monotonic() + 20
        while not marcador.exists() and time.monotonic() < limite:
            time.sleep(0.1)
        assert marcador.read_text() == "ok", "Tarefa não executou com a sessão do operador."
    finally:
        servico_windows.desinstalar()
