import os
import subprocess
import uuid
from pathlib import Path

from scenedetect import open_video, SceneManager
from scenedetect.detectors import ContentDetector
from scenedetect.video_splitter import split_video_ffmpeg

_blip_processor = None
_blip_model = None
_deepface_loaded = False

def _get_face_tag(image_path: str, known_faces_dir: str) -> str:
    global _deepface_loaded
    kf_path = Path(known_faces_dir)
    if not kf_path.exists() or not any(kf_path.iterdir()):
        return None
        
    try:
        if not _deepface_loaded:
            print("[footage_extractor] Initializing DeepFace for face recognition...")
            _deepface_loaded = True
            
        from deepface import DeepFace
        
        # enforce_detection=False so it doesn't throw exception if no face is found
        # We use VGG-Face as it's the default and fast enough
        dfs = DeepFace.find(
            img_path=image_path, 
            db_path=str(kf_path), 
            enforce_detection=False,
            silent=True
        )
        
        if len(dfs) > 0 and len(dfs[0]) > 0:
            # dfs[0] is a pandas DataFrame of matches
            matched_file = dfs[0].iloc[0]['identity']
            # Extract just the filename without extension (e.g. "Jokowi" from "Jokowi.jpg")
            name = Path(matched_file).stem
            # Clean up name (e.g. "gibran1" -> "gibran")
            name = "".join(c if c.isalnum() else '_' for c in name).strip('_').lower()
            name = name.rstrip('0123456789_')
            return name
            
        return None
    except Exception as e:
        # Optional dep (deepface/tensorflow) — warn sekali, jangan spam tiap clip
        global _FACE_WARNED
        if not _FACE_WARNED:
            print(f"[footage_extractor] Face recognition unavailable (optional): {e}")
            _FACE_WARNED = True
        return None


_FACE_WARNED = False

def _get_image_tag(image_path: str, gemini_key: str = None, openai_key: str = None, openai_model: str = "gpt-4o-mini", caption_model: str = "auto") -> str:
    """
    Fase 1B.2: caption a frame. Priority:
      auto  -> cloud VLM (Gemini free tier first, then OpenAI), BLIP fallback
      gemini-> Gemini vision (errors if key missing)
      openai-> OpenAI vision (errors if key missing)
      blip  -> local BLIP only
    Returns a short underscore-joined tag, or "clip" on any failure (never
    crashes the extraction loop).
    """
    # Cloud VLM paths first (they produce the descriptive captions 1B.2 wants).
    if caption_model in ("auto", "gemini") and (caption_model == "gemini" or gemini_key):
        if not gemini_key:
            print("[footage_extractor] CAPTION_MODEL=gemini but GEMINI_API_KEY missing — falling back.")
        else:
            try:
                tag = _gemini_caption(image_path, gemini_key)
                if tag:
                    return tag
            except Exception as e:
                print(f"[footage_extractor] Gemini caption error: {e}")
            if caption_model == "gemini":
                return "clip"

    if caption_model in ("auto", "openai") and (caption_model == "openai" or openai_key):
        if not openai_key:
            if caption_model == "openai":
                print("[footage_extractor] CAPTION_MODEL=openai but OPENAI_API_KEY missing — falling back.")
        else:
            try:
                tag = _openai_caption(image_path, openai_key, openai_model)
                if tag:
                    return tag
            except Exception as e:
                print(f"[footage_extractor] OpenAI caption error: {e}")
            if caption_model == "openai":
                return "clip"

    # Local BLIP (offline fallback / explicit "blip" mode).
    try:
        return _blip_caption(image_path)
    except Exception as e:
        print(f"BLIP error: {e}")
        return "clip"


def _sanitize_tag(raw: str, max_len: int = 40) -> str:
    """Strip a raw caption into an underscore-joined, filesystem-safe tag."""
    raw = raw.strip().lower()
    raw = raw.replace(",", " ").replace(";", " ").replace("|", " ")
    tag = "_".join(raw.split())
    tag = "".join(c if c.isalnum() or c == '_' else '' for c in tag).strip('_')
    if len(tag) > max_len:
        cut = tag[:max_len].rstrip('_')
        tag = cut[:cut.rfind('_')] if '_' in cut else cut
    return tag


def _gemini_caption(image_path: str, api_key: str) -> str:
    """Descriptive caption via Gemini vision (REST, free tier friendly).

    Uses the v1beta generateContent REST endpoint directly so we don't depend
    on either the deprecated google.generativeai SDK or the newer google-genai
    SDK (both have import quirks in this environment).
    """
    from config import CAPTION_VLM_PROMPT
    import base64
    try:
        import requests
    except ImportError:
        return ""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.0-flash:generateContent"
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": CAPTION_VLM_PROMPT},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            ],
        }],
    }
    resp = requests.post(url, params={"key": api_key}, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])
    return _sanitize_tag(parts[0].get("text", ""))


def _openai_caption(image_path: str, api_key: str, model: str) -> str:
    """Descriptive caption via OpenAI vision (gpt-4o-mini default)."""
    from config import CAPTION_VLM_PROMPT
    import base64
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": CAPTION_VLM_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
        max_tokens=60,
    )
    return _sanitize_tag(resp.choices[0].message.content or "")


def _blip_caption(image_path: str) -> str:
    """Local BLIP caption (offline fallback)."""
    global _blip_processor, _blip_model
    from transformers import BlipProcessor, BlipForConditionalGeneration
    import torch
    from PIL import Image
    
    global _blip_processor, _blip_model
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if _blip_processor is None or _blip_model is None:
        print("[footage_extractor] Loading local BLIP model (first time only)...")
        _blip_processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
        _blip_model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
    if device == "cuda" and str(_blip_model.device) != "cuda:0":
        _blip_model = _blip_model.to("cuda")
            
    device = "cuda" if torch.cuda.is_available() else "cpu"
    raw_image = Image.open(image_path).convert('RGB')
    
    inputs = _blip_processor(raw_image, return_tensors="pt").to(device)
    out = _blip_model.generate(**inputs, max_new_tokens=10)
    caption = _blip_processor.decode(out[0], skip_special_tokens=True)
    
    tag = caption.strip().lower()
    # Clean up text: only keep alphanumeric and spaces, then join with underscores
    tag = "".join(c if c.isalnum() or c == ' ' else '' for c in tag)
    tag = "_".join(tag.split())
    return tag if tag else "clip"


def _get_video_duration(video_path: str) -> float:
    """Probe clip duration in seconds; 0.0 if unreadable."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=15,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _extract_sample_frames(video_path: str, n_frames: int = 3) -> list[str]:
    """
    Fase 1B.1: sample N frames spread across the clip (20%/50%/80% of duration)
    instead of a single frame at second 1.0. Returns list of frame file paths;
    caller is responsible for deleting them.
    """
    duration = _get_video_duration(video_path)
    if duration <= 0:
        duration = 10.0  # unknown — fall back to absolute offsets

    offsets = [duration * frac for frac in (0.2, 0.5, 0.8)][:max(n_frames, 1)]
    frames = []
    for i, at in enumerate(offsets):
        frame_path = f"{video_path}.sample_{i}.jpg"
        cmd = ["ffmpeg", "-y", "-i", video_path, "-ss", str(round(at, 3)),
               "-vframes", "1", "-q:v", "2", frame_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if Path(frame_path).exists():
            frames.append(frame_path)
    return frames


def _combine_tags(face_tag: str | None, scene_tags: list[str], max_len: int = 60) -> str:
    """Merge face + multi-frame scene tags into one filesystem-safe filename
    part (alphanumeric + underscore only, bounded length)."""
    parts = []
    if face_tag:
        parts.append(face_tag)
    seen = set()
    for t in scene_tags:
        t = t.strip().lower()
        if t and t != "clip" and t not in seen:
            parts.append(t)
            seen.add(t)
    tag = "_".join(parts) if parts else "clip"
    # sanitize + bound length, keep whole words at the cut
    tag = "".join(c if c.isalnum() or c == '_' else '' for c in tag).strip('_')
    if len(tag) > max_len:
        cut = tag[:max_len].rstrip('_')
        tag = cut[:cut.rfind('_')] if '_' in cut else cut
    return tag or "clip"

def _precompute_clip_embedding(clip_path: str) -> bool:
    """
    Fase 1B.4: compute the CLIP image embedding for a clip and persist it as
    a sidecar JSON (<clip>.emb.json) next to the video. Stage 4's ClipMatcher
    loads the sidecar instead of re-extracting frames + re-running CLIP.
    Best-effort: any failure (CLIP not installed, no frames, timeout) just
    skips — matching falls back to on-the-fly scoring.
    """
    try:
        from config import PRECOMPUTE_CLIP_ON_EXTRACT, CLIP_SAMPLE_FRAMES
    except ImportError:
        PRECOMPUTE_CLIP_ON_EXTRACT = True
        CLIP_SAMPLE_FRAMES = 3
    if not PRECOMPUTE_CLIP_ON_EXTRACT:
        return False

    sidecar = Path(clip_path + ".emb.json")
    if sidecar.exists():
        return True  # already done

    try:
        from pipeline.stage4_footage import get_clip_matcher
        matcher = get_clip_matcher()
        emb = matcher.embed_video(clip_path, n_frames=CLIP_SAMPLE_FRAMES)
        if emb is None:
            return False
        import json as _json
        from datetime import datetime, timezone
        sidecar.write_text(_json.dumps({
            "clip_path": str(Path(clip_path).resolve()),
            "embedding": [round(float(x), 6) for x in emb],
            "model": matcher.model_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }), encoding="utf-8")
        return True
    except Exception as e:
        print(f"[footage_extractor] CLIP embedding skipped for {Path(clip_path).name}: {e}")
        return False


def extract_clips(video_path: str, output_dir: str, threshold: float = 27.0, min_duration_sec: float = 2.0, base_name: str = None, on_progress=None, topic: str = "") -> list[str]:
    """
    Extracts individual clips from a long video by detecting scene changes.
    Uses ffmpeg underneath to avoid re-encoding and to process sequentially.
    """
    video_path = str(Path(video_path).resolve())
    output_dir_path = Path(output_dir).resolve()
    output_dir_path.mkdir(parents=True, exist_ok=True)
    
    # Ensure known_faces directory exists
    known_faces_dir = Path(__file__).resolve().parent.parent / "known_faces"
    known_faces_dir.mkdir(parents=True, exist_ok=True)
    
    video_name = base_name if base_name else Path(video_path).stem

    print(f"[footage_extractor] Detecting scenes in {video_name}...")
    
    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))
    scene_manager.detect_scenes(video, show_progress=False)
    
    scene_list = scene_manager.get_scene_list()
    
    # Filter scenes by minimum duration
    filtered_scenes = []
    for start, end in scene_list:
        duration = end.get_seconds() - start.get_seconds()
        if duration >= min_duration_sec:
            filtered_scenes.append((start, end))

    print(f"[footage_extractor] Found {len(scene_list)} total scenes, {len(filtered_scenes)} after filtering (>= {min_duration_sec}s).")
    
    if not filtered_scenes:
        return []

    if on_progress:
        on_progress("Memotong video (ffmpeg)...", 75)
    
    print(f"[footage_extractor] Splitting video into {len(filtered_scenes)} clips...")
    
    split_video_ffmpeg(
        video_path,
        filtered_scenes,
        output_file_template=str(output_dir_path / f"{video_name}_clip_$SCENE_NUMBER.mp4"),
        arg_override="-map 0:v:0 -c:v libx264 -preset veryfast -crf 22 -an",
        suppress_output=True
    )
    
    # Auto-tagging process
    if on_progress:
        on_progress("Menganalisis dan memberi nama klip (AI)...", 85)
        
    import sys
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    try:
        from config import GEMINI_API_KEY, OPENAI_API_KEY, OPENAI_MODEL_NAME, CAPTION_MODEL
    except ImportError:
        GEMINI_API_KEY = None
        OPENAI_API_KEY = None
        OPENAI_MODEL_NAME = "gpt-4o-mini"
        CAPTION_MODEL = "auto"
    
    final_clips = []
    # Gather the generated files
    for i in range(1, len(filtered_scenes) + 1):
        expected_file = output_dir_path / f"{video_name}_clip_{i:03d}.mp4"
        if expected_file.exists():
            # Fase 1B.1: sample 20%/50%/80% of the clip instead of 1 frame at 1s,
            # so the tag reflects the whole clip, not just its first moment.
            frame_paths = _extract_sample_frames(str(expected_file), n_frames=3)

            face_tag = None
            scene_tags = []
            for frame_path in frame_paths:
                if face_tag is None:
                    face_tag = _get_face_tag(frame_path, str(known_faces_dir))
                # Fase 1B.2: VLM-first captioning (auto -> Gemini/OpenAI -> BLIP).
                scene_tag = _get_image_tag(
                    frame_path, gemini_key=GEMINI_API_KEY,
                    openai_key=OPENAI_API_KEY, openai_model=OPENAI_MODEL_NAME,
                    caption_model=CAPTION_MODEL,
                )
                scene_tags.append(scene_tag)
                Path(frame_path).unlink(missing_ok=True)

            tag = _combine_tags(face_tag, scene_tags)

            if topic:
                clean_topic = "".join(c if c.isalnum() else '_' for c in topic.replace(' ', '_')).strip('_').lower()
                tag = f"{clean_topic}_{tag}"

            unique_id = uuid.uuid4().hex[:6]
            new_name = f"{tag}_{unique_id}.mp4"
            new_path = output_dir_path / new_name
            
            while new_path.exists():
                unique_id = uuid.uuid4().hex[:6]
                new_name = f"{tag}_{unique_id}.mp4"
                new_path = output_dir_path / new_name
                
            expected_file.rename(new_path)
            final_clips.append(str(new_path))
            # Fase 1B.4: precompute the CLIP embedding sidecar now, while the
            # sampled frames are still hot, so Stage 4 matching is cheap later.
            _precompute_clip_embedding(str(new_path))
            
    if on_progress:
        on_progress("Ekstraksi selesai!", 100)
    print(f"[footage_extractor] Extracted {len(final_clips)} clips.")

    return final_clips

