@echo off
REM Abre a interface visual do Planejador Forever no navegador.
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\wow-plan.exe" (
    echo Rode o setup.bat primeiro.
    pause
    exit /b 1
)
".venv\Scripts\wow-plan.exe" ui
pause
