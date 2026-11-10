@echo off
title CQRS Ledger - Docker
cd /d "%~dp0"
docker compose up --build -d
if errorlevel 1 (echo Docker failed: is Docker Desktop running? & pause & exit /b 1)
echo Waiting for the application...
timeout /t 15 /nobreak >nul
start http://localhost:8000/
echo Logs (CTRL+C to stop watching; containers keep running. Stop them with: docker compose down)
docker compose logs -f web projector
