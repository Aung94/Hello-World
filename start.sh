#!/usr/bin/env bash
# One-command start on macOS/Linux: creates a virtual env, installs, opens the app in your browser.
# Needs Python 3.10+ and ffmpeg (brew install ffmpeg / apt install ffmpeg fonts-noto-core).
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
python -m app
