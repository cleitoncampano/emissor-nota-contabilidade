"""Inicialização no login do Windows, com o mesmo usuário que cadastrou as senhas.

Usa o Agendador de Tarefas com InteractiveToken: não guarda a senha de login
e não executa como SYSTEM, que não teria acesso ao cofre do usuário.
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .central import pasta_servidor
from .config import RAIZ_PROJETO, pasta_dados

NOME_TAREFA = "EmissorNFSe"
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def xml_tarefa(usuario: str, pythonw: Path, inicializador: Path) -> bytes:
    ET.register_namespace("", NS)
    raiz = ET.Element(f"{{{NS}}}Task", version="1.3")

    def elemento(pai, nome, texto=None, **atributos):
        filho = ET.SubElement(pai, f"{{{NS}}}{nome}", atributos)
        filho.text = texto
        return filho

    registro = elemento(raiz, "RegistrationInfo")
    elemento(registro, "Description", "Conector central de NFS-e do escritorio")
    gatilho = elemento(elemento(raiz, "Triggers"), "LogonTrigger")
    elemento(gatilho, "Enabled", "true")
    elemento(gatilho, "UserId", usuario)
    principal = elemento(elemento(raiz, "Principals"), "Principal", id="Operador")
    elemento(principal, "UserId", usuario)
    elemento(principal, "LogonType", "InteractiveToken")
    elemento(principal, "RunLevel", "LeastPrivilege")
    ajustes = elemento(raiz, "Settings")
    for nome, valor in {
        "MultipleInstancesPolicy": "IgnoreNew",
        "DisallowStartIfOnBatteries": "false",
        "StopIfGoingOnBatteries": "false",
        "StartWhenAvailable": "true",
        "ExecutionTimeLimit": "PT0S",
        "Enabled": "true",
    }.items():
        elemento(ajustes, nome, valor)
    reinicio = elemento(ajustes, "RestartOnFailure")
    elemento(reinicio, "Interval", "PT1M")
    elemento(reinicio, "Count", "3")
    acao = elemento(elemento(raiz, "Actions", Context="Operador"), "Exec")
    elemento(acao, "Command", str(pythonw))
    elemento(acao, "Arguments", subprocess.list2cmdline([str(inicializador)]))
    elemento(acao, "WorkingDirectory", str(RAIZ_PROJETO))
    return ET.tostring(raiz, encoding="utf-16", xml_declaration=True)


def instalar(porta: int) -> None:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError("Não encontrei pythonw.exe. Rode instalar-servidor-windows.cmd novamente.")
    resultado = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True, capture_output=True, text=True,
    )
    usuario = next(csv.reader(io.StringIO(resultado.stdout)))[1]
    pasta = pasta_servidor()
    inicializador = pasta / "iniciar-servidor.pyw"
    inicializador.write_text(
        "import os, runpy, sys\n"
        f"os.chdir({str(RAIZ_PROJETO)!r})\n"
        f"os.environ['EMISSOR_NFSE_DADOS'] = {str(pasta_dados())!r}\n"
        f"sys.stdout = sys.stderr = open({str(pasta / 'servidor.log')!r}, 'a', encoding='utf-8', buffering=1)\n"
        f"sys.argv = ['emissor-nfse-admin', 'servidor', '--porta', {str(porta)!r}]\n"
        "runpy.run_module('emissor_nfse.admin', run_name='__main__')\n",
        encoding="utf-8",
    )
    arquivo = pasta / "tarefa-windows.xml"
    arquivo.write_bytes(xml_tarefa(usuario, pythonw, inicializador))
    subprocess.run(["schtasks", "/Create", "/TN", NOME_TAREFA, "/XML", str(arquivo), "/F"], check=True)
    # Atualizar a tarefa também precisa reiniciar o processo que já estava rodando.
    subprocess.run(["schtasks", "/End", "/TN", NOME_TAREFA], capture_output=True)
    subprocess.run(["schtasks", "/Run", "/TN", NOME_TAREFA], check=True)
    print(f"Inicialização automática instalada (porta {porta}). Registro em {pasta / 'servidor.log'}.")
    print("Mantenha este usuário do Windows conectado. Bloquear a tela é permitido; sair da conta interrompe o conector.")


def desinstalar() -> None:
    consulta = subprocess.run(["schtasks", "/Query", "/TN", NOME_TAREFA], capture_output=True)
    if consulta.returncode:
        print("Inicialização automática não estava instalada.")
        return
    subprocess.run(["schtasks", "/End", "/TN", NOME_TAREFA], capture_output=True)
    subprocess.run(["schtasks", "/Delete", "/TN", NOME_TAREFA, "/F"], check=True)
    print("Inicialização automática removida. Certificados, senhas e notas foram preservados.")
