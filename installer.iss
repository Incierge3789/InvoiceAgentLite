; installer.iss
[Setup]
AppName=InvoiceAgent Lite
AppVersion=1.1.0
DefaultDirName={pf}\InvoiceAgentLite
DefaultGroupName=InvoiceAgent Lite
DisableDirPage=no
DisableProgramGroupPage=no
OutputBaseFilename=InvoiceAgentLite_Setup_1.1.0
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest

[Files]
Source: "dist\InvoiceAgentLite.exe"; DestDir: "{app}"; Flags: ignoreversion
; テンプレ等を同梱する場合
Source: "data\*"; DestDir: "{app}\data"; Flags: recursesubdirs
; 取扱説明書など（任意）
Source: "README.md"; DestDir: "{app}"

[Icons]
Name: "{group}\InvoiceAgent Lite"; Filename: "{app}\InvoiceAgentLite.exe"
Name: "{commondesktop}\InvoiceAgent Lite"; Filename: "{app}\InvoiceAgentLite.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作成"; GroupDescription: "その他"

[Run]
Filename: "{app}\InvoiceAgentLite.exe"; Description: "起動"; Flags: nowait postinstall skipifsilent
