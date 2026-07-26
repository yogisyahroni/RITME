import os
import subprocess
import uuid
from pathlib import Path
import google.generativeai as genai
import PIL.Image

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
        print(f"Face recognition error: {e}")
        return None

def _get_image_tag(image_path: str, gemini_key: str = None, openai_key: str = None, openai_model: str = "gpt-4o") -> str:
    global _blip_processor, _blip_model
    try:
        from transformers import BlipProcessor, BlipForConditionalGeneration
        import torch
        import PIL.Image
        
        global _blip_processor, _blip_model
        device = "cuda" if torch.cuda.is_available() else "cpu"
        if _blip_processor is None or _blip_model is None:
            print("[footage_extractor] Loading local BLIP model (first time only)...")
            _blip_processor = BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
            _blip_model = BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
        if device == "cuda" and str(_blip_model.device) != "cuda:0":
            _blip_model = _blip_model.to("cuda")
                
        device = "cuda" if torch.cuda.is_available() else "cpu"
        raw_image = PIL.Image.open(image_path).convert('RGB')
        
        inputs = _blip_processor(raw_image, return_tensors="pt").to(device)
        out = _blip_model.generate(**inputs, max_new_tokens=10)
        caption = _blip_processor.decode(out[0], skip_special_tokens=True)
        
        tag = caption.strip().lower()
        # Clean up text: only keep alphanumeric and spaces, then join with underscores
        tag = "".join(c if c.isalnum() or c == ' ' else '' for c in tag)
        tag = "_".join(tag.split())
        return tag if tag else "clip"
    except Exception as e:
        print(f"BLIP error: {e}")
        return "clip"

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
        from config import GEMINI_API_KEY, OPENAI_API_KEY, OPENAI_MODEL_NAME
    except ImportError:
        GEMINI_API_KEY = None
        OPENAI_API_KEY = None
        OPENAI_MODEL_NAME = "gpt-4o"
    
    final_clips = []
    # Gather the generated files
    for i in range(1, len(filtered_scenes) + 1):
        expected_file = output_dir_path / f"{video_name}_clip_{i:03d}.mp4"
        if expected_file.exists():
            # Extract a frame at 1 second
            frame_path = output_dir_path / f"{expected_file.stem}_frame.jpg"
            cmd = [
                "ffmpeg", "-y", "-i", str(expected_file), 
                "-ss", "00:00:01", "-vframes", "1", 
                "-q:v", "2", str(frame_path)
            ]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            tag = "clip"
            if frame_path.exists():
                face_tag = _get_face_tag(str(frame_path), str(known_faces_dir))
                scene_tag = _get_image_tag(str(frame_path))
                
                if face_tag and scene_tag and scene_tag != "clip":
                    tag = f"{face_tag}_{scene_tag}"
                elif face_tag:
                    tag = face_tag
                else:
                    tag = scene_tag
                    
                frame_path.unlink(missing_ok=True)
                
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
            
    if on_progress:
        on_progress("Ekstraksi selesai!", 100)
    print(f"[footage_extractor] Extracted {len(final_clips)} clips.")

    return final_clips

