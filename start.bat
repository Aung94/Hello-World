@echo off
REM One-click start on Windows: creates a virtual env, installs, opens the app in your browser.
REM Needs Python 3.10+ and ffmpeg on PATH (winget install Gyan.FFmpeg).
cd /d %~dp0
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r requirements.txt
python -m app
