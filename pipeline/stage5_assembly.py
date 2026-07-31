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

Roadmap features (see RITME_ROADMAP.md):
  - Fase 1.1: karaoke/word-highlight captions — subtitles appear word by
    word, synced to the per-word timestamps Stage 3 attaches.
  - Fase 1.2: background music with auto-ducking (volume drops while the
    narrator speaks) + fade in/out + loudness matching.
  - Fase 1.3: crossfade transitions on longer shots (short/fast cuts stay
    hard cuts so the pacing energy is preserved); total duration is kept
    equal to the narration duration so subtitle/audio sync is untouched.
  - Fase 1.4: Ken Burns subtle zoom on footage shots >= KEN_BURNS_MIN_DURATION
    (direction chosen deterministically per cut).
  - Fase 1.5: caption look driven by the template's `caption_style` field
    (preset name or inline dict) — see pipeline/caption_renderer.py.

Written against moviepy >= 2.0, whose API differs from the older
moviepy 1.x (no `moviepy.editor` module; `set_x()` methods were
renamed to `with_x()`; `resize`/`crop`/`subclip` were renamed to
`resized`/`cropped`/`subclipped`). If you're on moviepy 1.x, see the
NOTE at the bottom of this file for the equivalent calls.
"""
from pathlib import Path

from moviepy import (
    VideoFileClip, AudioFileClip, concatenate_videoclips,
    CompositeVideoClip, CompositeAudioClip, ColorClip, ImageClip,
)
from moviepy.video.fx import CrossFadeIn, CrossFadeOut

from config import OUTPUT_DIR, OUTPUT_RESOLUTION, \
    TRANSITION_ENABLED, TRANSITION_DURATION, TRANSITION_MIN_SHOT, \
    KEN_BURNS_ENABLED, KEN_BURNS_SCALE, KEN_BURNS_MIN_DURATION, \
    MUSIC_ENABLED

from pipeline.caption_renderer import (
    resolve_caption_style, render_karaoke_images, render_static_image,
)


def _resolve_font() -> str:
    """Backward-compatible alias (some code imported this from stage5)."""
    from pipeline.caption_renderer import resolve_font
    return resolve_font()


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


def _apply_kenburns(clip, seed: int, target_w: int, target_h: int):
    """
    Fase 1.4: subtle zoom over the clip's duration. Zoom-in (1.0 -> scale)
    or zoom-out (scale -> 1.0) is picked deterministically from `seed` so the
    same cut always renders the same way, but different cuts vary.

    Implementation note (moviepy 2.1.2): `resized(callable)` renders every
    frame at its own scale, and a static `cropped` box then shows the
    top-left `target_w x target_h` window of the growing frame — a
    corner-anchored zoom that reads as a gentle pan+zoom. Callable crops and
    callable positions are NOT supported in this moviepy version, so this is
    the most robust composition that still moves.
    """
    scale = max(KEN_BURNS_SCALE, 1.02)
    zoom_in = (int(seed * 7919) % 2) == 0
    dur = max(clip.duration, 0.001)

    def factor(t):
        progress = min(max(t / dur, 0.0), 1.0)
        p = progress if zoom_in else 1.0 - progress
        return 1.0 + (scale - 1.0) * p

    zoomed = clip.resized(factor)
    return zoomed.cropped(x1=0, y1=0, x2=target_w, y2=target_h)


def _build_segment_clip(footage_path: str, sub_duration: float, target_w: int, target_h: int,
                         offset_seed: float = 0.0, extend_secs: float = 0.0,
                         allow_kenburns: bool = True):
    """
    Load footage, take a `sub_duration`-long window from it (optionally
    extended by `extend_secs` — used to compensate crossfade overlaps at the
    very end of the timeline), fit to frame, and (if enabled) apply a subtle
    Ken Burns zoom.
    """
    source = VideoFileClip(footage_path)
    need = sub_duration + extend_secs

    if source.duration >= need:
        max_start = max(0, source.duration - need)
        start = min(offset_seed % (max_start + 0.01), max_start)
        clip = source.subclipped(start, start + need)
    else:
        loops = int(need / source.duration) + 1
        clip = concatenate_videoclips([source] * loops).subclipped(0, need)

    clip = _fit_clip_to_frame(clip, target_w, target_h).without_audio()

    if allow_kenburns and KEN_BURNS_ENABLED and sub_duration >= KEN_BURNS_MIN_DURATION:
        clip = _apply_kenburns(clip, offset_seed, target_w, target_h)

    return clip


# ---------------------------------------------------------------------------
# Captions (Fase 1.1 karaoke + Fase 1.5 styling)
# ---------------------------------------------------------------------------
def _caption_clips_for_segment(seg: dict, style: dict, frame_w: int, frame_h: int) -> list:
    """
    Returns the ImageClips that caption this segment. Karaoke mode renders one
    full-sentence image per word (the active word highlighted) using the
    per-word timestamps from Stage 3; static mode renders one image for the
    whole segment. Falls back to static when the segment has no word data.
    """
    words = seg.get("words") or []
    mode = style.get("mode", "karaoke")

    if mode == "karaoke" and words:
        frames = render_karaoke_images(words, style, frame_w, frame_h)
        clips = []
        seg_start = float(seg.get("start", 0.0))
        seg_end = float(seg.get("end", seg_start))
        for f in frames:
            w_start = max(seg_start, float(f["start"]))
            w_end = min(seg_end, float(f["end"]))
            if w_end - w_start < 0.03:
                w_end = w_start + 0.05  # keep ultra-short words visible
            if w_end <= seg_start:
                continue
            # moviepy 2.x ImageClip accepts numpy arrays (not PIL images directly)
            import numpy as _np
            clips.append(
                ImageClip(_np.array(f["image"]))
                .with_duration(w_end - w_start)
                .with_start(w_start)
            )
        if clips:
            return clips

    # Static fallback
    text = seg.get("text", "")
    img = render_static_image(text, style, frame_w, frame_h)
    start = float(seg.get("start", 0.0))
    duration = float(seg.get("duration", 2.0))
    import numpy as _np
    return [ImageClip(_np.array(img)).with_duration(duration).with_start(start)]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def assemble_video(timed_segments: list[dict], footage_map: dict[int, dict],
                    narration_audio_path: str, template: dict,
                    output_name: str = "final_output", on_progress=None,
                    music_path: str | None = None) -> str:
    """
    timed_segments: output of Stage 3 (align_keywords_to_timestamps)
    footage_map: {segment_index: {"video_path": ..., ...}} from Stage 4
    narration_audio_path: Stage 3 audio file
    template: Stage 1 template dict (used for pacing + caption_style)
    music_path: optional explicit music file; when None, music is auto-picked
                from the music/ folder based on the script's mood.
    on_progress(percent, message): optional callback for real encode progress
    (fed from moviepy's own frame-by-frame writer, not a simulated bar).
    """
    target_w, target_h = OUTPUT_RESOLUTION
    avg_shot_duration = template["pacing"]["avg_shot_duration"]
    caption_style = resolve_caption_style(template)

    if on_progress:
        on_progress(2, "Menyusun klip per segmen…")

    # ---- Build the sub-cut list with fade plan (Fase 1.3) -----------------
    # Each entry: {"clip", "dur", "fade_in", "start"}
    layers = []
    cursor = 0.0
    for idx, seg in enumerate(timed_segments):
        footage = footage_map.get(idx)
        seg_dur = seg["duration"]

        if not footage or not Path(footage["video_path"]).exists():
            print(f"[stage5] No footage for segment {idx} — inserting black frame with subtitle only.")
            filler = ColorClip(size=(target_w, target_h), color=(10, 10, 10)).with_duration(seg_dur)
            layers.append({"clip": filler, "dur": seg_dur, "fade_in": 0.0,
                           "start": cursor, "is_filler": True})
            cursor += seg_dur
            continue

        sub_durations = _split_segment_by_template_pacing(seg_dur, avg_shot_duration)
        for i, sub_dur in enumerate(sub_durations):
            layers.append({
                "clip": None,  # built below (needs fade plan for extension)
                "dur": sub_dur, "fade_in": 0.0, "start": cursor,
                "footage_path": footage["video_path"],
                "offset_seed": idx * 7.0 + i * 3.0,
                "is_last": False,
                "is_filler": False,
            })
            cursor += sub_dur

    # Decide crossfades: only boundaries where the FOLLOWING shot is long
    # enough — short punchy cuts stay hard cuts.
    n = len(layers)
    if TRANSITION_ENABLED and n > 1:
        for i in range(1, n):
            if layers[i]["dur"] >= TRANSITION_MIN_SHOT:
                fade = min(TRANSITION_DURATION, 0.9 * min(layers[i - 1]["dur"], layers[i]["dur"]))
                layers[i]["fade_in"] = max(fade, 0.05)

    # Compensate overlap: extend the FINAL shot so the total timeline length
    # stays exactly sum(durations) == narration duration (sync untouched).
    if n:
        layers[-1]["is_last"] = True
        layers[-1]["extend_secs"] = layers[-1]["fade_in"]

    # ---- Materialize the video layers -------------------------------------
    video_layers = []
    for i, L in enumerate(layers):
        if L["is_filler"]:
            video_layers.append(L["clip"])
            continue
        ext = L.get("extend_secs", 0.0) if L["is_last"] else 0.0
        clip = _build_segment_clip(
            L["footage_path"], L["dur"], target_w, target_h,
            offset_seed=L["offset_seed"], extend_secs=ext,
        )
        fade = L["fade_in"]
        if fade > 0:
            clip = clip.with_effects([CrossFadeIn(fade)])
            if i > 0:
                # outgoing layer fades out over the same window
                prev = video_layers[i - 1]
                video_layers[i - 1] = prev.with_effects([CrossFadeOut(fade)])
        # moviepy 2.x CrossFadeIn makes the clip transparent for the first
        # `fade` seconds WITHOUT shifting its start. Start the clip `fade`
        # seconds EARLY so the fade-in overlaps the previous shot's fade-out
        # tail — the fully-visible content still begins exactly at the cursor
        # position, so audio/subtitle sync is untouched.
        clip = clip.with_start(L["start"] - fade)
        video_layers.append(clip)

    if not video_layers:
        raise RuntimeError("No video layers could be built — no segments?")

    if on_progress:
        on_progress(10, "Menyusun caption…")

    # ---- Captions (Fase 1.1 + 1.5) ----------------------------------------
    caption_layers = []
    for idx, seg in enumerate(timed_segments):
        caption_layers.extend(_caption_clips_for_segment(seg, caption_style, target_w, target_h))

    if on_progress:
        on_progress(15, "Menggabungkan timeline…")

    full_video = CompositeVideoClip(video_layers, size=(target_w, target_h))
    narration_audio = AudioFileClip(narration_audio_path)
    trimmed_audio = narration_audio.subclipped(0, min(narration_audio.duration, full_video.duration))
    full_video = full_video.with_audio(trimmed_audio)

    # ---- Background music + auto-ducking (Fase 1.2) -----------------------
    if MUSIC_ENABLED:
        try:
            from pipeline import stage_music

            chosen = Path(music_path) if music_path else stage_music.pick_music_file(timed_segments)
            if chosen and chosen.exists():
                windows = [(float(s.get("start", 0.0)), float(s.get("end", 0.0))) for s in timed_segments]
                ducked = stage_music.build_ducked_music(
                    chosen, windows, full_video.duration, narration_audio_path,
                )
                if ducked:
                    music_clip = AudioFileClip(str(ducked))
                    full_video = full_video.with_audio(
                        CompositeAudioClip([trimmed_audio, music_clip])
                    )
                    print(f"[stage5] Background music: {chosen.name}")
            else:
                if music_path:
                    print(f"[stage5] Music file not found: {music_path} — continuing without music.")
        except Exception as e:
            print(f"[stage5] Music skipped ({e}) — continuing narration-only.")

    final = CompositeVideoClip([full_video, *caption_layers], size=(target_w, target_h))

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
