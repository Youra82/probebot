# Probebot — Market Forensics Runner (PowerShell)
param(
    [string]$Symbol = "BTC/USDT:USDT",
    [string]$Timeframe = "1d",
    [string]$StartDate = "2022-01-01",
    [string]$EndDate = "2025-01-01",
    [string]$Mode = "full",
    [string]$InvestigateDate = "",
    [double]$MinMovePct = 2.5,
    [int]$TopN = 5,
    [string]$MovementTypes = "",
    [switch]$NoDrillDown,
    [switch]$Clear
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = "$ScriptDir\src"

$args_list = @(
    "--symbol", $Symbol,
    "--timeframe", $Timeframe,
    "--start_date", $StartDate,
    "--end_date", $EndDate,
    "--mode", $Mode,
    "--min_move_pct", $MinMovePct,
    "--top_n", $TopN
)

if ($InvestigateDate) { $args_list += "--investigate_date", $InvestigateDate }
if ($MovementTypes)   { $args_list += "--movement_types", $MovementTypes }
if ($NoDrillDown)     { $args_list += "--no_drill_down" } else { $args_list += "--drill_down" }
if ($Clear)           { $args_list += "--clear" }

Write-Host "=== PROBEBOT — Market Forensics ===" -ForegroundColor Cyan
Write-Host "Symbol: $Symbol | TF: $Timeframe | $StartDate -> $EndDate" -ForegroundColor White

python -m probebot.run @args_list
