"""Claude-powered recap script writing and highlight picking (needs ANTHROPIC_API_KEY)."""
import os

from pydantic import BaseModel

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")


class Beat(BaseModel):
    start: float
    end: float
    narration: str


class RecapPlan(BaseModel):
    title: str
    description: str
    beats: list[Beat]


class ClipPick(BaseModel):
    start: float
    end: float
    title: str


class ClipPlan(BaseModel):
    clips: list[ClipPick]


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def _timeline(segs) -> str:
    return "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segs)


def _ask(system: str, prompt: str, schema):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.beta.messages.parse(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": prompt}],
        output_format=schema,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined to write this script")
    if resp.parsed_output is None:
        raise RuntimeError(f"Claude returned no usable plan (stop_reason={resp.stop_reason})")
    return resp.parsed_output


def write_recap(segs, duration, language, minutes, context="") -> RecapPlan:
    system = (
        "You are a professional movie-recap narrator and editor for YouTube, Facebook and TikTok. "
        f"You write recap narration natively in {language} — natural, spoken, gripping, in the "
        "storyteller style of popular recap channels, never a literal translation of the dialogue."
    )
    prompt = f"""Below is the timestamped transcript of a video ({duration / 60:.0f} min long). Plan a recap of about {minutes} minutes.

Return beats in chronological order. Each beat is a source time range (start/end in seconds, inside 0-{duration:.0f}) whose footage will play while the narration is read aloud.
- Cover the whole story arc: setup, key turns, climax, ending. Skip filler.
- Each beat should be 6-25 seconds of footage; pick ranges where the matching action happens.
- The narration for a beat must be speakable in roughly the beat's length (about 2.5 English words per second, or the equivalent in {language}).
- Write every narration line in {language}. Refer to characters by name when the transcript or context gives names.
- title: a short catchy video title in {language}. description: a 2-3 sentence post description in {language}.
{f"Context from the user (names, genre, notes): {context}" if context else ""}

TRANSCRIPT:
{_timeline(segs)}"""
    plan = _ask(system, prompt, RecapPlan)
    beats = [b for b in plan.beats if 0 <= b.start < b.end <= duration + 1 and b.narration.strip()]
    if not beats:
        raise RuntimeError("Claude returned no valid recap beats")
    plan.beats = sorted(beats, key=lambda b: b.start)
    return plan


def pick_clips(segs, duration, n, clip_len) -> list[tuple[float, float, str]]:
    system = "You are a short-form video editor who finds the most engaging, self-contained moments for Reels, Shorts and TikTok."
    prompt = f"""From this timestamped transcript of a {duration / 60:.0f}-minute video, pick the {n} best non-overlapping clips, each about {clip_len:.0f} seconds.
Score moments on: a strong hook in the first 3 seconds, a complete thought or payoff, emotion or surprise, and value to a viewer with no context.
Start and end each clip on sentence boundaries from the transcript. Give each clip a short catchy title.

TRANSCRIPT:
{_timeline(segs)}"""
    plan = _ask(system, prompt, ClipPlan)
    out = []
    for c in sorted(plan.clips, key=lambda c: c.start):
        if 0 <= c.start < c.end <= duration + 1 and all(c.end <= a or c.start >= b for a, b, _ in out):
            out.append((c.start, min(c.end, duration), c.title))
    return out[:n]
