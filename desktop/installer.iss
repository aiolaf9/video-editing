; Installer Windows NH AI Clip Studio (Inno Setup 6). Dibangun oleh .github/workflows/desktop.yml:
;   ISCC.exe /DAppVersion=0.1.0 desktop\installer.iss
; Bawaan: dipasang per pengguna (tanpa hak admin) di %LOCALAPPDATA%\Programs\NH AI Clip Studio.
; Data proyek (%LOCALAPPDATA%\NH AI Clip Studio) dan hasil ekspor (Videos\NH AI Clip Studio) TIDAK dihapus saat
; uninstall. Installer tidak mengubah pengaturan antivirus / Windows Defender / SmartScreen.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "NH AI Clip Studio"
#define AppExe "NH-AI-Clip-Studio.exe"

[Setup]
AppId={{AFAD5456-35F0-4DD1-80CA-1C4A7418DFB8}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} (beta internal)
; Penerbit: placeholder sampai identitas resmi NH ditetapkan.
AppPublisher=NH (placeholder)
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=NH-AI-Clip-Studio-Setup
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
SetupIconFile=..\build\desktop\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Update dari versi lama: berkas program lama (mis. model base M1, 145 MB) dibersihkan dulu. HANYA folder program;
; proyek, database, dan hasil editing ada di %LOCALAPPDATA%\NH AI Clip Studio (bukan {app}) dan tidak disentuh.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\NH-AI-Clip-Studio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\PANDUAN_UJI_NH.md"; DestDir: "{app}"; DestName: "Panduan Uji Akurasi NH.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\Panduan Uji Akurasi NH"; Filename: "{app}\Panduan Uji Akurasi NH.txt"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
