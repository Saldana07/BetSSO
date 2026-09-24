@echo off
setlocal
cd /d "%~dp0backend"
set PY=.venv\Scripts\python.exe

REM Partidos y cuotas de hoy y manana y sus picks (gasta ~2 + 1 consulta por partido con historial).
REM Es lo mismo que el boton de la pagina (jobs/on_demand.py): nada corre solo,
REM esto solo se ejecuta cuando lo abres tu. Salida completa: backend\actions.log

echo ============================================
echo   Actualizar el dia  [%date% %time%]
echo ============================================
echo.

REM --- Docker: arrancarlo si no esta (tras un reinicio o una suspension no suele estarlo) ---
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
if %INTENTOS% GEQ 36 echo ERROR: Docker no arranco en 3 minutos. & goto :end
goto wait_docker
:docker_ok
docker compose -f ..\docker-compose.yml up -d db >nul 2>&1
set INTENTOS=0
:wait_db
docker compose -f ..\docker-compose.yml exec -T db pg_isready -U bet_user -d bet_db >nul 2>&1 && goto db_ok
timeout /t 2 /nobreak >nul
set /a INTENTOS+=1
if %INTENTOS% GEQ 60 echo ERROR: la base de datos no respondio. & goto :end
goto wait_db
:db_ok

%PY% -m jobs.on_demand actualizar

:end
echo.
pause
