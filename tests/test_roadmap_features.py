"""
RITME roadmap verification suite — Fase 0 + Fase 1 + Fase 2.

Plain-Python (no pytest dependency). Run:
    venv_311/Scripts/python.exe tests/test_roadmap_features.py [--with-clip] [--with-server]

Covers:
  Fase 0: multi-frame CLIP sampling, MIN_ACCEPTABLE_CLIP_SCORE floor,
          YouTube 2-window fair-use download
  Fase 1: karaoke captions (1.1), music + ducking (1.2), crossfades (1.3),
          Ken Burns (1.4), caption styling (1.5)
  Fase 2: parallel stage4 helpers (2.1/2.3), Wikimedia N+1 fix (2.2),
          ClipMatcher singleton reuse (2.4)
  Bugs:  /api/render endpoint actually renders (regression for the dead-code bug)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

sys.stdout.reconfigure(encoding="utf-8")

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    if ok:
        PASSED.append(name)
        print(f"  PASS  {name}{' — ' + detail if detail else ''}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}{' — ' + detail if detail else ''}")


def run(cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def make_test_video(path: Path, color: str, duration: float = 4.0, size="360x640"):
    """Synthetic test footage via ffmpeg lavfi."""
    path.parent.mkdir(parents=True, exist_ok=True)
    r = run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:d={duration}:s={size}:r=30",
        "-pix_fmt", "yuv420p", str(path),
    ])
    assert r.returncode == 0, r.stderr
    return path


def make_test_audio(path: Path, freq: int, duration: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    r = run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
        str(path),
    ])
    assert r.returncode == 0, r.stderr
    return path


def probe_duration(path: str) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def probe_audio_streams(path: str) -> int:
    r = run(["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", path])
    return len([l for l in r.stdout.strip().splitlines() if l])


def sample_wav_rms(path: str, start_s: float, end_s: float) -> float:
    """RMS of a wav slice via ffmpeg -> raw s16le -> numpy."""
    import numpy as np
    r = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", str(start_s), "-t", str(end_s - start_s),
        "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "8000", "-",
    ], capture_output=True, timeout=60)  # text=False: need raw bytes
    if r.returncode != 0 or not r.stdout:
        return 0.0
    samples = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0


def make_timed_segments():
    """3 segments, 3.0/3.0/2.5s with fabricated per-word timestamps."""
    segs = []
    texts = ["Selamat pagi dunia hari ini", "Ini adalah video percobaan kedua", "Terima kasih sudah menonton"]
    cursor = 0.0
    for ti, text in enumerate(texts):
        words = text.split()
        word_ts = []
        t = cursor
        for w in words:
            word_ts.append({"word": w, "start": round(t, 3), "end": round(t + 0.25, 3)})
            t += 0.3
        dur = round(word_ts[-1]["end"] - cursor, 3) if word_ts else 1.0
        if ti == 0:
            dur = 3.0
        elif ti == 1:
            dur = 3.0
        else:
            dur = 2.5
        # renormalize word ts to fit the exact segment duration
        word_ts = []
        t = cursor
        step = dur / len(words)
        for w in words:
            word_ts.append({"word": w, "start": round(t, 3), "end": round(t + step * 0.8, 3)})
            t += step
        segs.append({
            "text": text, "keywords": ["red", "blue"],
            "start": round(cursor, 3), "end": round(cursor + dur, 3),
            "duration": round(dur, 3), "words": word_ts,
            "music_mood": "calm",
        })
        cursor += dur
    return segs, cursor


def test_caption_renderer():
    print("\n[1.1/1.5] Caption renderer (karaoke + styling)")
    from pipeline.caption_renderer import (
        CAPTION_PRESETS, resolve_caption_style, render_karaoke_images, render_static_image,
    )

    style = resolve_caption_style({"caption_style": "minimal-white-center"})
    check("preset resolves", style["mode"] == "karaoke" and style["position"] == "center")

    style_default = resolve_caption_style({})
    check("backward-compat default", style_default["mode"] == "karaoke" and style_default["position"] == "bottom")

    style_inline = resolve_caption_style({"caption_style": {"color": "#ff0000", "position": "lower-third"}})
    check("inline dict style", style_inline["color"] == "#ff0000" and style_inline["position"] == "lower-third")

    words = [
        {"word": "Halo", "start": 0.0, "end": 0.3},
        {"word": "dunia", "start": 0.3, "end": 0.6},
        {"word": "indah", "start": 0.6, "end": 0.9},
    ]
    frames = render_karaoke_images(words, style, 360, 640)
    check("karaoke frame count == word count", len(frames) == 3)
    # active word highlight: frame 0 and frame 1 must differ (different active word)
    import numpy as np
    a = np.array(frames[0]["image"]); b = np.array(frames[1]["image"])
    check("frames differ between words", not np.array_equal(a, b))
    check("frame timings", abs(frames[0]["start"] - 0.0) < 0.01 and abs(frames[1]["end"] - 0.6) < 0.01)

    img = render_static_image("Halo dunia", style, 360, 640)
    check("static image renders", img is not None and img.size == (360, 640))
    check("all presets exist", {"bold-white-bottom", "minimal-white-center", "news-style-lower-third"} <= set(CAPTION_PRESETS))


def test_stage3_words():
    print("\n[1.1] Stage 3 attaches word timestamps")
    from pipeline.stage3_narration import align_keywords_to_timestamps

    script = [{"text": "Halo dunia", "keywords": ["a"]}, {"text": "Ini percobaan", "keywords": ["b"]}]
    words = [
        {"word": "Halo", "start": 0.1, "end": 0.4}, {"word": "dunia", "start": 0.4, "end": 0.8},
        {"word": "Ini", "start": 0.9, "end": 1.2}, {"word": "percobaan", "start": 1.2, "end": 1.7},
    ]
    aligned = align_keywords_to_timestamps(script, words)
    check("words attached to seg 0", len(aligned[0]["words"]) == 2 and aligned[0]["words"][0]["word"] == "Halo")
    check("words attached to seg 1", len(aligned[1]["words"]) == 2)
    check("segment times from words", abs(aligned[0]["start"] - 0.1) < 0.01 and abs(aligned[0]["end"] - 0.8) < 0.01)
    check("fallback words empty", "words" in aligned[0])


def test_stage2_music_mood():
    print("\n[1.2] Stage 2 parses music_mood")
    from pipeline.stage2_script import _parse_json_response

    canned = '''{"music_mood": "epic", "segments": [{"act": "intro", "text": "Halo", "keywords": ["a"]}]}'''
    segs, meta = _parse_json_response(canned)
    check("music_mood parsed", meta.get("music_mood") == "epic")
    check("segments intact", len(segs) == 1 and segs[0]["act"] == "intro")

    from pipeline.stage_music import guess_music_mood
    check("heuristic: tense", guess_music_mood("bahaya mengancam kota") == "tense")
    check("heuristic: calm default", guess_music_mood("biasa saja") == "calm")


def test_stage_music_ducking():
    print("\n[1.2] Music ducking")
    import numpy as np
    from pipeline import stage_music
    from config import AUDIO_CACHE_DIR

    music_in = make_test_audio(AUDIO_CACHE_DIR / "test_music_in.wav", 220, 8.0)
    narration = make_test_audio(AUDIO_CACHE_DIR / "test_narration.wav", 440, 8.0)

    windows = [(2.0, 5.0)]  # narration speaks 2..5s
    out = stage_music.build_ducked_music(music_in, windows, 8.0, str(narration),
                                         out_path=AUDIO_CACHE_DIR / "test_music_ducked.wav")
    check("ducked file created", out is not None and Path(out).exists())

    inside = sample_wav_rms(str(out), 3.0, 4.0)   # during narration (ducked)
    outside = sample_wav_rms(str(out), 6.0, 7.0)  # after narration (full)
    check("ducking lowers volume", outside > 0 and inside < outside * 0.55,
          f"inside={inside:.1f} outside={outside:.1f}")

    first = sample_wav_rms(str(out), 0.0, 0.3)   # fade-in region
    check("fade-in present", first < outside * 0.9, f"first={first:.1f}")


def test_stage4_fase0():
    print("\n[Fase 0] Multi-frame sampling + score floor + YouTube 2-window")
    from pipeline import stage4_footage as s4

    clip = make_test_video(Path("cache") / "test" / "red_clip.mp4", "red", 4.0)
    frames = s4._extract_sample_frames(str(clip), n_frames=3)
    check("3 frames sampled", len(frames) == 3)
    check("middle frame named .sample.jpg", Path(str(clip) + ".sample.jpg").exists())
    check("duration probe", s4._get_video_duration(str(clip)) > 3.5)

    # score floor: weak match is rejected
    orig = s4.get_ranked_candidates_for_segment
    s4.get_ranked_candidates_for_segment = lambda *a, **k: [{
        "video_path": "x.mp4", "keyword_used": "k", "score": 0.05, "source": "test"}]
    try:
        from config import MIN_ACCEPTABLE_CLIP_SCORE
        res = s4.find_footage_for_segment(["red"], object())
        check("weak match rejected (floor)", res is None)
    finally:
        s4.get_ranked_candidates_for_segment = orig

    # YouTube 2-window: both windows downloaded, both returned.
    # Use a unique id per run so the footage cache never short-circuits the test.
    import uuid
    dl = []
    vid_id = f"VID{uuid.uuid4().hex[:10]}"
    s4._download_youtube_fairuse = lambda url, out, window: dl.append((url, out, window)) or _touch(out)
    try:
        paths = s4.download_candidate({"source": "youtube_fairuse", "id": vid_id, "url": "https://youtu.be/VID1"})
        check("2 windows downloaded", isinstance(paths, list) and len(paths) == 2 and len(dl) == 2)
        check("windows are 10-20 and 30-40", sorted(w[2][0] for w in dl) == [10, 30])
    finally:
        s4._download_youtube_fairuse = None  # restore


def _touch(path: str):
    Path(path).write_bytes(b"\x00\x00\x00\x00")


def test_stage4_wikimedia_n1():
    print("\n[2.2] Wikimedia N+1 fix")
    from pipeline import stage4_footage as s4
    import requests as real_requests
    calls = []
    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        class R:
            def raise_for_status(self): pass
            def json(self):
                if url.endswith("api.php") and params.get("list") == "search":
                    return {"query": {"search": [{"title": f"File:A{i}.webm"} for i in range(3)]}}
                return {"query": {"pages": {str(i): {"title": f"File:A{i}.webm", "imageinfo": [{"url": f"https://x/A{i}.webm"}]} for i in range(3)}}}

        return R()

    s4.requests.get = fake_get
    try:
        cands = s4.search_wikimedia("tesla")
        check("3 candidates", len(cands) == 3)
        check("exactly 2 API calls (N+1 fixed)", len(calls) == 2, f"calls={len(calls)}")
        check("titles pipe-joined", "File:A0.webm|File:A1.webm|File:A2.webm" in str(calls[1][1].get("titles", "")))
    finally:
        s4.requests.get = real_requests.get


def test_stage4_parallel():
    print("\n[2.1] Parallel search + download")
    from pipeline import stage4_footage as s4
    import concurrent.futures

    # search_all_sources runs all 5 fns in parallel
    order = []
    def f1(q, n): time.sleep(0.15); order.append("f1"); return [{"source": "f1", "id": 1, "url": "x"}]
    def f2(q, n): time.sleep(0.15); order.append("f2"); return []
    def f3(q, n): time.sleep(0.15); order.append("f3"); return []
    def f4(q, n): time.sleep(0.15); order.append("f4"); return []
    def f5(q, n): time.sleep(0.15); order.append("f5"); return []
    old = s4._INTERNET_SEARCH_FNS
    s4._INTERNET_SEARCH_FNS = (f1, f2, f3, f4, f5)
    old_workers = s4.STAGE4_SEARCH_WORKERS
    s4.STAGE4_SEARCH_WORKERS = 5
    try:
        t0 = time.time()
        res = s4.search_all_sources("test")
        elapsed = time.time() - t0
        check("parallel search < 0.3s for 5x0.15s sleeps", elapsed < 0.30, f"{elapsed:.2f}s")
        check("result merged", len(res) == 1)
    finally:
        s4._INTERNET_SEARCH_FNS = old
        s4.STAGE4_SEARCH_WORKERS = old_workers

    # match_all_segments parallel (2.3) — mocked matcher, 4 segments
    class MockMatcher:
        def rank_all(self, keyword, paths):
            time.sleep(0.15)
            return [(p, 0.9) for p in paths]
    s4.search_local_footage = lambda q, limit=5: []
    s4.search_all_sources = lambda q, per_source=3: [{"source": "test", "id": q, "url": f"http://x/{q}.mp4"}]
    s4.download_candidate = lambda c, dest_dir=None: f"cache/test/{c['id']}.mp4"
    old_singleton = s4._clip_matcher_singleton
    s4._clip_matcher_singleton = MockMatcher()
    try:
        segs = [{"keywords": [f"kw{i}"]} for i in range(4)]
        t0 = time.time()
        results = s4.match_all_segments(segs)
        elapsed = time.time() - t0
        check("4 segments done", len(results) == 4)
        check("parallel segments < 0.5s (serial would be ~0.6s)", elapsed < 0.5, f"{elapsed:.2f}s")
    finally:
        s4._clip_matcher_singleton = old_singleton


def test_stage5_assembly(with_music=True):
    print("\n[1.1-1.5] Stage 5 end-to-end assembly")
    import pipeline.stage5_assembly as s5
    from config import AUDIO_CACHE_DIR

    # patch to a small resolution + test output dir for a fast render
    test_out = ROOT / "output" / "test_renders"
    test_out.mkdir(parents=True, exist_ok=True)
    old_res = s5.OUTPUT_RESOLUTION
    old_dir = s5.OUTPUT_DIR
    s5.OUTPUT_RESOLUTION = (360, 640)
    s5.OUTPUT_DIR = test_out

    # caption style unit checks (before full render)
    segs, total = make_timed_segments()
    style = s5.resolve_caption_style({"caption_style": "bold-white-bottom"})
    clips = s5._caption_clips_for_segment(segs[0], style, 360, 640)
    check("karaoke -> one clip per word", len(clips) == len(segs[0]["words"]))
    style_static = dict(style); style_static["mode"] = "static"
    clips_s = s5._caption_clips_for_segment(segs[0], style_static, 360, 640)
    check("static -> one clip", len(clips_s) == 1)

    try:
        clip_a = make_test_video(Path("cache") / "test" / "clip_a.mp4", "red", 5.0)
        clip_b = make_test_video(Path("cache") / "test" / "clip_b.mp4", "blue", 5.0)
        clip_c = make_test_video(Path("cache") / "test" / "clip_c.mp4", "green", 5.0)
        narration = make_test_audio(AUDIO_CACHE_DIR / "test_narration_5s.wav", 440, total)
        music = make_test_audio(AUDIO_CACHE_DIR / "test_music_5s.wav", 220, total + 1)

        footage_map = {0: {"video_path": str(clip_a), "source": "test"},
                       1: {"video_path": str(clip_b), "source": "test"},
                       2: {"video_path": str(clip_c), "source": "test"}}
        template = {
            "template_name": "test_tpl",
            "pacing": {"avg_shot_duration": 8.0},  # one cut per segment
            "caption_style": "minimal-white-center",
        }

        progress = []
        out = s5.assemble_video(
            segs, footage_map, str(narration), template,
            output_name="test_roadmap_render", on_progress=lambda p, m: progress.append(p),
            music_path=str(music) if with_music else None,
        )
        check("render produced file", Path(out).exists())
        dur = probe_duration(out)
        check("duration == narration total", abs(dur - total) < 0.3, f"got {dur:.2f}s want {total:.2f}s")
        check("audio track present", probe_audio_streams(out) >= 1)
        check("progress reported", len(progress) > 3 and max(progress) >= 20)
        return str(out)
    finally:
        s5.OUTPUT_RESOLUTION = old_res
        s5.OUTPUT_DIR = old_dir


def test_server_render_endpoint():
    print("\n[BUG FIX] /api/render endpoint renders (regression test)")
    from fastapi.testclient import TestClient
    import server as server_mod
    from config import AUDIO_CACHE_DIR

    # prep payload
    segs, total = make_timed_segments()
    clip_a = make_test_video(Path("cache") / "test" / "clip_a.mp4", "red", 5.0)
    clip_b = make_test_video(Path("cache") / "test" / "clip_b.mp4", "blue", 5.0)
    clip_c = make_test_video(Path("cache") / "test" / "clip_c.mp4", "green", 5.0)
    narration = make_test_audio(AUDIO_CACHE_DIR / "test_narration_5s.wav", 440, total)

    import pipeline.stage5_assembly as s5
    old_res = s5.OUTPUT_RESOLUTION
    old_dir = s5.OUTPUT_DIR
    s5.OUTPUT_RESOLUTION = (360, 640)
    s5.OUTPUT_DIR = ROOT / "output" / "test_renders"
    (ROOT / "output" / "test_renders").mkdir(parents=True, exist_ok=True)

    template_json = {"template_name": "test_tpl", "pacing": {"avg_shot_duration": 8.0}, "caption_style": "minimal-white-center"}
    (ROOT / "templates" / "test_tpl.json").write_text(json.dumps(template_json), encoding="utf-8")

    try:
        with TestClient(server_mod.app) as client:
            resp = client.post("/api/render", json={
                "template_name": "test_tpl",
                "timed_segments": segs,
                "footage_map": {"0": {"video_path": str(clip_a)}, "1": {"video_path": str(clip_b)}, "2": {"video_path": str(clip_c)}},
                "narration_audio_path": str(narration),
                "output_name": "test_api_render",
            })
            check("POST accepted", resp.status_code == 200, f"status={resp.status_code}")
            job_id = resp.json()["job_id"]

            for _ in range(120):
                job = client.get(f"/api/jobs/{job_id}").json()
                if job["status"] in ("done", "error"):
                    break
                time.sleep(1)
            check("job done", job["status"] == "done", f"status={job['status']} error={job.get('error')}")
            out_url = job.get("result", {}).get("output_url") if job.get("result") else None
            check("output url returned", bool(out_url))
            if out_url:
                fpath = ROOT / "output" / "test_renders" / Path(out_url).name
                check("output file exists", fpath.exists())
    finally:
        s5.OUTPUT_RESOLUTION = old_res
        s5.OUTPUT_DIR = old_dir
        (ROOT / "templates" / "test_tpl.json").unlink(missing_ok=True)


def test_timeline_export_finishing_options():
    print("\n[1C.1] Timeline export via assemble_video with manual finishing options")
    from fastapi.testclient import TestClient
    import server as server_mod
    from config import AUDIO_CACHE_DIR

    segs, total = make_timed_segments()
    clip_a = make_test_video(Path("cache") / "test" / "clip_a.mp4", "red", 5.0)
    clip_b = make_test_video(Path("cache") / "test" / "clip_b.mp4", "blue", 5.0)
    clip_c = make_test_video(Path("cache") / "test" / "clip_c.mp4", "green", 5.0)
    narration = make_test_audio(AUDIO_CACHE_DIR / "test_narration_5s.wav", 440, total)

    import pipeline.stage5_assembly as s5
    old_res = s5.OUTPUT_RESOLUTION
    old_dir = s5.OUTPUT_DIR
    s5.OUTPUT_RESOLUTION = (360, 640)
    s5.OUTPUT_DIR = ROOT / "output" / "test_renders"
    (ROOT / "output" / "test_renders").mkdir(parents=True, exist_ok=True)

    def timeline_segments():
        return [
            {"index": 0, "video_path": str(clip_a), "narration_text": "Selamat pagi dunia hari ini", "start_trim": 0.0, "end_trim": 0.0, "duration": 3.0, "keywords": ["red"]},
            {"index": 1, "video_path": str(clip_b), "narration_text": "Ini adalah video percobaan kedua", "start_trim": 0.0, "end_trim": 0.0, "duration": 3.0, "keywords": ["blue"]},
            {"index": 2, "video_path": str(clip_c), "narration_text": "Terima kasih sudah menonton", "start_trim": 0.0, "end_trim": 0.0, "duration": 2.5, "keywords": ["green"]},
        ]

    try:
        with TestClient(server_mod.app) as client:
            # 1) all finishing OFF (defaults) — must still render (Fase 1 quality, not old ffmpeg path)
            r_off = client.post("/api/timeline/export", json={
                "segments": timeline_segments(),
                "narration_audio_path": str(narration),
                "output_name": "test_tl_off",
            })
            check("export all-OFF -> 200", r_off.status_code == 200, f"status={r_off.status_code}")

            # 2) all ON — independent flags must not error in combination
            r_on = client.post("/api/timeline/export", json={
                "segments": timeline_segments(),
                "narration_audio_path": str(narration),
                "output_name": "test_tl_on",
                "add_music": True, "music_mood": "calm",
                "caption_style": "news-style-lower-third",
                "transition_style": "crossfade",
                "ken_burns": True,
            })
            check("export all-ON -> 200", r_on.status_code == 200, f"status={r_on.status_code}")

            # 3) mixed combo (music + crossfade only, no Ken Burns) — proves independence
            r_mixed = client.post("/api/timeline/export", json={
                "segments": timeline_segments(),
                "narration_audio_path": str(narration),
                "output_name": "test_tl_mixed",
                "add_music": True, "music_mood": "upbeat",
                "transition_style": "crossfade",
            })
            check("export mixed -> 200", r_mixed.status_code == 200, f"status={r_mixed.status_code}")

            # 4) preview endpoint — same pipeline, low-res + ultrafast
            r_prev = client.post("/api/timeline/preview", json={
                "segments": timeline_segments(),
                "narration_audio_path": str(narration),
                "output_name": "test_tl_prev",
                "add_music": True, "music_mood": "calm",
                "caption_style": "minimal-white-center",
            })
            check("preview -> 200", r_prev.status_code == 200, f"status={r_prev.status_code}")

            off_path = ROOT / "output" / "test_renders" / "test_tl_off.mp4"
            on_path = ROOT / "output" / "test_renders" / "test_tl_on.mp4"
            prev_path = ROOT / "output" / "test_renders" / "test_tl_prev_preview.mp4"
            check("off file exists", off_path.exists())
            check("on file exists", on_path.exists())
            if off_path.exists():
                d = probe_duration(str(off_path))
                check("off duration == narration total", abs(d - total) < 0.3, f"got {d:.2f}s want {total:.2f}s")
            if prev_path.exists():
                d = probe_duration(str(prev_path))
                check("preview duration == narration total", abs(d - total) < 0.3, f"got {d:.2f}s want {total:.2f}s")
    finally:
        s5.OUTPUT_RESOLUTION = old_res
        s5.OUTPUT_DIR = old_dir


def test_export_project_finishing_metadata():
    print("\n[1C.2] Project export embeds music/caption/transition info")
    from fastapi.testclient import TestClient
    import io, zipfile
    import server as server_mod
    from config import AUDIO_CACHE_DIR

    segs, total = make_timed_segments()
    clip_a = make_test_video(Path("cache") / "test" / "clip_a.mp4", "red", 5.0)
    clip_b = make_test_video(Path("cache") / "test" / "clip_b.mp4", "blue", 5.0)
    clip_c = make_test_video(Path("cache") / "test" / "clip_c.mp4", "green", 5.0)
    narration = make_test_audio(AUDIO_CACHE_DIR / "test_narration_5s.wav", 440, total)
    music = make_test_audio(AUDIO_CACHE_DIR / "test_music_5s.wav", 220, total + 1)

    footage_map = {"0": {"video_path": str(clip_a)}, "1": {"video_path": str(clip_b)}, "2": {"video_path": str(clip_c)}}

    with TestClient(server_mod.app) as client:
        resp = client.post("/api/export/project", json={
            "timed_segments": segs,
            "footage_map": footage_map,
            "narration_audio_path": str(narration),
            "output_name": "test_finishing_export",
            "formats": ["edl", "fcpxml", "premiere_xml", "capcut_json"],
            "add_music": True,
            "music_mood": "calm",
            "music_path": str(music),
            "caption_style": "news-style-lower-third",
            "transition_style": "crossfade",
            "ken_burns": True,
        })
        check("export endpoint 200", resp.status_code == 200, f"status={resp.status_code}")
        if resp.status_code != 200:
            return

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()

        edl = zf.read("test_finishing_export.edl").decode("utf-8")
        check("EDL has music track", "MUS" in edl)
        check("EDL has finishing NOTE", "* NOTE: Caption style" in edl and "music" in edl.lower())
        check("EDL has music filename", Path(music).name in edl)

        fcpxml = zf.read("test_finishing_export.fcpxml").decode("utf-8")
        check("FCPXML has music clip", Path(music).stem in fcpxml)
        check("FCPXML has finishing comment", "<!--" in fcpxml and "Caption style" in fcpxml)

        premiere = zf.read("test_finishing_export_premiere.xml").decode("utf-8")
        check("Premiere XML has music file", Path(music).stem in premiere)

        html = zf.read("test_finishing_export_timeline.html").decode("utf-8")
        check("CapCut guide mentions finishing", "Elemen Finishing" in html)
        check("CapCut guide has music name", Path(music).name in html)
        check("CapCut guide has caption style", "news-style-lower-third" in html)
        check("CapCut guide has transition", "crossfade" in html)

        check("zip contains music file", Path(music).name in names)
        check("zip contains narration audio", Path(narration).name in names)


def test_script_generate_with_footage():
    print("\n[1B.3] Generate skrip + ekstraksi footage dalam 1 submit (paralel)")
    from fastapi.testclient import TestClient
    import server as server_mod
    import time

    video = Path("cache") / "test" / "footage_src.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    # hitam -> putih (kontras maksimal): ContentDetector(threshold=27.0)
    # baru deteksi boundary; warna pastel content-val-nya di bawah threshold.
    r = run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:d=2.5:s=320x240:r=30",
        "-f", "lavfi", "-i", "color=c=white:d=2.5:s=320x240:r=30",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
        "-pix_fmt", "yuv420p", str(video),
    ])
    assert r.returncode == 0, r.stderr
    custom = "Segmen satu membahas hal pertama. Segmen dua membahas hal kedua."

    with TestClient(server_mod.app) as client:
        with open(video, "rb") as f:
            resp = client.post("/api/script/generate_with_footage", data={
                "template_name": "demo_style",
                "topic": "tes footage paralel",
                "segments": "2",
                "language": "id",
                "custom_script": custom,
            }, files={"video": ("footage_src.mp4", f, "video/mp4")})
        check("submit 1 request -> 200", resp.status_code == 200, f"status={resp.status_code}")
        if resp.status_code != 200:
            return
        job_id = resp.json()["job_id"]

        deadline = time.time() + 420
        job = None
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(1.5)
        check("job selesai", job and job["status"] == "done", (job or {}).get("error"))
        if not job or job["status"] != "done":
            return

        result = job["result"]
        check("script punya segments", len(result.get("segments", [])) >= 1, f"n={len(result.get('segments', []))}")
        fe = result.get("footage_extraction") or {}
        check("footage_extraction dilaporkan", fe.get("count", 0) >= 1, f"count={fe.get('count')} err={fe.get('error')}")
        files = fe.get("files") or []
        check("klip hasil extract ada di disk", len(files) >= 1 and Path(files[0]).exists())


def test_footage_match_waits_for_extraction():
    print("\n[1B.3] /api/footage/match menunggu extraction paralel (race condition)")
    from fastapi.testclient import TestClient
    import server as server_mod
    from job_manager import job_manager
    import time

    segs, _ = make_timed_segments()
    with TestClient(server_mod.app) as client:
        # Fake script job dengan footage extraction yang masih berjalan.
        fake = job_manager.create()
        job_manager.update_footage(fake, "running", 40, "Memotong video...")

        resp = client.post("/api/footage/match", json={
            "segments": segs,
            "wait_for_script_job": fake,
        })
        check("match submit 200", resp.status_code == 200, f"status={resp.status_code}")
        if resp.status_code != 200:
            return
        mjob = resp.json()["job_id"]

        time.sleep(2.5)
        j = client.get(f"/api/jobs/{mjob}").json()
        check("match nunggu extraction dulu", j["status"] == "running" and "Menunggu ekstraksi" in (j.get("message") or ""), j.get("message"))

        # Extraction selesai -> match harus lanjut (bukan stuck).
        job_manager.update_footage(fake, "done", 100, "Selesai")
        time.sleep(3.0)
        j2 = client.get(f"/api/jobs/{mjob}").json()
        check("match lanjut setelah extraction selesai",
              j2["status"] in ("running", "done") and "Menunggu ekstraksi" not in (j2.get("message") or ""),
              j2.get("message"))


def test_clip_smoke():
    print("\n[2.4/CLIP] Real CLIP model smoke test (env fix verification)")
    from pipeline.stage4_footage import get_clip_matcher, _extract_sample_frames
    clip = make_test_video(Path("cache") / "test" / "clip_smoke.mp4", "red", 3.0)
    frames = _extract_sample_frames(str(clip), n_frames=2)
    matcher = get_clip_matcher()
    check("singleton identity", get_clip_matcher() is matcher)
    try:
        ranked = matcher.rank_all("a red screen", [str(clip)])
        check("clip scored", len(ranked) == 1 and isinstance(ranked[0][1], float))
    except Exception as e:
        check("clip scored", False, str(e))


def test_fase14_clip_sidecar():
    print("\n[1B.4] Precomputed CLIP embedding sidecar")
    from pipeline.stage4_footage import ClipMatcher
    m = ClipMatcher()
    clip = make_test_video(Path("cache") / "test" / "sidecar.mp4", "blue", 2.0)
    sidecar = Path(str(clip) + ".emb.json")
    sidecar.unlink(missing_ok=True)
    m._save_sidecar(str(clip), [0.1] * 512)
    check("sidecar written", sidecar.exists())
    loaded = m._load_sidecar(str(clip))
    check("sidecar loads same model", loaded is not None and len(loaded) == 512)
    sidecar.write_text(json.dumps({"model": "OTHER", "embedding": [1.0]}), encoding="utf-8")
    check("stale model rejected", m._load_sidecar(str(clip)) is None)
    sidecar.unlink(missing_ok=True)


def test_fase3_per_segment_audio():
    print("\n[3.0] Per-segment narration audio + concat + retimed segments")
    from pipeline.stage3_narration import audio_duration, concat_audio_files, transcribe_segment_audio
    d = Path("cache") / "test" / "fase3"
    a1 = make_test_audio(d / "seg1.wav", 330, 1.5)
    a2 = make_test_audio(d / "seg2.wav", 440, 2.0)
    check("audio_duration reads", abs(audio_duration(str(a1)) - 1.5) < 0.2)
    full = concat_audio_files([str(a1), str(a2)], str(d / "full.wav"))
    check("concat creates file", Path(full).exists())
    check("concat duration ≈ sum", abs(audio_duration(full) - 3.5) < 0.35, f"{audio_duration(full):.2f}")
    timed = transcribe_segment_audio([
        {"text": "satu dua tiga", "audio_path": str(a1), "keywords": ["satu"]},
        {"text": "empat lima", "audio_path": str(a2), "keywords": []},
    ])
    check("two timed segments", len(timed) == 2)
    check("cumulative start", abs(timed[0]["start"]) < 0.01 and timed[1]["start"] >= timed[0]["end"] - 0.01)
    check("duration from audio", abs(timed[0]["duration"] - 1.5) < 0.35 and abs(timed[1]["duration"] - 2.0) < 0.35,
          f"{timed[0]['duration']:.2f}/{timed[1]['duration']:.2f}")
    check("word/keyword keys present", "words" in timed[0] and timed[0]["keywords"] == ["satu"])
    # Empty-text segment yields ("", 0.0) via per-segment synthesizer helper contract
    from pipeline.stage3_narration import synthesize_narration_per_segment
    try:
        paths, durs = synthesize_narration_per_segment(
            [{"text": "Halo dunia."}, {"text": ""}], out_dir=str(d / "synth"), provider="pyttsx3")
        check("empty segment skipped", len(paths) == 2 and paths[1] == "" and durs[1] == 0.0)
        check("synth files exist", all(os.path.exists(p) for p in paths if p))
    except ImportError:
        check("pyttsx3 not installed (skipped)", True, "ImportError")


def test_fase34_subtitle_regenerate():
    print("\n[3.4] Regenerate subtitle endpoint")
    from fastapi.testclient import TestClient
    import server as server_mod
    d = Path("cache") / "test" / "fase34"
    a1 = make_test_audio(d / "s1.wav", 330, 1.0)
    with TestClient(server_mod.app) as client:
        resp = client.post("/api/timeline/regenerate_subtitles", json={"segments": [
            {"index": 0, "text": "satu dua tiga", "audio_path": str(a1), "keywords": []},
            {"index": 1, "text": "empat lima enam", "audio_path": "", "keywords": []},
        ]})
        check("200 OK", resp.status_code == 200, f"status={resp.status_code}")
        segs = resp.json()["segments"]
        check("two segments", len(segs) == 2)
        check("audio-backed duration", abs(segs[0]["duration"] - 1.0) < 0.35, f"{segs[0]['duration']:.2f}")
        check("fallback duration (no audio)", segs[1]["duration"] >= 1.0)
        check("cumulative windows", segs[1]["start"] >= segs[0]["end"] - 0.01)


def test_fase4_srt_export():
    print("\n[4] SRT export (segments_to_srt + endpoint)")
    from pipeline.stage3_narration import _srt_ts, segments_to_srt
    check("srt timestamp format", _srt_ts(65.5) == "00:01:05,500")
    check("srt timestamp overflow", _srt_ts(59.999) == "00:01:00,000" or _srt_ts(59.999) == "00:00:59,999")
    timed = [
        {"text": "Halo dunia ini kata satu dua tiga empat lima enam tujuh delapan", "start": 0.0, "end": 4.0,
         "words": [{"word": f"w{i}", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(10)]},
        {"text": "Segmen kedua tanpa kata", "start": 4.0, "end": 6.0, "words": []},
    ]
    srt = segments_to_srt(timed)
    check("srt has cues", srt.count("\n\n") >= 2, f"cues={srt.count(chr(10) + chr(10))}")
    check("srt has both texts", "Segmen kedua tanpa kata" in srt and "w0" in srt)
    check("srt timestamps valid", "00:00:00,000 --> " in srt and "00:00:04,000" in srt)
    check("srt numbered", srt.startswith("1\n"))

    from fastapi.testclient import TestClient
    import server as server_mod
    with TestClient(server_mod.app) as client:
        resp = client.post("/api/timeline/subtitles", json={"segments": [
            {"index": 0, "text": "satu dua tiga", "audio_path": "", "keywords": []},
            {"index": 1, "text": "empat lima", "audio_path": "", "keywords": []},
        ]})
        check("endpoint 200", resp.status_code == 200, f"status={resp.status_code}")
        check("content-type srt", "subrip" in resp.headers.get("content-type", ""))
        check("endpoint returns cues", "1\n00:00:00,000 --> " in resp.text)
        check("endpoint has fallback text", "satu dua tiga" in resp.text)


def test_clipper():
    print("\n[Clipper] analyze + render 9:16 + thumbnail + upload")
    import server as server_mod
    from fastapi.testclient import TestClient
    v = make_test_video(Path("cache") / "test" / "clipper_src.mp4", "red", 12.0)
    from pipeline.clipper import analyze_video, render_clips, probe_resolution
    clips = analyze_video(str(v), num_clips=4)
    check("exact clip count", len(clips) == 4, f"got {len(clips)}")
    check("windows cover full video", abs(sum(c["duration"] for c in clips) - 12.0) < 0.01)
    check("clips ordered", clips[0]["start"] >= 0 and clips[-1]["end"] <= 12.01)
    outs = render_clips(str(v), clips[:2], "output/clipper_test", aspect="9:16")
    check("rendered files exist", all(os.path.exists(p) and os.path.getsize(p) > 1000 for p in outs))
    w, h = probe_resolution(outs[0])
    check("9:16 resolution", (w, h) == (1080, 1920), f"{w}x{h}")

    from pipeline.thumbnail import generate_thumbnail
    t = generate_thumbnail(str(v), "Judul uji wrap panjang", str(Path("output") / "thumb_test.jpg"), subtitle="SUBSCRIBE")
    from PIL import Image
    im = Image.open(t)
    check("thumb 1280x720", im.size == (1280, 720), f"{im.size}")

    with TestClient(server_mod.app) as client:
        r = client.post("/api/clipper/analyze", json={"video_path": str(v), "num_clips": 3})
        check("analyze 200", r.status_code == 200, f"status={r.status_code}")
        data = r.json()
        check("analyze clips + preview", len(data["clips"]) == 3 and bool(data["clips"][0].get("thumbnail_url")))
        r = client.post("/api/clipper/render", json={"video_path": str(v), "clips": data["clips"][:2], "aspect": "9:16"})
        check("render 200", r.status_code == 200, f"status={r.status_code}")
        files = r.json()["files"]
        check("render files + zip", len(files) == 3 and files[-1].get("is_zip"))
        r = client.post("/api/thumbnail/generate", json={"video_path": str(v), "title": "Tes Judul"})
        check("thumb endpoint 200", r.status_code == 200 and "thumbnails/" in r.json()["url"])
        r = client.post("/api/clipper/upload", files={"video": ("c.mp4", open(v, "rb"), "video/mp4")})
        check("upload 200", r.status_code == 200 and r.json().get("video_path"))
        r = client.post("/api/clipper/youtube", json={"youtube_url": "http://169.254.169.254/x"})
        check("ssrf blocked", r.status_code == 400, f"status={r.status_code}")


def test_multi_voice():
    print("\n[5.1] Multi-voice per segmen (voices endpoint + generate)")
    import server as server_mod
    from fastapi.testclient import TestClient
    with TestClient(server_mod.app) as client:
        r = client.get("/api/narration/voices?provider=pyttsx3")
        check("voices 200", r.status_code == 200, f"status={r.status_code}")
        vs = r.json().get("voices", [])
        check("voices list non-empty", len(vs) > 0, f"got {len(vs)}")
        if vs:
            check("voice has id", bool(vs[0].get("id")), f"{vs[0]}")
        # generate dengan voices (2 segmen, segmen 1 pakai voice tertentu)
        voices_arg = [vs[0]["id"], ""] if vs else ["", ""]
        r = client.post("/api/narration/generate", json={
            "segments": [{"text": "Satu dua tiga empat lima"}, {"text": "Enam tujuh delapan sembilan"}],
            "tts_provider": "pyttsx3", "voices": voices_arg,
        })
        check("generate with voices 200", r.status_code == 200, f"status={r.status_code}")
        jid = r.json().get("job_id")
        # poll sebentar — kalau selesai, cek result shape
        if jid:
            import time
            for _ in range(30):
                j = client.get(f"/api/jobs/{jid}").json()
                if j["status"] in ("done", "error"):
                    break
                time.sleep(0.5)
            check("generate done", j["status"] == "done", f"status={j['status']} err={j.get('error')}")
            if j["status"] == "done":
                res = j["result"]
                check("has segment_audio_paths", len(res.get("segment_audio_paths", [])) == 2)
                check("has audio_url", bool(res.get("audio_url")))


def test_watermark():
    print("\n[5.2] Watermark upload + render with overlay")
    import server as server_mod
    from fastapi.testclient import TestClient
    from PIL import Image
    v = make_test_video(Path("cache") / "test" / "wm_src.mp4", "blue", 4.0)
    wm = Path("cache") / "test" / "wm.png"
    Image.new("RGBA", (200, 80), (232, 84, 46, 255)).save(wm)
    with TestClient(server_mod.app) as client:
        r = client.post("/api/watermark/upload", files={"image": ("logo.png", open(wm, "rb"), "image/png")})
        check("watermark upload 200", r.status_code == 200 and bool(r.json().get("watermark_path")))
        wm_path = r.json()["watermark_path"]
        r = client.post("/api/watermark/upload", files={"image": ("bad.txt", b"x", "text/plain")})
        check("watermark invalid ext rejected", r.status_code == 400, f"status={r.status_code}")
        seg = [{"index": 0, "video_path": str(v), "narration_text": "Tes", "start_trim": 0, "end_trim": 0,
                "duration": 3.0, "keywords": [], "audio_path": "", "words": []}]
        r = client.post("/api/timeline/export", json={
            "segments": seg, "output_name": "wm_render", "watermark_path": wm_path,
            "watermark_pos": "bottom-right", "caption_style": "minimal-white-center",
            "transition_style": "hard_cut",
        })
        check("export with watermark 200", r.status_code == 200, f"status={r.status_code}")
        check("has X-Render-Path", bool(r.headers.get("X-Render-Path")))


def test_batch_render():
    print("\n[5.3] Batch render (2 items sequential + validation)")
    import server as server_mod
    from fastapi.testclient import TestClient
    v = make_test_video(Path("cache") / "test" / "batch_src.mp4", "green", 4.0)
    seg = [{"index": 0, "video_path": str(v), "narration_text": "Batch", "start_trim": 0, "end_trim": 0,
            "duration": 3.0, "keywords": [], "audio_path": "", "words": []}]
    item = {"name": "batch_item_a", "segments": seg, "caption_style": "minimal-white-center",
            "transition_style": "hard_cut"}
    with TestClient(server_mod.app) as client:
        r = client.post("/api/batch/render", json={"items": []})
        check("empty batch -> 400", r.status_code == 400, f"status={r.status_code}")
        r = client.post("/api/batch/render", json={"items": [item, item]})
        check("batch start 200", r.status_code == 200, f"status={r.status_code}")
        jid = r.json()["job_id"]
        import time
        for _ in range(150):
            j = client.get(f"/api/jobs/{jid}").json()
            if j["status"] in ("done", "error"):
                break
            time.sleep(1)
        check("batch done", j["status"] == "done", f"status={j['status']} err={j.get('error')}")
        if j["status"] == "done":
            items = j["result"]["items"]
            check("2 items returned", len(items) == 2, f"got {len(items)}")
            check("all items ok", all(i["status"] == "ok" for i in items), f"{items}")
            check("items have url", all(i.get("url") for i in items))


def test_project_library_p01():
    """P0.1 Project Library — CRUD + metadata analytics + 404 + sanitize."""
    import server as server_mod
    from fastapi.testclient import TestClient
    with TestClient(server_mod.app) as client:
        # 400: nama kosong / segments kosong
        r = client.post("/api/projects", json={"name": "", "segments": []})
        check("save empty -> 400", r.status_code == 400, f"got {r.status_code}")
        seg = {"index": 0, "video_path": "", "narration_text": "Halo dunia test",
               "duration": 6.0, "keywords": ["test"]}
        r = client.post("/api/projects", json={"name": "Project Test", "segments": [seg],
                                               "finishing": {"add_music": True},
                                               "template_name": "dokumenter"})
        check("save ok", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        meta = r.json()
        pid = meta["id"]
        check("meta id present", bool(pid), f"{meta}")
        check("meta name", meta["name"] == "Project Test", f"{meta}")
        check("meta wpm", meta["wpm"] == 30, f"wpm={meta.get('wpm')}")  # 3 kata / 6s * 60
        check("meta scene_count", meta["scene_count"] == 0, f"{meta}")  # no video_path
        # list
        rl = client.get("/api/projects")
        ids = [p["id"] for p in rl.json()["projects"]]
        check("list contains id", pid in ids, f"{ids}")
        check("list has thumb_url", all(p.get("thumb_url") for p in rl.json()["projects"]))
        # get by id
        rg = client.get(f"/api/projects/{pid}")
        check("get ok", rg.status_code == 200, f"got {rg.status_code}")
        gj = rg.json()
        check("get segments", len(gj["segments"]) == 1)
        check("get finishing", gj["finishing"]["add_music"] is True)
        # 404
        check("get 404", client.get("/api/projects/nonexistent").status_code == 404)
        check("delete 404", client.delete("/api/projects/nonexistent").status_code == 404)
        # put update — id & created_at dipertahankan
        seg2 = {"index": 1, "video_path": "", "narration_text": "A B C D E F",
                "duration": 6.0}
        rp = client.put(f"/api/projects/{pid}", json={"name": "Project Renamed",
                                                      "segments": [seg, seg2]})
        check("put ok", rp.status_code == 200, f"got {rp.status_code}: {rp.text[:200]}")
        check("put same id", rp.json()["id"] == pid)
        check("put name", rp.json()["name"] == "Project Renamed")
        rg2 = client.get(f"/api/projects/{pid}").json()
        check("put segments count", len(rg2["segments"]) == 2)
        check("put keeps created_at", bool(rg2.get("created_at")))
        # delete
        check("delete ok", client.delete(f"/api/projects/{pid}").status_code == 200)
        check("list empty after delete", pid not in [p["id"] for p in client.get("/api/projects").json()["projects"]])


def test_title_overlay_p11():
    """P1.1 text/title overlay — helper render + endpoint preview terima title_overlays."""
    import pipeline.stage5_assembly as s5
    segs, total = make_timed_segments()
    overlays = [
        {"segment_index": 0, "text": "EPISODE 3 — RUANG ANGKASA", "position": "top-center",
         "font_size": 26, "color": "#FFD400", "background_pill": True, "duration": 2.0},
        {"segment_index": 1, "text": "Lower third", "position": "bottom-left", "font_size": 20},
    ]
    clips = s5._title_clips_for_overlays(overlays, segs, 360, 640)
    check("overlay -> 2 clips", len(clips) == 2, f"got {len(clips)}")
    check("start aligned to segment", abs(clips[0].start - segs[0]["start"]) < 1e-6,
          f"{clips[0].start} vs {segs[0]['start']}")
    check("duration clamped to segment", clips[0].duration <= segs[0]["duration"] + 0.05,
          f"{clips[0].duration}")
    check("empty overlays -> []", s5._title_clips_for_overlays([], segs, 360, 640) == [])
    check("none overlays -> []", s5._title_clips_for_overlays(None, segs, 360, 640) == [])
    # text kosong di-skip, sisanya tetap render
    clips2 = s5._title_clips_for_overlays(
        [{"segment_index": 0, "text": "   "}, {"segment_index": 0, "text": "OK"}], segs, 360, 640)
    check("blank text skipped", len(clips2) == 1, f"got {len(clips2)}")
    # posisi dihitung dalam frame
    x, y = s5._title_xy("tr", 100, 40, 360, 640, 14)
    check("pos top-right", x == 360 - 100 - 14 and y == 14, f"{x},{y}")

    # endpoint: preview render dengan overlay (1 segmen video pendek)
    import server as server_mod
    from fastapi.testclient import TestClient
    from config import AUDIO_CACHE_DIR
    clip = make_test_video(Path("cache") / "test" / "clip_title.mp4", "red", 2.5, size="360x640")
    with TestClient(server_mod.app) as client:
        body = {
            "segments": [{"index": 0, "video_path": str(clip), "narration_text": "Test",
                          "duration": 2.5, "start_trim": 0, "end_trim": 0}],
            "narration_audio_path": "",
            "output_name": "title_overlay_preview",
            "title_overlays": [{"segment_index": 0, "text": "JUDUL EPISODE",
                                "position": "top-center", "font_size": 24}],
        }
        r = client.post("/api/timeline/preview", json=body)
        check("preview with overlay 200", r.status_code == 200, f"got {r.status_code}: {r.text[:150]}")
        rp = client.post("/api/timeline/preview", json={**body, "title_overlays": []})
        check("preview no overlay 200", rp.status_code == 200, f"got {rp.status_code}")


def test_filter_p13():
    """P1.3 color-grade filter — presets numeric + endpoint preview terima filter."""
    import numpy as np
    import pipeline.stage5_assembly as s5
    from moviepy import ImageClip

    w, h = 64, 64
    base = np.zeros((h, w, 3), dtype=np.uint8)
    base[..., 0] = np.linspace(200, 60, w).astype(np.uint8)  # R turun ke kanan
    base[..., 2] = 80  # B konstan
    base[..., 1] = 120

    ic = ImageClip(base).with_duration(0.2)
    f_orig = s5._apply_filter(ic, "original").get_frame(0.05)
    check("original no-op", np.array_equal(f_orig, base), "")
    f_none = s5._apply_filter(ic, None).get_frame(0.05)
    check("none no-op", np.array_equal(f_none, base), "")

    f_bw = s5._apply_filter(ic, "bw").get_frame(0.05)
    check("bw channel sama", np.allclose(f_bw[..., 0], f_bw[..., 1]) and np.allclose(f_bw[..., 1], f_bw[..., 2]), "")
    check("bw tetap gradasi", f_bw[0, 0, 0] > f_bw[0, -1, 0], f"kiri {f_bw[0,0,0]} kanan {f_bw[0,-1,0]}")

    f_warm = s5._apply_filter(ic, "warm").get_frame(0.05)
    check("warm R naik", f_warm[..., 0].mean() > base[..., 0].mean(), f"{f_warm[...,0].mean():.1f}")
    check("warm B turun", f_warm[..., 2].mean() < base[..., 2].mean(), f"{f_warm[...,2].mean():.1f}")

    f_cool = s5._apply_filter(ic, "cool").get_frame(0.05)
    check("cool B naik", f_cool[..., 2].mean() > base[..., 2].mean(), f"{f_cool[...,2].mean():.1f}")

    f_vint = s5._apply_filter(ic, "vintage").get_frame(0.05)
    check("vintage channel beda", not np.allclose(f_vint[..., 0], f_vint[..., 2]), "")

    # endpoint: preview dengan filter bw di segmen
    import server as server_mod
    from fastapi.testclient import TestClient
    clip = make_test_video(Path("cache") / "test" / "clip_filter.mp4", "red", 2.0, size="360x640")
    with TestClient(server_mod.app) as client:
        body = {
            "segments": [{"index": 0, "video_path": str(clip), "narration_text": "Test",
                          "duration": 2.0, "start_trim": 0, "end_trim": 0, "filter": "bw"}],
            "narration_audio_path": "",
            "output_name": "filter_preview",
        }
        r = client.post("/api/timeline/preview", json=body)
        check("preview filter 200", r.status_code == 200, f"got {r.status_code}: {r.text[:150]}")
        r2 = client.post("/api/timeline/preview", json={**body, "filter": "warm"})
        check("preview warm 200", r2.status_code == 200, f"got {r2.status_code}: {r2.text[:150]}")


def main():
    which = set(sys.argv[1:])
    run_all = "--all" in which or len(which) == 0

    tests = [
        ("caption_renderer", test_caption_renderer),
        ("stage3_words", test_stage3_words),
        ("stage2_music_mood", test_stage2_music_mood),
        ("stage_music_ducking", test_stage_music_ducking),
        ("stage4_fase0", test_stage4_fase0),
        ("stage4_wikimedia_n1", test_stage4_wikimedia_n1),
        ("stage4_parallel", test_stage4_parallel),
        ("stage5_assembly", test_stage5_assembly),
        ("clip_sidecar_1b4", test_fase14_clip_sidecar),
        ("per_segment_audio_30", test_fase3_per_segment_audio),
        ("srt_export", test_fase4_srt_export),
        ("clipper", test_clipper),
        ("multi_voice", test_multi_voice),
        ("watermark", test_watermark),
        ("batch_render", test_batch_render),
        ("project_library_p01", test_project_library_p01),
        ("title_overlay_p11", test_title_overlay_p11),
        ("filter_p13", test_filter_p13),
    ]
    if run_all or "--with-server" in which:
        tests.append(("server_render", test_server_render_endpoint))
        tests.append(("timeline_export_finishing", test_timeline_export_finishing_options))
        tests.append(("export_finishing_metadata", test_export_project_finishing_metadata))
        tests.append(("script_generate_with_footage", test_script_generate_with_footage))
        tests.append(("footage_match_wait", test_footage_match_waits_for_extraction))
        tests.append(("subtitle_regenerate", test_fase34_subtitle_regenerate))
        tests.append(("srt_endpoint", test_fase4_srt_export))
    if run_all or "--with-clip" in which:
        tests.append(("clip_smoke", test_clip_smoke))

    for name, fn in tests:
        print(f"\n=== {name} ===")
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAILED.append(name)
            print(f"  FAIL  {name} — exception: {e}")

    print("\n" + "=" * 50)
    print(f"PASSED: {len(PASSED)}  FAILED: {len(FAILED)}")
    if FAILED:
        print("Failed:", ", ".join(FAILED))
        sys.exit(1)
    print("ALL GREEN ✅")


if __name__ == "__main__":
    main()
