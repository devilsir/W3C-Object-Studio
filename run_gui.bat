@echo off
rem compatibility: v3.10.12 cumulative patch lineage; current v3.10.20
setlocal EnableExtensions
cd /d "%~dp0"
title WC3 Reforged Object Studio v3.10.20 DEBUG - Python Log
set PYTHONUNBUFFERED=1

if not exist "logs" mkdir "logs" >nul 2>nul

cls
echo ================================================================
echo  WC3 REFORGED OBJECT STUDIO v3.10.20 - DEBUG CONSOLE
echo ================================================================
echo.
echo Este CMD DEVE ficar aberto junto com a interface.
echo O traceback completo aparece aqui e tambem em:
echo   %CD%\logs\latest.log
echo.
echo v3.10.20: runtime sync seletivo de placements - nivel/owner/estado/inventario no JASS somente quando necessario.
echo Nao feche esta janela enquanto estiver usando o programa.
echo ================================================================
echo.

echo [CHECK] Verificando arquivos obrigatorios da build...
for %%F in ("app.py" "setup_casclib.py" "wc3injector\ui\object_studio.py" "wc3injector\ui\workspace.py" "wc3injector\core\map_edit_session.py" "wc3injector\core\map_integrity.py" "wc3injector\casc.py" "wc3injector\ability_native.py" "wc3injector\unit_native.py" "wc3injector\native_data.py" "wc3injector\editor_strings.py" "wc3injector\unitsdoo.py" "wc3injector\placement_runtime.py" "wc3injector\ui\placed_hero.py" "wc3injector\ui\asset_export.py" "wc3injector\i18n.py" "assets\br.png" "assets\us.png" "assets\wc3_object_studio_icon.png" "assets\wc3_object_studio_icon.ico" "viewer\viewer.js") do (
  if not exist %%F (
    echo [FATAL] Arquivo ausente: %%~F
    echo Este hotfix/build foi extraido de forma incompleta.
    echo Reaplique a build v3.10.20 mantendo as pastas.
    set APP_EXIT=2
    goto :finished
  )
)
echo [CHECK] OK.
echo.

where python >nul 2>nul
if errorlevel 1 goto :try_py
python -u setup_native_data.py
python -u setup_casclib.py
python -u app.py
set APP_EXIT=%ERRORLEVEL%
goto :finished

:try_py
where py >nul 2>nul
if errorlevel 1 goto :no_python
py -3 -u setup_native_data.py
py -3 -u setup_casclib.py
py -3 -u app.py
set APP_EXIT=%ERRORLEVEL%
goto :finished

:no_python
echo [FATAL] Python nao foi encontrado no PATH.
echo Instale Python 3 x64 e marque Add Python to PATH.
set APP_EXIT=9009

:finished
echo.
echo ================================================================
echo O programa foi encerrado. Exit code: %APP_EXIT%
echo Log mais recente: %CD%\logs\latest.log
if "%APP_EXIT%"=="-1073740771" (
  echo [FATAL NATIVE] Windows 0xC000041D: excecao fatal em callback/UI nativa.
  echo Confira tambem: %CD%\logs\fatal_native.log
)
echo ================================================================
echo.
pause
exit /b %APP_EXIT%
