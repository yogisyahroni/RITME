"""
Stage 1 — Template Extraction

Analyzes a reference video the user already owns/likes and turns its
editing rhythm into a reusable JSON "template":
  - shot boundaries + durations (via scene detection)
  - overall pacing stats (avg/median/min/max shot length)
  - narration structure, if the reference has speech (via Whisper)

This template is later used in Stage 5 to drive how fast new footage
gets cut for a brand-new topic.
"""
import json
import statistics
import subprocess
from pathlib import Path
from typing import Optional

from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector

from config import TEMPLATES_DIR, WHISPER_MODEL_SIZE


def detect_shots(video_path: str, threshold: float = 27.0) -> list[dict]:
    """
    Detect shot/scene boundaries in a video using content-aware detection
    (looks at frame-to-frame visual differences, similar to how editors
    perceive a "cut").

    Returns a list of {"start": seconds, "end": seconds, "duration": seconds}.
    """
    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    shots = []
    for start, end in scene_list:
        start_s = start.get_seconds()
        end_s = end.get_seconds()
        shots.append({
            "start": round(start_s, 3),
            "end": round(end_s, 3),
            "duration": round(end_s - start_s, 3),
        })

    # scenedetect sometimes misses a final boundary on very short clips;
    # fall back to treating the whole video as one shot if nothing was found
    if not shots:
        duration = _get_duration(video_path)
        shots = [{"start": 0.0, "end": duration, "duration": duration}]

    return shots


def _get_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return round(float(result.stdout.strip()), 3)


def analyze_narration(video_path: str, whisper_model_size: str = WHISPER_MODEL_SIZE) -> Optional[dict]:
    """
    Transcribe the reference video's audio track (if any) to learn its
    narration structure: words-per-minute, sentence count/length.

    Requires `faster-whisper` (pip install faster-whisper) and downloads
    a model from Hugging Face on first run — needs internet access.
    Returns None (with a printed warning) if faster-whisper isn't installed
    or the video has no usable speech.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[stage1] faster-whisper not installed — skipping narration analysis. "
              "Run: pip install faster-whisper")
        return None

    try:
        model = WhisperModel(whisper_model_size, compute_type="int8")
        segments, info = model.transcribe(video_path, word_timestamps=True, vad_filter=True)
        segments = list(segments)
    except Exception as e:
        print(f"[stage1] Could not transcribe reference audio: {e}")
        return None

    if not segments:
        return None

    full_text = " ".join(s.text.strip() for s in segments)
    word_count = len(full_text.split())
    duration = segments[-1].end if segments else 0
    wpm = round(word_count / (duration / 60), 1) if duration > 0 else 0

    # crude sentence split for pacing stats
    sentences = [s.strip() for s in full_text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    avg_sentence_len = round(sum(len(s.split()) for s in sentences) / len(sentences), 1) if sentences else 0

    return {
        "language": info.language,
        "word_count": word_count,
        "duration_seconds": round(duration, 2),
        "words_per_minute": wpm,
        "sentence_count": len(sentences),
        "avg_words_per_sentence": avg_sentence_len,
    }


def build_template(video_path: str, template_name: str, scene_threshold: float = 27.0,
                    analyze_speech: bool = True) -> dict:
    """
    Full Stage 1 entry point: detect shots + (optionally) analyze narration,
    save the result as templates/<template_name>.json, and return it.
    """
    video_path = str(Path(video_path).resolve())
    shots = detect_shots(video_path, threshold=scene_threshold)
    durations = [s["duration"] for s in shots]

    pacing = {
        "shot_count": len(shots),
        "avg_shot_duration": round(statistics.mean(durations), 2),
        "median_shot_duration": round(statistics.median(durations), 2),
        "min_shot_duration": round(min(durations), 2),
        "max_shot_duration": round(max(durations), 2),
        "stdev_shot_duration": round(statistics.pstdev(durations), 2) if len(durations) > 1 else 0.0,
    }

    narration = analyze_narration(video_path) if analyze_speech else None

    template = {
        "template_name": template_name,
        "source_video": video_path,
        "total_duration": round(sum(durations), 2),
        "pacing": pacing,
        "shots": shots,
        "narration": narration,
    }

    out_path = TEMPLATES_DIR / f"{template_name}.json"
    out_path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[stage1] Template saved to {out_path}")
    print(f"[stage1] {pacing['shot_count']} shots detected, "
          f"avg {pacing['avg_shot_duration']}s/shot, "
          f"total {template['total_duration']}s")

    return template


def load_template(template_name: str) -> dict:
    path = TEMPLATES_DIR / f"{template_name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Template '{template_name}' not found at {path}. "
            f"Run `extract-template` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))
