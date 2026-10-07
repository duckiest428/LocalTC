<#
.SYNOPSIS
    Install LocalTC from this checkout's current commit, as the test LocalTC-Setup.exe would, without building it.

.DESCRIPTION
    Zips the committed source (git HEAD; uncommitted changes are left out, as in a release) and runs bootstrap.ps1
    with it: the same install as the setup's, into the same folder, keeping the Python environment, recordings and
    SimConnect.dll. Run it after each pull to fly the latest commit:

        powershell -ExecutionPolicy Bypass -File install\install-commit.ps1

    Any other arguments go on to install.ps1 (-Quality light, -NoOllama, -Cpu).
#>
param(
    [string]$Root = (Join-Path $env:LOCALAPPDATA "Programs\LocalTC"),
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallArgs
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$commit = (git -C $repo rev-parse --short HEAD).Trim()
if (-not $commit) { throw "Not a git checkout: $repo" }
$v = (Select-String -Path (Join-Path $repo "src\localtc\__init__.py") -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
$zip = Join-Path $env:TEMP "LocalTC-$v-$commit.zip"
Write-Host "== LocalTC $v at commit $commit" -ForegroundColor Cyan
git -C $repo archive --format=zip "--prefix=LocalTC-$v/" -o $zip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
& (Join-Path $PSScriptRoot "bootstrap.ps1") -Root $Root -Zip $zip @InstallArgs
exit $LASTEXITCODE
