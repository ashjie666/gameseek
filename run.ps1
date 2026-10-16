# 新游雷达 - 一键运行
#
#   .\run.ps1                采集一次，然后启动本地预览服务器
#   .\run.ps1 -CollectOnly   只采集，不启动服务器
#   .\run.ps1 -Probe         只体检媒体源，不写数据库
#   .\run.ps1 -Llm           用 LLM 抽取（需先设置 GAMENEWS_LLM_* 环境变量）
#   .\run.ps1 -Days 7 -Port 9000
#
# 本机没有把 python 加进 PATH 时，脚本会自动回退到 %USERPROFILE%\.local\bin\python3.12.exe。

param(
    [switch]$CollectOnly,
    [switch]$Probe,
    [switch]$Llm,
    [switch]$NoCache,
    [int]$Days = 30,
    [int]$Port = 8321
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Resolve-Python {
    $candidates = @()
    foreach ($name in @('python', 'python3', 'py')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }
    $candidates += @(
        (Join-Path $env:USERPROFILE '.local\bin\python3.12.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:USERPROFILE 'miniconda3\python.exe'),
        (Join-Path $env:USERPROFILE 'miniforge3\python.exe')
    )
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        # 跳过 Microsoft Store 的占位程序，它只会弹出应用商店
        if ($candidate -like '*\WindowsApps\*') { continue }
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw '未找到 Python 3.10+。请先安装 Python，或把解释器路径加进 PATH。'
}

$python = Resolve-Python
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "使用解释器: $python" -ForegroundColor DarkGray

$collectorArgs = @('-m', 'collector.main')
if ($Probe)     { $collectorArgs += '--probe' }
if ($Llm)       { $collectorArgs += '--llm' }
if ($NoCache)   { $collectorArgs += '--no-cache' }
if (-not $Probe) { $collectorArgs += @('--days', $Days) }

& $python @collectorArgs
if ($LASTEXITCODE -ne 0) { throw "采集器退出码 $LASTEXITCODE" }

if ($Probe -or $CollectOnly) { return }

Write-Host ''
Write-Host "预览地址: http://localhost:$Port/" -ForegroundColor Green
Write-Host '按 Ctrl+C 停止服务器。' -ForegroundColor DarkGray
& $python -m http.server $Port --directory web
