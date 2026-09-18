@echo off
setlocal
cd /d "%~dp0backend"
set PY=.venv\Scripts\python.exe
set AUTO=%1

REM Fecha en UTC (la API y la BD trabajan en UTC).
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')"') do set HOY=%%i

echo ============================================
echo   Flujo diario - %HOY%  [%date% %time%]
echo ============================================
echo.

REM --- Idempotencia diaria: si ya corrio con exito hoy, no repetir (gasta cuota de API).
REM     start.bat y el disparador "al iniciar sesion" lo llaman sin miedo; "force" lo salta.
set MARKER=.daily_done
if /i "%2"=="force" goto run
if not exist "%MARKER%" goto run
set /p DONE=<"%MARKER%"
if not "%DONE%"=="%HOY%" goto run
echo Ya se ejecuto hoy con exito. Usa "daily.bat auto force" para repetir.
if /i not "%AUTO%"=="auto" pause
exit /b 0
:run

REM --- Docker: arrancarlo si no esta (tras un reinicio no suele estarlo) ---
REM Sin bloques ( ) anidados: en batch las variables dentro de un bloque se
REM expanden al leer el bloque, antes de asignarse, y el contador quedaria vacio.
docker info >nul 2>&1 && goto docker_ok
echo Docker no responde, abriendo Docker Desktop...
explorer.exe "shell:AppsFolder\Docker.DockerForWindows.Settings" >nul 2>&1
set INTENTOS=0
:wait_docker
timeout /t 5 /nobreak >nul
set /a INTENTOS+=1
docker info >nul 2>&1 && goto docker_ok
if %INTENTOS% GEQ 36 echo ERROR: Docker no arranco en 3 minutos. & goto :fail
goto wait_docker
:docker_ok
docker compose -f ..\docker-compose.yml up -d db >nul 2>&1
:wait_db
docker compose -f ..\docker-compose.yml exec -T db pg_isready -U bet_user -d bet_db >nul 2>&1 || (timeout /t 2 /nobreak >nul & goto wait_db)

echo [1/6] Trayendo resultados de los dias con picks pendientes...
%PY% -m jobs.sync_pending_results || goto :fail

echo [2/6] Liquidando picks ya jugados (y capturando cuotas de cierre)...
%PY% -m jobs.settle_predictions || goto :fail

echo [3/6] Trayendo partidos de hoy...
%PY% -m ml.ingestion.sync_fixtures --date %HOY% || goto :fail

echo [4/6] Reconciliando equipos con el historico...
%PY% -m ml.ingestion.reconcile_teams --apply || goto :fail

echo [5/6] Trayendo cuotas (solo partidos con historial suficiente)...
%PY% -m ml.ingestion.sync_odds --date %HOY% --snapshot-type LIVE || goto :fail

echo [6/6] Generando picks del dia...
%PY% -m jobs.generate_daily_pick || goto :fail

echo %HOY%> "%MARKER%"
echo.
echo ============================================
echo   Listo. Abre http://localhost:5173
echo ============================================
if /i not "%AUTO%"=="auto" pause
exit /b 0

:fail
echo.
echo *** Un paso fallo. Revisa el mensaje de arriba. ***
if /i not "%AUTO%"=="auto" pause
exit /b 1
