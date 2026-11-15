@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo  WC3 Object Studio v3.10.13 DEBUG - Setup Windows
echo ============================================================
echo.

echo [1/5] Instalando dependencias do visualizador...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo.
  echo AVISO: falha ao instalar tkwry/Pillow. O editor ainda pode abrir,
  echo mas o preview 3D e/ou os icones podem ficar indisponiveis.
)

echo.
echo [2/5] Preparando StormLib...
python -u setup_stormlib.py
if errorlevel 1 goto :error

echo.
echo [3/5] Preparando visualizador MDX/MDL...
python -u setup_viewer.py
if errorlevel 1 (
  echo.
  echo AVISO: o Object Studio pode ser usado sem o viewer local.
  echo O viewer tentara usar o fallback CDN quando houver internet.
)

echo.
echo [4/5] Preparando dados nativos do Object Editor...
python -u setup_native_data.py
if errorlevel 1 (
  echo.
  echo AVISO: base nativa incompleta. Rode setup_native_data.py novamente com internet.
)

echo.
echo [5/5] Preparando acesso CASC local...
python -u setup_casclib.py
if errorlevel 1 echo AVISO: configure o CASC pelo botao CASC... no Studio.

echo.
echo Setup concluido.
pause
exit /b 0

:error
echo.
echo Falha no setup da StormLib. Nenhum mapa foi alterado.
pause
exit /b 1
