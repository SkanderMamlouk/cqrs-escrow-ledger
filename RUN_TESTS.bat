@echo off
title CQRS Ledger - tests
cd /d "%~dp0"
.venv\Scripts\python.exe -m pytest -v
.venv\Scripts\python.exe manage.py verify_ledger --catch-up
pause
