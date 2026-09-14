param(
    [ValidateSet(0, 8, 12, 16, 24)]
    [int]$VramGB = 0,
    [switch]$SkipPull
)

$ErrorActionPreference = "Stop"

Write-Host "=== KI-Codestudio v0.3 Setup ===" -ForegroundColor Cyan

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python wurde nicht gefunden. Python 3.11+ installieren und PATH aktivieren."
    exit 1
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Error "Ollama wurde nicht gefunden. Ollama fuer Windows installieren und starten."
    exit 1
}

Write-Host "Pruefe Ollama..."
try {
    Invoke-RestMethod http://localhost:11434/api/tags | Out-Null
}
catch {
    Write-Error "Ollama API ist nicht erreichbar. Ollama-App starten oder 'ollama serve' ausfuehren."
    exit 1
}

if ($VramGB -eq 0) {
    $raw = Read-Host "NVIDIA VRAM in GB [8/12/16/24] (Default 16)"
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $VramGB = 16
    } else {
        $VramGB = [int]$raw
        if ($VramGB -notin @(8, 12, 16, 24)) {
            Write-Error "Bitte 8, 12, 16 oder 24 angeben."
            exit 1
        }
    }
}

[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "1", "User")

switch ($VramGB) {
    8 {
        $model = "qwen2.5-coder:7b"
        $numCtx = 8192
    }
    12 {
        $model = "qwen2.5-coder:14b"
        $numCtx = 12288
    }
    16 {
        $model = "gpt-oss:20b"
        $numCtx = 16384
    }
    24 {
        $model = "qwen3-coder:30b"
        $numCtx = 16384
    }
}

$configPath = Join-Path $PSScriptRoot "config.json"
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json

$cfg.options.num_ctx = $numCtx
$cfg.roles.planer.model = $model
$cfg.roles.coder.model = $model
$cfg.roles.reviewer.model = $model

$cfg | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 $configPath

if (-not $SkipPull) {
    Write-Host "Ziehe Modell: $model"
    ollama pull $model
}

New-Item -ItemType Directory -Force `
    -Path (Join-Path $PSScriptRoot "workspace"), `
          (Join-Path $PSScriptRoot "runs"), `
          (Join-Path $PSScriptRoot "backups") | Out-Null

Write-Host ""
Write-Host "Profil gesetzt:" -ForegroundColor Green
Write-Host "  VRAM:     $VramGB GB"
Write-Host "  Modell:   $model"
Write-Host "  num_ctx:  $numCtx"
Write-Host ""
Write-Host "Fallback bei VRAM-Druck:"
switch ($VramGB) {
    12 { Write-Host "  qwen2.5-coder:7b" }
    16 { Write-Host "  qwen2.5-coder:14b   oder   qwen3-coder:30b (Offload)" }
    24 { Write-Host "  gpt-oss:20b" }
}
Write-Host ""
Write-Host "WICHTIG: Ollama jetzt einmal vollstaendig beenden und neu starten,"
Write-Host "damit OLLAMA_MAX_LOADED_MODELS=1 sicher vom Server uebernommen wird."
Write-Host ""
Write-Host "Danach:" -ForegroundColor Cyan
Write-Host '  python studio.py --doctor'
Write-Host '  python studio.py "deine Aufgabe"'
