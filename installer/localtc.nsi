; LocalTC-Setup.exe: a small per-user installer (no administrator rights) into %LOCALAPPDATA%\Programs\LocalTC.
; It runs install\bootstrap.ps1, which fetches the latest release from GitHub, checks it and runs install.ps1
; (Python, the models, the shortcuts).
;
; Built with NSIS (makensis runs on Windows, macOS and Linux) by tools/build_installer.py:
;   python tools/build_installer.py                # the release's setup (downloads the latest release)
;   python tools/build_installer.py --offline      # a test build carrying this checkout's committed source
;
; Defines (from build_installer.py): AppVersion, OutFile, LicenseFile, and SourceZip for the offline build.

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
!ifdef SourceZip
  Caption "LocalTC ${AppVersion} Setup (test build)"
!endif
OutFile "${OutFile}"
Icon "..\install\localtc.ico"
UninstallIcon "..\install\localtc.ico"
InstallDir "$LOCALAPPDATA\Programs\LocalTC"
InstallDirRegKey HKCU "${UninstallKey}" "InstallLocation"
BrandingText "LocalTC ${AppVersion}"
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
!define MUI_FINISHPAGE_TITLE "LocalTC is installed"
!define MUI_FINISHPAGE_TEXT "Start it from the LocalTC shortcut on the desktop or in the Start menu."
!ifdef LicenseFile
  !insertmacro MUI_PAGE_LICENSE "${LicenseFile}"
!endif
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "LocalTC"
  SetShellVarContext current
  SetOutPath "$INSTDIR\install"
  File "..\install\bootstrap.ps1"
  File "..\install\localtc.ico"
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

  ; The PowerShell window stays open so the progress (Python, the models: several minutes the first time)
  ; can be seen.
  DetailPrint "Downloading LocalTC and setting it up (see the PowerShell window) ..."
!ifdef SourceZip
  InitPluginsDir
  File "/oname=$PLUGINSDIR\LocalTC-source.zip" "${SourceZip}"
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\install\bootstrap.ps1" -Root "$INSTDIR" -Zip "$PLUGINSDIR\LocalTC-source.zip"' $0
!else
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\install\bootstrap.ps1" -Root "$INSTDIR"' $0
!endif
  IntCmp $0 0 done
    SetDetailsView show
    DetailPrint "The setup reported a problem (exit code $0). Nothing already installed was removed."
    Abort "LocalTC setup did not finish. Run LocalTC-Setup.exe again."
  done:
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  ; Everything in the install folder, including the Python environment. Downloaded models, settings and the
  ; logbook live in %LOCALAPPDATA%\LocalTC and are kept; delete that folder too to remove every trace.
  Delete "$DESKTOP\LocalTC.lnk"
  Delete "$SMPROGRAMS\LocalTC.lnk"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UninstallKey}"
SectionEnd
