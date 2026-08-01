"""
Background music + auto-ducking (Roadmap Fase 1.2).

Responsibilities:
  - pick a royalty-free music file from the local music/ folder, choosing a
    track whose mood fits the script (the `music_mood` field Stage 2 now adds,
    with a keyword heuristic fallback so old scripts still work);
  - build a full-length music track that:
      * ducks (volume drops to MUSIC_DUCK_LEVEL) while narration is speaking,
        ramping smoothly so there are no clicks,
      * fades in/out,
      * is loudness-normalised relative to the narration (simple RMS matching
        so music never drowns the voice and levels are consistent across
        videos).

Legal: music is sourced ONLY from the user's own local folder (royalty-free
tracks the user has rights to). No external music API is called.
"""

import hashlib
import re
from pathlib import Path

import numpy as np

from config import AUDIO_CACHE_DIR, MUSIC_DIR, MUSIC_DUCK_LEVEL, \
    MUSIC_DUCK_RAMP, MUSIC_FADE_IN, MUSIC_FADE_OUT, MUSIC_BASE_LEVEL

# Moods -> trigger keywords (Indonesian + English). Order matters: first match wins.
_MOOD_KEYWORDS = {
    "calm": ["tenang", "damai", "tentram", "meditasi", "relaks", "lembut", "nature", "calm", "relax", "peace", "soft", "gentle", "slow"],
    "tense": ["mencekam", "tegang", "bahaya", "ancaman", "krisis", "konflik", "perang", "panic", "tense", "danger", "threat", "crisis", "war", "conflict", "urgent"],
    "sad": ["sedih", "tragedi", "duka", "kehilangan", "meninggal", "korban", "pilu", "sad", "tragedy", "loss", "grief", "victim", "mourning"],
    "epic": ["epic", "heroik", "perjuangan", "besar", "megah", "sejarah", "revolusi", "dunia", "mighty", "heroic", "struggle", "grand", "epic", "history"],
    "upbeat": ["semangat", "ceria", "energik", "sukses", "inovasi", "peluang", "bahagia", "future", "upbeat", "happy", "energy", "success", "innovation", "opportunity", "bright"],
}


def guess_music_mood(text: str) -> str:
    """Heuristic mood detection from narration/keyword text (fallback when the
    LLM didn't provide `music_mood`). Defaults to "calm"."""
    lowered = (text or "").lower()
    for mood, keywords in _MOOD_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return mood
    return "calm"


def pick_music_file(script_segments: list[dict], music_dir: Path = MUSIC_DIR) -> Path | None:
    """
    Choose a music file for this script.

    Strategy:
      1. If the script carries an explicit `music_mood`, prefer a file whose
         name contains that mood (e.g. `calm_guitar.mp3`).
      2. Otherwise pick deterministically (hash of the combined text) so the
         same script always gets the same track, but different scripts vary.

    Returns None when the music folder is missing/empty — callers treat that
    as "no music" and render narration-only.
    """
    music_dir = Path(music_dir)
    if not music_dir.is_dir():
        return None

    supported = ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac")
    files = []
    for pattern in supported:
        files.extend(music_dir.glob(pattern))
    if not files:
        return None

    mood = None
    for seg in script_segments:
        if isinstance(seg.get("music_mood"), str) and seg["music_mood"]:
            mood = seg["music_mood"].lower()
            break

    if mood:
        mood_files = [f for f in files if mood in f.stem.lower()]
        if mood_files:
            return sorted(mood_files)[0]

    # Deterministic pick based on script content (stable across runs).
    blob = " ".join(str(seg.get("text", "")) for seg in script_segments)
    idx = int(hashlib.md5(blob.encode("utf-8")).hexdigest(), 16) % len(files)
    return sorted(files, key=lambda f: f.name)[idx]


def pick_music_by_mood(mood: str | None, music_dir: Path = MUSIC_DIR) -> Path | None:
    """
    Pick a music file whose name contains the given mood (e.g. "calm" ->
    calm_guitar.mp3). Returns None when the folder is missing/empty or no
    file matches — callers treat that as "no music".
    """
    music_dir = Path(music_dir)
    if not mood or not music_dir.is_dir():
        return None

    supported = ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac")
    files = []
    for pattern in supported:
        files.extend(music_dir.glob(pattern))
    if not files:
        return None

    mood_files = [f for f in files if mood.lower() in f.stem.lower()]
    return sorted(mood_files)[0] if mood_files else None


def _narration_rms(narration_audio_path: str) -> float:
    """RMS of the narration audio in raw int16 sample units (same scale as
    the music samples we mix against); 0.0 if unreadable."""
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(narration_audio_path)
        samples = np.array(seg.get_array_of_samples(), dtype=np.float64)
        if samples.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples ** 2)))
    except Exception as e:
        print(f"[music] Could not measure narration RMS ({e}) — using flat music level.")
        return 0.0


def build_ducked_music(music_path: Path, narration_windows: list[tuple[float, float]],
                       total_duration: float, narration_audio_path: str | None = None,
                       out_path: Path | None = None) -> Path | None:
    """
    Build a full-length, ducked, faded, level-matched music track.

    narration_windows: [(start, end), ...] in seconds — music ducks inside.
    total_duration: final track length in seconds (== video length).
    narration_audio_path: used to set the music base level relative to the
                          voice (simple RMS normalisation).

    Returns the path to the written WAV, or None if the music can't be decoded.
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        print("[music] pydub not installed — skipping background music. Run: pip install pydub")
        return None

    try:
        song = AudioSegment.from_file(str(music_path))
    except Exception as e:
        print(f"[music] Cannot decode {music_path.name}: {e} — skipping music.")
        return None

    if total_duration <= 0:
        return None

    # Loop (or trim) the song to the video length.
    target_ms = int(total_duration * 1000)
    if len(song) == 0:
        return None
    loops = int(target_ms / len(song)) + 1
    song = (song * loops)[:target_ms]

    # Convert to float mono-ish samples for envelope work (keep channels, work on max for envelope).
    samples = np.array(song.get_array_of_samples(), dtype=np.float64)
    channels = song.channels or 1
    frame_count = len(samples) // channels
    if frame_count == 0:
        return None
    left = samples[::channels].copy()
    if channels > 1:
        right = samples[1::channels].copy()
    else:
        right = left.copy()
    sr = song.frame_rate or 44100
    n = frame_count

    # ---- Gain envelope: 1.0 outside narration, DUCK inside, ramped ----
    gain = np.ones(n, dtype=np.float64)
    ramp_n = max(1, int(MUSIC_DUCK_RAMP * sr))
    for (ws, we) in narration_windows:
        if we <= 0 or ws >= total_duration:
            continue
        i0 = max(0, int(ws * sr))
        i1 = min(n, int(we * sr))
        if i1 <= i0:
            continue
        # cosine ramp from 1.0 -> duck at the start of the window
        r0 = min(ramp_n, (i1 - i0) // 2)
        duck = MUSIC_DUCK_LEVEL
        ramp_in = 0.5 * (1 - np.cos(np.pi * np.arange(r0) / r0)) if r0 > 0 else np.array([])
        gain[i0:i0 + r0] = 1.0 - (1.0 - duck) * ramp_in
        gain[i0 + r0:i1 - r0] = duck if i1 - r0 > i0 + r0 else gain[i0 + r0:i1 - r0]
        ramp_out = 0.5 * (1 - np.cos(np.pi * np.arange(r0) / r0)) if r0 > 0 else np.array([])
        gain[i1 - r0:i1] = duck + (1.0 - duck) * ramp_out

    # ---- Fade in/out ----
    fade_in_n = min(int(MUSIC_FADE_IN * sr), n)
    fade_out_n = min(int(MUSIC_FADE_OUT * sr), n)
    if fade_in_n > 0:
        gain[:fade_in_n] *= np.linspace(0, 1, fade_in_n)
    if fade_out_n > 0:
        gain[-fade_out_n:] *= np.linspace(1, 0, fade_out_n)

    left *= gain
    right *= gain

    # ---- Loudness matching: scale music RMS to MUSIC_BASE_LEVEL * narration RMS ----
    rms = float(np.sqrt(np.mean(left ** 2))) or 1e-9
    target_rms = MUSIC_BASE_LEVEL * (_narration_rms(narration_audio_path) if narration_audio_path else 0.0)
    if target_rms > 0:
        # Normalize music so that when NOT ducked it sits at `target_rms`.
        # (Ducking already applied; this scales the whole track so the
        # unducked segments reach the target.)
        scale = target_rms / (rms / max(np.max(gain), 1e-9))
        scale = min(scale, 4.0)  # cap gain so quiet music doesn't blow up
        left *= scale
        right *= scale

    # Clip to int16 range
    def _to_int16(arr):
        return np.clip(arr, -32767, 32767).astype(np.int16)

    interleaved = np.empty(n * 2, dtype=np.int16)
    interleaved[0::2] = _to_int16(left)
    interleaved[1::2] = _to_int16(right)

    out_path = out_path or (AUDIO_CACHE_DIR / "music_ducked.wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import wave
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(interleaved.tobytes())

    print(f"[music] Ducking applied over {len(narration_windows)} narration window(s) -> {out_path}")
    return out_path
