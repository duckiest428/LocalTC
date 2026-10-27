<#
.SYNOPSIS
    LocalTC's shortcuts: the LocalTC.cmd launcher in the install folder, and LocalTC on the desktop and in the Start
    menu, with the app's own id so Windows names its window after them. LocalTC-Setup.exe runs it (hidden), and so
    does install.ps1.
.PARAMETER Root
    The LocalTC folder (the one with pyproject.toml and .venv).
#>
param([Parameter(Mandatory = $true)][string]$Root)

$ErrorActionPreference = "Stop"
$Venv = Join-Path $Root ".venv"
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
# The app's window has its own app id (LocalTC.App, src/localtc/ui): with the same id on the shortcuts, Windows names it
# from them. Without, the taskbar's menu said "Python" (pythonw.exe's name) with Python's icon.
$ShortcutId = @"
using System;
using System.Runtime.InteropServices;

public static class LocalTcShortcut {
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint count);
        void GetAt(uint index, out PropertyKey key);
        void GetValue(ref PropertyKey key, IntPtr value);
        void SetValue(ref PropertyKey key, ref PropVariant value);
        void Commit();
    }

    [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPersistFile {
        void GetClassID(out Guid classId);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string fileName, uint mode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string fileName, [MarshalAs(UnmanagedType.Bool)] bool remember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string fileName);
        void GetCurFile(out IntPtr fileName);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink { }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid Format; public uint Id; }

    [StructLayout(LayoutKind.Explicit, Size = 24)]
    struct PropVariant { [FieldOffset(0)] public ushort Type; [FieldOffset(8)] public IntPtr Text; }

    // PKEY_AppUserModel_ID on a shortcut (a VT_LPWSTR).
    public static void SetAppId(string path, string appId) {
        object link = new ShellLink();
        ((IPersistFile)link).Load(path, 2);
        var key = new PropertyKey { Format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), Id = 5 };
        var value = new PropVariant { Type = 31, Text = Marshal.StringToCoTaskMemUni(appId) };
        try {
            ((IPropertyStore)link).SetValue(ref key, ref value);
            ((IPropertyStore)link).Commit();
            ((IPersistFile)link).Save(path, true);
        } finally {
            Marshal.FreeCoTaskMem(value.Text);
            Marshal.ReleaseComObject(link);
        }
    }
}
"@
try {
    $shell = New-Object -ComObject WScript.Shell
    $named = $true
    try { Add-Type -TypeDefinition $ShortcutId } catch { $named = $false }
    foreach ($folder in @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))) {
        $path = Join-Path $folder "LocalTC.lnk"
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $App
        $link.WorkingDirectory = $Root
        $link.Description = "LocalTC: offline ATC for MSFS 2024"
        $icon = Join-Path $Root "install\localtc.ico"
        if (Test-Path $icon) { $link.IconLocation = "$icon,0" }
        $link.Save()
        if ($named) { try { [LocalTcShortcut]::SetAppId($path, "LocalTC.App") } catch { $named = $false } }
    }
    Write-Host "Desktop and Start menu shortcuts: LocalTC"
    if (-not $named) { Write-Host "The taskbar may still call the window Python (the shortcut's app id couldn't be set)." }
} catch { Write-Host "Couldn't create shortcuts; start $App instead."; exit 1 }

