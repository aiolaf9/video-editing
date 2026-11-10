; Paket model tambahan OPSIONAL untuk NH AI Clip Studio: mode transkripsi "Kualitas tinggi" (Whisper large-v3-turbo).
; Tidak termasuk installer utama. Dipasang per pengguna ke folder data aplikasi:
;   %LOCALAPPDATA%\NH AI Clip Studio\models\whisper-large-v3-turbo
; Aplikasi menemukannya otomatis saat dibuka berikutnya. Butuh RAM lebih besar & waktu ±sama dengan durasi video di
; CPU 4 inti (lihat docs/M1_1_TRANSKRIP.md). Dibangun oleh .github/workflows/desktop.yml:
;   ISCC.exe /DAppVersion=0.1.0 desktop\model_addon.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "NH AI Clip Studio - Model Kualitas Tinggi"

[Setup]
AppId={{3C9A1E2D-7B44-4F0A-9E21-5D8C6B1F0A77}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=NH (placeholder)
DefaultDirName={localappdata}\NH AI Clip Studio\models\whisper-large-v3-turbo
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=NH-AI-Clip-Studio-Model-KualitasTinggi
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=4
WizardStyle=modern
InfoBeforeFile=addon_info.txt
SetupIconFile=..\build\desktop\app.ico
Uninstallable=yes
UninstallDisplayName={#AppName}
CreateUninstallRegKey=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\build\addon\whisper-large-v3-turbo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
