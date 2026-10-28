param([string]$PythonPath)
$ErrorActionPreference = "Stop"
$studioRoot = $PSScriptRoot
if (-not $PythonPath) { $PythonPath = (Get-Command pythonw.exe -ErrorAction Stop).Source }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw "找不到 Python 运行环境" }
$studioPrograms = [Environment]::GetFolderPath("Programs")
$studioShortcut = Join-Path $studioPrograms "守桥脚本工作室.lnk"
$studioShell = New-Object -ComObject WScript.Shell
$studioLink = $studioShell.CreateShortcut($studioShortcut)
$studioLink.TargetPath = $PythonPath
$studioLink.Arguments = ([char]34) + (Join-Path $studioRoot "launch_studio.py") + ([char]34)
$studioLink.WorkingDirectory = $studioRoot
$studioLink.IconLocation = (Join-Path $studioRoot "static\favicon.ico") + ",0"
$studioLink.Description = "守桥脚本工作室 · 口播生成与分镜精修"
$studioLink.WindowStyle = 7
$studioLink.Save()
Write-Output $studioShortcut
