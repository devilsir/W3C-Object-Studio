@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title WC3 Object Studio - Teste sem Setup
set "PATCH_ROOT=%~dp0"
set "APP_DIR="
set "PY_EXE="
set "USE_PY_LAUNCHER=0"
set "APP_EXIT=0"

cls
echo ================================================================
echo  WC3 REFORGED OBJECT STUDIO - TESTE SEM SETUP
echo ================================================================
echo.
echo Este BAT nao executa instalador nem scripts de setup.
echo Ele aplica apenas os arquivos deste hotfix e abre o programa.
echo.

rem 1) Se o BAT foi copiado para dentro da pasta app, use a propria pasta.
if exist "%PATCH_ROOT%app.py" (
    set "APP_DIR=%PATCH_ROOT%"
    goto :app_found
)

rem 2) Instalacao padrao do Setup por usuario.
if exist "%LOCALAPPDATA%\Programs\WC3 Reforged Object Studio\app\app.py" (
    set "APP_DIR=%LOCALAPPDATA%\Programs\WC3 Reforged Object Studio\app"
    goto :app_found
)

rem 3) Algumas builds antigas usam o nome sem Reforged.
if exist "%LOCALAPPDATA%\Programs\WC3 Object Studio\app\app.py" (
    set "APP_DIR=%LOCALAPPDATA%\Programs\WC3 Object Studio\app"
    goto :app_found
)

echo [ERRO] Nao encontrei a pasta app do WC3 Object Studio.
echo.
echo Opcao A: extraia este hotfix diretamente dentro da pasta:
echo   %%LOCALAPPDATA%%\Programs\WC3 Reforged Object Studio\app
echo.
echo Opcao B: copie TESTAR_SEM_SETUP.bat + a pasta wc3injector para
echo a pasta que contem app.py e execute o BAT novamente.
echo.
pause
exit /b 2

:app_found
echo [OK] App encontrado:
echo      %APP_DIR%
echo.

rem Se estamos fora da pasta app, aplique os arquivos alterados na instalacao.
if /I not "%PATCH_ROOT%"=="%APP_DIR%\" (
    echo [1/3] Criando backup dos arquivos atuais...
    set "BACKUP_DIR=%APP_DIR%\_backup_before_duplicate_terrain_fix"
    if not exist "%BACKUP_DIR%\wc3injector\ui" mkdir "%BACKUP_DIR%\wc3injector\ui" >nul 2>nul
    if not exist "%BACKUP_DIR%\wc3injector\core" mkdir "%BACKUP_DIR%\wc3injector\core" >nul 2>nul

    if not exist "%BACKUP_DIR%\wc3injector\ui\workspace.py" if exist "%APP_DIR%\wc3injector\ui\workspace.py" copy /Y "%APP_DIR%\wc3injector\ui\workspace.py" "%BACKUP_DIR%\wc3injector\ui\workspace.py" >nul
    if not exist "%BACKUP_DIR%\wc3injector\ui\object_browser.py" if exist "%APP_DIR%\wc3injector\ui\object_browser.py" copy /Y "%APP_DIR%\wc3injector\ui\object_browser.py" "%BACKUP_DIR%\wc3injector\ui\object_browser.py" >nul
    if not exist "%BACKUP_DIR%\wc3injector\ui\map_placement.py" if exist "%APP_DIR%\wc3injector\ui\map_placement.py" copy /Y "%APP_DIR%\wc3injector\ui\map_placement.py" "%BACKUP_DIR%\wc3injector\ui\map_placement.py" >nul
    if not exist "%BACKUP_DIR%\wc3injector\ui\placed_hero.py" if exist "%APP_DIR%\wc3injector\ui\placed_hero.py" copy /Y "%APP_DIR%\wc3injector\ui\placed_hero.py" "%BACKUP_DIR%\wc3injector\ui\placed_hero.py" >nul
    if not exist "%BACKUP_DIR%\wc3injector\ui\asset_importer.py" if exist "%APP_DIR%\wc3injector\ui\asset_importer.py" copy /Y "%APP_DIR%\wc3injector\ui\asset_importer.py" "%BACKUP_DIR%\wc3injector\ui\asset_importer.py" >nul
    if not exist "%BACKUP_DIR%\wc3injector\ui\asset_export.py" if exist "%APP_DIR%\wc3injector\ui\asset_export.py" copy /Y "%APP_DIR%\wc3injector\ui\asset_export.py" "%BACKUP_DIR%\wc3injector\ui\asset_export.py" >nul
    if not exist "%BACKUP_DIR%\wc3injector\core\map_edit_session.py" if exist "%APP_DIR%\wc3injector\core\map_edit_session.py" copy /Y "%APP_DIR%\wc3injector\core\map_edit_session.py" "%BACKUP_DIR%\wc3injector\core\map_edit_session.py" >nul

    echo [2/3] Aplicando o hotfix...
    copy /Y "%PATCH_ROOT%wc3injector\ui\workspace.py" "%APP_DIR%\wc3injector\ui\workspace.py" >nul || goto :copy_error
    copy /Y "%PATCH_ROOT%wc3injector\ui\object_browser.py" "%APP_DIR%\wc3injector\ui\object_browser.py" >nul || goto :copy_error
    copy /Y "%PATCH_ROOT%wc3injector\ui\map_placement.py" "%APP_DIR%\wc3injector\ui\map_placement.py" >nul || goto :copy_error
    copy /Y "%PATCH_ROOT%wc3injector\ui\placed_hero.py" "%APP_DIR%\wc3injector\ui\placed_hero.py" >nul || goto :copy_error
    copy /Y "%PATCH_ROOT%wc3injector\ui\asset_importer.py" "%APP_DIR%\wc3injector\ui\asset_importer.py" >nul || goto :copy_error
    copy /Y "%PATCH_ROOT%wc3injector\ui\asset_export.py" "%APP_DIR%\wc3injector\ui\asset_export.py" >nul || goto :copy_error
    copy /Y "%PATCH_ROOT%wc3injector\core\map_edit_session.py" "%APP_DIR%\wc3injector\core\map_edit_session.py" >nul || goto :copy_error
    echo [OK] Hotfix aplicado. Backup preservado em:
    echo      %BACKUP_DIR%
    echo.
) else (
    echo [1/3] Hotfix ja esta dentro da pasta app. Nada para copiar.
    echo [2/3] Usando os arquivos locais diretamente.
    echo.
)

:find_python
echo [3/3] Procurando o Python do proprio programa...
if exist "%APP_DIR%\..\runtime\python\python.exe" set "PY_EXE=%APP_DIR%\..\runtime\python\python.exe"
if not defined PY_EXE if exist "%APP_DIR%\..\runtime\python\pythonw.exe" set "PY_EXE=%APP_DIR%\..\runtime\python\pythonw.exe"

if not defined PY_EXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PY_EXE=python"
)

if not defined PY_EXE (
    where py >nul 2>nul
    if not errorlevel 1 set "USE_PY_LAUNCHER=1"
)

if not defined PY_EXE if "%USE_PY_LAUNCHER%"=="0" goto :python_error

set PYTHONUNBUFFERED=1
set PYTHONDONTWRITEBYTECODE=1
if not exist "%APP_DIR%\logs" mkdir "%APP_DIR%\logs" >nul 2>nul
cd /d "%APP_DIR%"

echo.
echo ================================================================
echo  ABRINDO O WC3 OBJECT STUDIO EM MODO DE TESTE
echo  Nenhum setup sera executado.
echo ================================================================
echo.

if "%USE_PY_LAUNCHER%"=="1" (
    py -3 -u app.py
    set "APP_EXIT=%ERRORLEVEL%"
) else (
    "%PY_EXE%" -u app.py
    set "APP_EXIT=%ERRORLEVEL%"
)

echo.
echo ================================================================
echo Programa encerrado. Exit code: %APP_EXIT%
echo Log: %APP_DIR%\logs\latest.log
echo ================================================================
echo.
pause
exit /b %APP_EXIT%

:copy_error
echo.
echo [ERRO] Nao consegui copiar um dos arquivos do hotfix.
echo Feche o WC3 Object Studio e tente executar este BAT novamente.
echo.
pause
exit /b 3

:python_error
echo.
echo [ERRO] Python nao foi encontrado.
echo O BAT procurou primeiro o runtime incluido na instalacao e depois Python/py no PATH.
echo.
pause
exit /b 9009
