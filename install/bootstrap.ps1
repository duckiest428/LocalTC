<#
.SYNOPSIS
    What LocalTC-Setup.exe runs: fetches the latest LocalTC release from GitHub, checks it, puts it in the
    install folder, and runs install.ps1 there (Python, the models, shortcuts).

.DESCRIPTION
    The release's SHA256SUMS must match the downloaded zip, or nothing is installed. Running it again over
    an install repairs it: the files are replaced, the Python environment (.venv), recordings and
    SimConnect.dll are kept. Any other arguments go on to install.ps1 (-Quality light, -NoOllama, -Cpu).

.PARAMETER Root
    Where LocalTC goes. The installer passes its folder (%LOCALAPPDATA%\Programs\LocalTC by default).
.PARAMETER Version
    A specific release (e.g. 0.2.0) instead of the latest.
#>
param(
    [string]$Root = (Join-Path $env:LOCALAPPDATA "Programs\LocalTC"),
    [string]$Version = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$InstallArgs
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest's progress bar makes downloads many times slower
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = "duckiest428/LocalTC"
$Headers = @{ "User-Agent" = "LocalTC-Setup"; "Accept" = "application/vnd.github+json" }

try {
    Write-Host "== Finding the latest LocalTC release" -ForegroundColor Cyan
    $url = if ($Version) { "https://api.github.com/repos/$Repo/releases/tags/v$Version" } else { "https://api.github.com/repos/$Repo/releases/latest" }
    $release = Invoke-RestMethod -Uri $url -Headers $Headers
    $v = $release.tag_name.TrimStart("v")
    $zipAsset = $release.assets | Where-Object { $_.name -eq "LocalTC-$v.zip" } | Select-Object -First 1
    $sumAsset = $release.assets | Where-Object { $_.name -eq "SHA256SUMS" } | Select-Object -First 1
    if (-not $zipAsset -or -not $sumAsset) { throw "Release $v is missing LocalTC-$v.zip or SHA256SUMS." }
    Write-Host "   LocalTC $v" -ForegroundColor Green

    $work = Join-Path $env:TEMP "LocalTC-setup-$v"
    if (Test-Path $work) { Remove-Item -Recurse -Force $work }
    New-Item -ItemType Directory -Path $work | Out-Null
    $zip = Join-Path $work "LocalTC-$v.zip"
    $sums = Join-Path $work "SHA256SUMS"
    Write-Host "== Downloading" -ForegroundColor Cyan
    Invoke-WebRequest -Uri $zipAsset.browser_download_url -OutFile $zip -Headers $Headers
    Invoke-WebRequest -Uri $sumAsset.browser_download_url -OutFile $sums -Headers $Headers

    $line = Get-Content $sums | Where-Object { $_ -match "\s\*?LocalTC-$([regex]::Escape($v))\.zip$" } | Select-Object -First 1
    if (-not $line) { throw "SHA256SUMS doesn't list LocalTC-$v.zip." }
    $want = ($line -split "\s+")[0].ToLower()
    $got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $want) { throw "The download is corrupt or was tampered with (sha256 $got, expected $want)." }
    Write-Host "   Checked: sha256 matches the release" -ForegroundColor Green

    Expand-Archive -Path $zip -DestinationPath (Join-Path $work "tree") -Force
    $tree = Join-Path $work "tree\LocalTC-$v"
    if (-not (Test-Path (Join-Path $tree "pyproject.toml"))) { throw "The release zip isn't laid out as expected." }

    Write-Host "== Installing into $Root" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    & robocopy $tree $Root /MIR /NFL /NDL /NJH /NP /R:3 /W:2 /XD (Join-Path $Root ".venv") (Join-Path $Root "recordings") /XF "unins*.*" "SimConnect.dll" "LocalTC.cmd" | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the files failed (robocopy $LASTEXITCODE)." }
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "install\install.ps1") @InstallArgs
    if ($LASTEXITCODE -ne 0) { throw "install.ps1 reported a problem (see above)." }
} catch {
    Write-Host "`nLocalTC setup failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Check the internet connection and run LocalTC-Setup.exe again. Nothing already installed was removed."
    Read-Host "Press Enter to close"
    exit 1
}
Read-Host "`nPress Enter to close"
