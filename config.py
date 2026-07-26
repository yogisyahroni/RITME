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
