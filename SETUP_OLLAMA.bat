@echo off
title CQRS Ledger - local AI
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_ollama.ps1"
pause
