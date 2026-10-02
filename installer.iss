; CleanTake Windows installer script (Inno Setup 6)
; Built by GitHub Actions:  iscc installer.iss /dMyAppVersion=1.0.1
; Local test (needs dist\CleanTake from PyInstaller first):
;   iscc installer.iss /dMyAppVersion=1.0.1
;
; Notes:
; - PrivilegesRequired=lowest => per-user install, no admin prompt.
;   {autopf} then resolves to %LOCALAPPDATA%\Programs\CleanTake.
; - The setup EXE is NOT code-signed, so Windows SmartScreen will show
;   "Unknown publisher" on first run. Users click "More info" -> "Run anyway".
;   (Same situation as the AudioX competitor; a code signing cert costs money,
;   out of scope for the 0d budget.)

#define MyAppName "CleanTake"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher "BronzeBase"

[Setup]
AppId={{9a41c744-466d-44a2-bc2d-b542a8fb7cb7}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/bronzebasehq-lang/cleantake
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputBaseFilename=CleanTake-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Files]
Source: "dist\CleanTake\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\CleanTake.exe"
