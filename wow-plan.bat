@echo off
REM Atalho: roda o wow-plan do .venv desta pasta sem precisar ativar o ambiente.
chcp 65001 >nul
"%~dp0.venv\Scripts\wow-plan.exe" %*
