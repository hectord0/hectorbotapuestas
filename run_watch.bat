@echo off
cd /d C:\Users\hecto\betting-bot
set PYTHONIOENCODING=utf-8
set DB_PATH=data\live.db
"C:\Users\hecto\venv\Scripts\python.exe" -u run.py watch >> data\watch.log 2>&1
