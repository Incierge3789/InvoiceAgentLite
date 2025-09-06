; installer.iss
[Setup]
AppName=InvoiceAgent Lite
AppVersion=1.1.1
; ★管理者権限なら Program Files、通常権限なら %LocalAppData%\Programs に自動切替
DefaultDirName={autopf}\InvoiceAgentLite
DefaultGroupName=InvoiceAgent Lite
DisableDirPage=no
DisableProgramGroupPage=no
OutputBaseFilename=InvoiceAgentLite_Setup_v1.1.1
Compression=lzma
SolidCompression=yes
; ★一般ユーザーで入れられる設定のままでOK
PrivilegesRequired=lowest

[Files]
Source: "dist\InvoiceAgentLite.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "data\*"; DestDir: "{app}\data"; Flags: recursesubdirs
Source: "README.md"; DestDir: "{app}"

[Icons]
Name: "{group}\InvoiceAgent Lite"; Filename: "{app}\InvoiceAgentLite.exe"
; ★共通デスクトップは管理者が必要 → ユーザーデスクトップに変更
Name: "{userdesktop}\InvoiceAgent Lite"; Filename: "{app}\InvoiceAgentLite.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成"; GroupDescription: "その他"

[Run]
Filename: "{app}\InvoiceAgentLite.exe"; Description: "起動"; Flags: nowait postinstall skipifsilent
