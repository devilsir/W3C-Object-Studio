@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title WC3 Object Studio v3.10.13 - First Run
set PYTHONUNBUFFERED=1

if not exist "logs" mkdir "logs" >nul 2>nul

echo ================================================================
echo  WC3 REFORGED OBJECT STUDIO v3.10.13 - PRIMEIRA EXECUCAO
echo ================================================================
echo.
echo [1/5] Instalando dependencias do visualizador (WebView2 + icones)...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 echo [AVISO] Falha ao instalar tkwry/Pillow. O editor abre, mas preview 3D/icones podem ficar indisponiveis.

echo.
echo [2/5] Configurando StormLib...
python -u setup_stormlib.py
if errorlevel 1 goto :storm_error

echo.
echo [3/5] Preparando renderer MDX/MDL...
python -u setup_viewer.py
if errorlevel 1 echo [AVISO] Viewer local nao foi baixado. O app ainda pode tentar CDN quando houver internet.

echo.
echo [4/5] Preparando dados nativos do Object Editor...
python -u setup_native_data.py
if errorlevel 1 echo [AVISO] Base nativa incompleta. Rode setup_native_data.py novamente com internet.

echo.
echo [5/5] Preparando acesso CASC do Warcraft III...
python -u setup_casclib.py
if errorlevel 1 echo [AVISO] CASC nao ficou pronto. Use o botao CASC... dentro do Studio.

echo.
echo Setup concluido. Iniciando o modo DEBUG...
echo.
call "%~dp0run_gui.bat"
exit /b %ERRORLEVEL%

:storm_error
echo.
echo [FATAL] Falha ao configurar StormLib.
echo Copie todo o texto deste CMD se precisar me mandar o erro.
echo.
pause
exit /b 1
