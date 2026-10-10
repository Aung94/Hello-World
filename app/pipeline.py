"""AI Clip Maker pipeline.

link -> download -> transcript (YouTube captions or Whisper) -> pick clips
     -> translate -> AI voice-over (Edge TTS or VoxCPM) -> dub + subtitles -> render
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ai

JOBS_DIR = Path(os.getenv("JOBS_DIR", "jobs")).resolve()
JOBS_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR = Path(__file__).parent / "fonts"
VOXCPM_VOICES_DIR = Path(os.getenv("VOXCPM_VOICES_DIR", "voices"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
RENDER_WORKERS = int(os.getenv("RENDER_WORKERS", str(min(4, os.cpu_count() or 2))))
SR = 24000  # voice track sample rate
# aspect ratio -> (width, height, subtitle font size)
ASPECTS = {"9:16": (1080, 1920, 12), "4:5": (1080, 1350, 14), "1:1": (1080, 1080, 16),
           "16:9": (1920, 1080, 20), "original": (None, None, 20)}

# code -> (name, google-translate code, edge-tts voices)
LANGUAGES = {
    "my": ("Burmese / မြန်မာ", "my", ["my-MM-NilarNeural", "my-MM-ThihaNeural"]),
    "en": ("English", "en", ["en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural",
                             "en-GB-SoniaNeural", "en-GB-RyanNeural"]),
    "th": ("Thai", "th", ["th-TH-PremwadeeNeural", "th-TH-NiwatNeural"]),
    "zh": ("Chinese", "zh-CN", ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"]),
    "hi": ("Hindi", "hi", ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"]),
    "ja": ("Japanese", "ja", ["ja-JP-NanamiNeural", "ja-JP-KeitaNeural"]),
    "ko": ("Korean", "ko", ["ko-KR-SunHiNeural", "ko-KR-InJoonNeural"]),
    "vi": ("Vietnamese", "vi", ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]),
    "id": ("Indonesian", "id", ["id-ID-GadisNeural", "id-ID-ArdiNeural"]),
    "es": ("Spanish", "es", ["es-ES-ElviraNeural", "es-MX-JorgeNeural"]),
    "fr": ("French", "fr", ["fr-FR-DeniseNeural", "fr-FR-HenriNeural"]),
}

JOBS: dict[str, dict] = {}
_job_slots = threading.Semaphore(int(os.getenv("MAX_CONCURRENT_JOBS", "2")))


# ---------------------------------------------------------------- job state
def new_job(opts: dict) -> dict:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "status": "queued", "progress": 0, "step": "Queued",
           "log": [], "outputs": [], "source": None, "error": None,
           "opts": opts, "created": time.time()}
    JOBS[jid] = job
    (JOBS_DIR / jid).mkdir(parents=True, exist_ok=True)
    return job


def _update(job, progress=None, step=None, log=None):
    if progress is not None:
        job["progress"] = round(progress, 1)
    if step:
        job["step"] = step
    msg = log or step
    if msg:
        job["log"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
    (JOBS_DIR / job["id"] / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1))


def start_job(job):
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()


def _run_job(job):
    with _job_slots:
        job["status"] = "running"
        t0 = time.time()
        try:
            run_pipeline(job)
            job["status"] = "done"
            _update(job, 100, "Done", f"Finished in {time.time() - t0:.0f}s")
        except Exception as e:  # surface every failure to the UI
            job["status"] = "error"
            job["error"] = str(e)
            _update(job, log=f"ERROR: {e}")


# ---------------------------------------------------------------- helpers
def sh(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {p.stderr[-1500:]}")
    return p.stdout


def probe_duration(path) -> float:
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip() or 0)


def has_audio(path) -> bool:
    out = sh(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
              "stream=index", "-of", "csv=p=0", str(path)])
    return bool(out.strip())


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def write_srt(path, segs, key="text"):
    lines = []
    for i, s in enumerate(segs, 1):
        lines += [str(i), f"{srt_time(s['start'])} --> {srt_time(s['end'])}", s[key].strip(), ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- 1. download
def download(job, url, workdir: Path):
    import yt_dlp

    _update(job, 2, "Downloading video")

    def hook(d):
        if d.get("status") == "downloading" and d.get("total_bytes"):
            _update(job, 2 + 18 * d["downloaded_bytes"] / d["total_bytes"])

    ydl_opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True,
        "progress_hooks": [hook],
        "concurrent_fragment_downloads": 8,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        captions = _fetch_captions(ydl, info)
    video = next(workdir.glob("source.*"))
    if video.suffix != ".mp4":
        mp4 = workdir / "source.mp4"
        sh(["ffmpeg", "-y", "-i", str(video), "-c", "copy", str(mp4)])
        video = mp4
    return video, info, captions


def _fetch_captions(ydl, info):
    """Use the site's own captions when available — skips Whisper entirely (much faster)."""
    lang = (info.get("language") or "").split("-")[0]
    for pool_key in ("subtitles", "automatic_captions"):
        pool = info.get(pool_key) or {}
        prefs = [k for k in pool if lang and k.split("-")[0] == lang and "orig" in k]
        prefs += [k for k in pool if lang and k.split("-")[0] == lang]
        if not lang and pool_key == "subtitles":
            prefs += list(pool)
        for key in prefs:
            fmt = next((f for f in pool[key] if f.get("ext") == "json3"), None)
            if not fmt:
                continue
            try:
                data = json.loads(ydl.urlopen(fmt["url"]).read().decode())
                segs = _parse_json3(data)
                if segs:
                    return {"lang": key.split("-")[0], "segments": segs}
            except Exception:
                continue
    return None


def _parse_json3(data):
    words = []
    for ev in data.get("events", []):
        if "segs" not in ev:
            continue
        start = ev.get("tStartMs", 0) / 1000
        dur = ev.get("dDurationMs", 0) / 1000
        text = "".join(s.get("utf8", "") for s in ev["segs"]).replace("\n", " ").strip()
        if text:
            words.append({"start": start, "end": start + dur, "text": text})
    return _merge_sentences(words)


def _merge_sentences(parts, max_len=8.0):
    """Merge caption fragments into sentence-sized segments."""
    out, cur = [], None
    for p in parts:
        if cur and (p["start"] - cur["end"] > 1.0 or cur["end"] - cur["start"] > max_len):
            out.append(cur)
            cur = None
        if cur is None:
            cur = dict(p)
        else:
            cur["text"] += " " + p["text"]
            cur["end"] = max(cur["end"], p["end"])
        if re.search(r"[.!?။]$", cur["text"]):
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    for a, b in zip(out, out[1:]):  # captions overlap; clamp
        a["end"] = min(a["end"], b["start"])
    return [s for s in out if s["end"] > s["start"]]


# ---------------------------------------------------------------- 2. transcribe
_whisper = None
_whisper_lock = threading.Lock()


def transcribe(job, video: Path, workdir: Path):
    global _whisper
    from faster_whisper import WhisperModel

    _update(job, 22, f"Transcribing with Whisper ({WHISPER_MODEL})")
    wav = workdir / "audio16k.wav"
    sh(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    total = probe_duration(wav) or 1
    with _whisper_lock:
        if _whisper is None:
            _whisper = WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")
        segments, info = _whisper.transcribe(str(wav), vad_filter=True, beam_size=1)
        out = []
        for s in segments:
            out.append({"start": s.start, "end": s.end, "text": s.text.strip()})
            _update(job, 22 + 28 * min(s.end / total, 1))
    return {"lang": info.language, "segments": out}


# ---------------------------------------------------------------- 3. pick clips
def select_clips(segs, duration, n, clip_len):
    """Pick the n densest, non-overlapping windows of ~clip_len seconds, aligned to sentences."""
    if n <= 0 or duration <= clip_len * 1.2:
        return [(0.0, duration)]
    cands = []
    j = 0
    for i, s in enumerate(segs):
        j = max(j, i)
        while j < len(segs) - 1 and segs[j].get("end") - s["start"] < clip_len:
            j += 1
        span = segs[j]["end"] - s["start"]
        if span < clip_len * 0.6:
            continue
        window = segs[i:j + 1]
        text = " ".join(w["text"] for w in window)
        speech = sum(w["end"] - w["start"] for w in window)
        score = speech / span + 0.02 * len(re.findall(r"[!?]", text))
        cands.append((score, s["start"], segs[j]["end"]))
    cands.sort(reverse=True)
    picked = []
    for _, a, b in cands:
        if all(b <= pa or a >= pb for pa, pb in picked):
            picked.append((a, b))
        if len(picked) >= n:
            break
    if not picked:  # no transcript: evenly spaced
        step = duration / n
        picked = [(i * step, min(i * step + clip_len, duration)) for i in range(n)]
    return sorted(picked)


# ---------------------------------------------------------------- 4. translate
def translate(texts, target, source):
    from deep_translator import GoogleTranslator

    if not texts or source.split("-")[0] == target.split("-")[0]:
        return list(texts)
    tr = GoogleTranslator(source="auto", target=target)
    out, batch, size = [], [], 0

    def flush():
        if not batch:
            return
        res = (tr.translate("\n".join(batch)) or "").split("\n")
        if len(res) != len(batch):  # line count drifted; translate one by one
            res = [tr.translate(t) or t for t in batch]
        out.extend(res)
        batch.clear()

    for t in texts:
        t = t.replace("\n", " ").strip() or "."
        if size + len(t) > 4000:
            flush()
            size = 0
        batch.append(t)
        size += len(t) + 1
    flush()
    return out


# ---------------------------------------------------------------- 5. TTS
async def _edge_tts_all(items, voice, rate):
    import edge_tts

    sem = asyncio.Semaphore(12)

    async def one(text, path):
        async with sem:
            for attempt in range(3):
                try:
                    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))
                    return
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1 + attempt)

    await asyncio.gather(*(one(t, p) for t, p in items))


_voxcpm = None
_voxcpm_lock = threading.Lock()


def _voxcpm_tts_all(items, prompt_wav, prompt_text):
    """Voice cloning with VoxCPM (GPU recommended). Based on the Channel99 VoxCPM script."""
    global _voxcpm
    import soundfile as sf
    from voxcpm import VoxCPM

    with _voxcpm_lock:
        if _voxcpm is None:
            _voxcpm = VoxCPM.from_pretrained(os.getenv("VOXCPM_MODEL", "openbmb/VoxCPM1.5"),
                                             load_denoiser=False)
        sr = _voxcpm.tts_model.sample_rate
        for text, path in items:
            wav = _voxcpm.generate(text=text + " ", prompt_wav_path=prompt_wav,
                                   prompt_text=prompt_text, cfg_value=2.1, inference_timesteps=15)
            sf.write(str(path), wav, sr)


def voxcpm_presets():
    """Each preset is voices/<name>.wav plus voices/<name>.txt holding what is said in the wav."""
    if not VOXCPM_VOICES_DIR.exists():
        return []
    return sorted(p.stem for p in VOXCPM_VOICES_DIR.glob("*.wav") if p.with_suffix(".txt").exists())


def synthesize(job, segs, opts, tts_dir: Path, source_video: Path, source_segs):
    tts_dir.mkdir(exist_ok=True)
    items = [(s["dub"], tts_dir / f"{i:05}.mp3") for i, s in enumerate(segs) if s["dub"].strip()]
    engine = opts.get("engine", "edge")
    if engine == "voxcpm":
        voice = opts.get("voice") or ""
        if voice == "clone-original":
            prompt_wav, prompt_text = _clone_prompt(source_video, source_segs, tts_dir)
        else:
            prompt_wav = str(VOXCPM_VOICES_DIR / f"{voice}.wav")
            prompt_text = (VOXCPM_VOICES_DIR / f"{voice}.txt").read_text(encoding="utf-8").strip()
        items = [(t, p.with_suffix(".wav")) for t, p in items]
        _voxcpm_tts_all(items, prompt_wav, prompt_text)
    else:
        voice = opts.get("voice") or LANGUAGES[opts["target_lang"]][2][0]
        asyncio.run(_edge_tts_all(items, voice, opts.get("rate", "+0%")))
    paths = {}
    k = 0
    for i, s in enumerate(segs):
        if s["dub"].strip():
            paths[i] = items[k][1]
            k += 1
    return paths


def _clone_prompt(video, segs, workdir):
    """Grab ~10s of the original speaker as a voice-clone reference."""
    picked, t = [], 0.0
    for s in segs:
        if s["end"] - s["start"] > 1.5:
            picked.append(s)
            t += s["end"] - s["start"]
        if t > 8:
            break
    if not picked:
        raise RuntimeError("No speech found to clone the original voice from")
    a, b = picked[0]["start"], picked[-1]["end"]
    out = workdir / "clone_ref.wav"
    sh(["ffmpeg", "-y", "-ss", str(a), "-to", str(b), "-i", str(video), "-vn", "-ac", "1",
        "-ar", "16000", str(out)])
    return str(out), " ".join(s["text"] for s in picked)


def build_voice_track(segs, tts_paths, clip_start, clip_end, out_wav: Path, tmp: Path):
    """Time-align every TTS line to its segment (speeding up lines that overrun), streamed to one wav."""
    total = int((clip_end - clip_start) * SR)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        written = 0
        for i, s in enumerate(segs):
            if i not in tts_paths:
                continue
            nxt = segs[i + 1]["start"] if i + 1 < len(segs) else clip_end
            slot = max(min(nxt, clip_end) - s["start"], 0.3)
            dur = probe_duration(tts_paths[i]) or slot
            tempo = min(max(dur / slot, 1.0), 2.0)
            pcm_path = tmp / f"{i:05}.pcm"
            sh(["ffmpeg", "-y", "-i", str(tts_paths[i]), "-af", f"atempo={tempo:.3f}",
                "-ac", "1", "-ar", str(SR), "-f", "s16le", str(pcm_path)])
            pcm = pcm_path.read_bytes()
            offset = int(max(s["start"] - clip_start, 0) * SR)
            if offset > written:
                w.writeframes(b"\0\0" * (offset - written))
                written = offset
            limit = min(int((min(nxt, clip_end) - clip_start) * SR) + SR // 4, total) - written
            if limit > 0:
                chunk = pcm[: limit * 2]
                w.writeframes(chunk)
                written += len(chunk) // 2
        if written < total:
            w.writeframes(b"\0\0" * (total - written))


def resolve_sub(o):
    """Subtitle language: None (no subs), "original", or a LANGUAGES code."""
    sl = o.get("sub_lang", "same")
    if not o.get("subtitles", True) or sl == "none":
        return None
    if sl == "same":
        return o["target_lang"] if o.get("voiceover", True) else "original"
    return sl if sl == "original" or sl in LANGUAGES else None


# ---------------------------------------------------------------- 6. render
def aspect_list(opts):
    return [a for a in opts.get("aspects") or ["9:16"] if a in ASPECTS] or ["9:16"]


def render_clip(source, start, end, voice_wav, srt_name, out_path: Path, opts, src_has_audio, aspect="9:16"):
    w, h, fsize = ASPECTS[aspect]
    tall = bool(h and h > w)
    orig_vol = float(opts.get("original_volume", 0.12))
    burmese = opts.get("sub_font_lang", opts["target_lang"]) == "my"
    font = "Noto Sans Myanmar" if burmese else "Noto Sans"
    style = (f"FontName={font},FontSize={fsize},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             f"BorderStyle=1,Outline=2,Shadow=0,MarginV={60 if tall else 30},Alignment=2")
    if w:  # fit the whole frame, fill the rest with a blurred copy
        v = ("[0:v]split=2[bg][fg];"
             f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=20:2[bgb];"
             f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
             "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    else:
        v = "[0:v]scale=-2:'min(1080,ih)'"
    if srt_name:
        fonts = f":fontsdir={FONTS_DIR}" if FONTS_DIR.exists() else ""
        v += f",subtitles={srt_name}{fonts}:force_style='{style}'"
    v += "[v]"
    if voice_wav and src_has_audio and orig_vol > 0:
        a = f"[0:a]volume={orig_vol}[a0];[1:a]apad[a1];[a0][a1]amix=inputs=2:duration=first:normalize=0[a]"
    elif voice_wav:
        a = "[1:a]apad[a]"
    elif src_has_audio:
        a = "[0:a]anull[a]"
    else:
        a = None
    cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source)]
    if voice_wav:
        cmd += ["-i", str(voice_wav)]
    cmd += ["-filter_complex", v + (";" + a if a else ""), "-map", "[v]"]
    if a:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]
    cmd += ["-c:v", "libx264", "-preset", opts.get("preset", "veryfast"), "-crf", "22",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-shortest", str(out_path)]
    sh(cmd, cwd=out_path.parent)


# ---------------------------------------------------------------- orchestration
def run_pipeline(job):
    o = job["opts"]
    workdir = JOBS_DIR / job["id"]
    video, info, captions = download(job, o["url"], workdir)
    job["source"] = {"file": f"/files/{job['id']}/{video.name}", "title": info.get("title"),
                     "duration": info.get("duration")}
    _update(job, 20, f"Downloaded: {info.get('title')}")
    if o.get("mode") == "download":
        return

    duration = probe_duration(video)
    if captions:
        _update(job, 50, f"Using site captions ({captions['lang']}) — skipped Whisper")
        tr = captions
    else:
        tr = transcribe(job, video, workdir)
    segs = tr["segments"]
    write_srt(workdir / "transcript_original.srt", segs)

    if o.get("mode") == "recap":
        return run_recap(job, video, segs, duration, tr["lang"])

    n, clip_len = int(o.get("num_clips", 5)), float(o.get("clip_length", 60))
    clips, titles = None, {}
    if n > 0 and segs and o.get("use_ai", True) and ai.available():
        try:
            _update(job, 51, "Claude is picking the best moments")
            picked = ai.pick_clips(segs, duration, n, clip_len)
            if picked:
                clips = [(a, b) for a, b, _ in picked]
                titles = {i: t for i, (_, _, t) in enumerate(picked)}
        except Exception as e:
            _update(job, log=f"AI clip picking failed ({e}); using built-in scoring")
    if not clips:
        clips = select_clips(segs, duration, n, clip_len)
    _update(job, 52, f"Selected {len(clips)} clip(s)")

    # only translate/voice what lands in a clip — huge saving on long videos
    clip_segs = []
    for a, b in clips:
        part = [dict(s, start=max(s["start"], a), end=min(s["end"], b))
                for s in segs if s["end"] > a and s["start"] < b]
        clip_segs.append(part)
    flat = [s for part in clip_segs for s in part]

    want_voice = o.get("voiceover", True)
    sub_mode = resolve_sub(o)
    o["sub_font_lang"] = tr["lang"] if sub_mode == "original" else sub_mode
    texts = [s["text"] for s in flat]
    if want_voice:
        _update(job, 55, f"Translating {len(flat)} lines to {LANGUAGES[o['target_lang']][0]} for the dub")
        for s, t in zip(flat, translate(texts, LANGUAGES[o["target_lang"]][1], tr["lang"])):
            s["dub"] = t
    if sub_mode == "original":
        for s in flat:
            s["sub"] = s["text"]
    elif sub_mode and want_voice and sub_mode == o["target_lang"]:
        for s in flat:
            s["sub"] = s["dub"]
    elif sub_mode:
        _update(job, 60, f"Translating subtitles to {LANGUAGES[sub_mode][0]}")
        for s, t in zip(flat, translate(texts, LANGUAGES[sub_mode][1], tr["lang"])):
            s["sub"] = t

    tts_paths_all = {}
    if want_voice and flat:
        _update(job, 65, f"Generating AI voice-over ({o.get('engine', 'edge')})")
        tts = synthesize(job, flat, o, workdir / "tts", video, segs)
        tts_paths_all = {id(flat[i]): p for i, p in tts.items()}

    _update(job, 80, "Dubbing + rendering clips")
    src_audio = has_audio(video)
    done = [0]

    def make(idx):
        a, b = clips[idx]
        part = clip_segs[idx]
        cdir = workdir / f"clip_{idx + 1:02}"
        cdir.mkdir(exist_ok=True)
        rel = [dict(s, start=s["start"] - a, end=s["end"] - a) for s in part]
        srt = None
        if sub_mode and rel:
            srt = "subs.srt"
            write_srt(cdir / srt, rel, key="sub")
        voice_wav = None
        paths = {i: tts_paths_all[id(s)] for i, s in enumerate(part) if id(s) in tts_paths_all}
        if paths:
            voice_wav = cdir / "voice.wav"
            build_voice_track(part, paths, a, b, voice_wav, cdir)
        videos = []
        for asp in aspect_list(o):
            out = cdir / f"clip_{idx + 1:02}_{asp.replace(':', 'x')}.mp4"
            render_clip(video, a, b, voice_wav, srt, out, o, src_audio, asp)
            videos.append({"aspect": asp, "url": f"/files/{job['id']}/{cdir.name}/{out.name}"})
        script = "\n".join(s.get("dub") or s.get("sub") or s["text"] for s in part)
        (cdir / "script.txt").write_text(script, encoding="utf-8")
        for f in cdir.glob("*.pcm"):
            f.unlink()
        done[0] += 1
        _update(job, 80 + 19 * done[0] / len(clips), log=f"Rendered clip {idx + 1}")
        return {"index": idx + 1, "title": titles.get(idx), "start": round(a, 2), "end": round(b, 2),
                "video": videos[0]["url"], "videos": videos,
                "subtitles": f"/files/{job['id']}/{cdir.name}/{srt}" if srt else None,
                "script": f"/files/{job['id']}/{cdir.name}/script.txt",
                "preview": ((part[0].get("dub") or part[0].get("sub") or part[0]["text"])[:120] if part else "")}

    with ThreadPoolExecutor(RENDER_WORKERS) as ex:
        job["outputs"] = sorted(ex.map(make, range(len(clips))), key=lambda r: r["index"])
    shutil.rmtree(workdir / "tts", ignore_errors=True)


# ---------------------------------------------------------------- movie recap mode
def split_subs(text, start, dur, max_chars=60):
    """Split narration into short subtitle cues, timed by character share."""
    parts = []
    for sent in re.split(r"(?<=[။.!?])\s*", text.strip()):
        while len(sent) > max_chars:
            cut = max(sent.rfind(c, 0, max_chars) for c in " ,၊")
            cut = cut if cut > max_chars // 3 else max_chars
            parts.append(sent[:cut + 1].strip())
            sent = sent[cut + 1:]
        if sent.strip():
            parts.append(sent.strip())
    total = sum(len(p) for p in parts) or 1
    cues, t = [], start
    for p in parts:
        d = dur * len(p) / total
        cues.append({"start": t, "end": t + d, "text": p})
        t += d
    return cues


def run_recap(job, video, segs, duration, src_lang):
    """LLM writes a narrated recap -> TTS per beat -> footage stretched to fit narration -> one video."""
    o = job["opts"]
    workdir = JOBS_DIR / job["id"]
    if not ai.available():
        raise RuntimeError("Movie recap mode needs ANTHROPIC_API_KEY set on the server")
    if not segs:
        raise RuntimeError("No speech found in the video to build a recap from")
    lang_name = LANGUAGES[o["target_lang"]][0]
    _update(job, 52, f"Claude is writing the {lang_name} recap script")
    plan = ai.write_recap(segs, duration, lang_name, float(o.get("recap_minutes", 5)), o.get("context", ""))
    beats = [{"start": b.start, "end": b.end, "dub": b.narration} for b in plan.beats]
    _update(job, 62, f"Script ready: {len(beats)} scenes — {plan.title}")

    sub_mode = resolve_sub(o)
    narr = [b["dub"] for b in beats]
    if sub_mode in (None, "original", o["target_lang"]):
        sub_texts = narr  # recap has no original-dialogue track; "original" = narration language
    else:
        _update(job, 63, f"Translating subtitles to {LANGUAGES[sub_mode][0]}")
        sub_texts = translate(narr, LANGUAGES[sub_mode][1], LANGUAGES[o["target_lang"]][1])
    o["sub_font_lang"] = o["target_lang"] if sub_mode in (None, "original") else sub_mode

    tts = {}
    if o.get("voiceover", True):
        _update(job, 65, f"Generating AI narration ({o.get('engine', 'edge')})")
        tts = synthesize(job, beats, o, workdir / "tts", video, segs)

    _update(job, 78, "Cutting scenes to match narration")
    src_audio = has_audio(video)
    rdir = workdir / "recap"
    rdir.mkdir(exist_ok=True)
    o = dict(o, original_volume=min(float(o.get("original_volume", 0.12)), 0.15))
    spans = []
    for i, b in enumerate(beats):
        tdur = probe_duration(tts[i]) if i in tts else b["end"] - b["start"]
        length = max(b["end"] - b["start"], tdur + 0.4)  # footage runs at least as long as the line
        a = max(0.0, min(b["start"], duration - length))
        spans.append((a, min(a + length, duration), tdur))
    done = [0]

    def make(i):
        a, end, tdur = spans[i]
        srt = None
        if sub_mode:
            srt = f"beat_{i:03}.srt"
            write_srt(rdir / srt, split_subs(sub_texts[i], 0, tdur))
        outs = {}
        for asp in aspects:
            out = rdir / f"beat_{i:03}_{asp.replace(':', 'x')}.mp4"
            render_clip(video, a, end, tts.get(i), srt, out, o, src_audio, asp)
            outs[asp] = out
        done[0] += 1
        _update(job, 78 + 18 * done[0] / len(beats))
        return outs

    aspects = aspect_list(o)
    with ThreadPoolExecutor(RENDER_WORKERS) as ex:
        rendered = list(ex.map(make, range(len(beats))))

    _update(job, 97, "Joining scenes into the final recap")
    videos = []
    for asp in aspects:
        tag = asp.replace(":", "x")
        (rdir / f"list_{tag}.txt").write_text("".join(f"file '{r[asp].name}'\n" for r in rendered))
        sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"list_{tag}.txt", "-c", "copy",
            "-movflags", "+faststart", str(workdir / f"recap_{tag}.mp4")], cwd=rdir)
        videos.append({"aspect": asp, "url": f"/files/{job['id']}/recap_{tag}.mp4"})
    parts = [p for r in rendered for p in r.values()]

    cues, t = [], 0.0
    for text, span, r in zip(sub_texts, spans, rendered):
        cues += split_subs(text, t, span[2])
        t += probe_duration(r[aspects[0]])
    write_srt(workdir / "recap.srt", cues)
    script = f"{plan.title}\n\n{plan.description}\n\n" + "\n\n".join(b["dub"] for b in beats)
    (workdir / "recap_script.txt").write_text(script, encoding="utf-8")
    shutil.rmtree(workdir / "tts", ignore_errors=True)
    for p in parts:
        p.unlink()
    job["outputs"] = [{"index": 1, "title": plan.title, "start": 0, "end": round(t, 2),
                       "video": videos[0]["url"], "videos": videos,
                       "subtitles": f"/files/{job['id']}/recap.srt",
                       "script": f"/files/{job['id']}/recap_script.txt",
                       "preview": plan.description[:200]}]
