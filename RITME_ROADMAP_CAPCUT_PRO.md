# RITME Roadmap — CapCut Pro Level (Fase P0–P3)

**Konteks:** Semua roadmap resmi (Fase 0–5) sudah selesai & ter-push (138 test PASS).
Dokumen ini = sisa kerjaan biar RITME terasa **selengkap CapCut Pro**, ditulis detail
per task biar bisa langsung dieksekusi.

**Arah produk (konsisten dengan roadmap sebelumnya):** automation berhenti di titik
timeline siap direview. Semua elemen finishing (transisi, filter, teks, sticker, speed)
adalah **keputusan manual user** di timeline editor — bukan default yang dipaksa nyala.

**🚫 EXCLUDED — Auto-upload YouTube**: user eksplisit TIDAK MAU. Jangan pernah
masukin task upload ke platform.

**Prinsip implementasi:** setiap fitur = opsi yang bisa dipilih user, matriks
kombinasi semua valid (filter + transisi + teks + sticker harus bisa barengan, bukan
saling menonaktifkan).

---

## Fase P0 — Fondasi (kerjakan PALING DULU)

### P0.1 Project Library server-side

**Masalah konkret:** project cuma disimpen di localStorage browser
(`ritme_timeline_project_v1`) + export/import `.ritme.json` manual. Ganti browser/PC
= project ilang. Gak ada grid/list project, gak ada thumbnail, gak ada versi.

**File kena:** `server.py`, baru `projects/` (folder storage), `frontend/src/ProjectsPage.jsx` (baru), `App.jsx` (route `#/projects`), `TimelineEditor.jsx`

**Detail eksekusi:**
1. Storage: folder `projects/<id>/` berisi `project.json` (segments + finishing +
   metadata: judul, template, created/updated, durasi) + `thumb.jpg`. File-based,
   konsisten dengan arsitektur existing (gak perlu DB).
2. Endpoint: `POST /api/projects` (simpan, return id), `GET /api/projects` (list +
   thumbnail URL + durasi), `GET /api/projects/{id}`, `DELETE /api/projects/{id}`,
   `PUT /api/projects/{id}` (update). Sanitize id (reuse pola `_safe_output_name`).
3. Thumbnail: ambil frame tengah footage segmen pertama (ffmpeg extract, pola sudah
   ada di `thumbnail.py`) pas save, simpan ke folder project.
4. UI: halaman `#/projects` — grid kartu (thumbnail, judul, durasi, tanggal update,
   tombol **Buka** / **Duplikat** / **Hapus**). Dari TimelineEditor, tombol
   "Simpan ke Library" → POST + redirect ke grid.
5. Autosave lokal tetap dipertahankan sebagai buffer, tapi library jadi source of
   truth.

**Acceptance criteria:** bikin project → simpan → tutup browser → buka lagi → project
muncul di grid dengan thumbnail → bisa di-restore utuh (segments + finishing + edit
belum hilang).

**Effort:** Sedang

---

## Fase P1 — Visual Premium (paling keliatan "CapCut")

### P1.1 Text / Title overlay manual

**Masalah konkret:** sekarang cuma ada caption karaoke otomatis dari narasi. Gak ada
cara nambah judul, intro/outro text, lower-third, callout — yang bikin video "dibikin
beneran", bukan cuma auto-render.

**File kena:** `server.py`, `pipeline/stage5_assembly.py`, `frontend/src/TimelineEditor.jsx` (track TEXT baru), `pipeline/project_exporter.py` (bawa data teks)

**Detail eksekusi:**
1. Model `TitleOverlay { id, segment_index, text, start_offset, duration, position
   (9 preset titik), font_size, color, background_pill: bool, style }` — default
   off, setiap field punya default wajar.
2. Render: reuse pola `_caption_clips_for_segment` (ImageClip numpy dari PIL) —
   render teks via PIL, overlay di window `[start, start+duration]` segmen, posisi
   sesuai preset.
3. Track TEXT baru di timeline (di bawah CAPTION) — tiap segmen bisa punya 0..N
   overlay. Klik track → panel edit (teks, posisi, ukuran, warna, pill bg).
4. Export `.ritme.json` + project library (P0.1) harus bawa `title_overlays`.

**Acceptance criteria:** user bisa tambah judul di segmen intro, lower-third di
segmen tengah — render final menampilkan teks dengan posisi/gaya yang dipilih, dan
ikut tersimpan di project.

**Effort:** Sedang

---

### P1.2 Transitions library (lebih dari crossfade)

**Masalah konkret:** transisi cuma `hard_cut` | `crossfade` global. CapCut punya
puluhan — minimal yang paling dipakai: dip-to-black, fade-from-black, slide,
zoom-in.

**File kena:** `pipeline/stage5_assembly.py`, `server.py` (`TimelineExportRequest`),
`frontend/src/TimelineEditor.jsx`

**Detail eksekusi:**
1. Tambah opsi: `fade_black` (dip to black 0.5s), `fade_from_black`, `slide_left`,
   `slide_right`, `zoom_in` — implementasi moviepy: overlay ImageClip hitam dengan
   opacity ramp (dip-to-black), atau clip digeser/di-zoom dengan kompensasi durasi
   (pola crossfade yang sudah ada: overlap dikompensasi biar durasi total tetap).
2. Transisi jadi **per-cut**, bukan global: `transitions: list[dict]` di request —
   tiap entry `{between_segment_idx, type}`. Default hard_cut.
3. UI: dropdown transisi muncul di antara 2 clip di timeline (di ruler/antara clip),
   atau panel per clip "Transisi keluar".

**Acceptance criteria:** user pilih transisi beda di cut yang berbeda → render final
menampilkan tiap transisi di cut-nya, durasi total tetap = durasi narasi.

**Effort:** Sedang–Besar (per-cut = perubahan model data)

---

### P1.3 Filters / Color grade presets

**Masalah konkret:** footage apa adanya, kadang gelap/keabuan/kontras kurang.
CapCut punya filter 1-klik.

**File kena:** `pipeline/stage5_assembly.py`, `server.py`, `frontend/src/TimelineEditor.jsx`

**Detail eksekusi:**
1. Preset filter (ffmpeg `eq`/`colorbalance`/`curves`):
   - `original` (tanpa filter), `warm`, `cool`, `bright`, `vivid` (saturasi+),
     `bw` (grayscale), `cinematic` (teal-orange), `vintage` (sepia lembut).
2. `filter: str = "original"` per segmen (bukan global) — `segments[i].filter`.
3. Penerapan: di `assemble_video`, clip per segmen di-pass filter chain-nya
   (`clip.fx(vfx.colorx, ...)` atau ffmpeg filter saat `write_videofile` via
   `ffmpeg_params`) — pilih yang paling stabil di moviepy 2.x.
4. UI: dropdown "Filter" di panel per clip.

**Acceptance criteria:** pilih filter beda per segmen → render final tiap segmen
kepengaruh filternya, segmen tanpa filter tetap original.

**Effort:** Kecil–Sedang

---

### P1.4 Sticker / gambar overlay

**Masalah konkret:** gak bisa nambah elemen visual non-video (logo animasi,
emoji gede, callout gambar) di atas footage.

**File kena:** `server.py`, `pipeline/stage5_assembly.py`, `frontend/src/TimelineEditor.jsx` (track STICKER), `uploads/` (reuse watermark upload pattern)

**Detail eksekusi:**
1. Reuse pola watermark (5.2) tapi jadi per-segmen & bisa banyak: model
   `StickerOverlay { id, segment_index, path, start_offset, duration, x, y
   (fraksi 0-1), scale, rotation }`.
2. Upload sticker ke `uploads/stickers/` (PNG transparan, reuse `_validate_upload`).
3. Render: ImageClip overlay di posisi fraksi, resize + rotasi via PIL/moviepy,
   layer order: `[full_video, *sticker_layers, *watermark, *caption]`.
4. UI: track STICKER di timeline + tombol upload, drag posisi (atau input X/Y %),
   slider scale/rotasi.

**Acceptance criteria:** upload PNG transparan → taruh di segmen tertentu dengan
posisi/ukuran/rotasi dipilih → render final menampilkannya di posisi itu, ikut
tersimpan di project.

**Effort:** Sedang

---

## Fase P2 — Advanced Editing

### P2.1 Speed control per clip (slow-mo / fast-mo)

**Masalah konkret:** gak bisa ubah kecepatan klip — footage yang kepanjangan gak
bisa dipadatkan, momen penting gak bisa di-slow.

**File kena:** `pipeline/stage5_assembly.py`, `server.py`, `frontend/src/TimelineEditor.jsx`, `pipeline/stage3_narration.py` (kalau audio ikut stretch)

**Detail eksekusi:**
1. `speed: float = 1.0` per segmen (0.25–4x). Video: `clip.fx(vfx.speedx, speed)`.
2. **Audio**: kalau segmen punya narasi (`audio_path`), speed harus di-apply juga —
   ffmpeg `atempo` (range 0.5–2) / `asetrate`+`aresample` (pitch preserved) — biar
   audio-video tetap sinkron. Kalau speed <0.5 atau >2, chain atempo ganda.
3. Durasi segmen berubah → posisi kumulatif dihitung ulang → caption re-sync
   (reuse `transcribe_segment_audio` — 3.4). UI kasih warning "caption akan
   disinkronkan ulang".
4. UI: field/slider speed di panel per clip + badge `1.5x` di clip timeline.

**Acceptance criteria:** set 2x di segmen footage tanpa narasi → segmen jadi setengah
durasi, video + (kalau ada) audio tetap sinkron, caption ikut timing baru.

**Effort:** Besar (audio time-stretch + re-sync caption = paling rawan desync)

---

### P2.2 Multi-aspect export 1 klik

**Masalah konkret:** content creator butuh 9:16 (Reels/TikTok), 16:9 (YouTube),
1:1 (feed) dari project yang sama — sekarang render ulang manual per aspect.

**File kena:** `pipeline/stage5_assembly.py` (resolution param sudah ada),
`server.py`, `frontend/src/TimelineEditor.jsx` (finishing)

**Detail eksekusi:**
1. Di finishing: opsi "Export aspect" → `9:16` / `16:9` / `1:1` / `Semua`.
2. `assemble_video` sudah punya `resolution` param — tiap aspect = render terpisah
   dengan center-crop (pola `_fit_to_aspect` sudah ada di stage5 line 59) + posisi
   caption disesuaikan (safe area 9:16 — pola clipper).
3. Kalau "Semua": 3 render sequential dalam 1 job (reuse job_manager), hasil zip.

**Acceptance criteria:** 1 project → pilih "Semua" → 3 file (9:16, 16:9, 1:1) tanpa
harus ubah-ubah project, semua footage center-crop rapi + caption gak kepotong.

**Effort:** Kecil–Sedang (fondasi resolution sudah ada)

---

### P2.3 Chroma key / background removal (opsional, AI)

**Masalah konkret:** fitur CapCut Pro paling ikonik — hapus background biar subjek
bisa di-overlay di footage lain.

**File kena:** `pipeline/stage5_assembly.py`, baru `pipeline/background_remover.py`, `server.py`, `requirements.txt`

**Detail eksekusi:**
1. `rembg` (U2Net) local — gak butuh API berbayar. Apply per segmen
   (`remove_bg: bool`).
2. Precompute saat import footage (mirip sidecar `.emb.json`) biar render gak nunggu
   — hasil disimpen sebagai clip PNG sequence / video alpha.
3. UI: toggle "Hapus Background" per clip + preview.
4. **Trade-off**: rembg lambat di CPU (~detik per frame) — dokumentasikan, kasih
   opsi low-res precompute.

**Acceptance criteria:** footage subjek (orang/produk) → toggle hapus bg → render
final subjek tanpa background di atas footage lain.

**Effort:** Besar (perf + integrasi render)

---

## Fase P3 — Pro Polish

### P3.1 Keyframe animation (posisi / scale / rotasi)

**Masalah konkret:** gak ada animasi — elemen statis. CapCut Pro: elemen bisa
bergerak (fade-in dari bawah, zoom-in judul, sticker geser).

**File kena:** `pipeline/stage5_assembly.py`, `frontend/src/TimelineEditor.jsx`, `server.py`

**Detail eksekusi:**
1. Model `Keyframe { t, x, y, scale, rotation }` per elemen (video clip / text /
   sticker), minimal 2 keyframe (start/end), interpolasi linear + ease-in-out.
2. Render: moviepy `position=lambda t: ...` / `resized(lambda t)` — interpolasi
   antar keyframe di lambda.
3. UI: track animasi per elemen terpilih (timeline mini dengan 2+ diamond point,
   klik untuk edit nilai X/Y/scale/rot).

**Acceptance criteria:** judul bisa animasi naik + fade-in saat segmen mulai,
sticker bisa geser dari kiri ke kanan — render final mulus (bukan lompat).

**Effort:** Besar (paling kompleks — UI keyframe editor + interpolasi render)

---

### P3.2 Analytics project (WPM, durasi, scene count)

**Masalah konkret:** gak ada insight soal project — berapa WPM narasi, berapa scene,
durasi per bab, mood dominan.

**File kena:** `server.py`, `frontend/src/ProjectsPage.jsx` (P0.1) / StudioPage

**Detail eksekusi:**
1. Hitung saat save (P0.1): WPM = total kata narasi / durasi, scene count (jumlah
   segmen + jumlah footage), durasi per segmen, template dipakai.
2. Simpan di metadata `project.json`; tampilkan di kartu project + panel studio.

**Acceptance criteria:** buka grid project → tiap kartu nampilin WPM + durasi +
scene count; buka project → panel info muncul.

**Effort:** Kecil

---

## Ringkasan Prioritas Eksekusi

| Urutan | Task | Effort | Alasan urutan |
|---|---|---|---|
| 1 | P0.1 — Project Library | Sedang | Fondasi: semua fitur lain bisa diuji di project tersimpan |
| 2 | P1.1 — Text/Title overlay | Sedang | Paling sering dipakai, langsung "keliatan dibikin" |
| 3 | P1.3 — Filters | Kecil–Sedang | Cepat, visual impact tinggi |
| 4 | P1.2 — Transitions library | Sedang–Besar | Visual, tapi ubah model data (per-cut) |
| 5 | P1.4 — Sticker overlay | Sedang | Mirip watermark 5.2, pola sudah ada |
| 6 | P2.2 — Multi-aspect export | Kecil–Sedang | Fondasi resolution sudah ada |
| 7 | P2.1 — Speed control | Besar | Rawan desync — kerjakan setelah yang ringan kelar |
| 8 | P3.1 — Keyframe | Besar | Butuh semua elemen (text/sticker) ada dulu |
| 9 | P3.2 — Analytics | Kecil | Bonus, bisa kapan aja |
| opt | P2.3 — Chroma key | Besar | Opsional — perf berat di CPU |

*Dokumen ini living roadmap — update checkbox tiap task selesai, tambah temuan baru
pas eksplorasi kode lebih dalam.*
