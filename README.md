# Emissor de notas para contabilidade

Conector para escritórios contábeis emitirem, consultarem e cancelarem NFS-e Nacional pelo Claude Desktop ou outro cliente MCP. Um computador central Windows guarda os certificados dos clientes; cada pessoa da equipe acessa com uma chave própria.

**Versão experimental (0.2.0).** Os testes usam o governo simulado. Ainda falta validar emissão, consulta, PDF, cancelamento e procuração no ambiente de homologação do governo. A instalação completa também precisa ser conferida em um Windows real antes da entrega ao escritório.

## Como funciona

```text
Windows da Maria ─┐                         ┌─ Certificados A1 dos clientes
Windows da Ana ───┼── HTTPS + chave pessoal ─┤  Computador central Windows ── Governo
Windows do João ──┘                         └─ Senhas no cofre do Windows
```

- Certificados e senhas ficam no computador central, na conta Windows usada na instalação.
- Cada cliente pode ter seu próprio certificado A1 e sua própria senha, cadastrados uma vez.
- A equipe recebe convites individuais. Ela não precisa conhecer nem digitar as senhas dos certificados para emitir.
- A saída de uma pessoa exige revogar somente o acesso dela.
- Cada operação registra quem a solicitou. Administradores gerenciam o cadastro fiscal e a liberação de produção.
- Toda emissão ou cancelamento exige uma prévia e a confirmação de quem solicitou.

O modo procuração está implementado para usar o certificado do escritório no lugar dos certificados individuais. Sua aceitação pelo governo ainda precisa ser validada em homologação.

## Instalar no escritório (Windows)

Requisitos: Windows com PowerShell 5.1 ou superior, acesso à internet na instalação e Claude Desktop nos computadores que vão conversar com o conector. O instalador baixa `uv` e Python 3.13 quando necessário.

### Computador central

Escolha um computador que permaneça ligado, com a conta Windows do responsável conectada. **Execute o instalador nessa conta**, para que o servidor use o mesmo cofre em que as senhas serão cadastradas.

1. Baixe e extraia este projeto.
2. Abra `instalar-servidor-windows.cmd`.
3. Informe a pasta com os certificados `.pfx` ou `.p12`. Cada senha é solicitada no próprio terminal, com entrada oculta.
4. Informe o nome de quem administrará o conector. O instalador cria um convite pessoal e registra a inicialização automática no Agendador de Tarefas.
5. No firewall do computador central, permita conexões de entrada **TCP 8443 somente na rede privada do escritório**. Essa configuração pode exigir o administrador de TI.

O conector fica em `%USERPROFILE%\EmissorNFSe`. Não é necessário deixar um terminal aberto. É permitido bloquear a tela; sair da conta ou desligar o computador interrompe o conector. O computador também precisa permanecer acordado.

### Computadores da equipe

1. Copie a pasta do projeto e o convite daquela pessoa para o computador dela.
2. Abra `instalar-windows.cmd` e informe o arquivo de convite.
3. Feche completamente o Claude Desktop e abra novamente.
4. Pergunte: “O conector de notas está conectado?”

O convite fica em `%USERPROFILE%\.emissor-nfse\convite.json`, protegido para a conta local. Não distribua certificados ou a pasta `dados` para a equipe. Entregue o convite por um canal privado e individual.

Se o computador central também for usado para emitir, instale a ponte nele com o convite do operador. Os instaladores de Mac continuam disponíveis para desenvolvimento e outros cenários.

## Administrar clientes, senhas e pessoas

No PowerShell do computador central, usando a mesma conta Windows da instalação:

```powershell
Set-Location "$env:USERPROFILE\EmissorNFSe"

# Importar uma pasta: solicita separadamente a senha de cada certificado.
uv run emissor-nfse-admin certificado "C:\Certificados dos clientes"

# Substituir um certificado ou atualizar a senha: importe o arquivo novamente.
uv run emissor-nfse-admin certificado "C:\Certificados dos clientes\cliente.pfx"

# Certificado do escritório, para o modo procuração.
uv run emissor-nfse-admin escritorio "C:\Certificados\escritorio.pfx"

uv run emissor-nfse-admin convidar "Maria"
uv run emissor-nfse-admin convidar "Ana" --admin
uv run emissor-nfse-admin pessoas
uv run emissor-nfse-admin remover "Maria"

# Use um nome/IP estável se o nome .local não resolver na rede Windows.
uv run emissor-nfse-admin convidar "Maria" --endereco "192.168.1.10"

uv run emissor-nfse-admin instalar-servico
uv run emissor-nfse-admin desinstalar-servico
```

Recriar um convite para o mesmo nome invalida o anterior. Os convites são gravados em `dados\servidor\convites`.

As senhas dos certificados ficam no cofre do sistema via [keyring](https://keyring.readthedocs.io/en/latest/), separadas por CNPJ. A pasta `dados` não contém essas senhas; restaurar essa pasta em outro computador exige recadastrar as senhas no novo cofre. O Agendador usa a sessão do operador, sem armazenar a senha de login do Windows.

## Usar pelo Claude

- “Emita uma nota de R$ 1.500 da Alfa para a Beta Tecnologia, consultoria de outubro.”
- “Quanto a Alfa faturou este mês?”
- “Quais notas a Alfa recebeu de fornecedores?”
- “Prepare o cancelamento da nota 1532 da Alfa; saiu em duplicidade.”
- “Tem alguma pendência?”
- “Me dê o PDF da nota.”

Toda empresa começa em homologação. Depois de uma nota de teste aceita pelo governo, um administrador pode solicitar a mudança para produção. Configurações fiscais devem ser conferidas pelo responsável do escritório.

## Arquivos e backup

No computador central, em `%USERPROFILE%\EmissorNFSe\dados`:

| Pasta ou arquivo | Conteúdo |
| --- | --- |
| `certificados/` | Certificados A1 importados, sem as senhas |
| `notas/` | XML, PDF e índices das notas, separados por empresa e ambiente |
| `historico.jsonl` | Operações com data e responsável |
| `servidor/` | Certificado TLS, resumos das chaves de acesso, convites e registro do servidor |

Faça backup privado dessa pasta. Ela e os convites nunca devem entrar no GitHub. Certificados, chaves, notas, logs, bancos de dados e arquivos `.env` estão excluídos no `.gitignore`.

## Desenvolvimento e testes

```bash
git clone https://github.com/cleitoncampano/emissor-nota-contabilidade.git
cd emissor-nota-contabilidade
uv sync --locked --group dev
uv run --locked --group dev pytest -q
uv build --no-sources
```

O projeto exige Python 3.11 ou superior e fixa 3.13 para o ambiente padrão de desenvolvimento. Os testes geram certificados temporários e usam um cofre em memória. O teste da ponte abre uma porta HTTPS local; não é preciso usar certificados reais nem acessar o governo.

A automação do GitHub testa Windows e Linux. No Windows, também valida a sintaxe dos instaladores com o Windows PowerShell e registra/executa/remove a tarefa em uma pasta temporária. Os testes automatizados não substituem a instalação completa no computador do cliente.

| Módulo | Responsabilidade |
| --- | --- |
| `server.py`, `operacoes.py` | Ferramentas MCP e operações fiscais |
| `dps.py`, `sefin.py` | XML, validação XSD e transporte via pynfse-nacional |
| `central.py`, `ponte.py`, `acesso.py` | Servidor HTTPS, ponte local e permissões |
| `admin.py`, `servico_windows.py` | Administração e inicialização automática |
| `config.py`, `certificado.py`, `notas.py` | Cadastro, cofre e armazenamento |

Variáveis opcionais (o programa não carrega `.env` automaticamente):

| Variável | Uso |
| --- | --- |
| `EMISSOR_NFSE_DADOS` | Outra pasta de dados no computador central |
| `EMISSOR_NFSE_CONVITE` | Outro caminho de convite no computador da equipe |

## Limites atuais

- Operação prevista para a rede interna do escritório. Acesso remoto depende de uma rede privada configurada pelo responsável de TI.
- Se o computador central suspender, desligar ou encerrar a sessão, a equipe perde a conexão.
- Emite NFS-e. IBS/CBS, obra, exportação, deduções e benefícios municipais ainda não são preenchidos.
- Ainda falta a primeira validação real com o governo, incluindo assinatura, distribuição ADN, PDF e procuração/cancelamento.
- Os testes automatizados de Windows só terão resultado após executar o workflow no GitHub; o desenvolvimento local foi feito em um Mac.

## Contribuir e publicar

Consulte [CONTRIBUTING.md](CONTRIBUTING.md) e [SECURITY.md](SECURITY.md). O roteiro para criar o repositório público na conta `cleitoncampano` está em [docs/PUBLICAR_GITHUB.md](docs/PUBLICAR_GITHUB.md).

## Licença

[AGPL-3.0-only](LICENSE), conforme a licença já declarada no projeto. Dependências e recursos de terceiros estão descritos em [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
