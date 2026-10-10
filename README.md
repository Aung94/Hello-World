# 🎬 AI Clip Maker — Movie Recap & Dubbing Tool

Paste a link from YouTube (or Facebook, TikTok, Vimeo, X and 1000+ other sites) and get:

- **⬇ Download** the original video
- **✨ AI clips**: the best moments cut into short clips, AI-dubbed and subtitled
- **🎬 Movie recap**: Claude watches the transcript, writes a narrated recap script (natively in Burmese, English, …), and the footage is cut to match the narration
- **🎙 Dub language** and **💬 subtitle language** chosen separately (e.g. Burmese voice + English subs, or no dub + Burmese subs)
- **Aspect ratios**: 9:16 (TikTok/Reels/Shorts), 4:5, 1:1, 16:9, original — several at once
- Voices: **Edge TTS** (fast, free, no GPU; Burmese `Nilar` / `Thiha`, English and more) or **VoxCPM** voice cloning (GPU)

Fast on long videos: uses the site's own captions when available (skips speech-to-text), translates and voices only the parts that end up in clips, and renders clips in parallel.

## 🚀 Run it on your computer

You need **Python 3.10+** and **ffmpeg**.

**Windows**
1. Install Python from python.org (tick "Add Python to PATH"), then in a terminal: `winget install Gyan.FFmpeg`
2. Download this repo (green **Code** button → Download ZIP) and unzip it
3. Double-click **`start.bat`**. Your browser opens at http://localhost:8000

**macOS / Linux**
```bash
brew install ffmpeg            # macOS   (Linux: sudo apt install ffmpeg fonts-noto-core)
git clone https://github.com/Aung94/Hello-World.git
cd Hello-World
./start.sh                     # opens http://localhost:8000
```

**Docker**
```bash
docker build -t clipmaker . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... clipmaker
```

**Google Colab (free GPU, nothing to install)**: open `colab.ipynb` in Colab:
https://colab.research.google.com/github/Aung94/Hello-World/blob/main/colab.ipynb

### Turn on Movie Recap (Claude)
Set your API key before starting (from https://console.anthropic.com):
- Windows: `set ANTHROPIC_API_KEY=sk-ant-...` then `start.bat`
- macOS/Linux: `export ANTHROPIC_API_KEY=sk-ant-...` then `./start.sh`

Without a key, everything except Movie Recap and AI highlight picking still works.

### Burmese voice cloning with VoxCPM (optional, GPU)
```bash
pip install voxcpm soundfile
```
Put reference voices in a `voices/` folder: `voices/<name>.wav` plus `voices/<name>.txt` containing exactly what is said in that wav. Then choose **VoxCPM clone** in the app. You can also pick "Clone original speaker".

## ⚙️ Settings (environment variables)

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Turns on Movie Recap and AI highlight picking |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Claude model used for scripts |
| `WHISPER_MODEL` | `small` | `base` is faster, `medium`/`large-v3` more accurate (GPU) |
| `RENDER_WORKERS` | min(4, CPUs) | Clips rendered in parallel |
| `VOXCPM_MODEL` | `openbmb/VoxCPM1.5` | VoxCPM checkpoint or local path |
| `VOXCPM_VOICES_DIR` | `voices` | Preset voice folder |
| `JOBS_DIR` | `jobs` | Where outputs are saved |

## 🧩 How it works

```
link ─► yt-dlp download ─► captions (or faster-whisper) ─► pick moments (Claude or built-in scoring)
     ─► translate (Google) / recap script (Claude) ─► TTS (Edge / VoxCPM)
     ─► time-fit voice + duck original audio ─► burn subtitles ─► ffmpeg render (per aspect ratio)
```

Code: `app/pipeline.py` (pipeline), `app/ai.py` (Claude prompts), `app/main.py` (API), `app/static/index.html` (UI).

## ⚠️ Copyright

Only process videos you own or have permission to use. Recaps with your own narration and short excerpts are much more likely to be accepted than re-uploaded footage. Platforms still review them, and this tool does not try to evade Content ID.

---

### မြန်မာလို အမြန်စတင်နည်း
1. Python 3.10+ နဲ့ ffmpeg ထည့်ပါ
2. Windows မှာ `start.bat` ကို နှိပ်ပါ (Mac/Linux မှာ `./start.sh` ကို run ပါ)။ Browser မှာ http://localhost:8000 အလိုအလျောက် ပွင့်လာပါမယ်
3. Video link ထည့်ပါ → အသံဘာသာ (Dub) နဲ့ စာတန်းဘာသာ (Subtitle) ကို ရွေးပါ → **Make AI clips** သို့မဟုတ် **Movie recap** ကို နှိပ်ပါ
4. Movie Recap အတွက် `ANTHROPIC_API_KEY` ထည့်ထားဖို့ လိုပါတယ်
