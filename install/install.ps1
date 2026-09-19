<#
.SYNOPSIS
    Installs LocalTC on Windows: Python, the LocalTC app with Whisper speech-to-text and Piper voices for ATC,
    GPU support when there's an NVIDIA card, Ollama with its language model, and the downloaded models, so
    flights work offline.

.DESCRIPTION
    Double-click "Install LocalTC.cmd" in the LocalTC folder, or run in PowerShell from that folder:

        powershell -ExecutionPolicy Bypass -File install\install.ps1

    The models are picked for this computer (-Quality auto): its RAM and graphics card decide between the
    light, balanced and quality sets. Change them any time in the app's Quick Settings.

    It is safe to run again: finished steps are skipped. Nothing is sent anywhere; downloads come from
    python.org/winget, PyPI, Hugging Face (Whisper model, Piper voice) and ollama.com.

.PARAMETER Cpu
    Don't install GPU support even if an NVIDIA card is present.
.PARAMETER NoOllama
    Skip Ollama and the language model (LocalTC then understands pilots with its grammar only).
.PARAMETER Quality
    Which models to install: auto (picked from this computer's hardware), light, balanced or quality.
.PARAMETER WhisperModel
    Also download this Whisper model (tiny.en, base.en, small.en, medium.en).
#>
param(
    [switch]$Cpu,
    [switch]$NoOllama,
    [ValidateSet("auto", "light", "balanced", "quality")][string]$Quality = "auto",
    [string]$WhisperModel = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$LocalTC = Join-Path $Venv "Scripts\localtc.exe"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   $text" -ForegroundColor Green }
function Note($text) { Write-Host "   $text" -ForegroundColor Yellow }

function Invoke-Checked {
    # Native programs don't stop the script on failure by themselves; check the exit code.
    param([string]$File, [string[]]$Arguments, [string]$What)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

function Update-Path {
    # Pick up PATH changes from installers that ran in this session.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Find-Python {
    # A Python 3.11-3.13 with a working interpreter; prefers the py launcher.
    # Probing a version that isn't installed writes to stderr, which "Stop" would turn into a fatal error.
    $ErrorActionPreference = "Continue"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @("3.12", "3.13", "3.11")) {
            $exe = & py "-$version" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        }
    }
    foreach ($candidate in @(
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

if (-not (Test-Path (Join-Path $Root "pyproject.toml"))) {
    throw "Run this from the LocalTC folder: powershell -ExecutionPolicy Bypass -File install\install.ps1"
}
Write-Host "Installing LocalTC in $Root" -ForegroundColor White

# --- 1. Python -------------------------------------------------------------------------------------------
Step "Python"
$Python = Find-Python
if (-not $Python) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 is needed. Install it from https://www.python.org/downloads/ (tick 'Add to PATH'), then run this again."
    }
    Note "Installing Python 3.12 with winget ..."
    Invoke-Checked winget @("install", "--exact", "--id", "Python.Python.3.12", "--scope", "user", "--silent",
        "--accept-package-agreements", "--accept-source-agreements") "Installing Python"
    Update-Path
    $Python = Find-Python
    if (-not $Python) { throw "Python was installed but can't be found yet. Open a new PowerShell window and run this again." }
}
Ok "Using $Python"

# --- 2. LocalTC and Whisper ------------------------------------------------------------------------------
Step "LocalTC with Whisper speech-to-text and Piper ATC voices"
if (-not (Test-Path $VenvPython)) {
    Invoke-Checked $Python @("-m", "venv", $Venv) "Creating the Python environment"
}
Invoke-Checked $VenvPython @("-m", "pip", "install", "--upgrade", "--quiet", "pip") "Updating pip"
Invoke-Checked $VenvPython @("-m", "pip", "install", "--quiet", "-e", $Root) "Installing LocalTC"
Ok "Installed (Whisper, Piper, microphone and push-to-talk support included)"

# --- 3. GPU ---------------------------------------------------------------------------------------------------
Step "Graphics card"
$Gpu = $false
if (-not $Cpu -and (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $name = & {
        $ErrorActionPreference = "Continue"  # a driver problem prints to stderr; that just means no GPU
        & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1
    }
    if ($LASTEXITCODE -eq 0 -and $name) {
        Note "NVIDIA $name found: adding CUDA libraries for Whisper (about 1 GB) ..."
        Invoke-Checked $VenvPython @("-m", "pip", "install", "--quiet", "-e", ('{0}[cuda]' -f $Root)) "Installing CUDA support"
        $Gpu = $true
        Ok "Whisper will run on the GPU"
    }
}
if (-not $Gpu) { Ok "Whisper will run on the CPU (fine for push-to-talk)" }

# --- 4. Ollama ------------------------------------------------------------------------------------------------
if (-not $NoOllama) {
    Step "Ollama (the local language model)"
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Note "Installing Ollama with winget ..."
            Invoke-Checked winget @("install", "--exact", "--id", "Ollama.Ollama", "--silent",
                "--accept-package-agreements", "--accept-source-agreements") "Installing Ollama"
            Update-Path
        } else {
            Note "Install Ollama from https://ollama.com/download, then run this again (or use -NoOllama)."
        }
    }
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        $up = $false
        foreach ($i in 1..30) {
            try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 2 | Out-Null; $up = $true; break }
            catch {
                if ($i -eq 1) { Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden }
                Start-Sleep -Seconds 2
            }
        }
        if ($up) { Ok "Ollama is running" } else { Note "Ollama didn't start; start the Ollama app and run this again." }
    }
}

# --- 5. Models -------------------------------------------------------------------------------------------------
Step "Downloading models (once; flights then work offline)"
$SetupArgs = @("setup", "--profile", $Quality)
if ($WhisperModel) { $SetupArgs += @("--whisper-model", $WhisperModel) }
if ($NoOllama) { $SetupArgs += "--no-llm" }
Push-Location $Root
try {
    & $LocalTC @SetupArgs
    if ($LASTEXITCODE -ne 0) { Note "Setup reported problems (see above); LocalTC still runs without the missing parts." }
} finally { Pop-Location }

# --- 6. SimConnect ------------------------------------------------------------------------------------------------
Step "MSFS 2024 SimConnect"
$Dll = @(
    $env:LOCALTC_SIMCONNECT_DLL,
    $(if ($env:MSFS2024_SDK) { Join-Path $env:MSFS2024_SDK "SimConnect SDK\lib\SimConnect.dll" }),
    $(if ($env:MSFS_SDK) { Join-Path $env:MSFS_SDK "SimConnect SDK\lib\SimConnect.dll" }),
    "$env:SystemDrive\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll",
    (Join-Path $Root "SimConnect.dll")
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($Dll) { Ok "Found $Dll" }
else { Note "SimConnect.dll not found. In MSFS 2024: Options > General > Developers > Developer Mode, then install the SDK." }

# --- 7. The app window ---------------------------------------------------------------------------------------------
Step "App window"
$WebView2 = @(
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($WebView2) { Ok "Microsoft Edge WebView2 is installed" }
elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    Note "Installing Microsoft Edge WebView2 (the app's window) with winget ..."
    & winget install --exact --id Microsoft.EdgeWebView2Runtime --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Note "WebView2 didn't install; LocalTC will open in your browser instead." }
} else { Note "WebView2 is missing; LocalTC will open in your browser instead." }

# --- 8. Shortcuts -------------------------------------------------------------------------------------------------
Step "Shortcuts"
$App = Join-Path $Venv "Scripts\localtc-app.exe"
$Launcher = Join-Path $Root "LocalTC.cmd"
@"
@echo off
rem LocalTC from a console: the app, or a command, e.g.
rem   LocalTC.cmd run --source live --voice --destination CYQB --cruise-ft 12000
cd /d "%~dp0"
".venv\Scripts\localtc.exe" %*
if errorlevel 1 pause
"@ | Set-Content -Path $Launcher -Encoding ASCII
try {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
        $link = $shell.CreateShortcut((Join-Path $folder "LocalTC.lnk"))
        $link.TargetPath = $App
        $link.WorkingDirectory = $Root
        $link.Description = "LocalTC: offline ATC for MSFS 2024"
        $link.Save()
    }
    Ok "Desktop and Start menu shortcuts: LocalTC"
} catch { Note "Couldn't create shortcuts; start $App instead." }

Write-Host "`nLocalTC is installed." -ForegroundColor Green
Write-Host "  Start it:         the LocalTC shortcut on the desktop or in the Start menu"
Write-Host "  Then:             New Flight (SimBrief or typed in), Start, and hold Right Ctrl to talk"
Write-Host "  Models, voice, push-to-talk key: the app's Quick Settings"
Write-Host "  From a console:   $Launcher run --source live --voice --destination <ICAO> --cruise-ft <feet>"
