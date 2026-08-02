"""
Clipper — ubah 1 video jadi N clip vertical 9:16 (Reels/TikTok).

Flow:
    analyze_video(path, num_clips) -> [{index, start, end, duration, thumbnail}]
        scene detection (scenedetect) -> pilih/gabung boundary jadi N segmen
    render_clip(path, start, end, out_path, aspect="9:16")
        ffmpeg center-crop + scale + audio track
    render_clips(path, clips, out_dir) -> [out_path, ...]

Sumber video bisa file lokal ATAU hasil download YouTube (lihat server.py).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ASPECT_TARGETS = {
    "9:16": (1080, 1920),   # Reels / TikTok / Shorts
    "16:9": (1920, 1080),   # YouTube / landscape
    "1:1": (1080, 1080),    # feed
}

SCENE_THRESHOLD = 27.0
MIN_CLIP_SEC = 6.0
MAX_CLIP_SEC = 75.0


def _ffprobe(path: str, key: str) -> str:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", f"stream={key}", "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=60,
    )
    return r.stdout.strip()


def probe_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def probe_resolution(path: str) -> tuple[int, int]:
    w = _ffprobe(path, "width")
    h = _ffprobe(path, "height")
    try:
        return int(w), int(h)
    except ValueError:
        return 0, 0


def _detect_scenes(path: str, threshold: float = SCENE_THRESHOLD) -> list[tuple[float, float]]:
    """Scene boundaries via scenedetect (same lib as footage_extractor)."""
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
    except ImportError:
        return []
    video = open_video(path)
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold))
    sm.detect_scenes(video, show_progress=False)
    return [(float(s[0].get_seconds()), float(s[1].get_seconds())) for s in sm.get_scene_list()]


def _split_evenly(total: float, num: int) -> list[tuple[float, float]]:
    step = total / num
    return [(i * step, min((i + 1) * step, total)) for i in range(num)]


def analyze_video(video_path: str, num_clips: int = 5,
                  min_duration: float = MIN_CLIP_SEC,
                  max_duration: float = MAX_CLIP_SEC) -> list[dict]:
    """
    Bagi video jadi `num_clips` segmen pintar:
    - even split dulu (jumlah clip pasti sesuai permintaan)
    - lalu snap tiap boundary dalam ke scene cut TERDEKAT (radius 20% window)
      supaya potongan nggak motong di tengah adegan
    Returns [{index, start, end, duration}].
    """
    total = probe_duration(video_path)
    if total <= 0:
        raise ValueError("Gagal membaca durasi video")
    num_clips = max(1, min(int(num_clips), 20))

    cuts = [s for s, _ in _detect_scenes(video_path)]  # titik-titik scene change
    windows = _split_evenly(total, num_clips)

    if cuts:
        snapped = [0.0]
        for i in range(1, num_clips):
            lo, hi = windows[i - 1]
            radius = (hi - lo) * 0.20
            lo_b, hi_b = max(lo, snapped[-1] + 0.2), min(windows[i][1], hi + radius)
            best = min(cuts, key=lambda c: abs(c - hi) if lo_b <= c <= hi_b else float("inf"))
            snapped.append(best if best != float("inf") else hi)
        snapped.append(total)
        # clamp: jangan biarkan dua boundary berdekatan < 2 detik
        for i in range(1, len(snapped) - 1):
            if snapped[i] - snapped[i - 1] < 2.0:
                snapped[i] = snapped[i - 1] + 2.0
            if snapped[i + 1] - snapped[i] < 2.0:
                snapped[i] = snapped[i + 1] - 2.0
        snapped = [max(0.0, min(s, total)) for s in snapped]
        windows = [(snapped[i], snapped[i + 1]) for i in range(len(snapped) - 1)]

    clips = []
    for i, (start, end) in enumerate(windows):
        dur = round(end - start, 2)
        clips.append({
            "index": i,
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": dur,
        })
    return clips


def render_clip(video_path: str, start: float, end: float, out_path: str,
                aspect: str = "9:16", target: tuple[int, int] | None = None,
                crf: int = 20, preset: str = "fast") -> str:
    """Render satu segmen jadi aspect tertentu (center crop). Retain audio."""
    if aspect not in ASPECT_TARGETS:
        aspect = "9:16"
    tw, th = target or ASPECT_TARGETS[aspect]
    w, h = probe_resolution(video_path)

    if w <= 0 or h <= 0:
        raise ValueError("Gagal membaca resolusi video")

    src_aspect = w / h
    dst_aspect = tw / th
    if src_aspect > dst_aspect:
        crop = f"crop=ih*{dst_aspect:.6f}:ih"
    elif src_aspect < dst_aspect:
        crop = f"crop=iw:iw/{dst_aspect:.6f}"
    else:
        crop = "null"
    vf = f"{crop},scale={tw}:{th}" if crop != "null" else f"scale={tw}:{th}"

    dur = end - start
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        "-pix_fmt", "yuv420p", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg gagal: {r.stderr[-500:]}")
    return out_path


def render_clips(video_path: str, clips: list[dict], out_dir: str,
                 aspect: str = "9:16") -> list[str]:
    """Render semua clip -> list out_path (urut). out_dir dibuat otomatis."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    outs = []
    for c in clips:
        out = str(Path(out_dir) / f"clip_{c['index'] + 1:02d}_{aspect.replace(':', 'x')}.mp4")
        render_clip(video_path, c["start"], c["end"], out, aspect=aspect)
        outs.append(out)
    return outs


def extract_frame(video_path: str, at_second: float, out_image: str) -> str:
    """Ekstrak 1 frame sebagai jpg (buat thumbnail clip)."""
    Path(out_image).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{at_second:.3f}", "-i", video_path,
        "-frames:v", "1", "-q:v", "3", out_image,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg frame gagal: {r.stderr[-300:]}")
    return out_image


# ---------------------------------------------------------------------------
# AutoCaption (2026-08-02)
# ---------------------------------------------------------------------------
def transcribe_clip_words(clip_path: str, model_size: str = "base") -> list[dict]:
    """Whisper word timestamps untuk 1 clip video (audio diekstrak via ffmpeg).

    Returns [{"word", "start", "end"}, ...] dalam waktu RELATIF clip.
    """
    from pipeline.stage3_narration import transcribe_with_timestamps
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wav = str(Path(td) / "clip_audio.wav")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", clip_path,
               "-vn", "-ac", "1", "-ar", "16000", wav]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not Path(wav).exists():
            raise RuntimeError(f"Ekstrak audio clip gagal: {r.stderr[-300:]}")
        return transcribe_with_timestamps(wav, model_size=model_size)


def burn_captions(clip_path: str, words: list[dict], style: dict | str,
                  out_path: str | None = None, fps: int = 30) -> str:
    """Burn karaoke caption ke clip video (moviepy overlay), retain audio.

    Reuse caption_renderer karaoke frames + pattern yang sama kayak
    stage5_assembly._caption_clips_for_segment. Returns out_path.
    """
    import numpy as _np
    from moviepy import VideoFileClip, CompositeVideoClip, ImageClip
    from pipeline.caption_renderer import render_karaoke_images, resolve_caption_style

    if not words:
        return clip_path  # gak ada kata → clip polos
    style = resolve_caption_style({"caption_style": style}) if isinstance(style, str) else style
    clip = VideoFileClip(clip_path)
    frame_w, frame_h = clip.w, clip.h
    frames = render_karaoke_images(words, style, frame_w, frame_h)

    overlays = []
    clip_dur = float(clip.duration or 0)
    for f in frames:
        w_start = max(0.0, float(f["start"]))
        w_end = min(clip_dur, float(f["end"]))
        if w_end - w_start < 0.03:
            w_end = w_start + 0.05
        if w_end <= 0 or w_start >= clip_dur:
            continue
        overlays.append(
            ImageClip(_np.array(f["image"]))
            .with_duration(w_end - w_start)
            .with_start(w_start)
        )
    if not overlays:
        clip.close()
        return clip_path

    final = CompositeVideoClip([clip, *overlays])
    final.fps = fps
    out = out_path or str(Path(clip_path).with_name(Path(clip_path).stem + "_captioned.mp4"))
    final.write_videofile(
        out, codec="libx264", audio_codec="aac", audio_bitrate="128k",
        preset="fast", fps=fps, threads=4, logger=None,
    )
    clip.close()
    return out
