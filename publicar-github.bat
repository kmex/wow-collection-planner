@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo.
echo === Publicar wow-collection-planner no GitHub ===
echo.
where git >nul 2>&1 || (echo [ERRO] Git nao encontrado. Instale: winget install Git.Git  ^(depois feche e abra esta janela^) & pause & exit /b 1)
where gh  >nul 2>&1 || (echo [ERRO] GitHub CLI nao encontrado. Instale: winget install GitHub.cli  ^(depois feche e abra esta janela^) & pause & exit /b 1)

gh auth status >nul 2>&1 || (echo Faca login no GitHub no navegador que vai abrir... & gh auth login --web --git-protocol https || (pause & exit /b 1))

set "REPO=wow-collection-planner"
set /p REPO="Nome do repositorio [wow-collection-planner]: "
set "VIS=public"
set /p VIS="Visibilidade public/private [public]: "
for /f "tokens=2 delims==" %%v in ('findstr /b "version" pyproject.toml') do set "VER=%%~v"
set "VER=%VER: =%"
set "VER=%VER:"=%"

if exist _github (
  xcopy /e /i /y _github .github >nul && rmdir /s /q _github
)
if not exist .git (
  git init -b main || (pause & exit /b 1)
)
git config user.name >nul 2>&1 || for /f "delims=" %%u in ('gh api user --jq .login') do git config user.name "%%u"
git config user.email >nul 2>&1 || for /f "delims=" %%u in ('gh api user --jq ".id"') do for /f "delims=" %%l in ('gh api user --jq .login') do git config user.email "%%u+%%l@users.noreply.github.com"

git add -A
git commit -m "wow-collection-planner v%VER%" >nul 2>&1
git remote get-url origin >nul 2>&1 || (
  gh repo create "%REPO%" --%VIS% --source . --remote origin --description "Planejador de personagens, racials, profissoes e colecao para World of Warcraft: Forever" || (pause & exit /b 1)
)
git push -u origin main || (pause & exit /b 1)

git rev-parse "v%VER%" >nul 2>&1 || git tag -a "v%VER%" -m "v%VER%"
git push origin "v%VER%"
echo.
echo Pronto! O GitHub esta montando a versao v%VER% para download (leva ~2 min).
for /f "delims=" %%u in ('gh repo view --json url --jq .url') do (echo Repositorio: %%u & echo Downloads:   %%u/releases/latest & start "" "%%u")
echo.
pause
