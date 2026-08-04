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
import os

from moviepy import (
    VideoFileClip, AudioFileClip, concatenate_videoclips,
    CompositeVideoClip, CompositeAudioClip, ColorClip, ImageClip,
)
from moviepy.video.fx import CrossFadeIn, CrossFadeOut, FadeIn, FadeOut, SlideIn

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


def _zoom_push(clip, fade: float, target_w: int, target_h: int):
    """P1.2 zoom transition — incoming shot starts 8% larger and settles to
    full frame over the fade window (CapCut-style push-in). Corner-anchored
    crop keeps it robust in moviepy 2.1.2 (same pattern as Ken Burns)."""
    scale = 1.08
    f = max(fade, 0.001)

    def factor(t):
        progress = min(max(t / f, 0.0), 1.0)
        return 1.0 + (scale - 1.0) * (1.0 - progress)

    zoomed = clip.resized(factor)
    return zoomed.cropped(x1=0, y1=0, x2=target_w, y2=target_h)


def _extend_tail(clip, extra: float):
    """P1.2: freeze the last frame of `clip` for `extra` seconds. Push
    transitions (slide/zoom) don't overlap the cursor, so the outgoing shot
    must stay visible until the incoming fully covers it — otherwise a black
    gap appears between the cut and the transition finishing."""
    if extra <= 0:
        return clip
    start = getattr(clip, "start", 0.0) or 0.0
    dur = clip.duration
    try:
        last = clip.get_frame(max(dur - 0.05, 0.0))
    except Exception:
        return clip
    frozen = ImageClip(last).with_duration(extra)
    return concatenate_videoclips([clip, frozen]).with_start(start)


def _build_segment_clip(footage_path: str, sub_duration: float, target_w: int, target_h: int,
                        offset_seed: float = 0.0, extend_secs: float = 0.0,
                        allow_kenburns: bool = True, kenburns_on: bool = True,
                        trim_start: float = 0.0, trim_end: float = 0.0,
                        sub_index: int = 0, filter_name: str = "original"):
    """
    Load footage, take a `sub_duration`-long window from it (optionally
    extended by `extend_secs` — used to compensate crossfade overlaps at the
    very end of the timeline), fit to frame, and (if enabled) apply a subtle
    Ken Burns zoom.

    trim_start/trim_end (seconds): timeline-editor trims on the source clip.
    When either is non-zero the window is taken from inside
    [trim_start, source.duration - trim_end] instead of the free offset_seed
    pick, and sub-cuts walk forward from trim_start (`sub_index * sub_duration`).
    """
    source = VideoFileClip(footage_path)
    need = sub_duration + extend_secs

    if trim_start > 0 or trim_end > 0:
        avail_start = trim_start
        avail_end = max(trim_start + 0.2, source.duration - trim_end)
        avail = avail_end - avail_start
        start = avail_start + (sub_index * sub_duration)
        if start + need > avail_end:
            start = max(avail_start, avail_end - need)
        if avail >= need:
            clip = source.subclipped(start, start + need)
        else:
            piece = source.subclipped(avail_start, avail_end)
            loops = int(need / max(piece.duration, 0.05)) + 1
            clip = concatenate_videoclips([piece] * loops).subclipped(0, need)
    elif source.duration >= need:
        max_start = max(0, source.duration - need)
        start = min(offset_seed % (max_start + 0.01), max_start)
        clip = source.subclipped(start, start + need)
    else:
        loops = int(need / max(source.duration, 0.05)) + 1
        clip = concatenate_videoclips([source] * loops).subclipped(0, need)

    clip = _fit_clip_to_frame(clip, target_w, target_h).without_audio()

    if allow_kenburns and kenburns_on and sub_duration >= KEN_BURNS_MIN_DURATION:
        clip = _apply_kenburns(clip, offset_seed, target_w, target_h)

    if filter_name and filter_name != "original":
        clip = _apply_filter(clip, filter_name)

    return clip


# ---------------------------------------------------------------------------
# Color filters (P1.3) — numpy per-frame, preset per segmen
# ---------------------------------------------------------------------------
def _apply_filter(clip, filter_name: str):
    """Color-grade preset per frame (numpy) — original = no-op.
    Presets: warm, cool, bright, vivid, bw, cinematic, vintage."""
    if not filter_name or filter_name == "original":
        return clip
    import numpy as _np

    def _f(img):
        a = _np.asarray(img).astype(_np.float32)
        if filter_name == "warm":
            a[..., 0] *= 1.18; a[..., 1] *= 1.04; a[..., 2] *= 0.86
        elif filter_name == "cool":
            a[..., 0] *= 0.88; a[..., 1] *= 1.00; a[..., 2] *= 1.18
        elif filter_name == "bright":
            a = a * 1.12 + 10
        elif filter_name == "vivid":
            gray = a.mean(axis=2, keepdims=True)
            a = gray + (a - gray) * 1.35
        elif filter_name == "bw":
            g = a.mean(axis=2, keepdims=True)
            a = _np.repeat(g, 3, axis=2)
        elif filter_name == "cinematic":
            a[..., 0] *= 1.12; a[..., 1] *= 1.02; a[..., 2] *= 0.85
            a = (a - 128.0) * 1.06 + 128.0
        elif filter_name == "vintage":
            r, g, b = a[..., 0], a[..., 1], a[..., 2]
            a = _np.stack([
                r * 0.393 + g * 0.769 + b * 0.189,
                r * 0.349 + g * 0.686 + b * 0.168,
                r * 0.272 + g * 0.534 + b * 0.131,
            ], axis=2)
        return _np.clip(a, 0, 255).astype(_np.uint8)

    return clip.image_transform(_f)  # moviepy 2.x (fl_image lama = image_transform)


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
# Title/text overlay manual (P1.1) — judul, lower-third, callout
# ---------------------------------------------------------------------------
_TITLE_POSITIONS = {
    "top-left": "tl", "top-center": "tc", "top-right": "tr",
    "center-left": "cl", "center": "cc", "center-right": "cr",
    "bottom-left": "bl", "bottom-center": "bc", "bottom-right": "br",
}

def _title_xy(pos_key: str, w: int, h: int, frame_w: int, frame_h: int, margin: int):
    cx, cy = frame_w / 2 - w / 2, frame_h / 2 - h / 2
    return {
        "tl": (margin, margin),
        "tc": (cx, margin),
        "tr": (frame_w - w - margin, margin),
        "cl": (margin, cy),
        "cc": (cx, cy),
        "cr": (frame_w - w - margin, cy),
        "bl": (margin, frame_h - h - margin),
        "bc": (cx, frame_h - h - margin),
        "br": (frame_w - w - margin, frame_h - h - margin),
    }.get(pos_key, (cx, margin))


def _hex_to_rgba(hex_color: str, alpha: int = 255):
    s = str(hex_color).lstrip("#")
    try:
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)
    except Exception:
        return (255, 255, 255, alpha)


def _title_clips_for_overlays(overlays, timed_segments, frame_w, frame_h) -> list:
    """Text/title overlay manual (P1.1) -> ImageClips pada timeline absolut.
    Setiap overlay: {segment_index, text, start_offset, duration, position,
    font_size, color, background_pill} — semua opsional dengan default wajar."""
    if not overlays:
        return []
    clips = []
    import numpy as _np
    from PIL import Image, ImageDraw, ImageFont
    seg_by_idx = {}
    for i, s in enumerate(timed_segments):
        idx = s.get("index", i)
        seg_by_idx[int(idx)] = s
    for ov in overlays:
        try:
            text = str(ov.get("text", "")).strip()
            if not text:
                continue
            seg = seg_by_idx.get(int(ov.get("segment_index", 0)) or 0)
            if seg is None:
                seg = timed_segments[0] if timed_segments else None
            if seg is None:
                continue
            seg_start = float(seg.get("start", 0.0) or 0.0)
            seg_end = float(seg.get("end", 0.0) or seg_start + 2.0)
            if seg_end <= seg_start:
                seg_end = seg_start + 2.0
            font_size = max(int(ov.get("font_size", 48)), 12)
            color = _hex_to_rgba(ov.get("color", "#FFFFFF"))
            pill = bool(ov.get("background_pill", False))
            pos_key = _TITLE_POSITIONS.get(str(ov.get("position", "top-center")), "tc")
            font_path = _resolve_font()
            font = ImageFont.truetype(font_path, font_size)
            dummy = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
            bbox = dummy.textbbox((0, 0), text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            pad = int(font_size * 0.55)
            w, h = tw + pad * 2, th + pad * 2
            img = Image.new("RGBA", (max(w, 1), max(h, 1)), (0, 0, 0, 0))
            d2 = ImageDraw.Draw(img)
            if pill:
                d2.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(h * 0.5),
                                     fill=(0, 0, 0, 175))
            d2.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=color)
            start_offset = float(ov.get("start_offset", 0.0) or 0.0)
            duration = float(ov.get("duration", 3.0) or 3.0)
            clip_start = seg_start + start_offset
            clip_end = min(clip_start + duration, seg_end + 0.02)
            if clip_end - clip_start < 0.1:
                continue
            margin = int(frame_w * 0.04)
            x, y = _title_xy(pos_key, w, h, frame_w, frame_h, margin)
            clips.append(
                ImageClip(_np.array(img))
                .with_duration(clip_end - clip_start)
                .with_start(clip_start)
                .with_position((x, y))
            )
        except Exception as e:
            print(f"[stage5] Title overlay skipped ({e})")
    return clips


def _sticker_clips_for_overlays(stickers, timed_segments, frame_w, frame_h) -> list:
    """Sticker/gambar overlay manual (P1.4) -> ImageClips pada timeline absolut.
    Setiap sticker: {segment_index, image_path, x, y (0-1 relatif frame,
    0.5 = tengah), scale (1.0 = 15% lebar frame), rotation (derajat),
    start_offset, duration}."""
    if not stickers:
        return []
    clips = []
    import numpy as _np
    from PIL import Image as PILImage
    seg_by_idx = {}
    for i, s in enumerate(timed_segments):
        idx = s.get("index", i)
        seg_by_idx[int(idx)] = s
    for st in stickers:
        try:
            path = str(st.get("image_path", "") or "")
            if not path or not Path(path).exists():
                continue
            seg = seg_by_idx.get(int(st.get("segment_index", 0)) or 0)
            if seg is None:
                seg = timed_segments[0] if timed_segments else None
            if seg is None:
                continue
            img = PILImage.open(path)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            rot = float(st.get("rotation", 0.0) or 0.0)
            if rot:
                img = img.rotate(rot, expand=True, resample=PILImage.BICUBIC)
            sw, sh = img.size
            base_w = int(frame_w * 0.15)
            scale = float(st.get("scale", 1.0) or 1.0)
            w = max(int(base_w * scale), 8)
            h = max(int(sh * w / sw), 8)
            clip = ImageClip(_np.array(img)).resized((w, h))
            # posisi dari CENTER sticker (rotasi gak bikin loncat)
            x = float(st.get("x", 0.5) or 0.5)
            y = float(st.get("y", 0.5) or 0.5)
            cx = x * frame_w - w / 2.0
            cy = y * frame_h - h / 2.0
            clip = clip.with_position((cx, cy))
            seg_start = float(seg.get("start", 0.0) or 0.0)
            seg_dur = float(seg.get("duration", 0.0) or 0.0)
            if seg_dur <= 0:
                seg_end = float(seg.get("end", 0.0) or seg_start + 2.0)
                seg_dur = max(seg_end - seg_start, 0.1)
            off = float(st.get("start_offset", 0.0) or 0.0)
            dur = float(st.get("duration", 0.0) or 0.0)
            if dur <= 0:
                dur = seg_dur - off
            dur = min(dur, max(seg_dur - off, 0.1))
            clips.append(
                clip.with_duration(max(dur, 0.1)).with_start(seg_start + off)
            )
        except Exception as e:
            print(f"[stage5] Sticker overlay skipped ({e})")
    return clips


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _safe_output_name(name: str, fallback: str = "ritme_output") -> str:
    """Sanitize user-supplied output_name (path traversal + header injection)."""
    import re
    if not name:
        return fallback
    s = re.sub(r"[^\w\-. ]+", "_", str(name))
    s = s.replace("..", "_").strip(" ._-")
    return (s or fallback)[:80]


def assemble_video(timed_segments: list[dict], footage_map: dict[int, dict],
                    narration_audio_path: str, template: dict,
                    output_name: str = "final_output", on_progress=None,
                    music_path: str | None = None,
                    add_music: bool | None = None,
                    music_mood: str | None = None,
                    caption_style: str | dict | None = None,
                    transition_style: str | None = None,
                    ken_burns: bool | None = None,
                    resolution: tuple[int, int] | None = None,
                    ffmpeg_preset: str = "medium",
                    segment_audio_paths: list[str] | None = None,
                    watermark_path: str | None = None,
                    watermark_pos: str = "bottom-right",
                    title_overlays: list[dict] | None = None,
                    sticker_overlays: list[dict] | None = None) -> str:
    """
    timed_segments: output of Stage 3 (align_keywords_to_timestamps)
    footage_map: {segment_index: {"video_path": ..., ...}} from Stage 4
    narration_audio_path: Stage 3 audio file
    segment_audio_paths: Fase 3.0 — one audio file per segment, aligned to
                timed_segments order. When provided (and paths exist), each
                segment's narration is placed at its own timeline window,
                so reordering/trimming a segment moves its voice with it.
                Falls back to narration_audio_path (whole track) otherwise.
    template: Stage 1 template dict (used for pacing + caption_style)
    music_path: optional explicit music file; when None, music is auto-picked
                from the music/ folder based on the script's mood.
    on_progress(percent, message): optional callback for real encode progress
    (fed from moviepy's own frame-by-frame writer, not a simulated bar).

    Finishing options (Fase 1C.1 — the timeline editor's manual choices).
    Each flag independently controls its feature; None means "follow the
    global config flag" so the /api/render quick mode keeps its old behaviour:
      add_music:       True/False force music on/off; None -> MUSIC_ENABLED
      music_mood:      explicit mood (e.g. "calm") -> pick file whose name
                       contains it; None -> auto-pick from script content
      caption_style:   preset name (CAPTION_PRESETS) or inline dict; None ->
                       resolve from the template (old behaviour)
      transition_style:"crossfade" forces transitions on, "hard_cut" forces
                       them off; None -> TRANSITION_ENABLED config
      ken_burns:       True/False force Ken Burns on/off; None -> KEN_BURNS_ENABLED
      resolution:      (w, h) override for preview renders (small = fast);
                       None -> OUTPUT_RESOLUTION from config
      ffmpeg_preset:   x264 preset ("medium" full render, "ultrafast" preview)
    """
    target_w, target_h = resolution or OUTPUT_RESOLUTION
    avg_shot_duration = template["pacing"]["avg_shot_duration"]

    # Fase 3.0: per-segment narration travels inside timed entries when the
    # caller doesn't pass an explicit segment_audio_paths list.
    if segment_audio_paths is None:
        segment_audio_paths = [s.get("audio_path", "") for s in timed_segments]

    # P1.2: transition_style -> per-boundary transition name.
    # "crossfade"|"dip_to_black"|"slide"|"zoom" = global transition style,
    # "hard_cut" = off; None -> TRANSITION_ENABLED config (legacy crossfade).
    if transition_style in ("crossfade", "dip_to_black", "slide", "zoom"):
        cut_transition = transition_style
    elif transition_style == "hard_cut":
        cut_transition = "none"
    else:
        cut_transition = "crossfade" if TRANSITION_ENABLED else "none"
    kenburns_on = ken_burns if ken_burns is not None else KEN_BURNS_ENABLED
    music_on = add_music if add_music is not None else MUSIC_ENABLED

    if caption_style is not None and isinstance(caption_style, str):
        from pipeline.caption_renderer import CAPTION_PRESETS
        if caption_style in CAPTION_PRESETS:
            caption_style_dict = resolve_caption_style({**template, "caption_style": caption_style})
        else:
            print(f"[stage5] Unknown caption_style preset '{caption_style}' — using template style.")
            caption_style_dict = resolve_caption_style(template)
    elif isinstance(caption_style, dict):
        merged = dict(resolve_caption_style(template))
        merged.update(caption_style)
        caption_style_dict = merged
    else:
        caption_style_dict = resolve_caption_style(template)

    if on_progress:
        on_progress(2, "Menyusun klip per segmen…")

    # ---- Build the sub-cut list with fade plan (Fase 1.3) -----------------
    # Each entry: {"clip", "dur", "fade_in", "start"}
    layers = []
    cursor = 0.0
    for idx, seg in enumerate(timed_segments):
        footage = footage_map.get(idx)
        seg_dur = seg["duration"]
        trim_start = float(seg.get("trim_start", 0.0) or 0.0)
        trim_end = float(seg.get("trim_end", 0.0) or 0.0)

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
                "trim_start": trim_start,
                "trim_end": trim_end,
                "sub_index": i,
                "filter_name": seg.get("filter", "original"),  # P1.3
            })
            cursor += sub_dur

    # Decide transitions: only boundaries where the FOLLOWING shot is long
    # enough — short punchy cuts stay hard cuts. Each layer records its
    # incoming transition; slide alternates sides for variety.
    n = len(layers)
    if cut_transition != "none" and n > 1:
        for i in range(1, n):
            if layers[i]["dur"] >= TRANSITION_MIN_SHOT:
                fade = min(TRANSITION_DURATION, 0.9 * min(layers[i - 1]["dur"], layers[i]["dur"]))
                layers[i]["fade_in"] = max(fade, 0.05)
                layers[i]["transition"] = cut_transition
                layers[i]["slide_side"] = "left" if (i % 2 == 0) else "right"

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
            kenburns_on=kenburns_on,
            trim_start=L.get("trim_start", 0.0), trim_end=L.get("trim_end", 0.0),
            sub_index=L.get("sub_index", 0),
            filter_name=L.get("filter_name", "original"),
        )
        fade = L["fade_in"]
        tr = L.get("transition", "none")
        # Fade-based transitions (crossfade / dip-to-black) start the clip
        # `fade` seconds EARLY so the incoming overlaps the outgoing tail —
        # fully-visible content still begins exactly at the cursor, so
        # audio/subtitle sync is untouched. Slide/zoom push transitions do
        # NOT shift the start: the incoming shot simply covers the outgoing
        # one from the cursor, so total duration stays exact.
        if fade > 0 and tr != "none":
            if tr == "crossfade":
                clip = clip.with_effects([CrossFadeIn(fade)])
                if i > 0:
                    prev = video_layers[i - 1]
                    video_layers[i - 1] = prev.with_effects([CrossFadeOut(fade)])
                clip = clip.with_start(L["start"] - fade)
            elif tr == "dip_to_black":
                if i > 0:
                    prev = video_layers[i - 1]
                    video_layers[i - 1] = prev.with_effects([FadeOut(fade, final_color=[0, 0, 0])])
                clip = clip.with_effects([FadeIn(fade, initial_color=[0, 0, 0])])
                clip = clip.with_start(L["start"] - fade)
            elif tr == "slide":
                clip = clip.with_effects([SlideIn(fade, side=L.get("slide_side", "left"))])
                clip = clip.with_start(L["start"])
                if i > 0:
                    video_layers[i - 1] = _extend_tail(video_layers[i - 1], fade)
            elif tr == "zoom":
                clip = _zoom_push(clip, fade, target_w, target_h)
                clip = clip.with_start(L["start"])
                if i > 0:
                    video_layers[i - 1] = _extend_tail(video_layers[i - 1], fade)
            else:
                clip = clip.with_start(L["start"])
        else:
            clip = clip.with_start(L["start"])
        video_layers.append(clip)

    if not video_layers:
        raise RuntimeError("No video layers could be built — no segments?")

    if on_progress:
        on_progress(10, "Menyusun caption…")

    # ---- Captions (Fase 1.1 + 1.5) ----------------------------------------
    caption_layers = []
    for idx, seg in enumerate(timed_segments):
        caption_layers.extend(_caption_clips_for_segment(seg, caption_style_dict, target_w, target_h))

    if on_progress:
        on_progress(15, "Menggabungkan timeline…")

    full_video = CompositeVideoClip(video_layers, size=(target_w, target_h))

    # ---- Narration audio (Fase 3.0: per-segment tracks) -------------------
    if segment_audio_paths:
        seg_clips = []
        for idx, seg in enumerate(timed_segments):
            if idx >= len(segment_audio_paths) or not segment_audio_paths[idx]:
                continue
            seg_path = segment_audio_paths[idx]
            if not Path(seg_path).exists():
                print(f"[stage5] Segment audio missing for {idx}: {seg_path} — skipping.")
                continue
            a = AudioFileClip(seg_path)
            seg_start = float(seg.get("start", 0.0) or 0.0)
            seg_dur = float(seg.get("duration", 0.0) or 0.0)
            # Voice placed at its timeline window; trim to window length.
            a = a.with_start(seg_start)
            if seg_dur > 0:
                a = a.subclipped(0, min(a.duration, seg_dur))
            seg_clips.append(a)
        if seg_clips:
            narration_audio = CompositeAudioClip(seg_clips).with_duration(full_video.duration)
        elif narration_audio_path and Path(narration_audio_path).exists():
            narration_audio = AudioFileClip(narration_audio_path)
            narration_audio = narration_audio.subclipped(0, min(narration_audio.duration, full_video.duration))
        else:
            narration_audio = None
    elif narration_audio_path and Path(narration_audio_path).exists():
        narration_audio = AudioFileClip(narration_audio_path)
        narration_audio = narration_audio.subclipped(0, min(narration_audio.duration, full_video.duration))
    else:
        narration_audio = None
    if narration_audio is not None:
        full_video = full_video.with_audio(narration_audio)

    # ---- Background music + auto-ducking (Fase 1.2) -----------------------
    if music_on:
        try:
            from pipeline import stage_music

            chosen = Path(music_path) if music_path else None
            if chosen is None:
                if music_mood:
                    chosen = stage_music.pick_music_by_mood(music_mood)
                else:
                    chosen = stage_music.pick_music_file(timed_segments)
            if chosen and chosen.exists():
                windows = [(float(s.get("start", 0.0)), float(s.get("end", 0.0))) for s in timed_segments]
                ducked = stage_music.build_ducked_music(
                    chosen, windows, full_video.duration, narration_audio_path,
                )
                if ducked:
                    music_clip = AudioFileClip(str(ducked))
                    full_video = full_video.with_audio(
                        CompositeAudioClip([narration_audio, music_clip])
                    )
                    print(f"[stage5] Background music: {chosen.name}")
            else:
                if music_path:
                    print(f"[stage5] Music file not found: {music_path} — continuing without music.")
                elif music_mood:
                    print(f"[stage5] No music file for mood '{music_mood}' — continuing narration-only.")
        except Exception as e:
            print(f"[stage5] Music skipped ({e}) — continuing narration-only.")

    watermark_layers = []
    if watermark_path and os.path.exists(watermark_path):
        try:
            from PIL import Image as PILImage
            wm = ImageClip(watermark_path).with_duration(full_video.duration)
            # cap ukuran logo ~12% lebar canvas, pertahankan aspect
            wm_w, wm_h = PILImage.open(watermark_path).size
            max_w = int(target_w * 0.12)
            scale = max_w / wm_w if wm_w > max_w else 1.0
            wm = wm.resized(max_w, int(wm_h * scale)) if scale != 1.0 else wm
            margin = int(target_w * 0.02)
            pos_map = {
                "top-left": (margin, margin),
                "top-right": ("right", margin),
                "bottom-left": (margin, "bottom"),
                "bottom-right": ("right", "bottom"),
                "center": ("center", "center"),
            }
            wm = wm.with_position(pos_map.get(watermark_pos, ("right", "bottom")))
            watermark_layers.append(wm)
        except Exception as e:
            print(f"[stage5] Watermark skipped ({e})")

    final = CompositeVideoClip(
        [full_video, *watermark_layers, *caption_layers,
         *_title_clips_for_overlays(title_overlays, timed_segments, target_w, target_h),
         *_sticker_clips_for_overlays(sticker_overlays, timed_segments, target_w, target_h)],
        size=(target_w, target_h),
    )

    if on_progress:
        on_progress(20, "Rendering (ffmpeg encode)…")

    out_path = str(OUTPUT_DIR / f"{_safe_output_name(output_name)}.mp4")

    logger = None
    if on_progress:
        logger = _RenderProgressLogger(on_progress)

    final.write_videofile(out_path, fps=30, codec="libx264", audio_codec="aac", threads=4,
                           preset=ffmpeg_preset, logger=logger if logger else None)

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
