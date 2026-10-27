; Ralenti installer (Inno Setup 6.3 or newer)
; Build: ISCC /DAppVersion=0.9.0 installer\ralenti.iss
; Optional: /DAppName=... /DAppGuid=... /DOutName=... for a separately installed variant.
; NEVER change the default AppGuid: Windows uses it to recognize upgrades.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppName
  #define AppName "Ralenti"
#endif
#ifndef AppGuid
  #define AppGuid "7E4C2B1A-5D3F-4A8E-9B6C-2F1D8E0A4C71"
#endif
#ifndef OutName
  #define OutName "Ralenti"
#endif
#define AppExe "Ralenti.exe"
#define AppUrl "https://github.com/Asterix404/ralenti"

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Asterix404
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
WizardSizePercent=110
SetupIconFile=..\ralenti\resources\ralenti.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename={#OutName}-{#AppVersion}-setup
Compression=lzma2/ultra64
SolidCompression=yes
AppMutex=RalentiSingleInstance
CloseApplications=yes
ShowLanguageDialog=auto

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Ralenti\*"; DestDir: "{app}"; Excludes: "tools\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: ShouldRelaunch

[CustomMessages]
english.DeleteData=Also delete your Ralenti settings, presets, logs and downloaded tools (FFmpeg, RIFE)?%n%nThey are stored in %1.
french.DeleteData=Supprimer aussi les réglages, préréglages, journaux et outils téléchargés de Ralenti (FFmpeg, RIFE) ?%n%nIls se trouvent dans %1.

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\Ralenti');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox(FmtMessage(CustomMessage('DeleteData'), [DataDir]), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
