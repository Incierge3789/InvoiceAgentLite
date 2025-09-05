@echo off
setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

REM ===== 設定 =====
if "%PORT%"=="" set PORT=5050

REM ===== Python 検出（py 優先）=====
set "PYEXE="
where py >nul 2>&1  && set "PYEXE=py -3"
if "%PYEXE%"=="" where python >nul 2>&1 && set "PYEXE=python"
if "%PYEXE%"=="" (
  echo Python が見つかりません。Python をインストールしてから実行してください。
  pause
  exit /b 1
)

REM ===== venv 準備 =====
if not exist ".venv" (
  %PYEXE% -m venv .venv
)
call ".venv\Scripts\activate"

REM ===== requirements.txt の変更時だけ依存導入 =====
set "NEWHASH="
for /f "skip=1 tokens=1" %%H in ('certutil -hashfile requirements.txt SHA256 ^| find /v ":"') do (
  if not defined NEWHASH set "NEWHASH=%%H"
)
set /p OLDHASH=<.deps_hash 2>nul
if /i not "%NEWHASH%"=="%OLDHASH%" (
  echo 依存関係をインストールしています…（初回のみ数分）
  python -m pip install -U pip wheel >nul
  python -m pip install -r requirements.txt >nul
  REM waitress が requirements に無い環境でも起動できるよう保険
  python -m pip show waitress >nul 2>&1 || python -m pip install waitress >nul
  > .deps_hash echo %NEWHASH%
)

REM ===== 空きポート探索（5050→5060）=====
for /l %%P in (%PORT%,1,5060) do (
  netstat -ano | findstr /r /c:":%%P *LISTENING" >nul
  if errorlevel 1 (
    set "PORT=%%P"
    goto :port_ok
  )
)
:port_ok

REM ===== サーバ起動（最小化・別ウィンドウ常駐）=====
echo サーバ起動中... http://127.0.0.1:%PORT%
REM 別コンソールで最小化起動し、ブラウザはヘルスチェック後に開く
start "InvoiceAgentLite-Server" /min cmd /c ".venv\Scripts\python -m waitress --listen=127.0.0.1:%PORT% app:app"
> .server.port echo %PORT%

REM ===== ヘルスチェック（最大30秒）=====
for /l %%I in (1,1,60) do (
  powershell -NoProfile -Command ^
    "try{(Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/healthz' -TimeoutSec 1) ^| Out-Null; exit 0}catch{ exit 1 }" >nul 2>&1
  if not errorlevel 1 goto :ok
  timeout /t 1 >nul
)
echo 起動に失敗しました。サーバウィンドウの表示/ logs を確認してください。
pause
exit /b 1

:ok
start "" "http://127.0.0.1:%PORT%/upload"
REM ランチャーはここで終了（サーバは最小化で動き続けます）
exit /b 0
