"""
Stage 3 — Narration Audio + Word-Level Timestamps

Turns the script from Stage 2 into an audio narration track, then
transcribes that audio back with Whisper to recover precise
word-level timestamps. This is the sync backbone: Stage 5 uses these
timestamps to know exactly which second a keyword was spoken, so the
matching footage from Stage 4 can be cut in at the right moment.

Why transcribe audio we just generated instead of using the TTS
engine's own timing? TTS engines rarely expose reliable per-word
timing, especially free/offline ones. Whisper's word_timestamps is
consistent regardless of which TTS engine produced the audio.
"""
import json
import os
from pathlib import Path

from config import TTS_PROVIDER, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, \
    WHISPER_MODEL_SIZE, AUDIO_CACHE_DIR, require


def _detect_language(text: str) -> str:
    """Simple heuristic to detect Indonesian vs English based on common stop words."""
    id_keywords = {"dan", "di", "yang", "ke", "dari", "ini", "itu", "untuk", "pada", "dengan"}
    en_keywords = {"and", "in", "the", "to", "of", "this", "that", "for", "on", "with"}
    
    words = set(text.lower().split())
    id_score = len(words.intersection(id_keywords))
    en_score = len(words.intersection(en_keywords))
    
    return "id" if id_score >= en_score else "en"

def _evaluate_audio_quality(audio_path: str) -> float:
    """Evaluates generated audio quality using UTMOS (Mean Opinion Score). Returns 1.0 to 5.0"""
    try:
        import torch
        import torchaudio
        from utmos_pytorch import UTMOSScoreTorch
    except ImportError:
        print("[stage3] Warning: utmos-pytorch or torchaudio not installed, returning default score 4.0")
        return 4.0
    
    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        utmos = UTMOSScoreTorch(device=device)
        wav, sr = torchaudio.load(audio_path)
        
        # UTMOS expects 16kHz mono audio
        if sr != 16000:
            transform = torchaudio.transforms.Resample(orig_freq=sr, new_freq=16000)
            wav = transform(wav)
            
        wav = wav.to(device)
        score = utmos.score(wav).item()
        return float(score)
    except Exception as e:
        print(f"[stage3] Error during UTMOS evaluation: {e}")
        return 4.0



def synthesize_narration(script_segments: list[dict], out_path: str | None = None, provider: str | None = None) -> str:
    """
    Concatenate all segment texts into one narration script and synthesize
    it to a single audio file. Returns the path to the generated audio.
    `provider` overrides the .env TTS_PROVIDER default (used by the web UI's
    TTS toggle) — pass "pyttsx3" or "elevenlabs" explicitly, or leave None
    to use whatever's configured in .env.
    """
    # Ensure each segment ends with proper punctuation so TTS doesn't run-on
    cleaned_segments = []
    for seg in script_segments:
        t = seg["text"].strip()
        if t and not t.endswith(('.', '!', '?')):
            t += "."
        cleaned_segments.append(t)
    
    # Join with double newline to form clear paragraphs for natural TTS pacing
    full_text = "\n\n".join(cleaned_segments)
    out_path = out_path or str(AUDIO_CACHE_DIR / "narration.wav")

    provider = (provider or TTS_PROVIDER).lower()
    lang = _detect_language(full_text)
    _synthesize_text(full_text, out_path, provider, lang)

    print(f"[stage3] Narration audio saved to {out_path}")
    return out_path


def _synthesize_text(text: str, out_path: str, provider: str, lang: str) -> None:
    """Route a single text through the configured TTS provider. Shared by the
    full-narration and per-segment paths (Fase 3.0) so both behave identically."""
    if provider == "pyttsx3":
        _synthesize_pyttsx3(text, out_path)
    elif provider == "elevenlabs":
        _synthesize_elevenlabs(text, out_path)
    elif provider == "xtts":
        # XTTS v2 does not support Bahasa Indonesia — fall back to English with a warning
        if lang == "id":
            print("[stage3] WARNING: XTTS v2 tidak mendukung Bahasa Indonesia. Menggunakan English sebagai fallback.")
        _synthesize_xtts(text, out_path, language="en")
    elif provider == "f5tts":
        max_retries = 3
        best_score = 0.0
        best_audio_path = None
        import shutil

        for attempt in range(max_retries):
            temp_out = out_path.replace(".wav", f"_attempt_{attempt}.wav")

            # Vary nfe_step slightly per attempt to ensure different random seeds/outputs and break determinism safely
            # Attempt 0: 32 (Default, high quality)
            # Attempt 1: 24 (Faster solver path, slightly different intonation)
            # Attempt 2: 40 (Slower solver path, detailed intonation)
            nfe_val = 32 if attempt == 0 else (24 if attempt == 1 else 40)

            if lang == "id":
                print(f"[stage3] [Attempt {attempt+1}/{max_retries}] Detected Indonesian text, routing to F5-TTS...")
                _synthesize_f5tts(text, temp_out, lang="id", nfe_step=nfe_val)
            else:
                print(f"[stage3] [Attempt {attempt+1}/{max_retries}] Detected English text, routing to F5-TTS...")
                _synthesize_f5tts(text, temp_out, lang="en", nfe_step=nfe_val)

            print(f"[stage3] Menilai kualitas audio dengan UTMOS...")
            score = _evaluate_audio_quality(temp_out)

            if score > best_score:
                best_score = score
                best_audio_path = temp_out

            if score >= 3.8:
                print(f"[stage3] LULUS: Kualitas Audio (MOS) = {score:.2f} / 5.0")
                break
            else:
                print(f"[stage3] GAGAL: Kualitas Audio (MOS) = {score:.2f} / 5.0 (Syarat kelulusan >= 3.8)")
                if attempt < max_retries - 1:
                    print(f"[stage3] Auto-Regenerate audio...")

        # Copy the best attempt to the final out_path
        if best_audio_path and os.path.exists(best_audio_path):
            shutil.copy2(best_audio_path, out_path)
            print(f"[stage3] Selesai! Menggunakan kandidat audio terbaik dengan skor MOS {best_score:.2f}")

            # Clean up temp files
            for attempt in range(max_retries):
                temp_file = out_path.replace(".wav", f"_attempt_{attempt}.wav")
                if os.path.exists(temp_file):
                    os.remove(temp_file)
    elif provider == "omnivoice":
        # Force cache clearing before loading OmniVoice to prevent OOM
        import torch
        if torch.cuda.is_available():
            print("[stage3] Clearing CUDA cache before loading OmniVoice...")
            torch.cuda.empty_cache()

        print("[stage3] Routing to OmniVoice...")
        _synthesize_omnivoice(text, out_path, lang)
    else:
        raise RuntimeError(f"Unknown TTS provider: {provider}")


def audio_duration(path: str) -> float:
    """Seconds of an audio file (torchaudio first, ffprobe fallback)."""
    try:
        import torchaudio
        info = torchaudio.info(path)
        return float(info.num_frames) / float(info.sample_rate)
    except Exception:
        pass
    try:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def synthesize_narration_per_segment(script_segments: list[dict], out_dir: str | None = None, provider: str | None = None) -> tuple[list[str], list[float]]:
    """
    Fase 3.0: synthesize each segment's text into its own audio file so the
    timeline can move/trim/regenerate segments independently. Returns
    (audio_paths, durations_sec) aligned to script_segments order. A segment
    with empty text yields ("", 0.0) at its position.
    """
    out_dir = Path(out_dir or (AUDIO_CACHE_DIR / "segments"))
    out_dir.mkdir(parents=True, exist_ok=True)
    provider = (provider or TTS_PROVIDER).lower()
    full_text = "\n\n".join(s.get("text", "").strip() for s in script_segments)
    lang = _detect_language(full_text)

    paths, durations = [], []
    for i, seg in enumerate(script_segments):
        t = (seg.get("text") or "").strip()
        if not t:
            paths.append("")
            durations.append(0.0)
            continue
        if not t.endswith(('.', '!', '?')):
            t += "."
        out_path = str(out_dir / f"segment_{i + 1:03d}.wav")
        _synthesize_text(t, out_path, provider, lang)
        d = audio_duration(out_path)
        paths.append(out_path)
        durations.append(d)
        print(f"[stage3] Segment {i + 1}: {d:.2f}s -> {out_path}")
    return paths, durations


def concat_audio_files(paths: list[str], out_path: str) -> str:
    """Concatenate WAV files into one continuous track via the ffmpeg concat
    demuxer (lossless for PCM). Used by Fase 3.0 to rebuild the full narration
    track from per-segment audio. Raises RuntimeError on failure."""
    import subprocess
    list_file = out_path + ".txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in paths:
            if p:
                f.write(f"file '{Path(p).resolve().as_posix()}'\n")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
             "-c", "copy", out_path],
            check=True, capture_output=True, text=True, timeout=120)
        Path(list_file).unlink(missing_ok=True)
        return out_path
    except Exception as e:
        Path(list_file).unlink(missing_ok=True)
        raise RuntimeError(f"concat_audio_files failed: {e}") from e


def _synthesize_pyttsx3(text: str, out_path: str) -> None:
    """Free, fully offline TTS. Quality is robotic — good for drafts/testing."""
    try:
        import pyttsx3
    except ImportError:
        raise RuntimeError("Run: pip install pyttsx3")

    engine = pyttsx3.init()
    engine.save_to_file(text, out_path)
    engine.runAndWait()


def _synthesize_elevenlabs(text: str, out_path: str) -> None:
    """Paid, natural-sounding TTS via ElevenLabs API."""
    require(ELEVENLABS_API_KEY, "ELEVENLABS_API_KEY",
            "Get one at https://elevenlabs.io/app/settings/api-keys")
    import requests

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {"text": text, "model_id": "eleven_multilingual_v2"}

    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    Path(out_path).write_bytes(resp.content)


def _synthesize_xtts(text: str, out_path: str, language: str = "es") -> None:
    """Local voice cloning via Coqui XTTS v2."""
    import os
    import torch
    import torchaudio

    # Monkey-patch torchaudio.load to use soundfile directly and bypass torchcodec/FFmpeg issues on Windows
    def _patched_torchaudio_load(filepath, *args, **kwargs):
        import soundfile as sf
        import torch
        data, sr = sf.read(filepath, dtype='float32')
        data = data.transpose() if data.ndim > 1 else data.reshape(1, -1)
        return torch.from_numpy(data), sr
    torchaudio.load = _patched_torchaudio_load

    # Monkey-patch torch.load to bypass PyTorch 2.6 weights_only=True restriction for old Coqui TTS checkpoints
    _original_torch_load = torch.load
    def _patched_torch_load(*args, **kwargs):
        if "weights_only" not in kwargs:
            kwargs["weights_only"] = False
        return _original_torch_load(*args, **kwargs)
    torch.load = _patched_torch_load

    try:
        from TTS.api import TTS
    except ImportError:
        raise RuntimeError("Run: pip install TTS")
    
    # Require a reference audio file from the user
    speaker_wav = "suara_gua.wav"
    if not os.path.exists(speaker_wav):
        raise FileNotFoundError(f"Untuk clone suara XTTS, siapkan file audio '{speaker_wav}' di folder RITME (folder utama).")
        
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[stage3] Loading XTTS v2 model on {device.upper()} (this may download ~2GB on first run)...")
    
    # Bypass Coqui TOS prompt which blocks headless server execution
    os.environ["COQUI_TOS_AGREED"] = "1"
    
    # Load model and synthesize
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    tts.tts_to_file(
        text=text, 
        speaker_wav=speaker_wav, 
        language=language, 
        file_path=out_path
    )


def _synthesize_f5tts(text: str, out_path: str, lang: str = "en", nfe_step: int = 32) -> None:
    """Local voice cloning via F5-TTS (Indonesian fine-tune or English Base)."""
    import os
    import torch
    import soundfile as sf
    from huggingface_hub import hf_hub_download

    try:
        from f5_tts.infer.utils_infer import infer_process, load_model, load_vocoder
        from f5_tts.model import DiT
    except ImportError:
        raise RuntimeError("Run: pip install f5-tts")

    if lang == "id":
        repo_id = "PapaRazi/Ijazah_Palsu_V2"
        ckpt_path = hf_hub_download(repo_id=repo_id, filename="model_last_v2_rev1.safetensors")
        vocab_file = hf_hub_download(repo_id=repo_id, filename="vocab.txt")
        speaker_wav = "suara_gua.wav"
    else:
        repo_id = "SWivid/F5-TTS"
        ckpt_path = hf_hub_download(repo_id=repo_id, subfolder="F5TTS_Base", filename="model_1200000.safetensors")
        vocab_file = hf_hub_download(repo_id=repo_id, subfolder="F5TTS_Base", filename="vocab.txt")
        # Use default English reference audio from f5_tts package for native accent
        from importlib.resources import files
        speaker_wav = str(files("f5_tts").joinpath("infer/examples/basic/basic_ref_en.wav"))

    if not os.path.exists(speaker_wav):
        if lang == "id":
            raise FileNotFoundError(f"Untuk clone suara F5-TTS, siapkan file audio 'suara_gua.wav' di folder RITME (folder utama).")
        else:
            raise FileNotFoundError(f"English reference audio not found at {speaker_wav}")
            
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[stage3] Loading F5-TTS model on {device.upper()}...")
    model_cfg = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)
    model = load_model(DiT, model_cfg, ckpt_path, vocab_file=vocab_file, device=device)
    vocoder = load_vocoder(is_local=False)
    
    # Trim reference audio to maximum 12 seconds
    audio_data, sr = sf.read(speaker_wav)
    max_samples = 12 * sr
    if len(audio_data) > max_samples:
        print(f"[stage3] Trimming reference audio from {len(audio_data)/sr:.1f}s to 12s for F5-TTS...")
        audio_data = audio_data[:max_samples]
        
    temp_ref = "temp_ref_audio.wav"
    sf.write(temp_ref, audio_data, sr)
    
    if lang == "en":
        ref_text = "Some call me nature, others call me mother nature."
        print(f"[stage3] Using built-in English reference text: '{ref_text}'")
    else:
        # Transcribe the reference audio
        try:
            w_model = _get_whisper_model("base")
            w_segments, _ = w_model.transcribe(temp_ref, word_timestamps=False)
            ref_text = " ".join([s.text for s in w_segments]).strip()
            
            if ref_text and not ref_text.endswith((".", "!", "?")):
                ref_text += "."
                
            print(f"[stage3] Whisper transcribed ref_text: '{ref_text}'")
            if not ref_text:
                ref_text = "Ini adalah contoh suara saya."
        except Exception as e:
            print(f"[stage3] Whisper transcribe error: {e}")
            ref_text = "Ini adalah contoh suara saya untuk referensi F5-TTS."
    
    import re
    import numpy as np

    print(f"[stage3] Chunking text into larger blocks to prevent stuttering...")
    # Group text into larger chunks (~500 chars) so F5-TTS maintains natural pacing and intonation
    raw_sentences = [c.strip() for c in re.split(r'(?<=[.!?\n])\s+', text) if c.strip()]
    chunks = []
    current_chunk = ""
    for sentence in raw_sentences:
        if len(current_chunk) + len(sentence) > 500:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence
        else:
            current_chunk += " " + sentence if current_chunk else sentence
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    all_audio = []
    out_sr = 24000  # Default F5-TTS sr fallback
    
    for i, chunk_text in enumerate(chunks):
        print(f"[stage3] F5-TTS synthesizing chunk {i+1}/{len(chunks)}: '{chunk_text[:50]}...'")
        audio_out, chunk_sr, _ = infer_process(
            temp_ref, 
            ref_text, 
            chunk_text, 
            model, 
            vocoder,
            device=device,
            cross_fade_duration=0.15,
            nfe_step=nfe_step
        )
        out_sr = chunk_sr
        all_audio.append(audio_out)
        
        # Add 0.4 seconds of silence between chunks to give breathing room
        silence = np.zeros(int(0.4 * out_sr), dtype=np.float32)
        all_audio.append(silence)
    
    if os.path.exists(temp_ref):
        os.remove(temp_ref)
        
    final_audio = np.concatenate(all_audio) if all_audio else np.zeros(100, dtype=np.float32)
    
    # Normalize volume to make it loud (lantang)
    max_amp = np.max(np.abs(final_audio))
    if max_amp > 0:
        final_audio = (final_audio / max_amp) * 0.95
        
    sf.write(out_path, final_audio, out_sr)


def _get_whisper_model(model_size: str = WHISPER_MODEL_SIZE):
    """Module-level singleton — WhisperModel load ~2-4s; transcribe_segment_audio
    would otherwise reload it once per segment (8 segments = 8 loads)."""
    import torch
    global _WHISPER_MODEL_CACHE, _WHISPER_MODEL_KEY
    if torch.cuda.is_available():
        device, compute_type = "cuda", "float16"
    else:
        device, compute_type = "cpu", "int8"
    key = f"{model_size}:{device}:{compute_type}"
    if _WHISPER_MODEL_CACHE is None or _WHISPER_MODEL_KEY != key:
        from faster_whisper import WhisperModel
        _WHISPER_MODEL_CACHE = WhisperModel(model_size, device=device, compute_type=compute_type)
        _WHISPER_MODEL_KEY = key
        print(f"[stage3] Whisper loaded ({model_size}, {device.upper()}/{compute_type})")
    return _WHISPER_MODEL_CACHE


_WHISPER_MODEL_CACHE = None
_WHISPER_MODEL_KEY = None


def transcribe_with_timestamps(audio_path: str, model_size: str = WHISPER_MODEL_SIZE) -> list[dict]:
    """
    Transcribe narration audio and return word-level timestamps:
    [{"word": "...", "start": 1.23, "end": 1.45}, ...]
    """
    try:
        import torch
    except ImportError:
        raise RuntimeError("Run: pip install faster-whisper torch")

    model = _get_whisper_model(model_size)
    segments, _info = model.transcribe(audio_path, word_timestamps=True)

    words = []
    for seg in segments:
        for w in seg.words:
            words.append({
                "word": w.word.strip(),
                "start": round(w.start, 3),
                "end": round(w.end, 3),
            })
    return words


def transcribe_segment_audio(segments_with_audio: list[dict], model_size: str = WHISPER_MODEL_SIZE) -> list[dict]:
    """
    Fase 3.4: after timeline edits (text change, audio swap, reorder), re-run
    Whisper per segment audio and rebuild timed segments with fresh per-word
    timestamps. Input: [{text, audio_path, keywords?}]. Output keeps input
    order; start/end accumulate from each segment's own audio duration, so
    subtitle timing always matches the edited narration.
    """
    timed = []
    cursor = 0.0
    for seg in segments_with_audio:
        text = (seg.get("text") or "").strip()
        audio = seg.get("audio_path") or ""
        if audio and os.path.exists(audio):
            words_abs = transcribe_with_timestamps(audio, model_size)
            words = [
                {"word": w["word"], "start": round(cursor + w["start"], 3),
                 "end": round(cursor + w["end"], 3)}
                for w in words_abs
            ]
            dur = max(audio_duration(audio), 0.5)
        else:
            words = []
            dur = max(len(text.split()) * 0.4, 1.0)  # ~150wpm fallback
        timed.append({
            "text": text or f"Segmen {len(timed) + 1}",
            "keywords": list(seg.get("keywords") or []),
            "words": words,
            "start": round(cursor, 3),
            "end": round(cursor + dur, 3),
            "duration": round(dur, 3),
        })
        cursor += dur
    return timed


def align_keywords_to_timestamps(script_segments: list[dict], word_timestamps: list[dict]) -> list[dict]:
    """
    Walks through the narration word-by-word (in order) and assigns each
    script segment a start/end time based on how many words it contains.
    This gives Stage 5 a timeline: "segment 3's footage should play from
    12.4s to 18.1s of the final video."

    This is a sequential word-count alignment rather than fuzzy text
    matching, which keeps it robust to minor TTS pronunciation differences.

    Roadmap Fase 1.1: each aligned segment also carries the per-word
    timestamps that belong to it (`seg["words"]`) — Stage 5 uses them to
    render karaoke/word-highlight captions synced to the actual speech.
    """
    aligned = []
    word_cursor = 0

    for seg in script_segments:
        seg_word_count = len(seg["text"].split())
        seg_words = word_timestamps[word_cursor: word_cursor + seg_word_count]

        if not seg_words:
            # ran out of transcribed words (e.g. TTS dropped some) — estimate
            start = aligned[-1]["end"] if aligned else 0.0
            end = start + max(seg_word_count * 0.4, 1.0)  # ~150wpm fallback
            words = []
        else:
            start = seg_words[0]["start"]
            end = seg_words[-1]["end"]
            words = [
                {"word": w["word"], "start": round(w["start"], 3), "end": round(w["end"], 3)}
                for w in seg_words
            ]

        aligned.append({
            **seg,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "words": words,
        })
        word_cursor += seg_word_count

    return aligned


def run_narration_stage(script: dict, model_size: str = WHISPER_MODEL_SIZE, provider: str | None = None) -> dict:
    """
    Full Stage 3 entry point. Takes the Stage 2 script dict, produces
    narration audio + a timed version of the segments, saves both, and
    returns {"audio_path":..., "segments": [...timed...]}.
    """
    audio_path = synthesize_narration(script["segments"], provider=provider)
    word_timestamps = transcribe_with_timestamps(audio_path, model_size)
    timed_segments = align_keywords_to_timestamps(script["segments"], word_timestamps)

    result = {"audio_path": audio_path, "segments": timed_segments}
    out_path = AUDIO_CACHE_DIR / "narration_timing.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[stage3] Timing data saved to {out_path}")

    return result


def _synthesize_omnivoice(text: str, out_path: str, lang: str = "id") -> None:
    """Local voice cloning via OmniVoice (INT8 Quantization ready / FP16 fallback)."""
    import os
    import torch
    import soundfile as sf
    try:
        from omnivoice import OmniVoice
    except ImportError:
        raise RuntimeError("Run: pip install omnivoice")

    # Require a reference audio file from the user for voice cloning
    speaker_wav = "suara_gua.wav"
    if not os.path.exists(speaker_wav):
        raise FileNotFoundError(f"Untuk clone suara OmniVoice, siapkan file audio '{speaker_wav}' di folder RITME (folder utama).")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[stage3] Loading OmniVoice model on {device.upper()} in FP16 for VRAM efficiency...")
    
    # FP16 halves VRAM footprint (~1.3 GB) compared to FP32.
    # LM Studio MUST be closed to prevent OOM on 4GB VRAM GPU.
    model = OmniVoice.from_pretrained(
        "k2-fsa/OmniVoice", 
        device_map=device, 
        dtype=torch.float16
    )

    print(f"[stage3] Synthesizing audio via OmniVoice...")
    audio = model.generate(
        text=text,
        ref_audio=speaker_wav
    )
    
    # Save the output
    sf.write(out_path, audio.cpu().numpy(), 24000)
    print(f"[stage3] OmniVoice synthesis complete.")




