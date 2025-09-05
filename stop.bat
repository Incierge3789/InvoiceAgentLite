@echo off
setlocal
cd /d "%~dp0"

set "PORT="
if exist .server.port set /p PORT=<.server.port

if not "%PORT%"=="" (
  for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% *LISTENING"') do (
    taskkill /PID %%P /F >nul 2>&1 && echo 停止しました（PID %%P, PORT %PORT%）。
  )
  del /q .server.port >nul 2>&1
) else (
  echo 実行中のポート情報がありません。既定の 5050-5060 を走査します…
  for /l %%p in (5050,1,5060) do (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%%p *LISTENING"') do (
      taskkill /PID %%P /F >nul 2>&1 && echo 停止しました（PID %%P, PORT %%p）。
    )
  )
)

pause
