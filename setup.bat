@echo off
REM ============================================================
REM  wow-collection-planner - instalacao no Windows
REM  Uso: de dois cliques ou rode "setup.bat" no terminal desta pasta
REM ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

REM --- 1. Encontra um Python 3.11+ (launcher "py" primeiro, depois "python")
set "PY="
for %%V in (3.13 3.12 3.11) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo [ERRO] Python 3.11 ou mais novo nao encontrado.
    echo        Instale em https://www.python.org/downloads/ marcando "Add python.exe to PATH".
    pause
    exit /b 1
)
echo [1/5] Usando: %PY%
%PY% --version

REM --- 2. Ambiente virtual
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Criando ambiente virtual .venv ...
    %PY% -m venv .venv || goto :erro
) else (
    echo [2/5] .venv ja existe
)

REM --- 3. Dependencias
echo [3/5] Instalando dependencias ...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :erro
".venv\Scripts\python.exe" -m pip install -e ".[dev]" || goto :erro

REM --- 4. .env
if not exist ".env" (
    echo [4/5] Criando .env a partir do .env.example - edite os caminhos do WoW depois
    copy /y ".env.example" ".env" >nul
) else (
    echo [4/5] .env ja existe - mantido
)

REM --- 5. Banco + testes
echo [5/5] Inicializando banco e rodando testes ...
".venv\Scripts\wow-plan.exe" init || goto :erro
".venv\Scripts\python.exe" -m pytest -q || goto :erro

echo.
echo ============================================================
echo  Pronto! Ative o ambiente com:   .venv\Scripts\activate
echo  e rode:                         wow-plan roadmap
echo ============================================================
pause
exit /b 0

:erro
echo.
echo [ERRO] Algo falhou acima. Copie a mensagem e me envie.
pause
exit /b 1
