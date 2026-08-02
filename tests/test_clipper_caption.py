"""Test end-to-end: clipper analyze + render dengan autocaption."""
import json, os, sys, time
import urllib.request

BASE = "http://localhost:8585"
VIDEO = r"E:\antigraviti google\SUDAH DEPLOY\RITME\cache\uploads\0a378b8cedb346f38878e145c7b9d0c4_script_yt.mp4"

def post_json(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)

# 1) upload
import mimetypes
boundary = "----RITME" + os.urandom(8).hex()
with open(VIDEO, "rb") as f:
    data = f.read()
body = b""
body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"test.mp4\"\r\nContent-Type: video/mp4\r\n\r\n".encode()
body += data + b"\r\n"
body += f"--{boundary}--\r\n".encode()
req = urllib.request.Request(BASE + "/api/clipper/upload", data=body,
                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
with urllib.request.urlopen(req, timeout=600) as r:
    up = json.load(r)
print("upload OK:", up["name"][:50])
vp = up["video_path"]

# 2) analyze (ambil clip pertama saja biar cepat)
an = post_json("/api/clipper/analyze", {"video_path": vp, "num_clips": 2})
print("analyze OK:", len(an["clips"]), "clip, video_url:", an.get("video_url"))
clip0 = an["clips"][0]
print("clip0:", clip0["start"], "-", clip0["end"], f"({clip0['duration']:.1f}s)")

# 3) render dengan autocaption
t0 = time.time()
rend = post_json("/api/clipper/render", {
    "video_path": vp, "clips": [clip0], "aspect": "9:16",
    "output_name": "caption_test", "captions": True, "caption_style": "bold-white-bottom",
})
dt = time.time() - t0
print(f"render+caption OK ({dt:.1f}s):", [f["name"] for f in rend["files"] if not f.get("is_zip")])

# 4) cek output file benar-benar ada & ukurannya
for f in rend["files"]:
    if not f.get("is_zip"):
        p = f["path"]
        print(f"  {os.path.basename(p)}: {os.path.getsize(p)/1024:.0f} KB")
