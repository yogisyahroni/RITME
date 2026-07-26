"""
Stage 4 — Footage Sourcing + Semantic Matching

LEGAL SOURCES ONLY:
  - Pexels        (free stock video, API key required)
  - Pixabay       (free stock video, API key required)
  - Wikimedia Commons (public domain / CC media, no key required)
  - Archive.org   (public domain / CC media, no key required)
  - YouTube       (ONLY videos explicitly licensed Creative Commons,
                    filtered server-side via videoLicense=creativeCommon —
                    never used to rip arbitrary copyrighted uploads)

This module intentionally does NOT support downloading arbitrary
non-CC YouTube videos or any workaround for copyright detection.
Cutting clips into short segments to evade Content ID is not a
supported or intended use of this code.

For each script segment's keywords, candidates are pulled from the
sources above, then ranked with CLIP (a joint text/image embedding
model) so the clip whose *visual content* best matches the keyword
phrase is chosen — not just whichever result an API happened to
rank first.
"""
import subprocess
from pathlib import Path
from typing import Optional

import requests

from config import PEXELS_API_KEY, PIXABAY_API_KEY, YOUTUBE_API_KEY, \
    FOOTAGE_CACHE_DIR, CLIP_MODEL_NAME, CLIP_PRETRAINED


# ---------------------------------------------------------------------------
# Source 1: Pexels
# ---------------------------------------------------------------------------
def search_pexels(query: str, per_page: int = 5) -> list[dict]:
    if not PEXELS_API_KEY:
        return []
    resp = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": query, "per_page": per_page, "orientation": "portrait"},
        timeout=20,
    )
    resp.raise_for_status()
    candidates = []
    for video in resp.json().get("videos", []):
        # pick a reasonably small file to keep downloads fast
        files = sorted(video["video_files"], key=lambda f: f.get("width", 9999))
        best = next((f for f in files if f.get("width", 0) >= 720), files[-1] if files else None)
        if best:
            candidates.append({
                "source": "pexels",
                "id": video["id"],
                "url": best["link"],
                "preview_image": video.get("image"),
                "duration": video.get("duration"),
            })
    return candidates


# ---------------------------------------------------------------------------
# Source 2: Pixabay
# ---------------------------------------------------------------------------
def search_pixabay(query: str, per_page: int = 5) -> list[dict]:
    if not PIXABAY_API_KEY:
        return []
    resp = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_API_KEY, "q": query, "per_page": per_page},
        timeout=20,
    )
    resp.raise_for_status()
    candidates = []
    for hit in resp.json().get("hits", []):
        videos = hit.get("videos", {})
        best = videos.get("medium") or videos.get("small") or videos.get("tiny")
        if best:
            candidates.append({
                "source": "pixabay",
                "id": hit["id"],
                "url": best["url"],
                "preview_image": hit.get("userImageURL"),
                "duration": hit.get("duration"),
            })
    return candidates


# ---------------------------------------------------------------------------
# Source 3: Wikimedia Commons (no key required)
# ---------------------------------------------------------------------------
def search_wikimedia(query: str, limit: int = 5) -> list[dict]:
    resp = requests.get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query", "format": "json", "list": "search",
            "srsearch": f"{query} filetype:video", "srnamespace": 6, "srlimit": limit,
        },
        timeout=20,
    )
    resp.raise_for_status()
    candidates = []
    for item in resp.json().get("query", {}).get("search", []):
        title = item["title"]  # e.g. "File:Some_video.webm"
        info_resp = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={"action": "query", "format": "json", "titles": title,
                    "prop": "imageinfo", "iiprop": "url"},
            timeout=20,
        )
        pages = info_resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            info = page.get("imageinfo", [{}])[0]
            if info.get("url"):
                candidates.append({
                    "source": "wikimedia",
                    "id": title,
                    "url": info["url"],
                    "preview_image": None,
                    "duration": None,
                })
    return candidates


# ---------------------------------------------------------------------------
# Source 4: Archive.org (no key required)
# ---------------------------------------------------------------------------
def search_archive_org(query: str, limit: int = 5) -> list[dict]:
    resp = requests.get(
        "https://archive.org/advancedsearch.php",
        params={
            "q": f'{query} AND mediatype:(movies)',
            "fl[]": "identifier",
            "rows": limit,
            "output": "json",
        },
        timeout=20,
    )
    resp.raise_for_status()
    candidates = []
    for doc in resp.json().get("response", {}).get("docs", []):
        identifier = doc["identifier"]
        candidates.append({
            "source": "archive_org",
            "id": identifier,
            "url": f"https://archive.org/download/{identifier}/{identifier}.mp4",
            "preview_image": f"https://archive.org/services/img/{identifier}",
            "duration": None,
        })
    return candidates


# ---------------------------------------------------------------------------
# Source 5: YouTube — All Videos (Fair Use)
# ---------------------------------------------------------------------------
def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    """
    Searches YouTube via the official Data API without videoLicense restrictions.
    Downloading is done separately via yt-dlp, restricted to 10 seconds for Fair Use.
    """
    if not YOUTUBE_API_KEY:
        return []
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key": YOUTUBE_API_KEY, "q": query, "part": "snippet",
            "type": "video",
            "maxResults": max_results,
        },
        timeout=20,
    )
    resp.raise_for_status()
    candidates = []
    for item in resp.json().get("items", []):
        video_id = item["id"]["videoId"]
        candidates.append({
            "source": "youtube_fairuse",
            "id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "preview_image": item["snippet"]["thumbnails"]["high"]["url"],
            "duration": None,
        })
    return candidates


def _download_youtube_fairuse(video_id_url: str, out_path: str) -> None:
    """Download a 10-second chunk (00:30-00:40) of a YouTube video via yt-dlp for Fair Use."""
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("Run: pip install yt-dlp")

    ydl_opts = {
        "format": "best[height<=1080]",
        "outtmpl": out_path,
        "quiet": True,
        "download_ranges": yt_dlp.utils.download_range_func(None, [(30, 40)]),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_id_url])


# ---------------------------------------------------------------------------
# Aggregation + download
# ---------------------------------------------------------------------------
def search_local_footage(query: str, limit: int = 5) -> list[dict]:
    """
    Scans the local outputs/extracted_footage/ directory for files whose 
    filename matches any part of the query.
    """
    local_dir = Path("outputs/extracted_footage")
    if not local_dir.exists():
        return []

    query_words = [w.lower() for w in query.split() if len(w) > 2]
    if not query_words:
        return []

    candidates = []
    for file_path in local_dir.glob("*.mp4"):
        name_lower = file_path.stem.lower()
        
        # Simple match: if any query word is in the filename
        if any(qw in name_lower for qw in query_words):
            candidates.append({
                "source": "local",
                "id": file_path.stem,
                "url": str(file_path.resolve()), # For local, url is just absolute path
                "preview_image": None,
                "duration": None, # Will be handled if needed
            })
            
    # Return at most 'limit' local candidates to avoid overwhelming CLIP
    return candidates[:limit]

def search_all_sources(query: str, per_source: int = 4) -> list[dict]:
    candidates = []
    for fn in (search_local_footage, search_pexels, search_pixabay, search_wikimedia, search_archive_org, search_youtube):
        try:
            candidates.extend(fn(query, per_source))
        except Exception as e:
            print(f"[stage4] {fn.__name__} failed for '{query}': {e}")
    return candidates


def download_candidate(candidate: dict, dest_dir: Path = FOOTAGE_CACHE_DIR) -> Optional[str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = "mp4"
    out_path = str(dest_dir / f"{candidate['source']}_{candidate['id']}.{ext}")

    if Path(out_path).exists():
        return out_path

    try:
        if candidate["source"] == "local":
            # It's already downloaded, url contains the absolute path
            return candidate["url"]
        elif candidate["source"] == "youtube_fairuse":
            _download_youtube_fairuse(candidate["url"], out_path)
        else:
            resp = requests.get(candidate["url"], timeout=60, stream=True)
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        return out_path
    except Exception as e:
        print(f"[stage4] Download failed for {candidate['source']}:{candidate['id']}: {e}")
        return None


# ---------------------------------------------------------------------------
# CLIP-based semantic matching
# ---------------------------------------------------------------------------
def _extract_sample_frame(video_path: str, out_image: str, at_second: float = 1.0) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(at_second), "-i", video_path,
         "-frames:v", "1", "-loglevel", "error", out_image],
        capture_output=True,
    )
    return result.returncode == 0 and Path(out_image).exists()


class ClipMatcher:
    """Lazily loads a CLIP model to rank downloaded clips against a text keyword.
    Auto-detects and uses a GPU (CUDA, or Apple Silicon MPS) when available,
    falling back to CPU otherwise — this is the most compute-heavy stage in
    the pipeline (one CLIP forward pass per candidate frame per keyword), so
    it's the one most worth accelerating."""

    def __init__(self, model_name: str = CLIP_MODEL_NAME, pretrained: str = CLIP_PRETRAINED, device: str | None = None):
        self.model_name = model_name
        self.pretrained = pretrained
        self._requested_device = device  # None = auto-detect
        self.device = None  # resolved on first use, see _lazy_load
        self._model = None
        self._preprocess = None
        self._tokenizer = None

    @staticmethod
    def _detect_device(torch) -> str:
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"  # Apple Silicon
        return "cpu"

    def _lazy_load(self):
        if self._model is not None:
            return
        try:
            import torch
            import open_clip
        except ImportError:
            raise RuntimeError("Run: pip install open_clip_torch torch")

        self._torch = torch
        self.device = self._requested_device or self._detect_device(torch)

        self._model, _, self._preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained
        )
        self._model = self._model.to(self.device)
        self._tokenizer = open_clip.get_tokenizer(self.model_name)
        self._model.eval()

        if self.device != "cpu":
            print(f"[stage4] CLIP running on {self.device.upper()}")
        else:
            print("[stage4] CLIP running on CPU (no GPU detected — this is the "
                  "slow path; each candidate clip takes noticeably longer to score)")

    def best_match(self, keyword: str, candidate_video_paths: list[str]) -> Optional[tuple[str, float]]:
        """Returns (best_video_path, similarity_score) or None if nothing scorable."""
        ranked = self.rank_all(keyword, candidate_video_paths)
        return ranked[0] if ranked else None

    def rank_all(self, keyword: str, candidate_video_paths: list[str]) -> list[tuple[str, float]]:
        """Returns [(video_path, score), ...] for every candidate that could be
        scored, sorted best-first. Used when a human should review multiple
        options rather than just getting the single auto-pick."""
        self._lazy_load()
        from PIL import Image

        text_tokens = self._tokenizer([keyword]).to(self.device)
        with self._torch.no_grad():
            text_features = self._model.encode_text(text_tokens)
            text_features /= text_features.norm(dim=-1, keepdim=True)

        scored = []
        for video_path in candidate_video_paths:
            frame_path = video_path + ".sample.jpg"
            if not _extract_sample_frame(video_path, frame_path):
                continue
            try:
                image = self._preprocess(Image.open(frame_path)).unsqueeze(0).to(self.device)
                with self._torch.no_grad():
                    image_features = self._model.encode_image(image)
                    image_features /= image_features.norm(dim=-1, keepdim=True)
                    score = (image_features @ text_features.T).item()
                scored.append((video_path, score))
            except Exception as e:
                print(f"[stage4] CLIP scoring failed for {video_path}: {e}")

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


def get_ranked_candidates_for_segment(keywords: list[str], matcher: ClipMatcher,
                                        per_source: int = 3, top_n: int = 4) -> list[dict]:
    """
    Like find_footage_for_segment, but returns up to top_n ranked candidates
    (not just the single auto-pick) so a human can review and override the
    choice — this is what powers the web UI's footage matching board.
    Each dict: {"video_path", "keyword_used", "score", "source"}, best first.
    """
    all_scored = []
    seen_paths = set()

    for keyword in keywords:
        # Prioritize local footage first
        local_candidates = search_local_footage(keyword, limit=per_source * 2)
        local_downloaded = []
        for c in local_candidates:
            path = download_candidate(c)
            if path and path not in seen_paths:
                local_downloaded.append((path, c))
                seen_paths.add(path)

        local_good = False
        if local_downloaded:
            ranked = matcher.rank_all(keyword, [p for p, _ in local_downloaded])
            for path, score in ranked:
                source_meta = next(c for p, c in local_downloaded if p == path)
                all_scored.append({
                    "video_path": path,
                    "keyword_used": keyword,
                    "score": round(score, 4),
                    "source": source_meta["source"],
                    "url": source_meta.get("url"),
                    "preview_image": source_meta.get("preview_image"),
                })
                # If CLIP score is good enough (>0.21 is typically a solid match), we skip internet search for this keyword
                if score >= 0.21:
                    local_good = True
        
        # If we didn't find any good local footage, fall back to internet sources
        if not local_good:
            internet_fns = (search_pexels, search_pixabay, search_wikimedia, search_archive_org, search_youtube)
            candidates = []
            for fn in internet_fns:
                try:
                    candidates.extend(fn(keyword, per_source))
                except Exception as e:
                    print(f"[stage4] {fn.__name__} failed for '{keyword}': {e}")
                    
            downloaded = []
            for c in candidates:
                path = download_candidate(c)
                if path and path not in seen_paths:
                    downloaded.append((path, c))
                    seen_paths.add(path)

            if not downloaded:
                continue

            ranked = matcher.rank_all(keyword, [p for p, _ in downloaded])
            for path, score in ranked:
                source_meta = next(c for p, c in downloaded if p == path)
                all_scored.append({
                    "video_path": path,
                    "keyword_used": keyword,
                    "score": round(score, 4),
                    "source": source_meta["source"],
                    "url": source_meta.get("url"),
                    "preview_image": source_meta.get("preview_image"),
                })

    all_scored.sort(key=lambda c: c["score"], reverse=True)
    return all_scored[:top_n]


def find_footage_for_segment(keywords: list[str], matcher: ClipMatcher, per_source: int = 3) -> Optional[dict]:
    """
    CLI-friendly convenience wrapper: returns just the single best match.
    (The web UI uses get_ranked_candidates_for_segment instead, so a human
    can review/override the pick.)
    """
    ranked = get_ranked_candidates_for_segment(keywords, matcher, per_source=per_source, top_n=1)
    if not ranked:
        print(f"[stage4] No footage found for any of: {keywords}")
        return None
    return ranked[0]


def match_all_segments(segments: list[dict], on_progress=None, top_n: int = 4) -> dict[int, list[dict]]:
    """
    Runs get_ranked_candidates_for_segment for every script segment.
    on_progress(index, total, segment) is called after each segment finishes,
    so a caller (e.g. the web API's job manager) can report real progress
    instead of a fake animated bar.
    Returns {segment_index: [ranked candidate dicts, best first, possibly empty]}
    """
    matcher = get_clip_matcher()
    results = {}
    total = len(segments)
    for i, seg in enumerate(segments):
        results[i] = get_ranked_candidates_for_segment(seg["keywords"], matcher, per_source=3, top_n=top_n)
        if on_progress:
            on_progress(i + 1, total, seg)
    return results

# Module-level singleton for CLIP matcher (avoids reloading model on every job)
_clip_matcher_singleton = None

def get_clip_matcher() -> ClipMatcher:
    """Return a singleton ClipMatcher instance — model loads once, reused across jobs."""
    global _clip_matcher_singleton
    if _clip_matcher_singleton is None:
        _clip_matcher_singleton = ClipMatcher()
    return _clip_matcher_singleton

