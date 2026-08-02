"""Smoke test: /api/narration/upload mode B (per-segment) + mode A validation."""
import io
import json
import struct
import time
import wave
import requests

BASE = "http://localhost:8585"


def make_wav(dur_s: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b"".join(struct.pack("<h", 0) for _ in range(int(16000 * dur_s)))
        w.writeframes(frames)
    return buf.getvalue()


def poll(job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = requests.get(f"{BASE}/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.5)
    return {"status": "timeout"}


segments = [
    {"text": "Bagian satu tentang kopi.", "keywords": ["kopi", "sejarah"]},
    {"text": "Bagian dua proses panen.", "keywords": ["panen", "proses"]},
]

# Mode B — per-segment, 2 files (skip seg 0? no — full 2)
wav0, wav1 = make_wav(1.2), make_wav(0.8)
fd = [
    ("segments", (None, json.dumps(segments))),
    ("seg_indices", (None, json.dumps([0, 1]))),
    ("audio_files", ("seg0.wav", wav0, "audio/wav")),
    ("audio_files", ("seg1.wav", wav1, "audio/wav")),
]
r = requests.post(f"{BASE}/api/narration/upload", files=fd)
assert r.status_code == 200, f"modeB status {r.status_code}: {r.text}"
j = poll(r.json()["job_id"])
assert j["status"] == "done", f"modeB job {j}"
res = j["result"]
assert len(res["segments"]) == 2, res
s0, s1 = res["segments"]
assert abs(s0["end"] - s0["start"] - 1.2) < 0.2, f"seg0 dur {s0}"
assert abs(s1["start"] - 1.2) < 0.25, f"seg1 start {s1}"
assert abs(s1["end"] - s1["start"] - 0.8) < 0.2, f"seg1 dur {s1}"
assert res["segment_audio_paths"] and res["segment_audio_durations"] == [1.2, 0.8] or abs(res["segment_audio_durations"][0] - 1.2) < 0.2
assert res["audio_path"], "concat full track missing"
print("MODE B (per-segment) PASS — windows:", [(round(s["start"], 2), round(s["end"], 2)) for s in res["segments"]], "| durs:", res["segment_audio_durations"])

# Mode B with skip (only seg 1) — seg_indices [1]
fd2 = [
    ("segments", (None, json.dumps(segments))),
    ("seg_indices", (None, json.dumps([1]))),
    ("audio_files", ("only1.wav", make_wav(1.0), "audio/wav")),
]
r2 = requests.post(f"{BASE}/api/narration/upload", files=fd2)
assert r2.status_code == 200, r2.text
j2 = poll(r2.json()["job_id"])
res2 = j2["result"]
s0b, s1b = res2["segments"]
assert s0b["end"] == s0b["start"] == 0, f"skipped seg0 should be empty: {s0b}"
assert abs(s1b["end"] - s1b["start"] - 1.0) < 0.2, f"seg1 {s1b}"
print("MODE B (skip seg 0) PASS — seg0:", (s0b["start"], s0b["end"]), "seg1:", (round(s1b["start"], 2), round(s1b["end"], 2)))

# No audio at all → 400
r3 = requests.post(f"{BASE}/api/narration/upload", files={"segments": (None, json.dumps(segments))})
assert r3.status_code == 400, f"no-audio status {r3.status_code}"
print("MODE A validation PASS — 400 when no audio")

print("\nALL NARRATION UPLOAD SMOKE TESTS PASS ✅")
