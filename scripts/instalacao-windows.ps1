Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Obter-Uv {
    $comando = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }
    $caminho = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $caminho) { return $caminho }
    Write-Host 'Instalando uv, o gerenciador de Python...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $temporario = Join-Path ([IO.Path]::GetTempPath()) ('uv-' + [guid]::NewGuid() + '.ps1')
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $temporario
        & $temporario | Out-Host
    } finally {
        Remove-Item -LiteralPath $temporario -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $caminho)) { throw 'Nao encontrei uv.exe apos a instalacao.' }
    return $caminho
}

function Executar-Uv {
    param([string]$Uv, [string[]]$Argumentos)
    & $Uv @Argumentos
    if ($LASTEXITCODE -ne 0) { throw "O conector encerrou com erro ($LASTEXITCODE)." }
}

function Copiar-Conector {
    param([string]$Origem, [string]$Destino)
    if ([IO.Path]::GetFullPath($Origem).TrimEnd('\') -eq [IO.Path]::GetFullPath($Destino).TrimEnd('\')) { return }
    New-Item -ItemType Directory -Path $Destino -Force | Out-Null
    & robocopy $Origem $Destino /E /XD .git .venv dados certificados convites servidor notas backups tests __pycache__ .pytest_cache .ruff_cache .claude .codex .agents .aws .ssh .emissor-nfse /XF *.pfx *.p12 *.pem *.key *.cer *.crt *.pdf *.xml *.sqlite* *.db .env .env.* .mcp.json auth.json pessoas.json empresas.json historico.jsonl convite*.json *.log /NFL /NDL /NJH /NJS | Out-Host
    if ($LASTEXITCODE -ge 8) { throw "Falha ao copiar o conector ($LASTEXITCODE)." }
}

function Proteger-Pasta {
    param([string]$Caminho)
    New-Item -ItemType Directory -Path $Caminho -Force | Out-Null
    $usuario = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $sistema = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @($usuario, $sistema)) {
        $regra = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($regra)
    }
    Set-Acl -LiteralPath $Caminho -AclObject $acl
}

function Ler-Caminho {
    param([string]$Mensagem)
    return (Read-Host $Mensagem).Trim().Trim('"').Trim("'")
}
