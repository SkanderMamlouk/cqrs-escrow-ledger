@echo off
title CQRS Ledger - reset demo
cd /d "%~dp0"
echo This deletes ALL events and recreates the demo data.
pause
.venv\Scripts\python.exe manage.py seed_demo --reset
pause
