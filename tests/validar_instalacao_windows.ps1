# Testa copia e permissoes reais, sem certificados ou instalacao de dependencias.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\instalacao-windows.ps1')

$temporario = Join-Path ([IO.Path]::GetTempPath()) ('emissor-teste-' + [guid]::NewGuid())
try {
    $origem = Join-Path $temporario 'origem com espacos'
    $destino = Join-Path $temporario 'destino com espacos'
    New-Item -ItemType Directory -Path (Join-Path $origem 'dados'), (Join-Path $origem '.git') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $origem 'codigo.py') -Value '# exemplo'
    foreach ($nome in @('cliente.pfx', 'cliente.P12', 'tls.key', 'convite-maria.json', '.env.local', '.mcp.json', 'empresas.json', 'nota.pdf', 'dados\empresas.json', '.git\config')) {
        Set-Content -LiteralPath (Join-Path $origem $nome) -Value 'dado ficticio'
    }
    Copiar-Conector $origem $destino
    if (-not (Test-Path -LiteralPath (Join-Path $destino 'codigo.py'))) { throw 'Codigo nao copiado.' }
    foreach ($nome in @('dados', '.git', 'cliente.pfx', 'cliente.P12', 'tls.key', 'convite-maria.json', '.env.local', '.mcp.json', 'empresas.json', 'nota.pdf')) {
        if (Test-Path -LiteralPath (Join-Path $destino $nome)) { throw "Arquivo privado copiado: $nome" }
    }
    Copiar-Conector $destino $destino

    $privado = Join-Path $temporario 'dados privados'
    Proteger-Pasta $privado
    $acl = Get-Acl -LiteralPath $privado
    if (-not $acl.AreAccessRulesProtected) { throw 'Pasta continua herdando permissoes.' }
    $usuario = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $regras = $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
    foreach ($regra in $regras) {
        if ($regra.IdentityReference.Value -notin @($usuario, 'S-1-5-18')) { throw 'Permissao para outro usuario.' }
    }
    if ($regras.Count -ne 2) { throw 'Permissoes incompletas.' }

    $uvFalso = Join-Path $temporario 'uv falso.cmd'
    Set-Content -LiteralPath $uvFalso -Encoding Ascii -Value @'
@echo off
if "%~1"=="falhar" exit /b 7
echo %~2
'@
    $saida = Executar-Uv $uvFalso @('ok', 'argumento com espacos')
    if ($saida.Trim() -ne 'argumento com espacos') { throw 'Argumentos com espacos foram alterados.' }
    $falhou = $false
    try { Executar-Uv $uvFalso @('falhar') } catch { $falhou = $true }
    if (-not $falhou) { throw 'Erro do comando foi ignorado.' }
    Write-Host 'Windows: copia, exclusao de dados privados, ACL e argumentos conferidos.'
} finally {
    Remove-Item -LiteralPath $temporario -Recurse -Force -ErrorAction SilentlyContinue
}
# O teste negativo acima deixa LASTEXITCODE diferente de zero. Uma excecao real
# interrompe o script antes deste ponto; todos os testes aprovados encerram com sucesso.
exit 0
