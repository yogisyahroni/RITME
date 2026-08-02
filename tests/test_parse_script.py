"""Test parser from_script dengan skrip dokumenter (heading + referensi link)."""
import sys, json
sys.path.insert(0, r"E:\antigraviti google\SUDAH DEPLOY\RITME")

from server import _parse_script_segments, _find_yt_urls

script = open(r"C:\Users\yogis\AppData\Local\hermes\cache\documents\doc_6abb6f11f39e_MEMAHAMI_MEMORY_MOTORIK_PADA_TUBUH_YANG_SUDAH_MENINGGAL (1).md", encoding="utf-8").read()

segs = _parse_script_segments(script)
print(f"Total segmen: {len(segs)}")
total_urls = 0
for i, s in enumerate(segs):
    urls = s["urls"]
    total_urls += len(urls)
    title = (s["title"] or "(tanpa judul)")[:60]
    print(f"  [{i}] {title!r} — {len(urls)} URL")
    for u in urls[:4]:
        print(f"       {u[:75]}")
    if len(urls) > 4:
        print(f"       … +{len(urls)-4} lagi")
print(f"\nTotal URL ter-map: {total_urls}")
# validasi semua URL unik & valid
flat = [u for s in segs for u in s["urls"]]
print(f"URL unik: {len(set(flat))} | duplikat: {len(flat)-len(set(flat))}")
