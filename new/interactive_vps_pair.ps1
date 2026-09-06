# Windows PowerShell 包装入口：自动寻找 Python 并启动本地交互式向导。
$ErrorActionPreference = "Stop"
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8
$OutputEncoding = $Utf8
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$MainScript = Join-Path (Split-Path -Parent $ScriptDir) "start.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -B $MainScript
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python -B $MainScript
    exit $LASTEXITCODE
}

if (Get-Command python3 -ErrorAction SilentlyContinue) {
    & python3 -B $MainScript
    exit $LASTEXITCODE
}

Write-Error "没有找到 Python 3。请先从 https://www.python.org/downloads/ 安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。"
exit 2
