@echo off
REM ---------------------------------------------------------------------
REM  AgroSuite - Windows launcher
REM
REM  On the first run it creates a virtual environment and installs the
REM  dependencies. After that it just starts the app.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set VENV=.venv
set PY=%VENV%\Scripts\python.exe

if not exist "%PY%" (
    echo.
    echo  First run: setting up the environment. This takes a few minutes.
    echo.
    where py >nul 2>nul
    if %errorlevel%==0 ( py -3 -m venv "%VENV%" ) else ( python -m venv "%VENV%" )
    if errorlevel 1 (
        echo.
        echo  Could not create the virtual environment.
        echo  Install Python 3.10 or newer from https://www.python.org/downloads/
        echo  and tick "Add Python to PATH" during installation.
        echo.
        pause
        exit /b 1
    )
    "%PY%" -m pip install --upgrade pip --quiet
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo  Failed to install the dependencies. Check your connection and try again.
        echo.
        pause
        exit /b 1
    )
)

"%PY%" -m agrosuite %*
if errorlevel 1 pause
endlocal
