"""
Stage 5 — Auto-Cut Assembly

Takes the timed script segments (Stage 3) + their matched footage
(Stage 4) and cuts a final video that:
  - plays the generated narration as the audio track
  - shows each segment's matched footage during its narration window
  - re-cuts LONG segments into multiple sub-cuts so the final pacing
    matches the reference template's average shot duration (Stage 1),
    instead of one static shot sitting on screen for 8+ seconds
  - crops/resizes everything to the target aspect ratio
  - burns in per-segment subtitles

Written against moviepy >= 2.0, whose API differs from the older
moviepy 1.x (no `moviepy.editor` module; `set_x()` methods were
renamed to `with_x()`; `resize`/`crop`/`subclip` were renamed to
`resized`/`cropped`/`subclipped`). If you're on moviepy 1.x, see the
NOTE at the bottom of this file for the equivalent calls.
"""
import glob
from pathlib import Path

from moviepy import (
    VideoFileClip, AudioFileClip, concatenate_videoclips,
    CompositeVideoClip, TextClip, ColorClip,
)

from config import OUTPUT_DIR, OUTPUT_RESOLUTION

# Any reasonable system font works; DejaVu Sans Bold ships on most Linux boxes.
_DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # macOS
    "C:/Windows/Fonts/arialbd.ttf",  # Windows
]


def _resolve_font() -> str:
    for candidate in _DEFAULT_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    found = glob.glob("/usr/share/fonts/**/*Bold*.ttf", recursive=True)
    if found:
        return found[0]
    raise RuntimeError(
        "No bold .ttf font found on this system. Set a font path manually "
        "in stage5_assembly.py's _resolve_font()."
    )


def _fit_clip_to_frame(clip, target_w: int, target_h: int):
    """Center-crop + resize a clip to exactly fill the target resolution."""
    clip_ratio = clip.w / clip.h
    target_ratio = target_w / target_h

    if clip_ratio > target_ratio:
        clip = clip.resized(height=target_h)
        excess = clip.w - target_w
        clip = clip.cropped(x1=excess / 2, x2=clip.w - excess / 2)
    else:
        clip = clip.resized(width=target_w)
        excess = clip.h - target_h
        clip = clip.cropped(y1=excess / 2, y2=clip.h - excess / 2)

    return clip


def _split_segment_by_template_pacing(duration: float, avg_shot_duration: float) -> list[float]:
    """
    Given a segment that lasts `duration` seconds, decide how many
    sub-cuts it should be split into so individual shot lengths land
    close to the reference template's average shot duration.
    Returns a list of sub-cut durations that sum to `duration`.
    """
    if avg_shot_duration <= 0:
        return [duration]

    n_cuts = max(1, round(duration / avg_shot_duration))
    base = duration / n_cuts
    return [base] * n_cuts


def _build_segment_clip(footage_path: str, sub_duration: float, target_w: int, target_h: int,
                         offset_seed: float = 0.0):
    """Load footage, take a `sub_duration`-long window from it, fit to frame."""
    source = VideoFileClip(footage_path)

    if source.duration >= sub_duration:
        max_start = max(0, source.duration - sub_duration)
        start = min(offset_seed % (max_start + 0.01), max_start)
        clip = source.subclipped(start, start + sub_duration)
    else:
        loops = int(sub_duration / source.duration) + 1
        clip = concatenate_videoclips([source] * loops).subclipped(0, sub_duration)

    return _fit_clip_to_frame(clip, target_w, target_h).without_audio()


def _subtitle_clip_for_segment(text: str, start: float, duration: float, frame_w: int, frame_h: int, font: str):
    txt_clip = (
        TextClip(
            font=font, text=text, font_size=int(frame_h * 0.045), color="white",
            stroke_color="black", stroke_width=2, method="caption",
            size=(int(frame_w * 0.9), None),
        )
        .with_position(("center", "bottom"))
        .with_start(start)
        .with_duration(duration)
    )
    return txt_clip


def assemble_video(timed_segments: list[dict], footage_map: dict[int, dict],
                    narration_audio_path: str, template: dict,
                    output_name: str = "final_output", on_progress=None) -> str:
    """
    timed_segments: output of Stage 3 (align_keywords_to_timestamps)
    footage_map: {segment_index: {"video_path": ..., ...}} from Stage 4
    narration_audio_path: Stage 3 audio file
    template: Stage 1 template dict (used for pacing)
    on_progress(percent, message): optional callback for real encode progress
    (fed from moviepy's own frame-by-frame writer, not a simulated bar).
    """
    target_w, target_h = OUTPUT_RESOLUTION
    avg_shot_duration = template["pacing"]["avg_shot_duration"]
    font = _resolve_font()

    if on_progress:
        on_progress(2, "Menyusun klip per segmen…")

    video_clips = []
    subtitle_clips = []
    cursor = 0.0

    for idx, seg in enumerate(timed_segments):
        footage = footage_map.get(idx)
        if not footage or not Path(footage["video_path"]).exists():
            print(f"[stage5] No footage for segment {idx} — inserting black frame with subtitle only.")
            filler = ColorClip(size=(target_w, target_h), color=(10, 10, 10)).with_duration(seg["duration"])
            video_clips.append(filler)
            subtitle_clips.append(
                _subtitle_clip_for_segment(seg["text"], cursor, seg["duration"], target_w, target_h, font)
            )
            cursor += seg["duration"]
            continue

        sub_durations = _split_segment_by_template_pacing(seg["duration"], avg_shot_duration)
        for i, sub_dur in enumerate(sub_durations):
            clip = _build_segment_clip(
                footage["video_path"], sub_dur, target_w, target_h,
                offset_seed=idx * 7.0 + i * 3.0,
            )
            video_clips.append(clip)

        subtitle_clips.append(
            _subtitle_clip_for_segment(seg["text"], cursor, seg["duration"], target_w, target_h, font)
        )
        cursor += seg["duration"]

    if on_progress:
        on_progress(15, "Menggabungkan timeline…")

    full_video = concatenate_videoclips(video_clips, method="compose")
    narration_audio = AudioFileClip(narration_audio_path)
    trimmed_audio = narration_audio.subclipped(0, min(narration_audio.duration, full_video.duration))
    full_video = full_video.with_audio(trimmed_audio)

    final = CompositeVideoClip([full_video, *subtitle_clips], size=(target_w, target_h))

    if on_progress:
        on_progress(20, "Rendering (ffmpeg encode)…")

    out_path = str(OUTPUT_DIR / f"{output_name}.mp4")

    logger = None
    if on_progress:
        logger = _RenderProgressLogger(on_progress)

    final.write_videofile(out_path, fps=30, codec="libx264", audio_codec="aac", threads=4,
                           logger=logger if logger else None)

    print(f"[stage5] Final video rendered to {out_path}")
    return out_path


from proglog import ProgressBarLogger


class _RenderProgressLogger(ProgressBarLogger):
    """Adapts moviepy/proglog's frame-write progress into our 20-100% range
    so the UI shows real encode progress instead of a simulated animation."""

    def __init__(self, on_progress):
        super().__init__()
        self._on_progress = on_progress

    def bars_callback(self, bar, attr, value, old_value=None):
        if attr == "index":
            total = self.bars.get(bar, {}).get("total") or 1
            pct = 20 + int(min(value / total, 1.0) * 80)
            self._on_progress(pct, "Rendering (ffmpeg encode)…")

# NOTE for moviepy 1.x users (pip install "moviepy<2"):
#   from moviepy.editor import ... (instead of `from moviepy import ...`)
#   .resize()   instead of .resized()
#   .crop()     instead of .cropped()
#   .subclip()  instead of .subclipped()
#   .set_position()/.set_start()/.set_duration()/.set_audio()
#               instead of .with_position()/.with_start()/.with_duration()/.with_audio()
#   TextClip(txt, fontsize=.., font="DejaVu-Sans-Bold", ...) instead of
#   TextClip(font=<path.ttf>, text=txt, font_size=.., ...)
