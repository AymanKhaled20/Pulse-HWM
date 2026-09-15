; Pulse-HWM ??? Inno Setup script
; Build (if Inno Setup installed):
;   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\pulse-hwm.iss
; Output: installer\output\PulseHWM-Setup-<version>.exe

#define AppName "Pulse-HWM"
#include "version.iss"
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
; the running app flags itself with this mutex so setup can detect (and, in
; silent mode, close) an instance that is still using the files
AppMutex=PulseHWMAppMutex
; Restart-Manager closes the app when files are locked (default), never
; restart-via-RM: the updater controls its own relaunch via /LAUNCHAFTER
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Start Pulse-HWM at login"; Flags: unchecked

[Files]
Source: "..\dist\PulseHWM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; pulsehwm:// URL scheme (auth callbacks from Google/GitHub/reset emails).
; HKCU = per-user, no admin needed; uninstall removes the registration.
[Registry]
Root: HKCU; Subkey: "Software\Classes\pulsehwm"; ValueType: string; ValueData: "URL:Pulse-HWM Auth Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\pulsehwm"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\pulsehwm\DefaultIcon"; ValueType: string; ValueData: "{app}\{#AppExeName},0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\pulsehwm\shell\open\command"; ValueType: string; ValueData: """{app}\{#AppExeName}"" ""%1"""; Flags: uninsdeletekey

[Icons]
Name: "{group}\PulseHWM"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\PulseHWM"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\PulseHWM"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; silent-update relaunch: ONLY when the in-app updater passes /LAUNCHAFTER=1
Filename: "{app}\{#AppExeName}"; Flags: nowait; Check: LaunchAfter

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"

[Code]
// /LAUNCHAFTER=1 is set by the in-app updater's silent install; normal
// interactive installs keep the classic postinstall checkbox instead
function LaunchAfter: Boolean;
begin
  Result := ExpandConstant('{param:LAUNCHAFTER|0}') = '1';
end;
