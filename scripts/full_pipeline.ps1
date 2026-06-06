# One-click pipeline: sync A -> export attachments -> parse B -> rebuild C -> start E
param(
    [switch]$SkipSync,
    [switch]$SkipExport,
    [switch]$SkipParse,
    [switch]$SkipRebuild,
    [switch]$StartServer
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "== Learning Assistant full pipeline ==" -ForegroundColor Cyan

if (-not (Test-Path ".env")) {
    Write-Host "Bootstrapping .env from local txt files (if present)..." -ForegroundColor Yellow
    python -c "from backend.app.env_manager import bootstrap_from_txt_files; print(bootstrap_from_txt_files())"
}

if (-not $SkipSync) {
    Write-Host "[1/4] Sync A module (all channels)..." -ForegroundColor Green
    python main.py --channel all --allow-network --output storage/collector.jsonl
}

if (-not $SkipExport) {
    Write-Host "[2/4] Export attachments..." -ForegroundColor Green
    $jsonl = if (Test-Path "storage/collector.jsonl") { "storage/collector.jsonl" } else { "storage/demo_collector.jsonl" }
    python scripts/export_attachments.py --source learn --jsonl $jsonl --limit 20
}

if (-not $SkipParse) {
    Write-Host "[3/4] Parse materials (B module)..." -ForegroundColor Green
    python scripts/parse_materials.py --incremental --records-jsonl storage/collector.jsonl
}

if (-not $SkipRebuild) {
    Write-Host "[4/4] Rebuild knowledge index (C module)..." -ForegroundColor Green
    python -c "from backend.app.adapters.module_c import ModuleCAdapter; import json; print(json.dumps(ModuleCAdapter().rebuild(force=True), ensure_ascii=False, indent=2))"
}

Write-Host "Running e2e verification..." -ForegroundColor Cyan
python scripts/verify_e2e.py

if ($StartServer) {
    Write-Host "Starting backend + frontend..." -ForegroundColor Cyan
    & "$PSScriptRoot/dev_start.ps1"
}

Write-Host "Pipeline complete." -ForegroundColor Cyan
