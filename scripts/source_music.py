"""
Source CC-licensed background music from YouTube for the RITME music/ folder.

Why YouTube + videoLicense=creativeCommon:
  - RITME already has YOUTUBE_API_KEY + yt-dlp in the venv (Stage 4 uses both).
  - The search API filter guarantees every picked video is Creative Commons,
    so monetized videos stay legally safe as long as attribution is given
    (see music/LICENSES.md — always credit the source in the video description).
  - No extra API signup needed.

Usage:
    venv_311/Scripts/python.exe scripts/source_music.py

Picks ONE track per mood (calm/tense/sad/epic/upbeat), names it <mood>_N.mp3
(matching pipeline/stage_music.py's filename-mood matching), skips moods that
already have a track, and rewrites music/LICENSES.md.
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
MUSIC_DIR = ROOT / "music"
MUSIC_DIR.mkdir(exist_ok=True)

KEY = os.getenv("YOUTUBE_API_KEY")
if not KEY:
    sys.exit("YOUTUBE_API_KEY missing in .env — set it first (used only to filter CC videos).")

MOOD_QUERIES = {
    "calm": "calm ambient piano background music",
    "tense": "tense dark cinematic background music",
    "sad": "sad emotional piano background music",
    "epic": "epic orchestral cinematic background music",
    "upbeat": "upbeat happy background music",
}


def search_videos(query: str):
    """All CC-licensed medium-length videos for the query (up to 5)."""
    params = urllib.parse.urlencode({
        "part": "snippet", "maxResults": 5, "type": "video",
        "videoLicense": "creativeCommon", "videoDuration": "medium",
        "q": query, "key": KEY,
    })
    url = f"https://www.googleapis.com/youtube/v3/search?{params}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    out = []
    for it in data.get("items", []):
        vid = it["id"]["videoId"]
        out.append((f"https://www.youtube.com/watch?v={vid}", {
            "title": it["snippet"]["title"],
            "channel": it["snippet"]["channelTitle"],
            "url": f"https://www.youtube.com/watch?v={vid}",
        }))
    return out


def download(mood: str, url: str, idx: int):
    out = MUSIC_DIR / f"{mood}_{idx}.mp3"
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio", "-x", "--audio-format", "mp3", "--audio-quality", "192K",
        "-o", str(MUSIC_DIR / f"{mood}_{idx}.%(ext)s"),
        "--no-playlist", "--quiet", "--no-warnings", url,
    ]
    subprocess.run(cmd, check=False, timeout=600)
    if out.exists() and out.stat().st_size > 0:
        return out
    if out.exists():
        out.unlink()  # cleanup partial file from a failed download
    return None


def load_entries() -> dict[str, dict]:
    """Load persisted track metadata (keyed by file name) from music/sources.json.
    Falls back to parsing the existing LICENSES.md table (pre-persistence runs)."""
    src = MUSIC_DIR / "sources.json"
    if src.exists():
        try:
            return json.loads(src.read_text(encoding="utf-8"))
        except Exception:
            pass
    entries = {}
    lic = MUSIC_DIR / "LICENSES.md"
    if lic.exists():
        for line in lic.read_text(encoding="utf-8").splitlines():
            if line.startswith("| ") and line.count("|") >= 5:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) == 5 and cells[0].endswith((".mp3", ".wav", ".m4a", ".ogg", ".flac")):
                    entries[cells[0]] = {"file": cells[0], "mood": cells[1], "title": cells[2], "channel": cells[3], "url": cells[4]}
    return entries


def save_entries(entries: dict[str, dict]):
    (MUSIC_DIR / "sources.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def write_licenses(entries: list[dict]):
    lines = [
        "# Music Licenses",
        "",
        "Semua track di folder ini diambil dari YouTube dengan filter "
        "`videoLicense=creativeCommon` (Creative Commons). WAJIB memberikan atribusi "
        "(credit) di deskripsi video final yang memakai track ini. Lisensi CC BY / BY-SA "
        "mengharuskan credit nama pembuat + link sumber — lihat tabel di bawah.",
        "",
        "Cara menambah/re-source track: `venv_311/Scripts/python.exe scripts/source_music.py`",
        "",
        "| File | Mood | Judul | Channel | Sumber |",
        "|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(f"| {e['file']} | {e['mood']} | {e['title']} | {e['channel']} | {e['url']} |")
    (MUSIC_DIR / "LICENSES.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[music] LICENSES.md updated ({len(entries)} tracks documented)")


def main():
    entries = load_entries()
    for mood, query in MOOD_QUERIES.items():
        existing = sorted(MUSIC_DIR.glob(f"{mood}_*.mp3"))
        if existing:
            print(f"[music] {mood}: sudah ada {existing[0].name} — skip")
            continue
        candidates = search_videos(query)
        if not candidates:
            print(f"[music] {mood}: tidak ada hasil — skip")
            continue
        idx = len(existing) + 1
        got = None
        for url, meta in candidates:
            print(f"[music] {mood}: coba '{meta['title']}' ({meta['channel']})")
            out = download(mood, url, idx)
            if out:
                size_mb = out.stat().st_size / 1024 / 1024
                print(f"  -> {out.name} ({size_mb:.1f} MB)")
                entries[out.name] = {"mood": mood, "file": out.name, **meta}
                got = out
                break
            print(f"  !! gagal, coba kandidat berikutnya")
        if not got:
            print(f"  !! semua kandidat gagal untuk {mood}")
    save_entries(entries)
    write_licenses(list(entries.values()))


if __name__ == "__main__":
    main()
