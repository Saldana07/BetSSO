@echo off
cd /d "%~dp0"

echo Deteniendo la base de datos...
docker compose stop db

echo.
echo Base de datos detenida. Cierra manualmente las ventanas
echo "Apuesta Segura - Backend" y "Apuesta Segura - Frontend"
echo (o simplemente ciertalas con la X, con Ctrl+C no basta si tienen hijos).
echo.
pause
