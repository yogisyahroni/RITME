"""
Central configuration for the video pipeline.
Loads API keys and settings from a .env file (see .env.example).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / "cache"
FOOTAGE_CACHE_DIR = CACHE_DIR / "footage"
AUDIO_CACHE_DIR = CACHE_DIR / "audio"

for d in (TEMPLATES_DIR, OUTPUT_DIR, CACHE_DIR, FOOTAGE_CACHE_DIR, AUDIO_CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- LLM provider for research + script generation ---
# One of: "anthropic", "openai", "gemini"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# --- TTS provider ---
# One of: "pyttsx3" (free, offline, robotic), "elevenlabs" (paid, high quality)
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "pyttsx3")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# --- Whisper (transcription) ---
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")  # tiny/base/small/medium/large-v3

# --- Legal footage sources only ---
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
# YouTube Data API key, used ONLY to filter search results by videoLicense=creativeCommon
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

# --- Video output settings ---
OUTPUT_ASPECT_RATIO = os.getenv("OUTPUT_ASPECT_RATIO", "9:16")  # 9:16, 16:9, 1:1
OUTPUT_RESOLUTION = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}.get(OUTPUT_ASPECT_RATIO, (1080, 1920))

NARRATION_LANGUAGE = os.getenv("NARRATION_LANGUAGE", "id")

# --- CLIP model for footage <-> keyword matching ---
CLIP_MODEL_NAME = os.getenv("CLIP_MODEL_NAME", "ViT-B-32")
CLIP_PRETRAINED = os.getenv("CLIP_PRETRAINED", "openai")

# --- Stage 4 matching quality (Fase 0 roadmap) ---
# Hard floor: auto-pick rejects matches scoring below this (weak CLIP match).
MIN_ACCEPTABLE_CLIP_SCORE = float(os.getenv("MIN_ACCEPTABLE_CLIP_SCORE", "0.20"))
# Number of frames sampled per candidate clip for multi-frame CLIP scoring.
# Frames are sampled at 20% / 50% / 80% of the clip duration and averaged.
CLIP_SAMPLE_FRAMES = int(os.getenv("CLIP_SAMPLE_FRAMES", "3"))
# Local footage is trusted to skip internet search when a candidate scores at
# or above this threshold (kept distinct from MIN_ACCEPTABLE_CLIP_SCORE so a
# local clip can still be *picked* below the floor if nothing better exists).
LOCAL_GOOD_SCORE = float(os.getenv("LOCAL_GOOD_SCORE", "0.21"))
# Fair-use windows (seconds) downloaded per YouTube candidate in Stage 4.
YOUTUBE_FAIRUSE_WINDOWS = [
    (10, 20),   # window 1
    (30, 40),   # window 2
]

# --- Stage 4 parallelism (Fase 2 roadmap) ---
STAGE4_SEARCH_WORKERS = int(os.getenv("STAGE4_SEARCH_WORKERS", "5"))   # 1 per source API
STAGE4_DOWNLOAD_WORKERS = int(os.getenv("STAGE4_DOWNLOAD_WORKERS", "4"))
STAGE4_SEGMENT_WORKERS = int(os.getenv("STAGE4_SEGMENT_WORKERS", "3")) # segments in parallel

# --- Caption / subtitle styling (Fase 1.1 + 1.5 roadmap) ---
# Default caption mode when the template has no caption_style: "karaoke" (word
# highlight) or "static" (one caption per segment). A template's own
# caption_style field can override this per-template.
CAPTION_MODE = os.getenv("CAPTION_MODE", "karaoke")

# --- Background music (Fase 1.2 roadmap) ---
MUSIC_ENABLED = os.getenv("MUSIC_ENABLED", "true").lower() in ("1", "true", "yes")
MUSIC_DIR = BASE_DIR / os.getenv("MUSIC_DIR", "music")
# Volume multiplier applied to the music while narration is speaking (auto-ducking).
MUSIC_DUCK_LEVEL = float(os.getenv("MUSIC_DUCK_LEVEL", "0.18"))
# Seconds of volume ramp at the start/end of each duck window (avoids clicks).
MUSIC_DUCK_RAMP = float(os.getenv("MUSIC_DUCK_RAMP", "0.4"))
MUSIC_FADE_IN = float(os.getenv("MUSIC_FADE_IN", "2.0"))
MUSIC_FADE_OUT = float(os.getenv("MUSIC_FADE_OUT", "2.0"))
# Target RMS ratio of music base level relative to narration (simple loudness
# normalisation, keeps music+narration consistent across videos).
MUSIC_BASE_LEVEL = float(os.getenv("MUSIC_BASE_LEVEL", "0.35"))

# --- Transitions (Fase 1.3 roadmap) ---
TRANSITION_ENABLED = os.getenv("TRANSITION_ENABLED", "true").lower() in ("1", "true", "yes")
TRANSITION_DURATION = float(os.getenv("TRANSITION_DURATION", "0.5"))       # seconds of crossfade
TRANSITION_MIN_SHOT = float(os.getenv("TRANSITION_MIN_SHOT", "2.5"))       # shots shorter than this stay hard cuts

# --- Ken Burns (Fase 1.4 roadmap) ---
KEN_BURNS_ENABLED = os.getenv("KEN_BURNS_ENABLED", "true").lower() in ("1", "true", "yes")
KEN_BURNS_SCALE = float(os.getenv("KEN_BURNS_SCALE", "1.06"))               # 1.0 -> 1.06 zoom
KEN_BURNS_MIN_DURATION = float(os.getenv("KEN_BURNS_MIN_DURATION", "2.0"))  # only clips >= this get motion


def require(value: str, name: str, setup_hint: str) -> str:
    """Raise a clear error if a required API key/setting is missing."""
    if not value:
        raise RuntimeError(
            f"Missing required setting: {name}.\n"
            f"Set it in your .env file. {setup_hint}"
        )
    return value


# --- Cache Management ---
import time

CACHE_MAX_SIZE_MB = 20000  # 20GB max cache
CACHE_MAX_AGE_DAYS = 30    # Auto-delete files older than 30 days

def cleanup_cache():
    "Auto-cleanup old cache files to prevent disk space issues."
    now = time.time()
    max_age_seconds = CACHE_MAX_AGE_DAYS * 86400
    
    for cache_dir in [FOOTAGE_CACHE_DIR, AUDIO_CACHE_DIR]:
        if not cache_dir.exists():
            continue
        for f in cache_dir.iterdir():
            if f.is_file():
                age = now - f.stat().st_mtime
                if age > max_age_seconds:
                    try:
                        f.unlink()
                        print(f"[config] Cleaned up old cache: {f.name} ({age/86400:.0f} days old)")
                    except Exception as e:
                        print(f"[config] Failed to delete {f.name}: {e}")
    
    # Check total cache size
    total_size = sum(f.stat().st_size for f in FOOTAGE_CACHE_DIR.rglob("*") if f.is_file()) if FOOTAGE_CACHE_DIR.exists() else 0
    if total_size > CACHE_MAX_SIZE_MB * 1024 * 1024:
        print(f"[config] WARNING: Cache size ({total_size/1024/1024/1024:.1f} GB) exceeds {CACHE_MAX_SIZE_MB/1000:.0f} GB limit")
        # Delete oldest files first until under limit
        files = sorted(FOOTAGE_CACHE_DIR.rglob("*"), key=lambda f: f.stat().st_mtime if f.is_file() else float('inf'))
        for f in files:
            if f.is_file() and total_size > CACHE_MAX_SIZE_MB * 1024 * 1024 * 0.8:
                try:
                    file_size = f.stat().st_size
                    f.unlink()
                    total_size -= file_size
                    print(f"[config] Deleted oversized cache: {f.name}")
                except:
                    pass

# Auto-cleanup on startup
cleanup_cache()
