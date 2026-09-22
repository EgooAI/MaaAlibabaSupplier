<#
.SYNOPSIS
  Restart the dev preview: kill whatever listens on the env-configured
  ports, then launch the backend + frontend from this checkout.

.DESCRIPTION
  1. Resolves ports: backend from MAA_API_PORT (.env, default 8000),
     frontend from PORT / FRONTEND_PORT / MAA_WEB_PORT (.env, default 3000).
  2. Tree-kills (taskkill /T /F) every process listening on those ports,
     which also takes down backend-spawned children (MaaFW, Yak).
  3. Starts `python -X utf8 -m backend.app.main` (repo root) and `pnpm dev`
     (frontend/), detached with logs under debug/. The dev server's /api
     proxy is pinned to the just-started backend via BACKEND_ORIGIN
     (next.config.ts defaults it to 127.0.0.1:8000, which may be a stale
     leftover process when MAA_API_PORT differs).
  4. Waits until both ports accept connections and prints the preview URLs.

  Assumes project init + env setup are done (see tools/install_all.py).
  Prefers the portable toolchain in .portable/, falls back to PATH.

.PARAMETER BackendPort
  Override the backend API port (0 = resolve from env, default).

.PARAMETER FrontendPort
  Override the frontend dev port (0 = resolve from env, default).

.PARAMETER Only
  Start (and kill) only "backend" or "frontend". Default: both.

.PARAMETER BackendTimeout
  Seconds to wait for the backend port. Default 120.

.PARAMETER FrontendTimeout
  Seconds to wait for the frontend port. Default 180.

.PARAMETER DryRun
  Only print the resolved ports, the PIDs that would be killed, and the
  commands that would run. Kills and starts nothing.
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0,
    [ValidateSet("all", "backend", "frontend")]
    [string]$Only = "all",
    [int]$BackendTimeout = 120,
    [int]$FrontendTimeout = 180,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$DebugDir = Join-Path $RepoRoot "debug"

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
        $eq = $trimmed.IndexOf("=")
        if ($eq -le 0) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $value = $trimmed.Substring($eq + 1).Trim().Trim('"', "'").Trim()
        if ($key -ne "") { $values[$key] = $value }
    }
    return $values
}

function Resolve-Port {
    param([int]$Override, [string[]]$Names, [hashtable]$DotEnv, [int]$Default)
    if ($Override -gt 0) { return $Override }
    foreach ($name in $Names) {
        $fromProcess = [Environment]::GetEnvironmentVariable($name)
        if ($fromProcess -match "^\d+$") { return [int]$fromProcess }
        $fromFile = $DotEnv[$name]
        if ($fromFile -match "^\d+$") { return [int]$fromFile }
    }
    return $Default
}

function Get-ListeningPids {
    param([int]$Port)
    try {
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    } catch {
        @()
    }
}

function Stop-PortListeners {
    param([int]$Port, [string]$Label)
    $pids = @(Get-ListeningPids -Port $Port)
    if ($pids.Count -eq 0) {
        Write-Host "[$Label] port $Port is free, nothing to kill."
        return
    }
    foreach ($listenerPid in $pids) {
        try {
            $proc = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
            $name = if ($proc) { $proc.ProcessName } else { "?" }
            Write-Host "[$Label] killing PID $listenerPid ($name) listening on port $Port ..."
            & taskkill /PID $listenerPid /T /F | Out-Null
        } catch {
            Write-Warning "[$Label] could not kill PID $listenerPid : $_"
        }
    }
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        if (@(Get-ListeningPids -Port $Port).Count -eq 0) { break }
        Start-Sleep -Milliseconds 500
    }
    $still = @(Get-ListeningPids -Port $Port)
    if ($still.Count -gt 0) {
        throw "[$Label] port $Port is still occupied by PID(s) $($still -join ',') after 15s."
    }
    Write-Host "[$Label] port $Port is free."
}

function Test-PortOpen {
    param([int]$Port)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(1000)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-PortOpen {
    param([int]$Port, [string]$Label, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen -Port $Port) {
            Write-Host "[$Label] port $Port is accepting connections."
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "[$Label] port $Port did not open within ${TimeoutSeconds}s. Check the log under debug/."
}

$dotEnv = Read-DotEnv -Path (Join-Path $RepoRoot ".env")
$backendPort = Resolve-Port -Override $BackendPort -Names @("MAA_API_PORT") -DotEnv $dotEnv -Default 8000
$frontendPort = Resolve-Port -Override $FrontendPort -Names @("PORT", "FRONTEND_PORT", "MAA_WEB_PORT") -DotEnv $dotEnv -Default 3000
# Proxy origin follows the backend binding host (MAA_API_HOST, default loopback).
$backendHost = [Environment]::GetEnvironmentVariable("MAA_API_HOST")
if (-not $backendHost) { $backendHost = $dotEnv["MAA_API_HOST"] }
if (-not $backendHost) { $backendHost = "127.0.0.1" }
$proxyHost = if ($backendHost -eq "0.0.0.0") { "127.0.0.1" } else { $backendHost }

$pythonExe = Join-Path $RepoRoot ".portable/python/python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    if (-not $pythonExe) { throw "No Python found (.portable/python/python.exe missing and no python on PATH)." }
}
$nodeDir = Join-Path $RepoRoot ".portable/node"
$pnpmCmd = Join-Path $nodeDir "pnpm.cmd"
if (-not (Test-Path -LiteralPath $pnpmCmd)) {
    $pnpmCmd = (Get-Command pnpm -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    if (-not $pnpmCmd) { throw "No pnpm found (.portable/node/pnpm.cmd missing and no pnpm on PATH)." }
}
if (Test-Path -LiteralPath (Join-Path $nodeDir "node.exe")) {
    $env:PATH = "$nodeDir;$env:PATH"
}

New-Item -ItemType Directory -Path $DebugDir -Force | Out-Null

$startBackend = ($Only -eq "all" -or $Only -eq "backend")
$startFrontend = ($Only -eq "all" -or $Only -eq "frontend")

if ($DryRun) {
    Write-Host "backend port:  $backendPort (python: $pythonExe -X utf8 -m backend.app.main)"
    Write-Host "frontend port: $frontendPort (pnpm: $pnpmCmd dev --port $frontendPort)"
    foreach ($entry in @(@{ Label = "backend"; Port = $backendPort; Wanted = $startBackend },
                         @{ Label = "frontend"; Port = $frontendPort; Wanted = $startFrontend })) {
        if (-not $entry.Wanted) { continue }
        $pids = @(Get-ListeningPids -Port $entry.Port)
        if ($pids.Count -eq 0) {
            Write-Host "[$($entry.Label)] port $($entry.Port): free, nothing would be killed."
        } else {
            foreach ($listenerPid in $pids) {
                $proc = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
                $procName = if ($proc) { $proc.ProcessName } else { "unknown" }
                Write-Host "[$($entry.Label)] port $($entry.Port): would kill PID $listenerPid ($procName)."
            }
        }
    }
    Write-Host "Dry run only; nothing was killed or started."
    exit 0
}

if ($startBackend) { Stop-PortListeners -Port $backendPort -Label "backend" }
if ($startFrontend) { Stop-PortListeners -Port $frontendPort -Label "frontend" }

if ($startBackend) {
    $backendLog = Join-Path $DebugDir "dev-preview-backend.log"
    Write-Host "[backend] starting: $pythonExe -X utf8 -m backend.app.main (log: debug/dev-preview-backend.log)"
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    $env:NO_COLOR = "1"
    $env:TERM = "dumb"
    $backendProc = Start-Process -FilePath $pythonExe -ArgumentList "-X", "utf8", "-u", "-m", "backend.app.main" `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $backendLog -RedirectStandardError ($backendLog + ".err")
    Write-Host "[backend] PID $($backendProc.Id), waiting for port $backendPort ..."
    Wait-PortOpen -Port $backendPort -Label "backend" -TimeoutSeconds $BackendTimeout
}

if ($startFrontend) {
    $frontendLog = Join-Path $DebugDir "dev-preview-frontend.log"
    Write-Host "[frontend] starting: pnpm dev --port $frontendPort (log: debug/dev-preview-frontend.log)"
    $env:PORT = "$frontendPort"  # inherited by the child; Start-Process -Environment is PS7-only
    # Pin the /api proxy to the backend this script started; the next.config
    # default (127.0.0.1:8000) would otherwise follow MAA_API_PORT-independent
    # history and may hit a stale leftover process.
    $env:BACKEND_ORIGIN = "http://${proxyHost}:$backendPort"
    $frontendProc = Start-Process -FilePath $pnpmCmd -ArgumentList "dev", "--port", "$frontendPort" `
        -WorkingDirectory (Join-Path $RepoRoot "frontend") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $frontendLog -RedirectStandardError ($frontendLog + ".err")
    Write-Host "[frontend] PID $($frontendProc.Id), waiting for port $frontendPort ..."
    Wait-PortOpen -Port $frontendPort -Label "frontend" -TimeoutSeconds $FrontendTimeout
}

Write-Host ""
Write-Host "Dev preview is up from this checkout:"
if ($startFrontend) { Write-Host "  frontend: http://127.0.0.1:$frontendPort (proxies /api -> ${proxyHost}:$backendPort)" }
if ($startBackend) { Write-Host "  backend:  http://127.0.0.1:$backendPort/api/status" }
Write-Host "Logs: debug/dev-preview-backend.log[.err], debug/dev-preview-frontend.log[.err]"
