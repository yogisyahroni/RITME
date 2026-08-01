#!/usr/bin/env python3
"""
Local web server for RITME. Wraps the 5 pipeline stages as a REST API
and serves the frontend — one process, one command:

    python server.py

Then open http://localhost:8000

Design notes:
- Single-user local tool, so job state lives in memory (job_manager.py),
  no database/queue needed.
- Slow stages (footage matching, rendering) run as background jobs the
  frontend polls via GET /api/jobs/{id}. Fast stages (template extraction
  is scene-detection-bound, script/narration are one API call each) also
  go through the job pattern for a consistent loading UI, but report
  coarser progress.
- Frontend and API share one origin (this server serves both), so there's
  no CORS configuration to worry about.
"""
import os
import shutil
import sys
import uuid
import threading
import torch
from pathlib import Path
import faulthandler
faulthandler.enable()

# Fix for "Fatal Python error: Aborted" related to OpenMP and Whisper in background threads
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# See main.py for why this is needed on Windows (legacy console codepage
# can't print the ✅/—/etc. characters used throughout the pipeline's logs).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import json
import os

from config import TEMPLATES_DIR, OUTPUT_DIR, CACHE_DIR
from job_manager import job_manager


from pipeline import stage1_template, stage2_script, stage3_narration, stage4_footage, stage5_assembly, footage_extractor, project_exporter

app = FastAPI(title="RITME pipeline API")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:8000", "http://localhost:8585", "http://127.0.0.1:8000", "http://127.0.0.1:8585"], allow_methods=["*"], allow_headers=["*"],
)

UPLOADS_DIR = CACHE_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Upload limits
MAX_UPLOAD_SIZE_MB = 2048  # 2GB max upload
ALLOWED_VIDEO_TYPES = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
ALLOWED_AUDIO_TYPES = {'.wav', '.mp3', '.m4a', '.ogg', '.flac'}


def _validate_upload(file: UploadFile, allowed_types: set) -> None:
    "Validate uploaded file type before processing."
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if not ext or ext not in allowed_types:
        raise HTTPException(400, f"Format file tidak didukung: {ext}. Yang diizinkan: {', '.join(sorted(allowed_types))}")


# ============================================================
# Stage 1 — Template extraction
# ============================================================
@app.post("/api/template/extract")
async def extract_template(
    video: UploadFile = File(...),
    name: str = Form(...),
    scene_threshold: float = Form(27.0),
    analyze_speech: bool = Form(True),
):
    _validate_upload(video, ALLOWED_VIDEO_TYPES)
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_{video.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(video.file, f)

    job_id = job_manager.create()

    def _run(job_id):
        job_manager.update(job_id, message="Mendeteksi shot boundaries…", progress=20)
        template = stage1_template.build_template(
            str(dest), name, scene_threshold=scene_threshold, analyze_speech=analyze_speech,
        )
        return template

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


@app.get("/api/template/list")
def list_templates_old():
    return [p.stem for p in TEMPLATES_DIR.glob("*.json") if not p.name.endswith("_script.json")]

@app.get("/api/template/{name}")
def get_template(name: str):
    try:
        return stage1_template.load_template(name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ============================================================
# Stage 2 — Script generation
# ============================================================

@app.get("/api/templates")
def list_templates():
    from config import TEMPLATES_DIR
    templates = []
    if TEMPLATES_DIR.exists():
        for p in sorted(TEMPLATES_DIR.glob("*.json")):
            if p.stem.endswith("_script"):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if "template_name" in data:
                    templates.append(data)
            except:
                pass
    return templates
def _validate_youtube_url(url: str) -> str:
    """Allow only real YouTube URLs — prevents yt-dlp SSRF against
    internal networks (169.254.169.254 metadata, localhost services, …)."""
    import re
    url = (url or "").strip()
    if not re.match(r"^https?://(www\.)?(youtube\.com/(watch\?v=|shorts/|live/|embed/)|youtu\.be/)", url):
        raise HTTPException(400, "URL harus video YouTube (youtube.com/watch, /shorts/, /live/, /embed/, atau youtu.be)")
    return url


class ScriptRequest(BaseModel):
    template_name: str
    topic: str
    segments: int = 8
    style_id: str | None = None
    language: str = "id"
    custom_script: str | None = None
    # Fase 1B.3: optional footage source processed in parallel with script gen.
    footage_youtube_url: str | None = None


@app.get("/api/script/styles")
def get_script_styles():
    return stage2_script.list_script_styles()


def _run_script_job(job_id: str, req, footage_video_path: str | None = None) -> dict:
    """
    Shared Stage-2 runner. If a long footage file (local upload path or
    YouTube URL) is attached, footage extraction starts on a background
    thread IN PARALLEL with web research + script writing. The job's
    `footage_extraction` field carries sub-progress so the UI shows one
    unified job. Returns the script dict (result of the job).
    """
    import yt_dlp  # lazy — heavy import, only needed when footage attached

    extract_result = {}
    footage_thread = None

    if footage_video_path or req.footage_youtube_url:
        job_manager.update_footage(job_id, "pending", 0, "Menunggu...")

        def _extract():
            try:
                dest = footage_video_path
                actual_topic = req.topic.strip()
                if req.footage_youtube_url:
                    job_manager.update_footage(job_id, "running", 2, "Mengunduh video dari YouTube...")
                    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_youtube.mp4"
                    ydl_opts = {"format": "best[height<=1080]", "outtmpl": str(dest), "quiet": True}
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(_validate_youtube_url(req.footage_youtube_url), download=True)
                        if info and 'title' in info and not actual_topic:
                            actual_topic = " ".join(info['title'].split()[:4])

                def on_progress(message, pct):
                    job_manager.update_footage(job_id, "running", pct, message)

                job_manager.update_footage(job_id, "running", 10, "Memulai deteksi scene...")
                files = footage_extractor.extract_clips(
                    str(dest),
                    output_dir="outputs/extracted_footage",
                    threshold=27.0,
                    min_duration_sec=2.0,
                    base_name=None,
                    on_progress=on_progress,
                    topic=actual_topic,
                )
                extract_result["files"] = files
                extract_result["count"] = len(files)
                extract_result["output_dir"] = "outputs/extracted_footage"
                job_manager.update_footage(job_id, "done", 100, "Selesai")
            except Exception as e:
                job_manager.update_footage(job_id, "error", 100, str(e))
                extract_result["error"] = str(e)

        footage_thread = threading.Thread(target=_extract, daemon=True)
        footage_thread.start()
        job_manager.update_footage(job_id, "running", 0, "Ekstraksi footage berjalan paralel...")

    try:
        if req.custom_script:
            job_manager.update(job_id, message="Menganalisis naskah buatan sendiri…", progress=30)
            sources = []
        else:
            job_manager.update(job_id, message="Riset web…", progress=15)
            sources = stage2_script.web_research(req.topic)

        job_manager.update(job_id, message="Menulis naskah…", progress=50)
        script = stage2_script.generate_script(
            req.topic, stage1_template.load_template(req.template_name),
            target_segments=req.segments,
            research_results=sources, style_id=req.style_id,
            language=req.language, custom_script=req.custom_script
        )
    finally:
        # Wait for footage extraction to finish so a single job result
        # carries both script + extraction outcome.
        if footage_thread is not None:
            footage_thread.join(timeout=900)

    if footage_thread is not None:
        script["footage_extraction"] = extract_result
    return script


@app.post("/api/script/generate")
def generate_script(req: ScriptRequest):
    try:
        stage1_template.load_template(req.template_name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    job_id = job_manager.create()

    def _run(job_id):
        return _run_script_job(job_id, req)

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


@app.post("/api/script/generate_with_footage")
async def generate_script_with_footage(
    template_name: str = Form(...),
    topic: str = Form(...),
    segments: int = Form(8),
    style_id: str = Form(None),
    language: str = Form("id"),
    custom_script: str = Form(None),
    video: UploadFile = File(...),
):
    """Same as /api/script/generate but accepts an uploaded long footage file
    that is extracted (scene-split + auto-tag) in parallel with script gen."""
    try:
        stage1_template.load_template(template_name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    _validate_upload(video, ALLOWED_VIDEO_TYPES)
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_{Path(video.filename).name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(video.file, f)

    req = ScriptRequest(
        template_name=template_name, topic=topic, segments=segments,
        style_id=style_id, language=language, custom_script=custom_script,
    )
    job_id = job_manager.create()

    def _run(job_id):
        return _run_script_job(job_id, req, footage_video_path=str(dest))

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


# ============================================================
# Stage 3 — Narration
# ============================================================
class NarrationRequest(BaseModel):
    segments: list[dict]  # [{text, keywords}, ...] from stage 2
    tts_provider: str | None = None  # "pyttsx3" | "elevenlabs" | None (use .env default)
    voices: list[str] | None = None  # Fase 5.1: per-segment TTS voice id


@app.get("/api/narration/voices")
def narration_voices(provider: str | None = None):
    """Fase 5.1: daftar voice tersedia untuk provider TTS (dropdown per segmen)."""
    try:
        return {"provider": provider or stage3_narration.TTS_PROVIDER,
                "voices": stage3_narration.list_available_voices(provider)}
    except Exception as e:
        raise HTTPException(500, f"Gagal memuat daftar voice: {e}")


@app.post("/api/narration/generate")
def generate_narration(req: NarrationRequest):
    job_id = job_manager.create()

    def _run(job_id):
        if req.tts_provider in ["xtts", "f5tts", "omnivoice"]:
            msg = f"Sintesis suara menggunakan AI Local ({req.tts_provider.upper()})..."
        else:
            msg = "Sintesis suara..."
        job_manager.update(job_id, message=msg, progress=20)
        # Fase 3.0: synthesize per segment (each voice file travels with its
        # timeline clip), then rebuild the full track by concatenation so the
        # whisper transcription & music ducking still see one continuous audio.
        seg_paths, seg_durs = stage3_narration.synthesize_narration_per_segment(
            req.segments, provider=req.tts_provider, voices=req.voices)
        if all(seg_paths):
            try:
                audio_path = stage3_narration.concat_audio_files(
                    seg_paths, str(CACHE_DIR / "audio" / "narration.wav"))
            except Exception as e:
                print(f"[server] Segment concat failed ({e}) — fallback full synth.")
                audio_path = stage3_narration.synthesize_narration(req.segments, provider=req.tts_provider)
                seg_paths, seg_durs = [], []
        else:
            audio_path = stage3_narration.synthesize_narration(req.segments, provider=req.tts_provider)
            seg_paths, seg_durs = [], []
        job_manager.update(job_id, message="Transkripsi timing per kata…", progress=60)
        word_timestamps = stage3_narration.transcribe_with_timestamps(audio_path)
        timed = stage3_narration.align_keywords_to_timestamps(req.segments, word_timestamps)
        return {
            "audio_path": audio_path,
            "audio_url": f"/outputs/audio/{Path(audio_path).name}",
            "segments": timed,
            "segment_audio_paths": seg_paths,
            "segment_audio_durations": seg_durs,
        }

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


@app.post("/api/narration/upload")
async def upload_narration(
    audio: UploadFile = File(...),
    segments: str = Form(...)
):
    import json
    segs = json.loads(segments)
    
    ext = Path(audio.filename).suffix
    audio_dir = CACHE_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    _validate_upload(audio, ALLOWED_AUDIO_TYPES)
    dest = audio_dir / f"upload_{uuid.uuid4().hex}{ext}"
    
    with open(dest, "wb") as f:
        shutil.copyfileobj(audio.file, f)
        
    job_id = job_manager.create()

    def _run(job_id):
        job_manager.update(job_id, message="Menyelaraskan teks dengan audio (Whisper)…", progress=30)
        word_timestamps = stage3_narration.transcribe_with_timestamps(str(dest))
        
        job_manager.update(job_id, message="Menyusun timing segmen…", progress=80)
        timed = stage3_narration.align_keywords_to_timestamps(segs, word_timestamps)
        
        return {"audio_path": str(dest), "audio_url": f"/outputs/audio/{dest.name}", "segments": timed}

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


# ============================================================
# Stage 4 — Footage sourcing + CLIP matching
# ============================================================
class FootageRequest(BaseModel):
    segments: list[dict]  # timed segments from stage 3, each has "keywords"
    # Fase 1B.3 race-condition handling: if set to a script job id that has
    # a parallel footage extraction running, block until that extraction
    # finishes (or fails) before starting to match — so local footage is
    # ready before CLIP scores segments against it.
    wait_for_script_job: str | None = None


@app.post("/api/footage/match")
def match_footage(req: FootageRequest):
    job_id = job_manager.create()

    def _run(job_id):
        # Wait for a parallel footage extraction attached to a script job.
        if req.wait_for_script_job:
            script_job = job_manager.get(req.wait_for_script_job)
            if script_job and script_job.get("footage_extraction"):
                fe = script_job["footage_extraction"]
                if fe.get("status") in ("pending", "running"):
                    job_manager.update(job_id, progress=0, message="Menunggu ekstraksi footage selesai…")
                    import time as _time
                    deadline = _time.time() + 900  # 15 min cap
                    while _time.time() < deadline:
                        fe = (job_manager.get(req.wait_for_script_job) or {}).get("footage_extraction") or {}
                        if fe.get("status") not in ("pending", "running"):
                            break
                        _time.sleep(1.5)
                    if fe.get("status") == "error":
                        print(f"[stage4] Parallel footage extraction errored: {fe.get('message')}")
                    job_manager.update(job_id, progress=2, message=f"Ekstraksi footage: {fe.get('message') or 'selesai'} — melanjutkan pencarian…")

        def on_progress(done, total, seg):
            pct = 2 + int(done / total * 96)
            job_manager.update(job_id, progress=pct, message=f"Segmen {done}/{total}: {seg['keywords'][0]}")

        results = stage4_footage.match_all_segments(req.segments, on_progress=on_progress, top_n=4)
        # results: {segment_index: [ranked candidates]}. Attach servable preview
        # URLs and surface the top pick separately so the UI can both show a
        # selectable grid and default to the auto-picked best match.
        # thumbnail_url points at the sample frame CLIP already extracted
        # during scoring (saved as "<video_file>.sample.jpg" next to it) —
        # no extra encoding work, just serving a file that already exists.
        out = {}
        for idx, candidates in results.items():
            enriched = []
            for c in candidates:
                fname = Path(c["video_path"]).name
                sample_path = Path(c["video_path"] + ".sample.jpg")
                enriched.append({
                    **c,
                    "preview_url": f"/outputs/footage/{fname}",
                    "thumbnail_url": f"/outputs/footage/{fname}.sample.jpg" if sample_path.exists() else None,
                })
            out[str(idx)] = {
                "candidates": enriched,
                "best_index": 0 if enriched else None,  # already sorted best-first
            }
        return out

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


# ============================================================
# Local Footage Extraction (Standalone feature)
# ============================================================
class YoutubeExtractRequest(BaseModel):
    youtube_url: str
    topic: str = ""

@app.post("/api/footage/extract_youtube")
async def extract_youtube_footage(req: YoutubeExtractRequest):
    import uuid
    import yt_dlp
    
    if not req.youtube_url.strip():
        raise HTTPException(400, "youtube_url wajib diisi")
    _validate_youtube_url(req.youtube_url)
    
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_youtube.mp4"
    
    job_id = job_manager.create()
    output_dir = "outputs/extracted_footage"

    def _run(job_id):
        def on_progress(message, pct):
            job_manager.update(job_id, progress=pct, message=message)

        job_manager.update(job_id, progress=0, message="Mengunduh video dari YouTube...")
        
        actual_topic = req.topic
        try:
            ydl_opts = {
                "format": "best[height<=1080]",
                "outtmpl": str(dest),
                "quiet": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(req.youtube_url, download=True)
                if not actual_topic and info and 'title' in info:
                    title_words = info['title'].split()
                    actual_topic = " ".join(title_words[:4])
        except Exception as e:
            job_manager.update(job_id, progress=100, message=f"Error: Gagal mengunduh YouTube: {e}")
            raise RuntimeError(f"Gagal mengunduh YouTube: {e}")
            
        job_manager.update(job_id, progress=10, message="Memulai deteksi scene...")
        
        files = footage_extractor.extract_clips(
            str(dest),
            output_dir=output_dir,
            threshold=27.0,
            min_duration_sec=2.0,
            base_name=None,
            on_progress=lambda msg, pct: on_progress(msg, 10 + pct * 0.9),
            topic=actual_topic
        )
        return {"extracted_files": files, "count": len(files), "output_dir": output_dir}

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


# ============================================================
@app.post("/api/footage/extract")
async def extract_local_footage(
    video: UploadFile = File(...),
    topic: str = Form("")
):
    import uuid
    import shutil
    import os
    
    actual_topic = topic
    if not actual_topic and video.filename:
        base_name = os.path.splitext(video.filename)[0]
        actual_topic = base_name.replace("_", " ")

    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_{video.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(video.file, f)
        
    job_id = job_manager.create()
    output_dir = "outputs/extracted_footage"

    def _run(job_id):
        def on_progress(message, pct):
            job_manager.update(job_id, progress=pct, message=message)

        job_manager.update(job_id, progress=0, message="Memulai deteksi scene...")
        
        files = footage_extractor.extract_clips(
            str(dest),
            output_dir=output_dir,
            threshold=27.0,
            min_duration_sec=2.0,
            base_name=None,
            on_progress=on_progress,
            topic=actual_topic
        )
        return {"extracted_files": files, "count": len(files), "output_dir": output_dir}

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


# ============================================================
# Stage 5 — Assembly & render
# ============================================================
class RenderRequest(BaseModel):
    template_name: str
    timed_segments: list[dict]
    footage_map: dict[str, dict]  # segment index (as string) -> match dict
    narration_audio_path: str
    output_name: str = "final_output"
    music_path: str | None = None  # explicit music file; None = auto-pick from music/ by script mood


@app.post("/api/render")
def render_video(req: RenderRequest):
    try:
        template = stage1_template.load_template(req.template_name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    job_id = job_manager.create()
    footage_map_int = {int(k): v for k, v in req.footage_map.items() if v}

    def _run(job_id):
        def on_progress(pct, message):
            job_manager.update(job_id, progress=pct, message=message)

        out_path = stage5_assembly.assemble_video(
            req.timed_segments, footage_map_int, req.narration_audio_path, template,
            output_name=req.output_name, on_progress=on_progress,
            music_path=req.music_path,
        )
        return {"output_path": out_path, "output_url": f"/outputs/render/{Path(out_path).name}"}

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


# ============================================================
# Project Export — Download project files for editors
# ============================================================
class ExportRequest(BaseModel):
    timed_segments: list[dict]
    footage_map: dict[str, dict]
    narration_audio_path: str = ""
    output_name: str = "ritme_project"
    formats: list[str] = ["edl", "fcpxml", "premiere_xml", "capcut_json"]
    # --- Finishing info embedded in the export (Fase 1C.2) ---
    add_music: bool = False
    music_mood: str | None = None
    music_path: str | None = None   # explicit; else resolved from mood
    caption_style: str = "minimal-white-center"
    transition_style: str = "hard_cut"
    ken_burns: bool = False


@app.post("/api/export/project")
def export_project_endpoint(req: ExportRequest):
    from fastapi.responses import FileResponse
    footage_map_int = {int(k): v for k, v in req.footage_map.items() if v}

    # Resolve the music file for the export (Fase 1C.2)
    music_path = req.music_path
    if not music_path and req.add_music:
        try:
            from pipeline import stage_music
            picked = stage_music.pick_music_by_mood(req.music_mood) if req.music_mood else None
            if picked is None:
                picked = stage_music.pick_music_file(req.timed_segments)
            music_path = str(picked) if picked else None
        except Exception:
            music_path = None

    finishing = {
        "music_path": music_path or "",
        "music_mood": req.music_mood,
        "caption_style": req.caption_style,
        "transition_style": req.transition_style,
        "ken_burns": req.ken_burns,
    }
    
    try:
        zip_path = project_exporter.export_project(
            req.timed_segments, footage_map_int,
            req.narration_audio_path, req.output_name, req.formats,
            finishing=finishing,
        )
        
        if not os.path.exists(zip_path):
            raise HTTPException(500, f"Export failed: {zip_path} not found")
            
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{_safe_output_name(req.output_name)}_project.zip"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Export gagal: {e}")


# ============================================================
# Job polling
# ============================================================
@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


# ============================================================
# Setup check (mirrors check_setup.py, as JSON for the UI)
# ============================================================
@app.get("/api/setup/check")
def setup_check():
    import os
    keys = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
            "PEXELS_API_KEY", "PIXABAY_API_KEY", "YOUTUBE_API_KEY", "ELEVENLABS_API_KEY"]
    return {k: bool(os.getenv(k)) for k in keys}


# ============================================================

# Timeline Export — Render edited timeline
# ============================================================
class TimelineSegment(BaseModel):
    index: int
    video_path: str
    narration_text: str = ""
    start_trim: float = 0.0  # seconds to trim from start of source
    end_trim: float = 0.0    # seconds to trim from end of source
    duration: float = 3.0    # final duration after trimming
    keywords: list[str] = []
    audio_path: str = ""     # Fase 3.0: per-segment narration audio
    words: list[dict] = []   # Fase 3.4: per-word subtitle timing (karaoke)

class TimelineExportRequest(BaseModel):
    segments: list[TimelineSegment]
    narration_audio_path: str = ""
    output_name: str = "ritme_timeline"
    template_name: str = ""           # optional; fallback pacing when empty
    # --- Finishing options (Fase 1C.1): manual user choices, defaults OFF ---
    add_music: bool = False
    music_mood: str | None = None
    caption_style: str = "minimal-white-center"
    transition_style: str = "hard_cut"   # "hard_cut" | "crossfade"
    ken_burns: bool = False
    # --- Watermark (Fase 5.2): logo overlay ---
    watermark_path: str | None = None
    watermark_pos: str = "bottom-right"


class BatchItem(BaseModel):
    name: str = "batch_item"
    segments: list[TimelineSegment]
    narration_audio_path: str = ""
    template_name: str = ""
    add_music: bool = False
    music_mood: str | None = None
    caption_style: str = "minimal-white-center"
    transition_style: str = "hard_cut"
    ken_burns: bool = False
    watermark_path: str | None = None
    watermark_pos: str = "bottom-right"


class BatchRenderRequest(BaseModel):
    items: list[BatchItem]


@app.post("/api/batch/render")
def batch_render(req: BatchRenderRequest):
    """Fase 5.3: render banyak project berurutan dalam satu job.
    Setiap item pakai pipeline yang sama dengan /api/timeline/export.
    Progress global = rata-rata per-item; result = daftar per-item."""
    if not req.items:
        raise HTTPException(400, "Tidak ada item untuk dirender")
    if len(req.items) > 10:
        raise HTTPException(400, "Maksimal 10 project per batch")
    job_id = job_manager.create()
    total = len(req.items)

    def _run(job_id):
        results = []
        for idx, item in enumerate(req.items):
            job_manager.update(
                job_id, progress=round(idx / total * 100), message=f"Render {idx + 1}/{total}: {item.name}")
            entry = {"name": item.name, "status": "ok", "url": "", "path": "", "error": ""}
            try:
                timed, footage = _timeline_to_stage5(item.segments)
                if not timed:
                    raise RuntimeError("Tidak ada segmen video valid")
                template = _load_timeline_template(item.template_name)
                out = stage5_assembly.assemble_video(
                    timed, footage, item.narration_audio_path, template,
                    output_name=_safe_output_name(item.name),
                    add_music=item.add_music, music_mood=item.music_mood,
                    caption_style=item.caption_style,
                    transition_style=item.transition_style,
                    ken_burns=item.ken_burns,
                    watermark_path=item.watermark_path, watermark_pos=item.watermark_pos,
                )
                entry["path"] = out
                rel = str(Path(out).relative_to(OUTPUT_DIR))
                entry["url"] = f"/outputs/render/{rel}"
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = str(e)
            results.append(entry)
        job_manager.update(job_id, progress=100, message="Batch selesai")
        return {"items": results}

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


def _timeline_to_stage5(segments: list[TimelineSegment]):
    """Convert TimelineSegment list -> (timed_segments, footage_map) in the
    shape assemble_video() expects. Segments without a usable video file are
    skipped; timeline positions are rebuilt contiguously from the survivors."""
    timed, footage = [], {}
    cursor = 0.0
    for i, seg in enumerate(segments):
        if not seg.video_path or not os.path.exists(seg.video_path):
            continue
        dur = max(float(seg.duration), 0.5)
        timed.append({
            "text": seg.narration_text or f"Segmen {i + 1}",
            "keywords": list(seg.keywords or []),
            "start": cursor, "end": cursor + dur, "duration": dur,
            "trim_start": float(seg.start_trim or 0.0),
            "trim_end": float(seg.end_trim or 0.0),
            # Fase 3.0: per-segment voice travels with the clip.
            "audio_path": seg.audio_path or "",
            # Fase 3.4: per-word timing regenerated after edits.
            "words": list(seg.words or []),
        })
        footage[len(footage)] = {"video_path": seg.video_path}
        cursor += dur
    return timed, footage


def _load_timeline_template(name: str) -> dict:
    """Template for assembly pacing/caption resolution. Falls back to a
    neutral default when the request carries no template name."""
    if name:
        try:
            return stage1_template.load_template(name)
        except FileNotFoundError:
            pass
    return {"pacing": {"avg_shot_duration": 3.0}}


def _safe_output_name(name: str, fallback: str = "ritme_output") -> str:
    """Sanitize user-supplied output_name:
    - strip path separators & traversal (../, ..\\)
    - drop CR/LF (Content-Disposition header injection)
    - keep only word chars, dash, dot, space; clamp length
    """
    import re
    if not name:
        return fallback
    s = re.sub(r"[^\w\-. ]+", "_", str(name))
    s = s.replace("..", "_").strip(" ._-")
    return (s or fallback)[:80]


def _preview_resolution() -> tuple[int, int]:
    """Downscaled even-numbered resolution derived from OUTPUT_RESOLUTION —
    used by timeline preview so renders stay fast without manual ffmpeg."""
    from config import OUTPUT_RESOLUTION
    tw, th = OUTPUT_RESOLUTION
    scale = 360.0 / max(tw, th)
    w = int(round(tw * scale)); h = int(round(th * scale))
    w += w % 2; h += h % 2
    return (w, h)


@app.post("/api/timeline/export")
def timeline_export(req: TimelineExportRequest):
    timed, footage = _timeline_to_stage5(req.segments)
    if not timed:
        raise HTTPException(400, "No valid video segments to export")

    template = _load_timeline_template(req.template_name)

    out_path = stage5_assembly.assemble_video(
        timed, footage, req.narration_audio_path, template,
        output_name=req.output_name,
        add_music=req.add_music, music_mood=req.music_mood,
        caption_style=req.caption_style,
        transition_style=req.transition_style,
        ken_burns=req.ken_burns,
        watermark_path=req.watermark_path, watermark_pos=req.watermark_pos,
    )
    if not os.path.exists(out_path):
        raise HTTPException(500, "Render selesai tapi file output tidak ditemukan")
    return FileResponse(out_path, media_type="video/mp4", filename=f"{_safe_output_name(req.output_name)}.mp4",
                        headers={"X-Render-Path": out_path})


@app.post("/api/timeline/preview")
def timeline_preview(req: TimelineExportRequest):
    """Low-res fast preview through the same assembly pipeline as the final
    export (so finishing options are visible in preview too)."""
    timed, footage = _timeline_to_stage5(req.segments)
    if not timed:
        raise HTTPException(400, "No valid segments")

    template = _load_timeline_template(req.template_name)

    out_path = stage5_assembly.assemble_video(
        timed, footage, req.narration_audio_path, template,
        output_name=f"{req.output_name}_preview",
        add_music=req.add_music, music_mood=req.music_mood,
        caption_style=req.caption_style,
        transition_style=req.transition_style,
        ken_burns=req.ken_burns,
        resolution=_preview_resolution(),
        ffmpeg_preset="ultrafast",
        watermark_path=req.watermark_path, watermark_pos=req.watermark_pos,
    )
    if not os.path.exists(out_path):
        raise HTTPException(500, "Preview generation failed")
    return FileResponse(out_path, media_type="video/mp4")


@app.post("/api/watermark/upload")
async def watermark_upload(image: UploadFile = File(...)):
    """Fase 5.2: upload logo watermark (png/jpg/webp) -> path untuk dipakai render."""
    _validate_upload(image, {".png", ".jpg", ".jpeg", ".webp"})
    dest = UPLOADS_DIR / f"wm_{uuid.uuid4().hex}_{Path(image.filename).name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(image.file, f)
    return {"watermark_path": str(dest), "name": image.filename}

class SubtitleRegenRequest(BaseModel):
    segments: list[dict]  # [{index, text, audio_path, keywords?}]


@app.post("/api/timeline/subtitles")
def timeline_subtitles(req: SubtitleRegenRequest):
    """Export .srt from the timeline's per-segment word timestamps.
    Falls back to re-transcribing segment audio when word timing is missing."""
    try:
        timed = stage3_narration.transcribe_segment_audio([dict(s) for s in req.segments])
    except Exception:
        timed = [dict(s) for s in req.segments]
    srt = stage3_narration.segments_to_srt(timed)
    return Response(
        content=srt,
        media_type="application/x-subrip",
        headers={"Content-Disposition": 'attachment; filename="ritme_subtitles.srt"'},
    )


@app.post("/api/timeline/regenerate_subtitles")
def regenerate_subtitles(req: SubtitleRegenRequest):
    """
    Fase 3.4: after timeline edits, re-transcribe each segment's own audio and
    return freshly timed segments (per-word timestamps + cumulative windows).
    """
    try:
        timed = stage3_narration.transcribe_segment_audio([dict(s) for s in req.segments])
    except Exception as e:
        raise HTTPException(500, f"Subtitle regeneration failed: {e}")
    return {"segments": timed}


# ============================================================
# Clipper — 1 video -> N clip vertical 9:16 (Reels/TikTok)
# ============================================================
class ClipperAnalyzeRequest(BaseModel):
    video_path: str
    num_clips: int = 5


class ClipperRenderRequest(BaseModel):
    video_path: str
    clips: list[dict] = []   # [{index, start, end}]
    aspect: str = "9:16"
    output_name: str = "clipper"


CLIPPER_DIR = OUTPUT_DIR / "clipper"
CLIPPER_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/clipper/upload")
async def clipper_upload(video: UploadFile = File(...)):
    """Upload video -> simpan ke UPLOADS_DIR, return video_path buat analyze."""
    _validate_upload(video, ALLOWED_VIDEO_TYPES)
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_{Path(video.filename).name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(video.file, f)
    return {"video_path": str(dest), "name": video.filename}


class ClipperYoutubeRequest(BaseModel):
    youtube_url: str
    topic: str = ""


@app.post("/api/clipper/youtube")
async def clipper_youtube(req: ClipperYoutubeRequest):
    """Download video YouTube (async job) -> result.video_path."""
    _validate_youtube_url(req.youtube_url)
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_youtube.mp4"
    job_id = job_manager.create()

    def _run(job_id):
        job_manager.update(job_id, progress=5, message="Mengunduh video dari YouTube...")
        import yt_dlp
        ydl_opts = {"format": "best[height<=1080]", "outtmpl": str(dest), "quiet": True,
                    "noplaylist": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(req.youtube_url, download=True)
        if not os.path.exists(dest):
            raise RuntimeError("Gagal mengunduh video")
        return {"video_path": str(dest), "name": Path(dest).name}

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


@app.post("/api/clipper/analyze")
def clipper_analyze(req: ClipperAnalyzeRequest):
    """Bagi video jadi N clip pintar (scene-aware) + frame preview tiap clip."""
    from pipeline import clipper as cli
    if not os.path.exists(req.video_path):
        raise HTTPException(400, "video_path tidak ditemukan")
    try:
        clips = cli.analyze_video(req.video_path, num_clips=req.num_clips)
    except Exception as e:
        raise HTTPException(500, f"Clipper analyze gagal: {e}")

    # frame preview per clip (thumbnail kecil, diserve via /outputs/render)
    for c in clips:
        frame_rel = f"clipper_frames/{Path(req.video_path).stem}_{c['index']}.jpg"
        frame_abs = OUTPUT_DIR / frame_rel
        try:
            cli.extract_frame(req.video_path, c["start"] + min(0.8, c["duration"] / 3), str(frame_abs))
            c["thumbnail_url"] = f"/outputs/render/{frame_rel}"
        except Exception:
            c["thumbnail_url"] = ""
    return {"clips": clips, "total_duration": cli.probe_duration(req.video_path)}


@app.post("/api/clipper/render")
def clipper_render(req: ClipperRenderRequest):
    """Render clip terpilih jadi aspect target. Return per-clip URLs + zip."""
    from pipeline import clipper as cli
    import zipfile
    if not os.path.exists(req.video_path):
        raise HTTPException(400, "video_path tidak ditemukan")
    if not req.clips:
        raise HTTPException(400, "Pilih minimal 1 clip")
    safe = _safe_output_name(req.output_name)
    job_dir = CLIPPER_DIR / f"{safe}_{uuid.uuid4().hex[:8]}"
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        outs = cli.render_clips(req.video_path, req.clips, str(job_dir), aspect=req.aspect)
    except Exception as e:
        raise HTTPException(500, f"Clipper render gagal: {e}")

    files = []
    for i, p in enumerate(outs):
        rel = str(Path(p).relative_to(OUTPUT_DIR))
        files.append({
            "name": Path(p).name,
            "url": f"/outputs/render/{rel}",
            "path": str(p),
        })

    # zip semua clip
    zip_path = job_dir / f"{safe}_clips.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in outs:
            zf.write(p, arcname=Path(p).name)
    files.append({
        "name": zip_path.name,
        "url": f"/outputs/render/{str(zip_path.relative_to(OUTPUT_DIR))}",
        "path": str(zip_path),
        "is_zip": True,
    })
    return {"files": files, "job_dir": str(job_dir)}


# ============================================================
# Thumbnail generator
# ============================================================
class ThumbnailRequest(BaseModel):
    video_path: str
    title: str
    subtitle: str = ""


@app.post("/api/thumbnail/generate")
def thumbnail_generate(req: ThumbnailRequest):
    """Frame terbaik + overlay judul -> 1280x720 jpg."""
    from pipeline.thumbnail import generate_thumbnail
    if not os.path.exists(req.video_path):
        raise HTTPException(400, "video_path tidak ditemukan")
    if not req.title.strip():
        raise HTTPException(400, "title wajib diisi")
    safe = _safe_output_name(f"thumb_{req.title}").replace(" ", "_") or "thumb"
    out = OUTPUT_DIR / f"thumbnails/{safe}_{uuid.uuid4().hex[:6]}.jpg"
    try:
        generate_thumbnail(req.video_path, req.title.strip(), str(out), subtitle=req.subtitle.strip())
    except Exception as e:
        raise HTTPException(500, f"Thumbnail gagal: {e}")
    return {"url": f"/outputs/render/thumbnails/{out.name}", "path": str(out)}


# Static file serving: outputs (video/audio previews) + frontend
# ============================================================
(OUTPUT_DIR / "render").mkdir(parents=True, exist_ok=True)
app.mount("/outputs/render", StaticFiles(directory=str(OUTPUT_DIR)), name="render_output")
app.mount("/outputs/audio", StaticFiles(directory=str(CACHE_DIR / "audio")), name="audio_output")
app.mount("/outputs/footage", StaticFiles(directory=str(CACHE_DIR / "footage")), name="footage_output")

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    @app.get("/")
    def frontend_missing():
        return {
            "error": "Frontend build not found at frontend/dist. "
                     "See README.md 'Running the web app' section.",
        }


if __name__ == "__main__":
    import uvicorn
    print("RITME running at http://localhost:8000")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)






# ============================================================
