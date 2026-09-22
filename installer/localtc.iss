; LocalTC-Setup.exe: a small installer that fetches the latest release from GitHub and sets it up.
; Built by .github/workflows/release.yml:  ISCC.exe /DAppVersion=X.Y.Z installer\localtc.iss
; Per-user (no administrator rights): %LOCALAPPDATA%\Programs\LocalTC.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8C1F6A52-4E1B-4B8E-9C0B-6A1D2B7E4C10}
AppName=LocalTC
AppVersion={#AppVersion}
AppVerName=LocalTC
AppPublisher=LocalTC
AppPublisherURL=https://localtc.tech
AppSupportURL=https://github.com/duckiest428/LocalTC/issues
DefaultDirName={localappdata}\Programs\LocalTC
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LocalTC-Setup
SetupIconFile=..\install\localtc.ico
UninstallDisplayIcon={app}\install\localtc.ico
UninstallDisplayName=LocalTC
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Messages]
FinishedLabel=LocalTC is installed. Start it from the LocalTC shortcut on the desktop or in the Start menu.

[Files]
Source: "..\install\bootstrap.ps1"; DestDir: "{app}\install"; Flags: ignoreversion

[Run]
; The window stays open so the progress (Python, the models: several minutes the first time) can be seen.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install\bootstrap.ps1"" -Root ""{app}"""; StatusMsg: "Downloading LocalTC and setting it up ..."; Flags: waituntilterminated

[UninstallDelete]
; Everything in the install folder, including the Python environment. Downloaded models, settings and the
; logbook live in %LOCALAPPDATA%\LocalTC and are kept; delete that folder too to remove every trace.
Type: filesandordirs; Name: "{app}"
Type: files; Name: "{userdesktop}\LocalTC.lnk"
Type: files; Name: "{userprograms}\LocalTC.lnk"
