@echo off
chcp 65001 >nul 2>&1
if "%1"=="web" goto web
if "%1"=="server" goto web
py "%~dp0tvdb_crawler.py" %*
goto end
:web
cd /d "%~dp0backend"
py -m uvicorn app.main:app --host 0.0.0.0 --port 7711
:end
