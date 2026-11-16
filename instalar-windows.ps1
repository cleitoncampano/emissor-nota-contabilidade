# Computador da equipe: recebe so o convite pessoal, sem certificados dos clientes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\instalacao-windows.ps1')

Write-Host '== Emissor NFS-e: computador da equipe =='
$convite = Ler-Caminho 'Cole ou arraste o caminho do seu arquivo de convite'
if (-not (Test-Path -LiteralPath $convite -PathType Leaf)) { throw 'Arquivo de convite nao encontrado.' }
# Valida antes de substituir um convite que ja funciona.
$conteudo = Get-Content -LiteralPath $convite -Raw | ConvertFrom-Json
foreach ($campo in @('endereco', 'chave', 'certificado_tls')) {
    if (-not $conteudo.PSObject.Properties[$campo] -or -not $conteudo.$campo) { throw "Convite incompleto: falta $campo." }
}
$pastaConvite = Join-Path $env:USERPROFILE '.emissor-nfse'
Proteger-Pasta $pastaConvite
$conviteDestino = Join-Path $pastaConvite 'convite.json'
if ([IO.Path]::GetFullPath($convite) -ne [IO.Path]::GetFullPath($conviteDestino)) {
    Copy-Item -LiteralPath $convite -Destination $conviteDestino -Force
}
$uv = Obter-Uv
$destino = Join-Path $env:USERPROFILE 'EmissorNFSe'
Copiar-Conector $PSScriptRoot $destino
Set-Location -LiteralPath $destino
Executar-Uv $uv @('sync', '--locked', '--no-dev')
Executar-Uv $uv @('run', '--locked', '--no-dev', 'python', '-m', 'emissor_nfse.instalar', '--modo', 'ponte', '--uv', $uv)
Write-Host 'Pronto. Feche completamente o Claude Desktop e abra novamente.'
Write-Host 'Pergunte: o conector de notas esta conectado?'
