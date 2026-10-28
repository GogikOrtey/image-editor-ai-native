@echo off
REM ============================================================================
REM  Image Editor (AI native) - launcher for the local dev server.
REM  Runs the FastAPI backend (port 8000) and the Vite frontend (port 5173)
REM  as background children of THIS window, waits until both answer, then
REM  opens the app in the default browser. One console window only.
REM  Closing the window (or Ctrl+C) stops both servers.
REM ============================================================================
setlocal EnableExtensions DisableDelayedExpansion
title Image Editor - dev server
chcp 65001 >nul 2>&1

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%"
set "FRONTEND_URL=http://127.0.0.1:%FRONTEND_PORT%"
set "BACKEND_LOG=%TEMP%\image-editor-backend.log"
set "FRONTEND_LOG=%TEMP%\image-editor-frontend.log"

echo.
echo   Image Editor (AI native)
echo   ---------------------------------------------
echo   App      : %FRONTEND_URL%
echo   Backend  : %BACKEND_URL%/api/health
echo   Logs     : %BACKEND_LOG%
echo              %FRONTEND_LOG%
echo   ---------------------------------------------
echo.

echo [1/5] Checking project files...
if not exist "%ROOT%\backend\app\main.py" goto :no_project
if not exist "%ROOT%\frontend\package.json" goto :no_project
if exist "%ROOT%\backend\.env" (echo   backend\.env found.) else (echo   WARNING: backend\.env missing - image generation will fail.)

echo [2/5] Checking Python...
where python >nul 2>&1
if errorlevel 1 goto :no_python
python -c "import fastapi, uvicorn, httpx, PIL, dotenv" >nul 2>&1
if errorlevel 1 (
    echo   Dependencies missing, installing from requirements.txt...
    python -m pip install -r "%ROOT%\backend\requirements.txt" --quiet
    if errorlevel 1 goto :pip_failed
)
echo   OK

echo [3/5] Checking Node.js and frontend dependencies...
where node >nul 2>&1
if errorlevel 1 goto :no_node
if not exist "%ROOT%\frontend\node_modules" (
    echo   frontend\node_modules missing, running npm install...
    pushd "%ROOT%\frontend"
    call npm install
    if errorlevel 1 (popd & goto :npm_failed)
    popd
)
echo   OK

echo [4/5] Starting servers...
if exist "%BACKEND_LOG%" del "%BACKEND_LOG%" >nul 2>&1
if exist "%FRONTEND_LOG%" del "%FRONTEND_LOG%" >nul 2>&1

REM Both servers are launched with /B so they share this console instead of
REM opening windows of their own. Their output goes to log files, otherwise
REM Vite's interactive keys would fight with the prompt below.
pushd "%ROOT%\backend"
start "" /b cmd /c "python -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT% > "%BACKEND_LOG%" 2>&1"
popd
pushd "%ROOT%\frontend"
start "" /b cmd /c "npx vite --host=127.0.0.1 --port=%FRONTEND_PORT% --strictPort > "%FRONTEND_LOG%" 2>&1"
popd

echo [5/5] Waiting for the app to answer...
call :wait_for "%BACKEND_URL%/api/health" "backend"
if errorlevel 1 goto :backend_dead
call :wait_for "%FRONTEND_URL%/" "frontend"
if errorlevel 1 goto :frontend_dead

echo.
echo   Ready. Opening the browser...
start "" "%FRONTEND_URL%"
echo.
echo   =============================================
echo   Servers are running. This window keeps them
echo   alive - closing it stops everything.
echo.
echo   Press any key to stop the servers now.
echo   =============================================
echo.
pause >nul
goto :shutdown

REM ---------------------------------------------------------------------------
REM  :wait_for <url> <label> - polls until the URL answers, ~90 s budget.
REM ---------------------------------------------------------------------------
:wait_for
set "WAIT_URL=%~1"
set "WAIT_LABEL=%~2"
set /a WAIT_TRIES=0
:wait_loop
set /a WAIT_TRIES+=1
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%WAIT_URL%' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo   %WAIT_LABEL% is up.
    exit /b 0
)
if %WAIT_TRIES% geq 45 exit /b 1
if %WAIT_TRIES% equ 15 echo   still waiting for %WAIT_LABEL%...
if %WAIT_TRIES% equ 30 echo   still waiting for %WAIT_LABEL%...
ping -n 3 127.0.0.1 >nul 2>&1
goto :wait_loop

:shutdown
echo.
echo   Shutting down...
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%
echo   Stopped.
timeout /t 1 >nul
endlocal
exit /b 0

:kill_port
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /r /c:":%~1 .*LISTENING"') do (
    taskkill /F /PID %%P >nul 2>&1
)
exit /b 0

:no_project
echo.
echo   ERROR: backend\app\main.py or frontend\package.json not found.
echo   Keep start-server.cmd in the project root.
goto :fail

:no_python
echo.
echo   ERROR: Python not found on PATH.
echo   Install Python 3.10+ and tick "Add python.exe to PATH".
goto :fail

:no_node
echo.
echo   ERROR: Node.js not found on PATH.
echo   Install Node.js 20+ from https://nodejs.org
goto :fail

:pip_failed
echo.
echo   ERROR: could not install backend dependencies.
echo   Try manually: python -m pip install -r backend\requirements.txt
goto :fail

:npm_failed
echo.
echo   ERROR: npm install failed in frontend.
echo   Try manually: cd frontend ^&^& npm install
goto :fail

:backend_dead
echo.
echo   ERROR: backend did not start. Last lines of its log:
echo   ---------------------------------------------
powershell -NoProfile -Command "if (Test-Path '%BACKEND_LOG%') { Get-Content '%BACKEND_LOG%' -Tail 20 }"
echo   ---------------------------------------------
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%
goto :fail

:frontend_dead
echo.
echo   ERROR: frontend did not start. Last lines of its log:
echo   ---------------------------------------------
powershell -NoProfile -Command "if (Test-Path '%FRONTEND_LOG%') { Get-Content '%FRONTEND_LOG%' -Tail 20 }"
echo   ---------------------------------------------
call :kill_port %BACKEND_PORT%
call :kill_port %FRONTEND_PORT%
goto :fail

:fail
echo.
echo   Press any key to close.
pause >nul
endlocal
exit /b 1