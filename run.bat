@echo off
REM ---------------------------------------------------------------------
REM  AgroSuite - inicializador para Windows
REM
REM  Na primeira execucao cria um ambiente virtual e instala as
REM  dependencias. Nas seguintes, apenas sobe o app.
REM ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set VENV=.venv
set PY=%VENV%\Scripts\python.exe

if not exist "%PY%" (
    echo.
    echo  Primeira execucao: preparando o ambiente. Isso leva alguns minutos.
    echo.
    where py >nul 2>nul
    if %errorlevel%==0 ( py -3 -m venv "%VENV%" ) else ( python -m venv "%VENV%" )
    if errorlevel 1 (
        echo.
        echo  Nao foi possivel criar o ambiente virtual.
        echo  Instale o Python 3.10 ou mais novo em https://www.python.org/downloads/
        echo  marcando "Add Python to PATH" durante a instalacao.
        echo.
        pause
        exit /b 1
    )
    "%PY%" -m pip install --upgrade pip --quiet
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo  Falha ao instalar as dependencias. Verifique a conexao e tente de novo.
        echo.
        pause
        exit /b 1
    )
)

"%PY%" -m agrosuite %*
if errorlevel 1 pause
endlocal
