# Computador central: use a conta Windows que ficara conectada no escritorio.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\instalacao-windows.ps1')

Write-Host '== Emissor NFS-e: computador central =='
$uv = Obter-Uv
$destino = Join-Path $env:USERPROFILE 'EmissorNFSe'
Copiar-Conector $PSScriptRoot $destino
Set-Location -LiteralPath $destino
$dados = Join-Path $destino 'dados'
Proteger-Pasta $dados
$env:EMISSOR_NFSE_DADOS = $dados
Executar-Uv $uv @('sync', '--locked', '--no-dev')

$escritorio = Ler-Caminho 'Certificado A1 do escritorio para procuracoes (Enter para pular)'
if ($escritorio) { Executar-Uv $uv @('run', '--locked', '--no-dev', 'emissor-nfse-admin', 'escritorio', $escritorio) }
$certificados = Ler-Caminho 'Pasta com os certificados A1 dos clientes (Enter para pular)'
if ($certificados) { Executar-Uv $uv @('run', '--locked', '--no-dev', 'emissor-nfse-admin', 'certificado', $certificados) }

$nome = (Read-Host 'Seu nome para o acesso de administrador').Trim()
if (-not $nome) { throw 'Informe um nome para criar o acesso de administrador.' }
Executar-Uv $uv @('run', '--locked', '--no-dev', 'emissor-nfse-admin', 'convidar', $nome, '--admin')
Executar-Uv $uv @('run', '--locked', '--no-dev', 'emissor-nfse-admin', 'instalar-servico')

Write-Host ''
Write-Host 'Pronto. Mantenha este usuario conectado; pode bloquear a tela.'
Write-Host 'Libere TCP 8443 no firewall somente para a rede privada do escritorio.'
Write-Host 'Para adicionar pessoas, abra o PowerShell nesta pasta e rode:'
Write-Host '  uv run emissor-nfse-admin convidar "Nome da pessoa"'
Write-Host 'Cada pessoa instala instalar-windows.cmd com o convite pessoal.'
Write-Host 'Se este PC tambem for usado para emitir, instale a ponte com seu convite.'
