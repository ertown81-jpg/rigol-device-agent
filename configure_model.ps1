param(
    [ValidateSet("deepseek", "doubao")]
    [string]$Provider = "deepseek",
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}

if (-not $Model) {
    $Model = if ($Provider -eq "deepseek") {
        "deepseek-v4-flash"
    }
    else {
        "doubao-seed-2-0-lite-260215"
    }
}

Write-Host "Provider: $Provider"
Write-Host "Model:    $Model"
$secureKey = Read-Host "Paste the API key (input is hidden)" -AsSecureString
$encryptedKey = $secureKey | ConvertFrom-SecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:RIGOL_MODEL_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    $env:RIGOL_MODEL_PROVIDER = $Provider
    $env:RIGOL_MODEL_NAME = $Model

    Set-Location -LiteralPath $projectRoot
    Write-Host "Testing the model connection..."
    & $pythonPath -m rigol_agent --simulate plan "Read CH1 frequency and peak-to-peak voltage" --planner $Provider --model $Model
    if ($LASTEXITCODE -ne 0) {
        throw "The API key could not be verified for $Provider. Nothing was saved."
    }
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    Remove-Item Env:RIGOL_MODEL_API_KEY -ErrorAction SilentlyContinue
}

$credentialDirectory = Join-Path $env:APPDATA "RigolDeviceAgent"
New-Item -ItemType Directory -Path $credentialDirectory -Force | Out-Null
Set-Content -LiteralPath (Join-Path $credentialDirectory "model-key.txt") -Value $encryptedKey -NoNewline
@{ provider = $Provider; model = $Model } |
    ConvertTo-Json |
    Set-Content -LiteralPath (Join-Path $credentialDirectory "model.json") -Encoding UTF8

$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -ne $listener) {
    $running = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if ($null -ne $running -and $running.CommandLine -match "rigol_agent\s+serve") {
        Stop-Process -Id $listener.OwningProcess -Force
        Start-Sleep -Milliseconds 500
    }
}

Write-Host "Model configuration saved for the current Windows user."
& (Join-Path $projectRoot "start_agent.ps1")
