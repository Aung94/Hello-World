# AI Clip Maker: notes for Claude

A FastAPI web app: paste a video link, get AI-dubbed clips or a narrated movie recap. The entry point is `python -m app`, which serves http://localhost:8000 and opens the browser.

- `app/pipeline.py`: download (yt-dlp) → captions/faster-whisper → clip picking → translate → TTS → ffmpeg render
- `app/ai.py`: Claude prompts (recap script, highlight picking); active only when `ANTHROPIC_API_KEY` is set
- `app/main.py`: API (`/api/options`, `POST /api/jobs`, `GET /api/jobs/{id}`, `/files/...`)
- `app/static/index.html`: the whole UI (vanilla JS)

## Launching locally (follow in order)

1. **Detect the OS** and check `python --version` (needs 3.10+) and `ffmpeg -version`.
   - If something is missing, ask the user before installing it.
   - Windows: `winget install Python.Python.3.12` / `winget install Gyan.FFmpeg`.
   - macOS: `brew install python ffmpeg`.
   - Linux: `sudo apt install python3-venv ffmpeg fonts-noto-core`.
   - On Windows, a freshly installed ffmpeg is only on PATH in a **new** terminal. Use the full path or reopen the shell.
2. **Create the virtual env and install** from the repo root:
   - Windows: `python -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt`
   - macOS/Linux: `python3 -m venv .venv` then `.venv/bin/pip install -r requirements.txt`
3. **Burmese subtitle font** (Windows and macOS need this; Linux has it via fonts-noto-core):
   - Create `app/fonts/`.
   - Download `NotoSansMyanmar-Regular.ttf` into it from https://github.com/google/fonts/raw/main/ofl/notosansmyanmar/NotoSansMyanmar-Regular.ttf (or from https://fonts.google.com/noto/specimen/Noto+Sans+Myanmar).
   - Check the file is a real font (more than 100 KB, not an HTML page).
   - ffmpeg picks it up through `fontsdir` automatically.
4. **Claude API key (optional)**: it turns on Movie Recap and AI highlight picking.
   - Never ask the user to paste the key into chat.
   - Tell them to set it themselves in the terminal that runs the app (`set ANTHROPIC_API_KEY=...` on Windows cmd, `$env:ANTHROPIC_API_KEY="..."` in PowerShell, `export ANTHROPIC_API_KEY=...` on macOS/Linux), or to put it in their shell profile.
   - Without the key, everything else works.
5. **Start the server in the background**: `.venv/bin/python -m app` (Windows: `.venv\Scripts\python -m app`).
   - It opens the default browser at http://localhost:8000.
   - Confirm it is up with `curl http://localhost:8000/api/options`.
   - If port 8000 is busy, set `PORT=8001` (or another free port).
6. **Smoke test**:
   - Ask the user for a short (1–3 min) video they have rights to.
   - Create a job:
     ```
     curl -X POST localhost:8000/api/jobs -H "content-type: application/json" -d '{"url":"<link>","num_clips":1,"clip_length":30,"target_lang":"my","sub_lang":"my","aspects":["9:16"]}'
     ```
   - Poll `GET /api/jobs/<id>` until `status` is `done` or `error`.
   - Report the output file path under `jobs/<id>/`.

## Troubleshooting

- **Download fails / "Sign in to confirm you're not a bot" / HTTP 403**: run `pip install -U yt-dlp` (sites change often). For YouTube bot checks, yt-dlp may need browser cookies; it supports `cookiesfrombrowser`. Add it to `ydl_opts` in `download()` only if the user agrees.
- **Edge TTS errors (403 / no audio)**: run `pip install -U edge-tts`.
- **Translation errors**: Google Translate (deep-translator) rate-limits. Retry, or reduce `num_clips`.
- **Whisper slow on CPU**: set `WHISPER_MODEL=base` (faster) before starting. Videos with YouTube captions skip Whisper entirely.
- **Burmese text shows as boxes or broken letters**: the font in step 3 is missing.
- **First Whisper run**: it downloads the model (~500 MB for `small`). That is normal.
- Job logs appear in the UI's log box and in `jobs/<id>/job.json`.

## Rules

- Do not add features meant to evade copyright detection (mirror flips, color shifts, pitch shifting to dodge Content ID).
- Keep the default model `claude-opus-5-5` in `app/ai.py` unless the user asks otherwise.
