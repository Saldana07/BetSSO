@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Apuesta Segura - arrancando el entorno
echo ============================================
echo.

echo [1/5] Verificando Docker Desktop...
REM Sin bloques ( ) con etiquetas dentro: en batch un goto dentro de un bloque
REM pierde el contexto y las variables se expanden antes de asignarse.
docker info >nul 2>&1 && goto docker_ok
echo   Docker no responde, intentando abrir Docker Desktop...
explorer.exe "shell:AppsFolder\Docker.DockerForWindows.Settings" >nul 2>&1
echo   Esperando a que Docker arranque ^(puede tardar ~30-60s la primera vez^)...
set INTENTOS=0
:wait_docker
timeout /t 5 /nobreak >nul
set /a INTENTOS+=1
docker info >nul 2>&1 && goto docker_ok
if %INTENTOS% GEQ 36 echo   ERROR: Docker no arranco en 3 minutos. & pause & exit /b 1
echo   ... todavia esperando a Docker
goto wait_docker
:docker_ok
echo   Docker listo.
echo.

echo [2/5] Levantando la base de datos...
docker compose up -d db
if errorlevel 1 (
    echo   ERROR al levantar la base de datos. Revisa docker-compose.yml
    pause
    exit /b 1
)
echo.

echo [3/5] Iniciando el backend (FastAPI) en una nueva ventana...
REM Sin --reload: sus workers sobreviven al cerrar la ventana y siguen sirviendo codigo viejo en el puerto.
start "Apuesta Segura - Backend" cmd /k "cd /d "%~dp0backend" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
timeout /t 3 /nobreak >nul
echo.

echo [4/5] Iniciando el frontend (Vite) en una nueva ventana...
start "Apuesta Segura - Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"
timeout /t 4 /nobreak >nul
echo.

echo ============================================
echo   Todo arriba:
echo   - API:       http://127.0.0.1:8000/docs
echo   - Frontend:  http://localhost:5173
echo ============================================
echo.
echo [5/5] Liquidando picks pendientes y generando los de hoy y manana...
echo       (en su propia ventana: cerrar esta no lo interrumpe; si ya se hizo hoy, se salta)
start "Apuesta Segura - Flujo diario" cmd /c ""%~dp0daily.bat" auto & timeout /t 15"
echo.

start "" "http://localhost:5173"

pause
