"""
Caption renderer — karaoke word-highlight + configurable caption styles.

Roadmap Fase 1.1 + 1.5:
  - 1.1: captions appear word-by-word, the currently-spoken word highlighted,
        following the per-word timestamps Stage 3 already produces.
  - 1.5: caption look is driven by a `caption_style` field on the Stage 1
        template (preset name or inline dict) — swap 1 field in the template
        JSON and the captions change without touching code.

Why PIL instead of moviepy TextClip?
  - TextClip supports a single color per clip; karaoke needs one highlighted
    word inside a multi-word sentence. Rendering each frame to a PIL image
    lets us color each word independently (done / active / upcoming).
  - Layout is measurable and deterministic: same font metrics for every
    word-frame, so the sentence never shifts between frames.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --- Caption style presets (Fase 1.5) -------------------------------------
CAPTION_PRESETS = {
    # Default look — matches the old hardcoded subtitle (white, bold, bottom).
    "bold-white-bottom": {
        "mode": "karaoke",
        "font": None,                    # None = auto-resolve system font
        "font_size_ratio": 0.045,        # fraction of frame height
        "color": "white",                # words already spoken
        "active_color": "#ffd400",       # word being spoken (karaoke)
        "ghost_color": "#ffffff88",      # upcoming words (dimmed)
        "stroke_color": "black",
        "stroke_width": 2,
        "position": "bottom",
        "margin": 0.06,                  # fraction of frame height from edge
        "max_width_ratio": 0.9,          # fraction of frame width
        "background": None,
    },
    "minimal-white-center": {
        "mode": "karaoke",
        "font": None,
        "font_size_ratio": 0.04,
        "color": "white",
        "active_color": "#ffffff",
        "ghost_color": "#ffffff60",
        "stroke_color": "black",
        "stroke_width": 1,
        "position": "center",
        "margin": 0.05,
        "max_width_ratio": 0.85,
        "background": None,
    },
    "news-style-lower-third": {
        "mode": "karaoke",
        "font": None,
        "font_size_ratio": 0.038,
        "color": "#f5f5f5",
        "active_color": "#ffd400",
        "ghost_color": "#f5f5f566",
        "stroke_color": "#000000",
        "stroke_width": 1,
        "position": "lower-third",
        "margin": 0.08,
        "max_width_ratio": 0.92,
        "background": "#00000099",       # semi-transparent bar behind text
    },
}

# If a template asks for a static caption style, this is the default.
_DEFAULT_MODE = "karaoke"
_DEFAULT_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",            # Windows bold
    "C:/Windows/Fonts/arial.ttf",              # Windows regular
    "C:/Windows/Fonts/segoeuib.ttf",           # Windows Segoe UI bold
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",     # macOS
    "/System/Library/Fonts/Helvetica.ttc",
]


def resolve_font(custom_path: str | None = None) -> str:
    """Return the first font file that exists. `custom_path` (from the
    template's caption_style) wins when set and present on disk."""
    if custom_path:
        if Path(custom_path).exists():
            return custom_path
        print(f"[caption] Custom font not found at '{custom_path}' — falling back to system font.")
    for candidate in _DEFAULT_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(
        "No .ttf font found on this system. Set a font path in the template's "
        "caption_style.font or install a system font."
    )


def resolve_caption_style(template: dict | None, mode_override: str | None = None) -> dict:
    """
    Merge the template's caption_style (preset name OR inline dict) over the
    defaults, and apply the global CAPTION_MODE when the template is silent.
    Backward compatible: templates without the field get the classic look.
    """
    from config import CAPTION_MODE

    base = dict(CAPTION_PRESETS["bold-white-bottom"])
    mode = _DEFAULT_MODE

    raw = (template or {}).get("caption_style")
    if isinstance(raw, str):
        if raw in CAPTION_PRESETS:
            base = dict(CAPTION_PRESETS[raw])
        else:
            print(f"[caption] Unknown caption_style preset '{raw}' — using default.")
    elif isinstance(raw, dict):
        base.update(raw)

    mode = base.get("mode", mode) if isinstance(base.get("mode"), str) else mode
    if mode_override in ("karaoke", "static"):
        mode = mode_override

    base["mode"] = mode
    base["font"] = resolve_font(base.get("font"))
    return base


def _wrap_words(words: list[str], font: ImageFont.FreeTypeFont, max_width: int) -> list[list[int]]:
    """Greedy word-wrap: returns a list of lines, each a list of word indices."""
    lines: list[list[int]] = []
    current: list[int] = []
    current_w = 0
    for i, w in enumerate(words):
        w_w = font.getlength(w + " ")
        if current and current_w + w_w > max_width:
            lines.append(current)
            current = [i]
            current_w = w_w
        else:
            current.append(i)
            current_w += w_w
    if current:
        lines.append(current)
    return lines


def _draw_background(draw: ImageDraw.ImageDraw, box, radius: int, fill):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill)


def render_karaoke_images(words: list[dict], style: dict, frame_w: int, frame_h: int) -> list[dict]:
    """
    Render one full-sentence image per word, with that word highlighted.
    `words`: [{"word", "start", "end"}, ...] (absolute times, from Stage 3).

    Returns [{"start", "end", "image" (PIL Image)}, ...] sorted by start.
    If words is empty, returns [] — caller falls back to a static caption.
    """
    if not words:
        return []

    font_path = style["font"]
    font_size = max(12, int(frame_h * style.get("font_size_ratio", 0.045)))
    font = ImageFont.truetype(font_path, font_size)

    max_width = int(frame_w * style.get("max_width_ratio", 0.9))
    line_gap = int(font_size * 0.35)
    words_text = [w.get("word", "") for w in words]
    lines = _wrap_words(words_text, font, max_width)

    # Line height + total block height for vertical placement
    line_h = font_size + line_gap
    block_h = len(lines) * line_h - line_gap

    position = style.get("position", "bottom")
    margin = int(frame_h * style.get("margin", 0.06))
    if position == "center":
        top = (frame_h - block_h) // 2
    elif position == "lower-third":
        top = frame_h - margin - block_h
    else:  # bottom
        top = frame_h - margin - block_h
    top = max(0, top)

    # Precompute each word's bounding box (used for the background bar)
    word_boxes = {}
    cursor_y = top
    for line in lines:
        line_w = sum(font.getlength(words_text[i] + " ") for i in line)
        x = (frame_w - line_w) / 2
        for i in line:
            w_w = font.getlength(words_text[i])
            word_boxes[i] = (x, cursor_y, x + w_w, cursor_y + font_size)
            x += font.getlength(words_text[i] + " ")
        cursor_y += line_h

    stroke_w = max(1, int(style.get("stroke_width", 2)))

    frames = []
    for active_idx, w in enumerate(words):
        img = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if style.get("background"):
            # One soft bar behind the whole caption block
            x1 = min(b[0] for b in word_boxes.values())
            x2 = max(b[2] for b in word_boxes.values())
            _draw_background(
                draw,
                (x1 - 20, top - 10, x2 + 20, top + block_h + 10),
                radius=int(font_size * 0.35),
                fill=style["background"],
            )

        cursor_y = top
        for line in lines:
            line_w = sum(font.getlength(words_text[i] + " ") for i in line)
            x = (frame_w - line_w) / 2
            for i in line:
                color = style["color"]
                if i < active_idx:
                    color = style.get("color", "white")
                elif i == active_idx:
                    color = style.get("active_color", "#ffd400")
                else:
                    color = style.get("ghost_color", "#ffffff88")
                draw.text(
                    (x, cursor_y), words_text[i], font=font, fill=color,
                    stroke_width=stroke_w, stroke_fill=style.get("stroke_color", "black"),
                )
                x += font.getlength(words_text[i] + " ")
            cursor_y += line_h

        frames.append({
            "start": float(w.get("start", 0.0)),
            "end": float(w.get("end", 0.0)),
            "image": img,
        })

    return frames


def render_static_image(text: str, style: dict, frame_w: int, frame_h: int):
    """Render the whole segment text as one static caption image (fallback)."""
    font_path = style["font"]
    font_size = max(12, int(frame_h * style.get("font_size_ratio", 0.045)))
    font = ImageFont.truetype(font_path, font_size)
    max_width = int(frame_w * style.get("max_width_ratio", 0.9))
    line_gap = int(font_size * 0.35)

    words = text.split()
    lines = _wrap_words(words, font, max_width)
    line_h = font_size + line_gap
    block_h = len(lines) * line_h - line_gap

    position = style.get("position", "bottom")
    margin = int(frame_h * style.get("margin", 0.06))
    if position == "center":
        top = (frame_h - block_h) // 2
    elif position == "lower-third":
        top = frame_h - margin - block_h
    else:
        top = frame_h - margin - block_h
    top = max(0, top)

    img = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cursor_y = top
    for line in lines:
        line_text = " ".join(words[i] for i in line)
        line_w = font.getlength(line_text)
        x = (frame_w - line_w) / 2
        if style.get("background"):
            draw.rounded_rectangle(
                [x - 20, cursor_y - 8, x + line_w + 20, cursor_y + font_size + 8],
                radius=int(font_size * 0.35), fill=style["background"],
            )
        draw.text(
            (x, cursor_y), line_text, font=font,
            fill=style.get("color", "white"),
            stroke_width=max(1, int(style.get("stroke_width", 2))),
            stroke_fill=style.get("stroke_color", "black"),
        )
        cursor_y += line_h

    return img
