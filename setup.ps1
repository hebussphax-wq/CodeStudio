param([ValidateSet(0,8,12,16)][int]$VramGB=0,[switch]$SkipPull)
$ErrorActionPreference="Stop"
Write-Host "=== CodeStudio Setup ===" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Write-Error "Python 3.11+ fehlt oder ist nicht im PATH."; exit 1 }
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Write-Error "Ollama fehlt."; exit 1 }
try { Invoke-RestMethod http://localhost:11434/api/tags | Out-Null } catch { Write-Error "Ollama läuft nicht."; exit 1 }
if ($VramGB -eq 0) { $raw=Read-Host "NVIDIA VRAM [8/12/16] GB (Default 16)"; if ([string]::IsNullOrWhiteSpace($raw)) {$VramGB=16} else {$VramGB=[int]$raw} }
switch ($VramGB) { 8 {$model="qwen2.5-coder:7b";$ctx=8192} 12 {$model="qwen2.5-coder:14b";$ctx=12288} 16 {$model="gpt-oss:20b";$ctx=16384} default {Write-Error "Nur 8/12/16 GB"; exit 1} }
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS","1","User")
[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION","1","User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE","q8_0","User")
$configPath=Join-Path $PSScriptRoot "config.json"
$cfg=Get-Content $configPath -Raw | ConvertFrom-Json
$cfg.options.num_ctx=$ctx
$cfg.roles.planner.model=$model
$cfg.roles.coder.model=$model
$cfg.roles.reviewer.model=$model
$cfg | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 $configPath
if (-not $SkipPull) { ollama pull $model }
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "workspace"),(Join-Path $PSScriptRoot "runs"),(Join-Path $PSScriptRoot "backups") | Out-Null
Write-Host "Fertig. Ollama neu starten, dann .\start.cmd" -ForegroundColor Green
