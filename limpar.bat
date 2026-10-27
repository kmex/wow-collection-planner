@echo off
REM Remove arquivos temporarios gerados pelo Python e pelos testes.
REM NAO apaga: .venv (programa instalado), data (seu banco), .env (sua config).
chcp 65001 >nul
cd /d "%~dp0"
echo Limpando arquivos temporarios...
for /d /r "%~dp0src" %%D in (__pycache__) do if exist "%%D" rd /s /q "%%D"
for /d /r "%~dp0tests" %%D in (__pycache__) do if exist "%%D" rd /s /q "%%D"
if exist ".pytest_cache" rd /s /q ".pytest_cache"
if exist "build" rd /s /q "build"
if exist "exports" echo (pasta exports mantida - contem seus arquivos exportados)
echo Pronto.
pause
