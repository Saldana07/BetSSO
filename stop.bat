@echo off
cd /d "%~dp0"

echo Deteniendo backend (uvicorn y sus workers)...
REM uvicorn --reload lanza workers hijos que sobreviven si se cierra la ventana o
REM se mata solo al padre, y siguen sirviendo codigo VIEJO en el puerto 8000.
REM Se matan todos los python de uvicorn y los workers huerfanos de multiprocessing.
powershell -NoProfile -Command ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' };" ^
  "$alive = $procs | ForEach-Object { $_.ProcessId };" ^
  "$procs | Where-Object { $_.CommandLine -match 'uvicorn' -or ($_.CommandLine -match 'multiprocessing' -and $alive -notcontains $_.ParentProcessId) } |" ^
  "  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('  terminado ' + $_.ProcessId) }"

echo Deteniendo frontend (vite)...
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'node*' -and $_.CommandLine -match 'vite' } |" ^
  "  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('  terminado ' + $_.ProcessId) }"

echo Deteniendo la base de datos...
docker compose stop db

echo.
echo Todo detenido.
pause
