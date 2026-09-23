@echo off
setlocal
cd /d "%~dp0backend"
set PY=.venv\Scripts\python.exe
set AUTO=%1

REM Liquidacion frecuente (cada 2 h, via tarea programada): trae el resultado y
REM las estadisticas de los partidos con picks pendientes y los liquida. Es
REM barata en cuota de API (solo pide datos de partidos con picks sin liquidar:
REM ~3 peticiones por pick, una sola vez), asi que puede correr varias veces
REM al dia sin comprometer las 100 diarias. El flujo diario (daily.bat) sigue
REM siendo el que trae partidos, cuotas y genera picks.

echo ============================================
echo   Liquidacion  [%date% %time%]
echo ============================================

REM Comparte el candado con daily.bat: si el flujo diario esta corriendo ya
REM liquida el, y dos liquidaciones a la vez chocarian con el limite por minuto.
set LOCK=.daily_lock
if exist "%LOCK%" (
    powershell -NoProfile -Command "if ((Get-Item '%LOCK%').LastWriteTime -lt (Get-Date).AddHours(-2)) { Remove-Item '%LOCK%' -Recurse -Force }" >nul 2>&1
)
mkdir "%LOCK%" 2>nul || goto :busy

REM Sin Docker no hay base de datos; no lo arrancamos desde aqui (eso lo hace
REM daily.bat / start.bat): esta tarea corre cada 2 h y no debe abrir ventanas.
docker info >nul 2>&1 || goto :nodocker
docker compose -f ..\docker-compose.yml exec -T db pg_isready -U bet_user -d bet_db >nul 2>&1 || goto :nodocker

REM Arbitros 2022-2024 (solo lo que falte; no es fatal).
%PY% -m ml.ingestion.import_referees

echo [1/2] Trayendo resultado + estadisticas de partidos jugados (con presupuesto de cuota)...
%PY% -m jobs.fetch_match_data || goto :fail

echo [2/2] Liquidando picks y mercados evaluados (+ cuota de cierre)...
%PY% -m jobs.settle_predictions || goto :fail

rmdir "%LOCK%" 2>nul
echo Listo.
if /i not "%AUTO%"=="auto" pause
exit /b 0

:busy
echo Ya hay un flujo corriendo (candado %LOCK%). No se lanza otro.
if /i not "%AUTO%"=="auto" pause
exit /b 0

:nodocker
rmdir "%LOCK%" 2>nul
echo Docker / la base de datos no estan arriba; se reintenta en la proxima corrida.
if /i not "%AUTO%"=="auto" pause
exit /b 0

:fail
rmdir "%LOCK%" 2>nul
echo *** Un paso fallo. Revisa el mensaje de arriba. ***
if /i not "%AUTO%"=="auto" pause
exit /b 1
