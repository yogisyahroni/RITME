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
import re
import shutil
import subprocess
import sys
import uuid
import threading
from datetime import datetime
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

from config import TEMPLATES_DIR, OUTPUT_DIR, CACHE_DIR, MUSIC_DIR
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
    audio: UploadFile | None = File(None),
    audio_files: list[UploadFile] = File(default=[]),
    segments: str = Form(...),
    seg_indices: str = Form("[]")
):
    """Upload suara — 2 mode:
    - `audio` (1 file, full track): Whisper auto-sync ke segmen (timing per kata).
    - `audio_files` (N file, per segmen): tiap file jadi audio segmen-nya,
      timing = durasi file kumulatif (tanpa Whisper, cepat).
      `seg_indices` = JSON array index segmen untuk tiap file (default: berurutan).
    """
    import json
    segs = json.loads(segments)
    idx_map = json.loads(seg_indices) if seg_indices.strip() else []
    job_id = job_manager.create()

    # Mode B — per-segment audio files
    if audio_files:
        # Baca bytes DI HANDLER (UploadFile handle mati setelah return —
        # background thread cuma dapat bytes, bukan file handle).
        uploads = []
        for f in audio_files:
            _validate_upload(f, ALLOWED_AUDIO_TYPES)
            uploads.append((f.filename or f"seg_{len(uploads)}.wav", f.file.read()))

        def _run_per_segment(job_id):
            audio_dir = CACHE_DIR / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            paths, durs, seg_idx = [], [], []
            total = len(uploads)
            for i, (fname, data) in enumerate(uploads):
                ext = Path(fname).suffix.lower() or ".wav"
                dest = audio_dir / f"seg_{i}_{uuid.uuid4().hex}{ext}"
                with open(dest, "wb") as fh:
                    fh.write(data)
                d = stage3_narration.audio_duration(str(dest))
                paths.append(str(dest))
                durs.append(d)
                seg_idx.append(idx_map[i] if i < len(idx_map) else i)
                job_manager.update(job_id, progress=int((i + 1) / total * 60),
                                   message=f"Upload audio segmen {i + 1}/{total}…")
            # cumulative windows per segment (hanya segmen ber-audio yang jalan)
            timed, t = [], 0.0
            for i, seg in enumerate(segs):
                if i in seg_idx:
                    j = seg_idx.index(i)
                    d = durs[j]
                    timed.append({**seg, "start": t, "end": t + d,
                                  "audio_path": paths[j]})
                    t += d
                else:
                    timed.append({**seg, "start": t, "end": t,
                                  "audio_path": None})
            # full track gabungan (buat preview & music ducking)
            if len(paths) == 1:
                audio_path = paths[0]
            else:
                try:
                    audio_path = stage3_narration.concat_audio_files(
                        paths, str(audio_dir / "narration_upload.wav"))
                except Exception as e:
                    print(f"[server] per-segment concat failed ({e})")
                    audio_path = paths[0] if paths else ""
            return {
                "audio_path": audio_path,
                "audio_url": f"/outputs/audio/{Path(audio_path).name}" if audio_path else "",
                "segments": timed,
                "segment_audio_paths": paths,
                "segment_audio_durations": durs,
            }

        job_manager.run_async(job_id, _run_per_segment)
        return {"job_id": job_id}

    # Mode A — single full audio, Whisper auto-sync
    if audio is None:
        raise HTTPException(400, "Butuh `audio` (1 file penuh) atau `audio_files` (per segmen).")

    ext = Path(audio.filename).suffix
    audio_dir = CACHE_DIR / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    _validate_upload(audio, ALLOWED_AUDIO_TYPES)
    dest = audio_dir / f"upload_{uuid.uuid4().hex}{ext}"

    with open(dest, "wb") as f:
        shutil.copyfileobj(audio.file, f)

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
# Footage dari Skrip Lengkap — skrip punya link YouTube per bagian,
# otomatis download tiap link lalu extract clips (1 job, sequential).
# ============================================================
class ScriptFootageRequest(BaseModel):
    script_text: str


_YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/|embed/)|youtu\.be/)[^\s\"'<>]+"
)


def _find_yt_urls(text: str) -> list[str]:
    """Ambil URL YouTube valid dari teks (SSRF-guarded, skip yang invalid)."""
    out = []
    for u in _YOUTUBE_URL_RE.findall(text):
        try:
            _validate_youtube_url(u)
            out.append(u)
        except Exception:
            continue  # URL gak valid / bukan youtube — skip diam-diam
    return out


def _parse_script_segments(script_text):
    """Split skrip jadi segmen per heading markdown/ALL-CAPS, kumpulin semua URL
    YouTube, dan map URL dari blok referensi (tanpa heading) ke segmen relevan
    via keyword scoring judul URL vs judul segmen."""
    lines = script_text.splitlines()
    REF_KEYWORDS = ("referensi", "riset", "source", "sumber", "link", "daftar", "bibliografi", "reference")
    # 1) split per heading (## / ### / baris ALL-CAPS panjang)
    segments = []  # {title, lines: [...]}
    cur = None
    for ln in lines:
        stripped = ln.strip()
        is_heading = bool(re.match(r"^#{1,4}\s+", stripped)) or (
            len(stripped) >= 6 and stripped.isupper() and stripped.isalpha() and not re.match(r"^\[", stripped)
        )
        if is_heading:
            cur = {"title": re.sub(r"^#{1,4}\s+", "", stripped), "lines": [], "is_ref": False}
            segments.append(cur)
        else:
            if cur is None:
                cur = {"title": "", "lines": [], "is_ref": False}
                segments.append(cur)
            cur["lines"].append(ln)

    # tandai segmen referensi (judul mengandung kata kunci) — URL di sini jadi POOL
    KONTEN_MARK = ("bab ", "intro", "outro", "episode", "bagian ", "pendahuluan", "kesimpulan", "disclaimer")
    for seg in segments:
        t = seg["title"].lower()
        seg["is_ref"] = any(k in t for k in REF_KEYWORDS)
    # propagasi: sub-heading di bawah segmen referensi (mis. kategori link YT)
    # ikut jadi referensi, sampai ketemu heading konten (BAB/INTRO/OUTRO/dll)
    for i in range(1, len(segments)):
        if segments[i - 1]["is_ref"]:
            t = segments[i]["title"].lower()
            if not any(k in t for k in KONTEN_MARK):
                segments[i]["is_ref"] = True

    # 2) kumpulin semua URL + judul (untuk keyword matching)
    all_urls = []  # {url, title}
    for seg in segments:
        for ln in seg["lines"]:
            for u in _find_yt_urls(ln):
                all_urls.append({"url": u, "title": ""})
    # ambil judul link dari baris markdown "[Judul](url)" atau baris teks sebelumnya
    for seg in segments:
        prev = ""
        for ln in seg["lines"]:
            for u in _find_yt_urls(ln):
                m = re.match(r"^\[([^\]]+)\]\(([^)]+)\)", ln.strip())
                title = m.group(1) if m else prev
                for a in all_urls:
                    if a["url"] == u and not a["title"]:
                        a["title"] = title
            prev = re.sub(r"^[-*•]\s*", "", ln.strip())[:120]

    def _score(title, seg):
        # skor = overlap kata judul URL vs JUDUL bab (bobot 5x) + ISI bab (1x)
        words = set(re.findall(r"[a-z0-9]+", title.lower()))
        title_words = set(re.findall(r"[a-z0-9]+", seg["title"].lower()))
        body = " ".join(seg["lines"])[:1500].lower()
        body_words = set(re.findall(r"[a-z0-9]+", body))
        return len(words & title_words) * 5 + len(words & body_words)

    # 3) assign URL: segmen NON-referensi yang punya URL inline → pakai itu;
    #    URL di segmen referensi → pool, di-assign ke segmen bab via keyword.
    inline_used = set()
    for seg in segments:
        seg["urls"] = []
        for ln in seg["lines"]:
            for u in _find_yt_urls(ln):
                if seg["is_ref"]:
                    continue  # referensi: URL masuk pool
                seg["urls"].append(u)
                inline_used.add(u)

    pool = [a for a in all_urls if a["url"] not in inline_used]
    # target = segmen bab non-referensi
    targets = [s for s in segments if not s["is_ref"]]
    # assign per URL: skor tertinggi; kalau semua skor 0 → bagi rata round-robin
    scored = [(a, max(targets, key=lambda s: _score(a["title"], s))) for a in pool]
    zero = [a for a in pool if all(_score(a["title"], s) == 0 for s in targets)]
    for a, best in scored:
        if _score(a["title"], best) > 0:
            best["urls"].append(a["url"])
    # round-robin sisa yang skor 0, urutkan target paling panjang dulu
    if zero:
        order = sorted([s for s in targets if s["title"]], key=lambda s: -len("\n".join(s["lines"])))
        if not order:
            order = [s for s in targets if s["title"]] or targets
        for i, a in enumerate(zero):
            order[i % len(order)]["urls"].append(a["url"])

    return segments


@app.post("/api/footage/from_script")
def extract_footage_from_script(req: ScriptFootageRequest):
    import yt_dlp

    segments = _parse_script_segments(req.script_text)
    entries = []
    for i, seg in enumerate(segments):
        if seg["urls"]:
            entries.append({
                "index": i,
                "text": (seg["title"] + "\n" + "\n".join(seg["lines"]))[:200],
                "url": seg["urls"][0],
                "extra_urls": seg["urls"][1:],
            })
    if not entries:
        raise HTTPException(400, "Tidak ada link YouTube valid ditemukan di skrip. Pastikan skrip punya link youtube.com atau youtu.be (bisa di bagian referensi).")

    job_id = job_manager.create()
    output_dir = "outputs/extracted_footage"

    # flatten: semua URL (utama + extra) jadi satu daftar job dengan segment_index
    jobs = []
    for e in entries:
        jobs.append({"segment_index": e["index"], "url": e["url"], "text": e["text"]})
        for extra in e["extra_urls"]:
            jobs.append({"segment_index": e["index"], "url": extra, "text": e["text"]})

    def _run(job_id):
        results, failed = [], []
        # aggregate per segment_index
        agg = {}
        total = len(jobs)
        for n, job in enumerate(jobs):
            job_manager.update(job_id, progress=int(n / total * 90),
                               message=f"[{n + 1}/{total}] Mengunduh {job['url'][:70]}…")
            try:
                dest = UPLOADS_DIR / f"{uuid.uuid4().hex}_script_yt.mp4"
                ydl_opts = {"format": "best[height<=1080]", "outtmpl": str(dest), "quiet": True}
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(job["url"], download=True)
                topic = " ".join((info or {}).get("title", "").split()[:4])
                files = footage_extractor.extract_clips(
                    str(dest), output_dir=output_dir, threshold=27.0,
                    min_duration_sec=2.0, base_name=None, topic=topic)
                agg.setdefault(job["segment_index"], {"count": 0, "files": []})
                agg[job["segment_index"]]["count"] += len(files)
                agg[job["segment_index"]]["files"].extend(files)
            except Exception as ex:
                print(f"[from_script] job {n} gagal: {ex}")
                failed.append({"segment_index": job["segment_index"], "url": job["url"], "error": str(ex)})
        for e in entries:
            r = agg.get(e["index"], {"count": 0, "files": []})
            results.append({"segment_index": e["index"], "url": e["url"],
                            "count": r["count"], "files": r["files"]})
        return {
            "segments": results, "total": len(jobs),
            "ok": sum(1 for r in results if r["count"]),
            "failed": failed, "output_dir": output_dir,
        }

    job_manager.run_async(job_id, _run)
    return {"job_id": job_id, "segments": entries, "found": len(entries), "total_urls": len(jobs)}


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
    add_music: bool = False
    music_mood: str | None = None
    bgm_volume: float = 1.0            # P4
    bgm_fade_in: float = 2.0           # P4
    bgm_fade_out: float = 2.0          # P4
    bgm_custom_path: str | None = None # P4


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
            add_music=req.add_music, music_mood=req.music_mood,
            bgm_volume=req.bgm_volume, bgm_fade_in=req.bgm_fade_in,
            bgm_fade_out=req.bgm_fade_out,
            bgm_custom_path=req.bgm_custom_path,
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
    bgm_volume: float = 1.0            # P4
    bgm_fade_in: float = 2.0           # P4
    bgm_fade_out: float = 2.0          # P4
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
    filter: str = "original"  # P1.3: color-grade preset per clip
    speed: float = 1.0          # P2.1: play-speed multiplier (0.25x–4x)

class TitleOverlay(BaseModel):
    """Teks/title manual di atas footage (P1.1) — 9 posisi, pill bg opsional."""
    segment_index: int = 0
    text: str = ""
    start_offset: float = 0.0   # detik relatif ke awal segmen
    duration: float = 3.0
    position: str = "top-center"  # 9 preset: top-left..bottom-right
    font_size: int = 48
    color: str = "#FFFFFF"
    background_pill: bool = False


class StickerOverlay(BaseModel):
    """Sticker/gambar overlay manual (P1.4) — posisi relatif 0-1, scale, rotasi.
    P3.1: keyframes = list {t, x?, y?, scale?, rotation?} -> animasi linear."""
    segment_index: int = 0
    image_path: str = ""          # path file hasil upload sticker
    x: float = 0.5                # 0-1 relatif lebar frame (0.5 = tengah)
    y: float = 0.5
    scale: float = 1.0            # 1.0 = 15% lebar frame
    rotation: float = 0.0         # derajat
    start_offset: float = 0.0     # detik relatif ke awal segmen
    duration: float = 0.0         # 0 = sisa durasi segmen
    keyframes: list[dict] = []    # P3.1: [{t, x?, y?, scale?, rotation?}]


class TimelineExportRequest(BaseModel):
    segments: list[TimelineSegment]
    narration_audio_path: str = ""
    output_name: str = "ritme_timeline"
    template_name: str = ""           # optional; fallback pacing when empty
    # --- Finishing options (Fase 1C.1): manual user choices, defaults OFF ---
    add_music: bool = False
    music_mood: str | None = None
    bgm_volume: float = 1.0            # P4: 0.0–2.0 multiplier post-ducking
    bgm_fade_in: float = 2.0           # P4: fade in seconds
    bgm_fade_out: float = 2.0          # P4: fade out seconds
    bgm_custom_path: str | None = None # P4: uploaded custom BGM path
    caption_style: str = "minimal-white-center"
    transition_style: str = "hard_cut"   # "hard_cut" | "crossfade"
    ken_burns: bool = False
    # --- P2.2: multi-aspect export ---
    aspect_ratio: str = "9:16"           # "9:16" | "16:9" | "1:1" | "original"
    # --- Watermark (Fase 5.2): logo overlay ---
    watermark_path: str | None = None
    watermark_pos: str = "bottom-right"
    # --- Text/title overlay manual (P1.1) ---
    title_overlays: list[TitleOverlay] = []
    # --- Sticker/gambar overlay manual (P1.4) ---
    sticker_overlays: list[StickerOverlay] = []


class BatchItem(BaseModel):
    name: str = "batch_item"
    segments: list[TimelineSegment]
    narration_audio_path: str = ""
    template_name: str = ""
    add_music: bool = False
    music_mood: str | None = None
    bgm_volume: float = 1.0            # P4
    bgm_fade_in: float = 2.0           # P4
    bgm_fade_out: float = 2.0          # P4
    bgm_custom_path: str | None = None # P4
    caption_style: str = "minimal-white-center"
    transition_style: str = "hard_cut"
    ken_burns: bool = False
    aspect_ratio: str = "9:16"
    watermark_path: str | None = None
    watermark_pos: str = "bottom-right"
    title_overlays: list[TitleOverlay] = []
    sticker_overlays: list[StickerOverlay] = []


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
                    bgm_volume=item.bgm_volume, bgm_fade_in=item.bgm_fade_in,
                    bgm_fade_out=item.bgm_fade_out, bgm_custom_path=item.bgm_custom_path,
                    caption_style=item.caption_style,
                    transition_style=item.transition_style,
                    ken_burns=item.ken_burns,
                    resolution=_aspect_resolution(item.aspect_ratio),
                    watermark_path=item.watermark_path, watermark_pos=item.watermark_pos,
                    title_overlays=[o.model_dump() for o in item.title_overlays],
                    sticker_overlays=[o.model_dump() for o in item.sticker_overlays],
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
        # P2.1: speed shortens/lengthens the timeline window; footage window
        # stays `dur * speed` (MultiplySpeed restores final duration).
        speed = max(0.25, min(float(getattr(seg, "speed", 1.0) or 1.0), 4.0))
        final_dur = dur / speed
        timed.append({
            "text": seg.narration_text or f"Segmen {i + 1}",
            "keywords": list(seg.keywords or []),
            "start": cursor, "end": cursor + final_dur, "duration": final_dur,
            "speed": speed,
            "trim_start": float(seg.start_trim or 0.0),
            "trim_end": float(seg.end_trim or 0.0),
            # Fase 3.0: per-segment voice travels with the clip.
            "audio_path": seg.audio_path or "",
            # Fase 3.4: per-word timing regenerated after edits.
            "words": list(seg.words or []),
        })
        footage[len(footage)] = {"video_path": seg.video_path}
        cursor += final_dur
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


ASPECT_RESOLUTIONS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


def _aspect_resolution(aspect: str, scale: float = 1.0) -> tuple[int, int]:
    """P2.2: resolve '9:16' | '16:9' | '1:1' | 'original' -> (w, h).
    'original' fallback ke OUTPUT_RESOLUTION config. Optional downscale
    (preview) dengan hasil selalu even (ffmpeg butuh)."""
    from config import OUTPUT_RESOLUTION
    if aspect == "original":
        w, h = OUTPUT_RESOLUTION
    else:
        w, h = ASPECT_RESOLUTIONS.get(aspect, ASPECT_RESOLUTIONS["9:16"])
    if scale != 1.0:
        w = int(round(w * scale)); h = int(round(h * scale))
        w += w % 2; h += h % 2
    return (w, h)


def _preview_resolution(aspect: str = "9:16") -> tuple[int, int]:
    """Downscaled even-numbered resolution derived from target aspect —
    used by timeline preview so renders stay fast without manual ffmpeg."""
    w, h = _aspect_resolution(aspect)
    scale = 360.0 / max(w, h)
    w = int(round(w * scale)); h = int(round(h * scale))
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
        bgm_volume=req.bgm_volume, bgm_fade_in=req.bgm_fade_in,
        bgm_fade_out=req.bgm_fade_out, bgm_custom_path=req.bgm_custom_path,
        caption_style=req.caption_style,
        transition_style=req.transition_style,
        ken_burns=req.ken_burns,
        resolution=_aspect_resolution(req.aspect_ratio),
        watermark_path=req.watermark_path, watermark_pos=req.watermark_pos,
        title_overlays=[o.model_dump() for o in req.title_overlays],
        sticker_overlays=[o.model_dump() for o in req.sticker_overlays],
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
        bgm_volume=req.bgm_volume, bgm_fade_in=req.bgm_fade_in,
        bgm_fade_out=req.bgm_fade_out,
        caption_style=req.caption_style,
        transition_style=req.transition_style,
        ken_burns=req.ken_burns,
        resolution=_preview_resolution(req.aspect_ratio),
        ffmpeg_preset="ultrafast",
        watermark_path=req.watermark_path, watermark_pos=req.watermark_pos,
        title_overlays=[o.model_dump() for o in req.title_overlays],
        sticker_overlays=[o.model_dump() for o in req.sticker_overlays],
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


@app.post("/api/sticker/upload")
async def sticker_upload(image: UploadFile = File(...)):
    """P1.4: upload gambar sticker (png/jpg/webp) -> path untuk dipakai render."""
    _validate_upload(image, {".png", ".jpg", ".jpeg", ".webp"})
    dest = UPLOADS_DIR / f"st_{uuid.uuid4().hex}_{Path(image.filename).name}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(image.file, f)
    return {"sticker_path": str(dest), "name": image.filename}


# ============================================================
# P4: Background Music — upload, list, preview
# ============================================================
BGM_UPLOAD_DIR = UPLOADS_DIR / "bgm"

@app.post("/api/bgm/upload")
async def bgm_upload(audio: UploadFile = File(...)):
    """P4: upload custom BGM (mp3/wav/m4a/ogg/flac) -> path untuk dipakai render."""
    _validate_upload(audio, ALLOWED_AUDIO_TYPES)
    BGM_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(audio.filename).suffix.lower() or ".mp3"
    dest = BGM_UPLOAD_DIR / f"bgm_{uuid.uuid4().hex[:8]}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(audio.file, f)
    return {"bgm_path": str(dest), "name": audio.filename}


@app.get("/api/bgm/list")
def bgm_list():
    """P4: list semua file BGM yang tersedia (music/ + cache/uploads/bgm/)."""
    tracks = []
    for d in [MUSIC_DIR, BGM_UPLOAD_DIR]:
        if not d.is_dir():
            continue
        for pat in ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac"):
            for f in sorted(d.glob(pat)):
                tracks.append({"name": f.name, "path": str(f),
                               "source": "library" if d == MUSIC_DIR else "custom"})
    return {"tracks": tracks}


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
    captions: bool = False
    caption_style: str = "bold-white-bottom"


class ClipperPreviewCapsRequest(BaseModel):
    video_path: str
    start: float = 0
    end: float = 10


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
    return {
        "clips": clips,
        "total_duration": cli.probe_duration(req.video_path),
        "video_url": f"/uploads/{Path(req.video_path).name}",
        "safe_area": cli.detect_black_bars(req.video_path),
    }


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

    # AutoCaption: transcribe tiap clip + burn karaoke captions
    if req.captions:
        try:
            for i, p in enumerate(outs):
                words = cli.transcribe_clip_words(p, model_size="base")
                out_c = str(Path(p).with_name(Path(p).stem + "_cap.mp4"))
                cli.burn_captions(p, words, req.caption_style, out_c)
                if Path(out_c).exists():
                    Path(p).unlink(missing_ok=True)
                    outs[i] = out_c
        except Exception as e:
            print(f"[clipper] autocaption gagal (dipakai clip polos): {e}")

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


@app.post("/api/clipper/preview_captions")
def clipper_preview_captions(req: ClipperPreviewCapsRequest):
    """Transcribe segmen video sumber (tanpa render) -> word timestamps relatif.

    Buat live caption overlay di preview player.
    """
    from pipeline import clipper as cli
    if not os.path.exists(req.video_path):
        raise HTTPException(400, "video_path tidak ditemukan")
    try:
        words = cli.transcribe_segment_words(req.video_path, req.start, req.end)
        return {"words": words, "start": req.start}
    except Exception as e:
        raise HTTPException(500, f"Transcribe preview gagal: {e}")


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


# ============================================================
# Project Library — P0.1 (CapCut Pro roadmap)
# File-based storage: projects/<id>/project.json + thumb.jpg
# ============================================================
PROJECTS_DIR = Path(__file__).parent / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


class ProjectSaveRequest(BaseModel):
    name: str
    segments: list[TimelineSegment]
    finishing: dict = {}
    narration_meta: dict = {}
    template_name: str = ""
    title_overlays: list[TitleOverlay] = []
    sticker_overlays: list[StickerOverlay] = []


def _analytics(segments: list[TimelineSegment]) -> dict:
    """P3.2: statistik lengkap project — speed-aware duration, WPM per segmen,
    shot pacing, distribusi kata. Dipakai meta kartu + endpoint analytics."""
    if not segments:
        return {"segments_count": 0, "duration": 0, "words": 0, "wpm": 0,
                "avg_shot_duration": 0, "max_duration": 0, "min_duration": 0,
                "speed_variants": [], "wordiest_index": -1}
    per_seg = []
    for s in segments:
        base = max(float(s.duration or 0), 0)
        speed = max(0.25, min(float(getattr(s, "speed", 1.0) or 1.0), 4.0))
        final = base / speed
        w = len((s.narration_text or "").split())
        per_seg.append({"index": s.index, "duration": round(final, 2), "words": w,
                        "wpm": round(w / (final / 60), 1) if final > 0 else 0,
                        "speed": speed, "has_footage": bool(s.video_path)})
    total = sum(p["duration"] for p in per_seg)
    words = sum(p["words"] for p in per_seg)
    wpm = round(words / (total / 60), 1) if total > 0 else 0
    durs = [p["duration"] for p in per_seg]
    wordiest = max(per_seg, key=lambda p: p["words"], default=None)
    return {
        "segments_count": len(segments),
        "scene_count": sum(1 for p in per_seg if p["has_footage"]),
        "duration": round(total, 2),
        "words": words,
        "wpm": wpm,
        "avg_shot_duration": round(total / len(per_seg), 2) if per_seg else 0,
        "max_duration": max(durs), "min_duration": min(durs),
        "speed_variants": sorted({p["speed"] for p in per_seg}),
        "wordiest_index": wordiest["index"] if wordiest else -1,
        "per_segment": per_seg,
    }


def _project_meta(pid: str, name: str, segments: list[TimelineSegment],
                  finishing: dict, narration_meta: dict, template_name: str) -> dict:
    """Metadata kartu project — dipakai buat grid list (P3.2 analytics included)."""
    a = _analytics(segments)
    return {
        "id": pid,
        "name": name,
        "segments_count": a["segments_count"],
        "scene_count": a["scene_count"],
        "duration": a["duration"],
        "wpm": a["wpm"],
        "template_name": template_name or (narration_meta or {}).get("template_name", ""),
        "thumb_url": f"/projects/{pid}/thumb.jpg",
    }


def _project_thumbnail(segments: list[TimelineSegment], out_path: str) -> bool:
    """Frame tengah footage pertama yang valid -> jpg kecil buat grid."""
    vid = next((s.video_path for s in segments
                if s.video_path and os.path.exists(s.video_path)), None)
    if not vid:
        return False
    try:
        from pipeline.clipper import probe_duration
        total = probe_duration(vid)
        if total <= 0:
            total = 10.0
        at = min(max(total / 2, 0.1), max(total - 0.3, 0.1))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{at:.3f}", "-i", vid,
            "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", out_path,
        ], capture_output=True, text=True, timeout=120)
        return Path(out_path).exists() and Path(out_path).stat().st_size > 0
    except Exception:
        return False


@app.post("/api/projects")
def project_save(req: ProjectSaveRequest):
    """Simpan project baru (id unik). Thumbnail dari footage segmen pertama."""
    if not req.name.strip():
        raise HTTPException(400, "Nama project wajib diisi")
    if not req.segments:
        raise HTTPException(400, "Project kosong — tidak ada segmen")
    safe = _safe_output_name(req.name) or "project"
    pid = uuid.uuid4().hex[:12]
    folder = PROJECTS_DIR / pid
    folder.mkdir(parents=True, exist_ok=True)
    meta = _project_meta(pid, safe, req.segments, req.finishing, req.narration_meta, req.template_name)
    data = {
        **meta,
        "segments": [s.model_dump() for s in req.segments],
        "finishing": req.finishing,
        "narration_meta": req.narration_meta,
        "title_overlays": [o.model_dump() for o in req.title_overlays],
        "sticker_overlays": [o.model_dump() for o in req.sticker_overlays],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    (folder / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    has_thumb = _project_thumbnail(req.segments, str(folder / "thumb.jpg"))
    if not has_thumb:
        meta["thumb_url"] = None
    return meta


@app.get("/api/projects")
def project_list():
    """List semua project (metadata saja, urut terbaru)."""
    items = []
    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        pf = d / "project.json"
        if not d.is_dir() or not pf.exists():
            continue
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
            items.append({k: data.get(k) for k in (
                "id", "name", "segments_count", "scene_count", "duration",
                "wpm", "template_name", "thumb_url", "saved_at")})
        except Exception:
            continue
    return {"projects": items}


@app.get("/api/projects/{pid}")
def project_get(pid: str):
    pf = PROJECTS_DIR / pid / "project.json"
    if not pf.exists():
        raise HTTPException(404, "Project tidak ditemukan")
    try:
        return json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "File project corrupt")


@app.get("/api/projects/{pid}/analytics")
def project_analytics(pid: str):
    """P3.2: statistik project lengkap (speed-aware) dari project tersimpan."""
    pf = PROJECTS_DIR / pid / "project.json"
    if not pf.exists():
        raise HTTPException(404, "Project tidak ditemukan")
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(500, "File project corrupt")
    segs = [TimelineSegment(**s) for s in data.get("segments", [])]
    a = _analytics(segs)
    a["name"] = data.get("name", "")
    a["saved_at"] = data.get("saved_at", "")
    a["template_name"] = data.get("template_name", "") or (data.get("finishing") or {}).get("template_name", "")
    return a


@app.put("/api/projects/{pid}")
def project_update(pid: str, req: ProjectSaveRequest):
    """Update project existing — id & created_at dipertahankan."""
    pf = PROJECTS_DIR / pid / "project.json"
    if not pf.exists():
        raise HTTPException(404, "Project tidak ditemukan")
    safe = _safe_output_name(req.name) or "project"
    old = json.loads(pf.read_text(encoding="utf-8"))
    meta = _project_meta(pid, safe, req.segments, req.finishing, req.narration_meta, req.template_name)
    data = {
        **meta,
        "segments": [s.model_dump() for s in req.segments],
        "finishing": req.finishing,
        "narration_meta": req.narration_meta,
        "title_overlays": [o.model_dump() for o in req.title_overlays],
        "sticker_overlays": [o.model_dump() for o in req.sticker_overlays],
        "created_at": old.get("created_at") or old.get("saved_at"),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    pf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    has_thumb = _project_thumbnail(req.segments, str(PROJECTS_DIR / pid / "thumb.jpg"))
    if not has_thumb:
        meta["thumb_url"] = None
    return meta


@app.delete("/api/projects/{pid}")
def project_delete(pid: str):
    folder = PROJECTS_DIR / pid
    if not folder.is_dir():
        raise HTTPException(404, "Project tidak ditemukan")
    shutil.rmtree(folder)
    return {"ok": True}


# Static file serving: outputs (video/audio previews) + uploads (clipper source) + frontend
# ============================================================
app.mount("/projects", StaticFiles(directory=str(PROJECTS_DIR)), name="projects_library")
app.mount("/outputs/render", StaticFiles(directory=str(OUTPUT_DIR)), name="render_output")
app.mount("/outputs/audio", StaticFiles(directory=str(CACHE_DIR / "audio")), name="audio_output")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
app.mount("/outputs/footage", StaticFiles(directory=str(CACHE_DIR / "footage")), name="footage_output")

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    @app.middleware("http")
    async def no_cache_bundle(request, call_next):
        """Bundle/HTML gak di-cache browser — mencegah UI basi setelah rebuild."""
        resp = await call_next(request)
        if request.url.path in ("/", "/index.html", "/bundle.js", "/tailwind.css"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
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
