; Pulse-HWM — Inno Setup script
; Build (if Inno Setup installed):
;   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\pulse-hwm.iss
; Output: installer\output\PulseHWM-Setup-<version>.exe

#define AppName "Pulse-HWM"
#define AppVersion "1.0.0"
#define AppExeName "PulseHWM.exe"
#define AppPublisher "Pulse-HWM"

[Setup]
AppId={{4E5C1B77-D5F2-4A0F-9B33-2D1C1B0AA001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\PulseHWM
DefaultGroupName=PulseHWM
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=PulseHWM-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=..\pulse_hwm\assets\icons\pulse.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start Pulse-HWM at login"; Flags: unchecked

[Files]
Source: "..\dist\PulseHWM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PulseHWM"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\PulseHWM"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\PulseHWM"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
