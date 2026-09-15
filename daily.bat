@echo off
setlocal
cd /d "%~dp0backend"
set PY=.venv\Scripts\python.exe

REM Fechas en UTC (la API y la BD trabajan en UTC).
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')"') do set HOY=%%i
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToUniversalTime().AddDays(-1).ToString('yyyy-MM-dd')"') do set AYER=%%i

echo ============================================
echo   Flujo diario - %HOY%
echo ============================================
echo.

docker info >nul 2>&1 || (echo ERROR: Docker no esta corriendo. Ejecuta start.bat primero. & pause & exit /b 1)
docker compose -f ..\docker-compose.yml up -d db >nul 2>&1

echo [1/6] Trayendo resultados de ayer (%AYER%)...
%PY% -m ml.ingestion.sync_fixtures --date %AYER% || goto :fail

echo [2/6] Liquidando picks ya jugados...
%PY% -m jobs.settle_predictions || goto :fail

echo [3/6] Trayendo partidos de hoy...
%PY% -m ml.ingestion.sync_fixtures --date %HOY% || goto :fail

echo [4/6] Reconciliando equipos con el historico...
%PY% -m ml.ingestion.reconcile_teams --apply || goto :fail

echo [5/6] Trayendo cuotas (solo partidos con historial suficiente)...
%PY% -m ml.ingestion.sync_odds --date %HOY% --snapshot-type LIVE || goto :fail

echo [6/6] Generando picks del dia...
%PY% -m jobs.generate_daily_pick || goto :fail

echo.
echo ============================================
echo   Listo. Abre http://localhost:5173
echo ============================================
pause
exit /b 0

:fail
echo.
echo *** Un paso fallo. Revisa el mensaje de arriba. ***
pause
exit /b 1
