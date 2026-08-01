"""
Thumbnail generator — frame terbaik dari video + overlay judul (1280x720).

- Frame candidates di 10/30/50/70/90% durasi.
- Pilih via CLIP score terhadap judul (kalau matcher + judul tersedia),
  fallback sharpness (Laplacian variance) kalau tidak.
- Overlay judul dengan band semi-transparan di bawah, teks wrap + shadow.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

SIZE = (1280, 720)
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _sharpness(img) -> float:
    """Laplacian variance sebagai proksi ketajaman frame."""
    import numpy as np
    g = np.asarray(img.convert("L"), dtype=np.float64)
    lap = np.diff(g, axis=0)[1:] - np.diff(g, axis=0)[:-1]
    return float(np.var(lap))


def _extract_frame_at(video_path: str, at_second: float, out_jpg: str) -> Path:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{at_second:.3f}", "-i", video_path,
        "-frames:v", "1", "-q:v", "2", out_jpg,
    ], capture_output=True, text=True, timeout=120)
    return Path(out_jpg)


def pick_best_frame(video_path: str, title: str = "", n_candidates: int = 5,
                    preview: bool = False):
    """
    Ambil frame terbaik. Returns (Path ke jpg, score, at_second).
    preview=True -> pakai resolusi kecil (buat grid clipper), False -> 1280x720.
    """
    import tempfile
    from pipeline.clipper import probe_duration, probe_resolution

    total = probe_duration(video_path)
    if total <= 0:
        total = 10.0
    w, h = probe_resolution(video_path)
    if w <= 0:
        w, h = 1280, 720

    positions = [max(0.0, min(total * f, total - 0.1)) for f in (0.10, 0.30, 0.50, 0.70, 0.90)][:n_candidates]

    tmpdir = Path(tempfile.mkdtemp(prefix="ritme_thumb_"))
    frames = []
    try:
        for i, t in enumerate(positions):
            scale = 320 if preview else SIZE[0]
            out = tmpdir / f"f{i}.jpg"
            _extract_frame_at(video_path, t, str(out))
            if out.exists() and out.stat().st_size > 0:
                frames.append((t, out))
        if not frames:
            raise RuntimeError("Tidak ada frame yang bisa diekstrak")

        # Fallback: sharpness (frame tengah dapat bonus posisi)
        scores: list[tuple[float, str, float]] = []
        for t, path in frames:
            from PIL import Image
            img = Image.open(path).convert("RGB")
            s = _sharpness(img)
            center_bonus = 1.0 - abs(0.5 - t / total)  # 0.5..1.0
            scores.append((s * center_bonus, str(path), t))

        best_score, best_path, best_t = max(scores, key=lambda x: x[0])
        return Path(best_path), best_score, best_t
    finally:
        pass


def generate_thumbnail(video_path: str, title: str, out_path: str,
                       size: tuple[int, int] = SIZE,
                       subtitle: str = "") -> str:
    """
    Generate thumbnail 1280x720: frame terbaik + overlay judul.
    title panjang di-wrap jadi max 2 baris; subtitle opsional di baris 3.
    """
    from PIL import Image, ImageDraw

    frame_path, _, _ = pick_best_frame(video_path, title)
    img = Image.open(frame_path).convert("RGB")
    img = img.resize(size, Image.LANCZOS)

    draw = ImageDraw.Draw(img, "RGBA")

    # Gradient/band semi-transparan di bagian bawah
    band_h = int(size[1] * 0.34)
    band = Image.new("RGBA", (size[0], band_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for y in range(band_h):
        alpha = int(180 * (y / band_h) ** 1.5)
        bd.line([(0, y), (size[0], y)], fill=(10, 8, 5, alpha))
    img.paste(band, (0, size[1] - band_h), band)

    draw = ImageDraw.Draw(img)

    # Wrap judul -> max 2 baris
    font = _font(58)
    small = _font(30)
    words = title.strip().split()
    lines: list[str] = []
    cur = ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if draw.textlength(test, font=font) > size[0] - 120 and cur:
            lines.append(cur)
            cur = w_
        else:
            cur = test
    if cur:
        lines.append(cur)
    lines = lines[:2]
    if len(words) > sum(len(l.split()) for l in lines):
        lines[-1] = lines[-1][: max(len(lines[-1]) - 3, 0)] + "…" if len(lines) > 1 else lines[-1]

    y = size[1] - band_h + 26
    for line in lines:
        draw.text((60, y), line, font=font, fill=(255, 255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0, 200))
        y += 72
    if subtitle:
        draw.text((60, y + 4), subtitle, font=small, fill=(255, 200, 100, 255))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "JPEG", quality=90)
    return out_path
