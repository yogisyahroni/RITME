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
from fastapi.responses import FileResponse
from pydantic import BaseModel

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
    import json
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
class ScriptRequest(BaseModel):
    template_name: str
    topic: str
    segments: int = 8
    style_id: str | None = None
    language: str = "id"
    custom_script: str | None = None


@app.get("/api/script/styles")
def get_script_styles():
    return stage2_script.list_script_styles()


@app.post("/api/script/generate")
def generate_script(req: ScriptRequest):
    try:
        template = stage1_template.load_template(req.template_name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    job_id = job_manager.create()

    def _run(job_id):
        if req.custom_script:
            job_manager.update(job_id, message="Menganalisis naskah buatan sendiri…", progress=30)
            sources = []
        else:
            job_manager.update(job_id, message="Riset web…", progress=15)
            sources = stage2_script.web_research(req.topic)
            
        job_manager.update(job_id, message="Menulis naskah…", progress=50)
        script = stage2_script.generate_script(
            req.topic, template, target_segments=req.segments,
            research_results=sources, style_id=req.style_id,
            language=req.language, custom_script=req.custom_script
        )
        return script

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


# ============================================================
# Stage 3 — Narration
# ============================================================
class NarrationRequest(BaseModel):
    segments: list[dict]  # [{text, keywords}, ...] from stage 2
    tts_provider: str | None = None  # "pyttsx3" | "elevenlabs" | None (use .env default)


@app.post("/api/narration/generate")
def generate_narration(req: NarrationRequest):
    job_id = job_manager.create()

    def _run(job_id):
        if req.tts_provider in ["xtts", "f5tts", "omnivoice"]:
            msg = f"Sintesis suara menggunakan AI Local ({req.tts_provider.upper()})..."
        else:
            msg = "Sintesis suara..."
        job_manager.update(job_id, message=msg, progress=20)
        audio_path = stage3_narration.synthesize_narration(req.segments, provider=req.tts_provider)
        job_manager.update(job_id, message="Transkripsi timing per kata…", progress=60)
        word_timestamps = stage3_narration.transcribe_with_timestamps(audio_path)
        timed = stage3_narration.align_keywords_to_timestamps(req.segments, word_timestamps)
        return {"audio_path": audio_path, "audio_url": f"/outputs/audio/{Path(audio_path).name}", "segments": timed}

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


@app.post("/api/footage/match")
def match_footage(req: FootageRequest):
    job_id = job_manager.create()

    def _run(job_id):
        def on_progress(done, total, seg):
            pct = int(done / total * 100)
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
    
    _validate_upload(video, ALLOWED_VIDEO_TYPES)
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


@app.post("/api/render")
def render_video(req: RenderRequest):
    try:
        template = stage1_template.load_template(req.template_name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


# ============================================================
# Project Export — Download project files for editors
# ============================================================
class ExportRequest(BaseModel):
    timed_segments: list[dict]
    footage_map: dict[str, dict]
    narration_audio_path: str = ""
    output_name: str = "ritme_project"
    formats: list[str] = ["edl", "fcpxml", "premiere_xml", "capcut_json"]


@app.post("/api/export/project")
def export_project_endpoint(req: ExportRequest):
    from fastapi.responses import FileResponse
    import os
    
    footage_map_int = {int(k): v for k, v in req.footage_map.items() if v}
    
    try:
        zip_path = project_exporter.export_project(
            req.timed_segments, footage_map_int,
            req.narration_audio_path, req.output_name, req.formats
        )
        
        if not os.path.exists(zip_path):
            raise HTTPException(500, f"Export failed: {zip_path} not found")
            
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{req.output_name}_project.zip"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, f"Export gagal: {e}")


    job_id = job_manager.create()
    footage_map_int = {int(k): v for k, v in req.footage_map.items() if v}

    def _run(job_id):
        def on_progress(pct, message):
            job_manager.update(job_id, progress=pct, message=message)

        out_path = stage5_assembly.assemble_video(
            req.timed_segments, footage_map_int, req.narration_audio_path, template,
            output_name=req.output_name, on_progress=on_progress,
        )
        return {"output_path": out_path, "output_url": f"/outputs/render/{Path(out_path).name}"}

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id}


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

class TimelineExportRequest(BaseModel):
    segments: list[TimelineSegment]
    narration_audio_path: str = ""
    output_name: str = "ritme_timeline"


@app.post("/api/timeline/export")
def timeline_export(req: TimelineExportRequest):
    import subprocess
    import os
    from pathlib import Path
    
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_path = str(output_dir / f"{req.output_name}.mp4")
    
    # Build ffmpeg concat filter for all trimmed clips
    filter_parts = []
    inputs = []
    audio_path = None
    total_duration = 0
    
    for i, seg in enumerate(req.segments):
        if not seg.video_path or not os.path.exists(seg.video_path):
            continue
        
        # Calculate trim: seek to start_trim, take (duration + end_trim) seconds
        trim_start = seg.start_trim
        clip_duration = seg.duration
        
        # Input label
        input_label = f"v{i}"
        inputs.append(["-i", seg.video_path])
        
        # Trim filter
        filter_parts.append(
            f"[{i}:v]trim=start={trim_start}:duration={clip_duration},setpts=PTS-STARTPTS[{input_label}_v]"
        )
        total_duration += clip_duration
    
    if not filter_parts:
        raise HTTPException(400, "No valid video segments to export")
    
    # Audio track from narration
    if req.narration_audio_path and os.path.exists(req.narration_audio_path):
        audio_path = req.narration_audio_path
        inputs.append(["-i", audio_path])
        # Concatenate video plus audio
        concat_v = "".join([f"[{i}_v]" for i in range(len(req.segments))])
        filter_complex = ";".join(filter_parts) + f";{concat_v}concat=n={len(req.segments)}:v=1:a=0[outv]"
        
        cmd = ["ffmpeg", "-y"]
        for inp in inputs:
            cmd.extend(inp)
        cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]"])
        
        # Map audio if we have narration
        cmd.extend(["-map", f"{len(req.segments)}:a:0", "-c:v", "libx264", "-preset", "medium", 
                    "-crf", "22", "-c:a", "aac", "-shortest", output_path])
    else:
        # Video only
        concat_v = "".join([f"[{i}_v]" for i in range(len(req.segments))])
        filter_complex = ";".join(filter_parts) + f";{concat_v}concat=n={len(req.segments)}:v=1:a=0[outv]"
        
        cmd = ["ffmpeg", "-y"]
        for inp in inputs:
            cmd.extend(inp)
        cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]", "-c:v", "libx264", "-preset", "medium",
                    "-crf", "22", output_path])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "Export timeout (>5 min)")
    except Exception as e:
        raise HTTPException(500, f"Export failed: {e}")
    
    if os.path.exists(output_path):
        from fastapi.responses import FileResponse
        return FileResponse(output_path, media_type="video/mp4", filename=f"{req.output_name}.mp4")
    else:
        raise HTTPException(500, "Output file not found")


@app.post("/api/timeline/preview")
def timeline_preview(req: TimelineExportRequest):
    """Generate a low-res preview of the timeline quickly."""
    import subprocess
    import os
    from pathlib import Path
    
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    output_path = str(output_dir / f"{req.output_name}_preview.mp4")
    
    filter_parts = []
    inputs = []
    
    for i, seg in enumerate(req.segments):
        if not seg.video_path or not os.path.exists(seg.video_path):
            continue
        trim_start = seg.start_trim
        clip_duration = seg.duration
        inputs.append(["-i", seg.video_path])
        filter_parts.append(
            f"[{i}:v]trim=start={trim_start}:duration={clip_duration},setpts=PTS-STARTPTS,scale=640:-2[v{i}]"
        )
    
    if not filter_parts:
        raise HTTPException(400, "No valid segments")
    
    concat_v = "".join([f"[v{i}]" for i in range(len(req.segments))])
    filter_complex = ";".join(filter_parts) + f";{concat_v}concat=n={len(req.segments)}:v=1:a=0,format=yuv420p[out]"
    
    cmd = ["ffmpeg", "-y"]
    for inp in inputs:
        cmd.extend(inp)
    cmd.extend(["-filter_complex", filter_complex, "-map", "[out]", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "28", "-movflags", "+faststart", output_path])
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg preview error: {result.stderr[:300]}")
    except Exception as e:
        raise HTTPException(500, f"Preview failed: {e}")
    
    if os.path.exists(output_path):
        from fastapi.responses import FileResponse
        return FileResponse(output_path, media_type="video/mp4")
    else:
        raise HTTPException(500, "Preview generation failed")

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
