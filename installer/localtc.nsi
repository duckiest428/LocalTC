; LocalTC-Setup.exe: a per-user installer (no administrator rights) into %LOCALAPPDATA%\Programs\LocalTC.
; It carries everything LocalTC itself needs and downloads nothing: LocalTC's files, a Python of its own (runtime\) and
; every package as a wheel. install\setup_env.py, run hidden with that Python, makes the environment (.venv) and
; installs LocalTC into it; the output shows in this window. Ollama, the models, GPU support and WebView2 come after,
; from the app's setup guide (localtc.components and localtc.models).
;
; Built with NSIS (makensis runs on Windows, macOS and Linux) by tools/build_installer.py, which passes:
;   AppVersion, OutFile, LicenseFile, SourceDir (the committed source), RuntimeDir (Python), WheelsDir (the packages)

Unicode true
SetCompressor /SOLID lzma
RequestExecutionLevel user

!ifndef AppVersion
  !define AppVersion "0.0.0"
!endif
!ifndef OutFile
  !define OutFile "..\dist\LocalTC-Setup.exe"
!endif
!define UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\LocalTC"

Name "LocalTC"
Caption "LocalTC ${AppVersion} Setup"
OutFile "${OutFile}"
Icon "..\install\localtc.ico"
UninstallIcon "..\install\localtc.ico"
InstallDir "$LOCALAPPDATA\Programs\LocalTC"
InstallDirRegKey HKCU "${UninstallKey}" "InstallLocation"
BrandingText "LocalTC ${AppVersion}"
ShowInstDetails show
VIProductVersion "${AppVersion}.0"
VIAddVersionKey "ProductName" "LocalTC"
VIAddVersionKey "ProductVersion" "${AppVersion}"
VIAddVersionKey "FileVersion" "${AppVersion}"
VIAddVersionKey "FileDescription" "LocalTC Setup"
VIAddVersionKey "LegalCopyright" "LocalTC contributors, AGPL-3.0"

!include "MUI2.nsh"
!define MUI_ICON "..\install\localtc.ico"
!define MUI_UNICON "..\install\localtc.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "LocalTC ${AppVersion}"
!define MUI_WELCOMEPAGE_TEXT "ATC and a copilot for MSFS 2024, running on this PC.$\r$\n$\r$\nThis installs LocalTC with everything it needs to start: no administrator rights, nothing downloaded.$\r$\n$\r$\nWhen LocalTC opens, its setup guide downloads the rest once (the speech recognition, the voices and the language model) and sets up your microphone. Flights then work offline.$\r$\n$\r$\nIf LocalTC is running, it is closed first."
!define MUI_FINISHPAGE_TITLE "LocalTC is installed"
!define MUI_FINISHPAGE_TEXT "Start it from the LocalTC shortcut on the desktop or in the Start menu. The first time, its setup guide walks you through the rest."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start LocalTC"
!define MUI_FINISHPAGE_RUN_FUNCTION StartLocalTC
!insertmacro MUI_PAGE_WELCOME
!ifdef LicenseFile
  !insertmacro MUI_PAGE_LICENSE "${LicenseFile}"
!endif
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!macro CloseLocalTC
  ; Its files can't be replaced while it runs: asked to close, then made to.
  nsExec::Exec 'taskkill /IM localtc-app.exe /T'
  Pop $0
  Sleep 2000
  nsExec::Exec 'taskkill /IM localtc-app.exe /T /F'
  Pop $0
!macroend

Function StartLocalTC
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\.venv\Scripts\localtc-app.exe"'
FunctionEnd

Section "LocalTC"
  SetShellVarContext current
  AddSize 650000
  DetailPrint "Closing LocalTC if it's running ..."
  !insertmacro CloseLocalTC

  ; LocalTC's files, as this version has them: the old ones go first so nothing removed since stays behind. The
  ; environment (.venv), the recordings and the settings (in %LOCALAPPDATA%\LocalTC) are kept.
  RMDir /r "$INSTDIR\src"
  RMDir /r "$INSTDIR\config"
  RMDir /r "$INSTDIR\install"
  SetOutPath "$INSTDIR"
  DetailPrint "Copying LocalTC ${AppVersion} ..."
  File /r "${SourceDir}\*.*"

  ; Its own Python (the one the environment runs on).
  RMDir /r "$INSTDIR\runtime"
  SetOutPath "$INSTDIR\runtime"
  DetailPrint "Copying Python ..."
  File /r "${RuntimeDir}\*.*"

  ; The packages, only for the install: in the temporary folder, which goes when the setup closes.
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\wheels"
  DetailPrint "Unpacking LocalTC's packages ..."
  File /r "${WheelsDir}\*.*"

  SetOutPath "$INSTDIR"
  DetailPrint "Setting up LocalTC's Python environment (a minute or two) ..."
  nsExec::ExecToLog '"$INSTDIR\runtime\python.exe" -u -I "$INSTDIR\install\setup_env.py" --root "$INSTDIR" --wheels "$PLUGINSDIR\wheels"'
  Pop $0
  StrCmp $0 "0" envok
    DetailPrint "Setting up the environment failed ($0). Nothing you had is lost: run LocalTC-Setup.exe again."
    Abort "LocalTC setup did not finish (the environment, code $0). See the details above."
  envok:

  DetailPrint "Shortcuts ..."
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\install\shortcuts.ps1" -Root "$INSTDIR"'
  Pop $0
  StrCmp $0 "0" linksok
    ; Without PowerShell: plain shortcuts (Windows may then call the window Python on the taskbar).
    CreateShortcut "$DESKTOP\LocalTC.lnk" "$INSTDIR\.venv\Scripts\localtc-app.exe" "" "$INSTDIR\install\localtc.ico" 0
    CreateShortcut "$SMPROGRAMS\LocalTC.lnk" "$INSTDIR\.venv\Scripts\localtc-app.exe" "" "$INSTDIR\install\localtc.ico" 0
  linksok:

  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${UninstallKey}" "DisplayName" "LocalTC"
  WriteRegStr HKCU "${UninstallKey}" "DisplayVersion" "${AppVersion}"
  WriteRegStr HKCU "${UninstallKey}" "Publisher" "LocalTC"
  WriteRegStr HKCU "${UninstallKey}" "URLInfoAbout" "https://localtc.tech"
  WriteRegStr HKCU "${UninstallKey}" "DisplayIcon" "$INSTDIR\install\localtc.ico"
  WriteRegStr HKCU "${UninstallKey}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UninstallKey}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "${UninstallKey}" "NoModify" 1
  WriteRegDWORD HKCU "${UninstallKey}" "NoRepair" 1
  DetailPrint "LocalTC ${AppVersion} is installed."
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  !insertmacro CloseLocalTC
  ; Everything in the install folder: LocalTC, its Python and its environment. Downloaded models, settings and the
  ; logbook live in %LOCALAPPDATA%\LocalTC and are kept; delete that folder too to remove every trace. Ollama, if
  ; LocalTC installed it, is a program of its own (Settings > Apps).
  Delete "$DESKTOP\LocalTC.lnk"
  Delete "$SMPROGRAMS\LocalTC.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UninstallKey}"
SectionEnd
