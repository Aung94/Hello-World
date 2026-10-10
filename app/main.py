from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, pipeline

app = FastAPI(title="AI Clip Maker")
STATIC = Path(__file__).parent / "static"


class JobRequest(BaseModel):
    url: str
    mode: Literal["download", "clips", "recap"] = "clips"
    target_lang: str = "my"  # dub / narration language
    sub_lang: str = "same"  # same | original | none | a language code
    engine: Literal["edge", "voxcpm"] = "edge"
    voice: str | None = None
    rate: str = "+0%"
    num_clips: int = Field(5, ge=0, le=30)  # 0 = dub the whole video
    clip_length: float = Field(60, ge=10, le=600)
    voiceover: bool = True
    subtitles: bool = True
    aspects: list[Literal["9:16", "4:5", "1:1", "16:9", "original"]] = ["9:16"]
    original_volume: float = Field(0.12, ge=0, le=1)
    use_ai: bool = True
    recap_minutes: float = Field(5, ge=1, le=60)
    context: str = Field("", max_length=1000)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/options")
def options():
    return {
        "languages": {k: {"name": v[0], "voices": v[2]} for k, v in pipeline.LANGUAGES.items()},
        "voxcpm_presets": pipeline.voxcpm_presets(),
        "ai": ai.available(),
    }


@app.post("/api/jobs")
def create_job(req: JobRequest):
    if req.target_lang not in pipeline.LANGUAGES:
        raise HTTPException(400, "Unsupported language")
    if req.sub_lang not in ("same", "original", "none", *pipeline.LANGUAGES):
        raise HTTPException(400, "Unsupported subtitle language")
    job = pipeline.new_job(req.model_dump())
    pipeline.start_job(job)
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = pipeline.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


app.mount("/files", StaticFiles(directory=pipeline.JOBS_DIR), name="files")
