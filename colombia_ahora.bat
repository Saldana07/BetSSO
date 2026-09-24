@echo off
setlocal
cd /d "%~dp0backend"
set PY=.venv\Scripts\python.exe
set AUTO=%1

REM Apartado colombiano: cuotas y picks de los partidos de las proximas horas.
REM Corre a las 19:10 (la cuota de la API se renueva a las 19:00 y muchos
REM partidos colombianos empiezan entre 19:30 y 21:00). Gasta 1 peticion por
REM partido y nunca toca la reserva del flujo de las 08:00.

echo ============================================
echo   Colombia ahora  [%date% %time%]
echo ============================================

set LOCK=.daily_lock
if exist "%LOCK%" (
    powershell -NoProfile -Command "if ((Get-Item '%LOCK%').LastWriteTime -lt (Get-Date).AddHours(-2)) { Remove-Item '%LOCK%' -Recurse -Force }" >nul 2>&1
)
mkdir "%LOCK%" 2>nul || goto :busy

REM Docker: si esta cerrado se abre y se espera (es una tarea con hora limite:
REM el 23/09 encontro Docker cerrado a las 19:10 y el partido de las 19:30 se perdio).
docker info >nul 2>&1 && goto docker_ok
explorer.exe "shell:AppsFolder\Docker.DockerForWindows.Settings" >nul 2>&1
set INTENTOS=0
:wait_docker
timeout /t 5 /nobreak >nul
set /a INTENTOS+=1
docker info >nul 2>&1 && goto docker_ok
if %INTENTOS% GEQ 36 goto :nodocker
goto wait_docker
:docker_ok
docker compose -f ..\docker-compose.yml up -d db >nul 2>&1
set INTENTOS=0
:wait_db
docker compose -f ..\docker-compose.yml exec -T db pg_isready -U bet_user -d bet_db >nul 2>&1 && goto db_ok
timeout /t 2 /nobreak >nul
set /a INTENTOS+=1
if %INTENTOS% GEQ 60 goto :nodocker
goto wait_db
:db_ok

%PY% -m jobs.colombia_now --hours 6 || goto :fail

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
echo Docker / la base de datos no estan arriba.
if /i not "%AUTO%"=="auto" pause
exit /b 0

:fail
rmdir "%LOCK%" 2>nul
echo *** Un paso fallo. Revisa el mensaje de arriba. ***
if /i not "%AUTO%"=="auto" pause
exit /b 1
