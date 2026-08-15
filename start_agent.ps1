$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$agentUrl = "http://127.0.0.1:8765/"
$healthUrl = "http://127.0.0.1:8765/health"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python environment not found: $pythonPath"
}

$credentialDirectory = Join-Path $env:APPDATA "RigolDeviceAgent"
$settingsPath = Join-Path $credentialDirectory "model.json"
$keyPath = Join-Path $credentialDirectory "model-key.txt"
if ((Test-Path -LiteralPath $settingsPath) -and (Test-Path -LiteralPath $keyPath)) {
    $modelSettings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    $encryptedKey = (Get-Content -LiteralPath $keyPath -Raw).Trim()
    $secureKey = $encryptedKey | ConvertTo-SecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $env:RIGOL_MODEL_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        $env:RIGOL_MODEL_PROVIDER = [string]$modelSettings.provider
        $env:RIGOL_MODEL_NAME = [string]$modelSettings.model
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}

function Test-AgentReady {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 1
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (Test-AgentReady) {
    Start-Process $agentUrl
    Write-Host "RIGOL Device Agent is already running: $agentUrl"
    exit 0
}

$logDirectory = Join-Path $projectRoot "output\agent"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$stdoutPath = Join-Path $logDirectory "service_stdout.log"
$stderrPath = Join-Path $logDirectory "service_stderr.log"

$agentProcess = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @("-m", "rigol_agent", "serve", "--planner", "auto", "--allow-adaptive-changes") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if (Test-AgentReady) {
        $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        $serviceProcessId = if ($null -ne $listener) { $listener.OwningProcess } else { $agentProcess.Id }
        Set-Content -LiteralPath (Join-Path $logDirectory "service.pid") -Value $serviceProcessId
        Start-Process $agentUrl
        Write-Host "RIGOL Device Agent started: $agentUrl"
        exit 0
    }

    if ($agentProcess.HasExited) {
        $errorText = ""
        if (Test-Path -LiteralPath $stderrPath) {
            $errorText = Get-Content -LiteralPath $stderrPath -Raw
        }
        throw "RIGOL Device Agent stopped during startup.`n$errorText"
    }

    Start-Sleep -Milliseconds 250
}

throw "RIGOL Device Agent did not become ready. See: $stderrPath"
