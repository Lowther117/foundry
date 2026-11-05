@echo off
setlocal
title Foundry
cd /d "%~dp0"

set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1 && set "PY=%%~C"
    )
)

if not defined PY (
    echo No Python 3.8+ found - installing Python 3.13 for this user with winget...
    winget install -e --id Python.Python.3.13 --scope user --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo winget could not install Python. Install it from https://www.python.org/downloads/ and run this again.
        pause
        exit /b 1
    )
    rem pick up the new PATH without reopening the window
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "PATH=%%B;%PATH%"
    set "PY=py -3"
)

%PY% "%~dp0foundry.py" %*
echo.
pause
