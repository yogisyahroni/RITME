"""
Project Exporter — Generate editable project files for video editors.

Supported formats:
  - EDL (CMX 3600)      : Adobe Premiere, DaVinci Resolve, Avid, FCP (via import)
  - FCPXML              : Final Cut Pro, Adobe Premiere, DaVinci Resolve
  - Premiere XML (FCP7) : Adobe Premiere Pro (File > Import)
  - CapCut JSON         : CapCut Desktop (manual import via timeline)
"""
import json
import os
import subprocess
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _timecode(seconds: float) -> str:
    """Convert seconds to HH:MM:SS:FF timecode (24fps)."""
    fps = 24
    total_frames = int(seconds * fps)
    h = total_frames // (fps * 3600)
    m = (total_frames % (fps * 3600)) // (fps * 60)
    s = (total_frames % (fps * 60)) // fps
    f = total_frames % fps
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _smtpe_tc(seconds: float) -> str:
    """Convert seconds to SMPTE timecode for EDL (drop-frame style)."""
    fps = 30
    total_frames = int(seconds * fps)
    h = total_frames // (fps * 3600)
    m = (total_frames % (fps * 3600)) // (fps * 60)
    s = (total_frames % (fps * 60)) // fps
    f = total_frames % fps
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _resolve_finishing(finishing: dict | None) -> dict:
    """Normalize the optional finishing dict (Fase 1C.2)."""
    f = finishing or {}
    return {
        "music_path": str(f.get("music_path") or ""),
        "music_mood": f.get("music_mood"),
        "caption_style": f.get("caption_style") or "minimal-white-center",
        "transition_style": f.get("transition_style") or "hard_cut",
        "ken_burns": bool(f.get("ken_burns")),
    }


def _finishing_notes(finishing: dict | None) -> list[str]:
    """Human-readable finishing summary lines (used in guides/README)."""
    f = _resolve_finishing(finishing)
    notes = []
    music_name = Path(f["music_path"]).name if f["music_path"] else None
    if music_name:
        mood = f" (mood: {f['music_mood']})" if f["music_mood"] else ""
        notes.append(f"Musik latar: {music_name}{mood}")
    else:
        notes.append("Musik latar: tidak ada")
    notes.append(f"Caption style: {f['caption_style']}")
    notes.append(f"Transisi: {f['transition_style']}")
    notes.append(f"Ken Burns: {'ya' if f['ken_burns'] else 'tidak'}")
    return notes


# ---------------------------------------------------------------------------
# EDL (CMX 3600) — most universal format
# ---------------------------------------------------------------------------
def generate_edl(timed_segments: list[dict], footage_map: dict, narration_audio: str,
                  output_name: str = "ritme_project", finishing: dict | None = None) -> str:
    """
    Generate an EDL file compatible with Premiere, DaVinci Resolve, Avid.
    Each narration segment maps to one video clip on the V1 track.
    The narration audio sits on A1/A2 (stereo); background music (when given)
    is added as an extra A-track event (Fase 1C.2).
    """
    f = _resolve_finishing(finishing)
    lines = [
        f"TITLE: {output_name}",
        "FCM: NON-DROP FRAME",
        ""
    ]

    record_cursor = 0.0  # tracks position on the timeline

    for idx, seg in enumerate(timed_segments):
        clip = footage_map.get(idx)
        if not clip:
            continue

        source_path = clip.get("video_path", "")
        source_duration = _get_video_duration(source_path) if os.path.exists(source_path) else 10.0
        seg_duration = seg.get("duration", 3.0)

        # Source in/out: use first N seconds of source clip
        source_in = 0.0
        source_out = min(source_duration, seg_duration)

        event_num = idx + 1
        reel = Path(source_path).stem[:8]  # EDL reel field is max 8 chars
        track = "V"

        rec_in = _smtpe_tc(record_cursor)
        rec_out = _smtpe_tc(record_cursor + seg_duration)

        line = f"{event_num:03d}  {reel:<8s}  {track}     C        {_smtpe_tc(source_in)} {_smtpe_tc(source_out)} {rec_in} {rec_out}"
        lines.append(line)
        lines.append(f"* FROM CLIP NAME: {Path(source_path).name}")
        lines.append(f"* COMMENT: {seg.get('text', '')[:60]}")
        lines.append("")

        record_cursor += seg_duration

    # Add narration audio track
    if narration_audio and os.path.exists(narration_audio):
        total_dur = record_cursor
        lines.append(f"099  AUD     A     C        {_smtpe_tc(0)} {_smtpe_tc(total_dur)} {_smtpe_tc(0)} {_smtpe_tc(total_dur)}")
        lines.append(f"* FROM CLIP NAME: {Path(narration_audio).name}")
        lines.append(f"* COMMENT: Narration audio track")
        lines.append("")

    # Add background music track (Fase 1C.2)
    if f["music_path"] and os.path.exists(f["music_path"]):
        total_dur = record_cursor
        lines.append(f"100  MUS     A     C        {_smtpe_tc(0)} {_smtpe_tc(total_dur)} {_smtpe_tc(0)} {_smtpe_tc(total_dur)}")
        lines.append(f"* FROM CLIP NAME: {Path(f['music_path']).name}")
        mood = f" (mood: {f['music_mood']})" if f["music_mood"] else ""
        lines.append(f"* COMMENT: Background music{mood}")

    # Finishing notes as EDL comments (Fase 1C.2)
    for note in _finishing_notes(finishing):
        lines.append(f"* NOTE: {note}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FCPXML — Final Cut Pro / Premiere / DaVinci
# ---------------------------------------------------------------------------
def generate_fcpxml(timed_segments: list[dict], footage_map: dict, narration_audio: str,
                     output_name: str = "ritme_project", finishing: dict | None = None) -> str:
    """Generate FCPXML 1.8 compatible with FCP, Premiere Pro, DaVinci Resolve."""
    f = _resolve_finishing(finishing)

    total_duration = sum(s.get("duration", 3.0) for s in timed_segments)
    total_frames = int(total_duration * 30)  # 30fps

    clips_xml = ""
    offset = 0

    for idx, seg in enumerate(timed_segments):
        clip = footage_map.get(idx)
        if not clip:
            continue

        source_path = clip.get("video_path", "")
        source_duration = _get_video_duration(source_path) if os.path.exists(source_path) else 10.0
        seg_duration = seg.get("duration", 3.0)
        seg_frames = int(seg_duration * 30)
        src_frames = int(min(source_duration, seg_duration) * 30)

        clips_xml += f"""
                <clip name="{Path(source_path).stem}" offset="{offset}" duration="{seg_frames}/30s">
                    <media>
                        <video>
                            <format>
                                <video-format id="r3" name="FFVideoFormat1080p30" frameDuration="1001/30000s" width="1920" height="1080"/>
                            </format>
                            <source clip="{Path(source_path).stem}" start="0/{30}s" duration="{src_frames}/30s"/>
                        </video>
                    </media>
                </clip>"""
        offset += seg_frames

    # Add narration audio
    audio_xml = ""
    if narration_audio and os.path.exists(narration_audio):
        audio_frames = int(total_duration * 30)
        audio_xml = f"""
                <clip name="{Path(narration_audio).stem}" offset="0" duration="{audio_frames}/30s">
                    <media>
                        <audio>
                            <format audio-format="r3" mediaRepLocation="local">
                                <audio-format id="r3" channelCount="2" sampleRate="48000"/>
                            </format>
                            <source start="0/48000s" duration="{audio_frames}/30s"/>
                        </audio>
                    </media>
                </clip>"""

    # Add background music as a second audio clip (Fase 1C.2)
    if f["music_path"] and os.path.exists(f["music_path"]):
        music_frames = int(total_duration * 30)
        audio_xml += f"""
                <clip name="{Path(f['music_path']).stem}" offset="0" duration="{music_frames}/30s">
                    <media>
                        <audio>
                            <format audio-format="r3" mediaRepLocation="local">
                                <audio-format id="r3" channelCount="2" sampleRate="48000"/>
                            </format>
                            <source start="0/48000s" duration="{music_frames}/30s"/>
                        </audio>
                    </media>
                </clip>"""

    # Finishing metadata as XML comments (Fase 1C.2)
    notes_xml = "".join(f"<!-- {note} -->\n" for note in _finishing_notes(finishing))

    fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" name="FFVideoFormatRateProject" frameDuration="1001/30000s"/>
        <format id="r3" name="FFVideoFormat1080p30" frameDuration="1001/30000s" width="1920" height="1080"/>
    </resources>
{notes_xml}    <library>
        <event name="{output_name}">
            <project name="{output_name}">
                <sequence format="r1" duration="{total_frames}/30s" tcStart="0/30s">
                    <spine>
                        <gap name="Gap" offset="0" duration="{total_frames}/30s">
{clips_xml}
{audio_xml}
                        </gap>
                    </spine>
                </sequence>
            </project>
        </event>
    </library>
</fcpxml>"""

    return fcpxml


# ---------------------------------------------------------------------------
# Premiere XML (FCP7 format) — Adobe Premiere Pro
# ---------------------------------------------------------------------------
def generate_premiere_xml(timed_segments: list[dict], footage_map: dict, narration_audio: str,
                           output_name: str = "ritme_project", finishing: dict | None = None) -> str:
    """Generate FCP7 XML for Adobe Premiere Pro import (File > Import)."""
    f = _resolve_finishing(finishing)

    clips_xml = ""
    clip_id = 0
    total_duration = 0

    for idx, seg in enumerate(timed_segments):
        clip = footage_map.get(idx)
        if not clip:
            continue

        source_path = clip.get("video_path", "")
        source_duration = _get_video_duration(source_path) if os.path.exists(source_path) else 10.0
        seg_duration = seg.get("duration", 3.0)

        clip_id += 1
        clip_name = Path(source_path).stem[:32]
        duration_frames = int(seg_duration * 25)  # 25fps PAL
        src_frames = int(min(source_duration, seg_duration) * 25)

        clips_xml += f"""
    <clipitem id="clipitem-{clip_id}">
      <name>{clip_name}</name>
      <duration>{src_frames}/25s</duration>
      <rate>
        <timebase>25</timebase>
        <ntsc>FALSE</ntsc>
      </rate>
      <start>0/25s</start>
      <end>{src_frames}/25s</end>
      <in>0/25s</in>
      <out>{src_frames}/25s</out>
      <file id="file-{clip_id}">
        <name>{clip_name}</name>
        <pathurl>file:///{Path(source_path).as_posix()}</pathurl>
        <rate>
          <timebase>25</timebase>
          <ntsc>FALSE</ntsc>
        </rate>
        <duration>{src_frames}/25s</duration>
      </file>
    </clipitem>"""
        total_duration += seg_duration

    total_frames = int(total_duration * 25)

    # Second audio track for background music (Fase 1C.2)
    music_track = ""
    if f["music_path"] and os.path.exists(f["music_path"]):
        music_track = f"""
        <track>
          <clipitem id="clipitem-music">
            <name>{Path(f['music_path']).stem}</name>
            <duration>{total_frames}/25s</duration>
            <rate><timebase>25</timebase></rate>
            <start>0/25s</start>
            <end>{total_frames}/25s</end>
            <in>0/25s</in>
            <out>{total_frames}/25s</out>
            <file id="file-music">
              <name>{Path(f['music_path']).stem}</name>
              <pathurl>file:///{Path(f['music_path']).as_posix()}</pathurl>
              <rate><timebase>25</timebase></rate>
              <duration>{total_frames}/25s</duration>
            </file>
          </clipitem>
        </track>"""

    # Finishing metadata as XML comments (Fase 1C.2)
    notes_xml = "".join(f"      <!-- {note} -->\n" for note in _finishing_notes(finishing))

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence>
    <name>{output_name}</name>
    <duration>{total_frames}/25s</duration>
    <rate>
      <timebase>25</timebase>
      <ntsc>FALSE</ntsc>
    </rate>
    <media>
      <video>
        <track>
{clips_xml}
        </track>
      </video>
      <audio>
        <track>
          <clipitem id="clipitem-audio">
            <name>narration</name>
            <duration>{total_frames}/25s</duration>
            <rate><timebase>25</timebase></rate>
            <start>0/25s</start>
            <end>{total_frames}/25s</end>
            <in>0/25s</in>
            <out>{total_frames}/25s</out>
            <file id="file-audio">
              <name>{Path(narration_audio).stem if narration_audio else 'narration'}</name>
              <pathurl>file:///{Path(narration_audio).as_posix() if narration_audio else ''}</pathurl>
              <rate><timebase>25</timebase></rate>
              <duration>{total_frames}/25s</duration>
            </file>
          </clipitem>
        </track>
{music_track}      </audio>
    </media>
{notes_xml}  </sequence>
</xmeml>"""

    return xml


# ---------------------------------------------------------------------------
# CapCut Guide + Renamed Footage
# ---------------------------------------------------------------------------
def generate_capcut_guide(timed_segments: list[dict], footage_map: dict,
                           narration_audio: str, output_name: str = "ritme_project",
                           finishing: dict | None = None) -> str:
    """
    Generate an HTML timeline guide for CapCut Desktop.
    
    CapCut doesn't support JSON/XML project import. Two methods work:
    
    METHOD 1 (Recommended): Import EDL
      CapCut Desktop (v3.0+) supports EDL import.
      Buka CapCut > File > Import > pilih ritme_project.edl
    
    METHOD 2: Manual drag-drop
      1. Buka CapCut > Buat project baru
      2. Drag semua file dari folder /footage/ (sudah di-rename berurutan)
      3. Drag narration audio ke audio track
      4. Potong dan susun clips sesuai durasi di guide ini
    
    This HTML guide shows the complete timeline visually.
    """
    html_parts = [f"""<!DOCTYPE html>
<html lang="id">
<head><meta charset="utf-8"><title>{output_name} - Timeline Guide</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; font-family: system-ui, sans-serif; }}
  body {{ background: #111; color: #eee; padding: 24px; max-width: 800px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin-bottom: 4px; }}
  p {{ color: #888; font-size: 13px; margin-bottom: 24px; }}
  .segment {{ background: #1e1e1e; border: 1px solid #333; border-radius: 6px; margin-bottom: 8px; padding: 16px; }}
  .seg-header {{ display: flex; gap: 16px; margin-bottom: 8px; }}
  .seg-num {{ background: #e8542e; color: #fff; padding: 2px 10px; border-radius: 4px; font-size: 12px; font-weight: 700; }}
  .seg-time {{ color: #6fe7dd; font-family: monospace; font-size: 14px; }}
  .seg-file {{ color: #e8a33d; font-family: monospace; font-size: 12px; }}
  .seg-dur {{ color: #888; font-size: 12px; }}
  .seg-text {{ color: #c4b9a8; font-size: 13px; margin-top: 4px; line-height: 1.5; }}
  .seg-kw {{ color: #7fb88a; font-size: 11px; margin-top: 4px; }}
  .total {{ margin-top: 24px; padding: 16px; background: #1a1a2e; border-radius: 6px; text-align: center; }}
  .method {{ background: #1e2833; border: 1px solid #2a3a4a; border-radius: 6px; padding: 16px; margin-bottom: 24px; }}
  .method h2 {{ color: #6fe7dd; font-size: 15px; margin-bottom: 8px; }}
  .method li {{ color: #c4b9a8; font-size: 12px; margin: 4px 0; margin-left: 20px; }}
</style></head><body>
<h1>{output_name}</h1>
<p>CapCut Desktop Timeline Guide — Generated by RITME</p>

<div class="method">
<h2>  Cara 1: Import EDL (CapCut v3.0+)</h2>
<ol>
  <li>Buka CapCut Desktop</li>
  <li>File > Import Project > pilih <strong>{output_name}.edl</strong></li>
  <li>Footage dan audio akan otomatis tersusun di timeline</li>
</ol>
<h2 style="margin-top:12px">  Cara 2: Manual Drag-Drop</h2>
<ol>
  <li>Buka CapCut > Buat project baru (1920x1080, 30fps)</li>
  <li>Drag SEMUA file dari folder <strong>/footage/</strong> ke media panel</li>
  <li>Drag <strong>narration audio</strong> ke audio track</li>
  <li>Susun clips sesuai tabel di bawah</li>
</ol>
</div>

<h2>Timeline ({len(timed_segments)} segments, {sum(s.get('duration',3) for s in timed_segments):.1f}s)</h2>
"""
    ]

    # Finishing section (Fase 1C.2) — tells the user exactly what to re-apply
    # manually in CapCut (music track + caption style + transitions).
    f = _resolve_finishing(finishing)
    music_name = Path(f["music_path"]).name if f["music_path"] else "tidak ada"
    mood_txt = f" (mood: {f['music_mood']})" if f["music_mood"] else ""
    html_parts.append(f"""
<div class="finishing" style="background:#1e2833;border:1px solid #2a3a4a;border-radius:6px;padding:16px;margin-bottom:24px">
  <h2 style="color:#e8a33d;font-size:15px;margin-bottom:8px">Elemen Finishing (Fase 1)</h2>
  <ul style="color:#c4b9a8;font-size:12px;line-height:1.7">
    <li><strong>Musik latar:</strong> {music_name}{mood_txt} — drag file musik dari folder export ke audio track (di bawah narasi), atur volume agar tidak menenggelamkan narasi (~35%).</li>
    <li><strong>Caption style:</strong> {f["caption_style"]} — tambahkan teks per segmen sesuai gaya ini:
      <em>{"bold putih bawah" if f["caption_style"] == "bold-white-bottom" else ("putih tipis tengah" if f["caption_style"] == "minimal-white-center" else "lower-third dengan bar semi-transparan")}</em>.</li>
    <li><strong>Transisi:</strong> {f["transition_style"]}{" — tambahkan crossfade 0.5s antar klip" if f["transition_style"] == "crossfade" else " — potongan langsung (tanpa transisi)"}.</li>
    <li><strong>Ken Burns:</strong> {"ya — tambahkan zoom pelan (1.0 → 1.06) pada klip ≥2 detik" if f["ken_burns"] else "tidak"}.</li>
  </ul>
</div>
""")

    time_cursor = 0.0
    for idx, seg in enumerate(timed_segments):
        clip = footage_map.get(idx)
        seg_dur = seg.get("duration", 3.0)
        clip_path = Path(clip.get("video_path", "")).name if clip else "-"
        keywords = ", ".join(seg.get("keywords", []))
        text = seg.get("text", "")[:120]

        time_start = time_cursor
        time_end = time_cursor + seg_dur
        time_cursor = time_end

        def fmt(s):
            m = int(s // 60)
            sec = s % 60
            return f"{m}:{sec:05.2f}"

        html_parts.append(f"""
<div class="segment">
  <div class="seg-header">
    <div class="seg-num">SEG {idx+1:02d}</div>
    <div class="seg-time">{fmt(time_start)} – {fmt(time_end)}</div>
    <div class="seg-file">{clip_path}</div>
    <div class="seg-dur">{seg_dur:.1f}s</div>
  </div>
  <div class="seg-text">{text}</div>
  <div class="seg-kw">Keywords: {keywords}</div>
</div>""")

    total = sum(s.get("duration", 3.0) for s in timed_segments)
    html_parts.append(f"""
<div class="total">
  <p style="font-size:16px;color:#6fe7dd;font-weight:600">Total Durasi: {fmt(total)} menit</p>
  <p style="font-size:12px;color:#888;margin-top:4px">{len(timed_segments)} segmen menggunakan {len(footage_map)} file footage</p>
</div>
</body></html>""")

    return "\n".join(html_parts)


# Copy and rename footage for CapCut (numbered order)
def capcut_prepare_footage(timed_segments: list[dict], footage_map: dict,
                            footage_dir: Path) -> dict[int, str]:
    """
    Copy footage clips with numbered filenames (01_intro.mp4, 02_topic.mp4, etc.)
    Returns {segment_index: new_filename} mapping.
    """
    import shutil
    result = {}
    for idx, seg in enumerate(timed_segments):
        clip = footage_map.get(idx)
        if not clip:
            continue
        src = clip.get("video_path", "")
        if not src or not os.path.exists(src):
            continue
        
        # Build filename: 01_keyword.mp4
        kw = seg.get("keywords", [])
        kw_part = kw[0].replace(" ", "_")[:20] if kw else "clip"
        # Keep only safe chars
        kw_part = "".join(c if c.isalnum() or c == '_' else '' for c in kw_part)
        if not kw_part:
            kw_part = "clip"
            
        new_name = f"{idx+1:02d}_{kw_part}.mp4"
        dst = footage_dir / new_name
        # Avoid overwrite
        counter = 1
        while dst.exists():
            dst = footage_dir / f"{idx+1:02d}_{kw_part}_{counter}.mp4"
            counter += 1
            
        shutil.copy2(src, dst)
        result[idx] = dst.name
        
    return result


# ---------------------------------------------------------------------------
# Main export function — bundles everything into a zip
# ---------------------------------------------------------------------------
def export_project(timed_segments: list[dict], footage_map: dict,
                    narration_audio: str, output_name: str = "ritme_project",
                    formats: list[str] = None,
                    finishing: dict | None = None) -> str:
    """
    Export project in multiple formats. Returns path to the zip file.

    Supported formats: edl, fcpxml, premiere_xml, capcut_json
    finishing: optional dict {music_path, music_mood, caption_style,
               transition_style, ken_burns} — embedded into the exported
               files so editors know what Fase-1 elements to re-apply
               (Fase 1C.2).
    """
    if formats is None:
        formats = ["edl", "fcpxml", "premiere_xml", "capcut_json"]

    export_dir = Path(tempfile.mkdtemp(prefix="ritme_export_"))
    zip_path = Path(f"output/{output_name}_project.zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate project files
    if "edl" in formats:
        edl_content = generate_edl(timed_segments, footage_map, narration_audio, output_name, finishing)
        (export_dir / f"{output_name}.edl").write_text(edl_content, encoding="utf-8")

    if "fcpxml" in formats:
        fcpxml_content = generate_fcpxml(timed_segments, footage_map, narration_audio, output_name, finishing)
        (export_dir / f"{output_name}.fcpxml").write_text(fcpxml_content, encoding="utf-8")

    if "premiere_xml" in formats:
        premiere_content = generate_premiere_xml(timed_segments, footage_map, narration_audio, output_name, finishing)
        (export_dir / f"{output_name}_premiere.xml").write_text(premiere_content, encoding="utf-8")

    if "capcut_json" in formats:
        capcut_content = generate_capcut_guide(timed_segments, footage_map, narration_audio, output_name, finishing)
        (export_dir / f"{output_name}_timeline.html").write_text(capcut_content, encoding="utf-8")

    # Copy narration audio
    if narration_audio and os.path.exists(narration_audio):
        import shutil
        audio_dest = export_dir / Path(narration_audio).name
        shutil.copy2(narration_audio, audio_dest)

    # Copy background music into the export (Fase 1C.2)
    f = _resolve_finishing(finishing)
    if f["music_path"] and os.path.exists(f["music_path"]):
        import shutil
        shutil.copy2(f["music_path"], export_dir / Path(f["music_path"]).name)

    # Copy footage clips with numbered names for CapCut
    footage_dir = export_dir / "footage"
    footage_dir.mkdir(exist_ok=True)
    import shutil
    copied = set()
    capcut_rename = {}
    for idx, clip in (footage_map or {}).items():
        src = clip.get("video_path", "")
        if src and os.path.exists(src) and src not in copied:
            shutil.copy2(src, footage_dir / Path(src).name)
            copied.add(src)
    
    # Generate numbered footage specifically for CapCut
    capcut_dir = export_dir / "capcut_footage"
    capcut_dir.mkdir(exist_ok=True)
    capcut_rename = capcut_prepare_footage(timed_segments, footage_map, capcut_dir)

    # Write README
    finishing_notes = "\n".join(f"- {n}" for n in _finishing_notes(finishing))
    readme = f"""# RITME Project Export: {output_name}

## Format yang tersedia:

### 1. EDL ({output_name}.edl)
- **Kompatibel:** Adobe Premiere Pro, DaVinci Resolve, Final Cut Pro, Avid
- **Cara buka:** File > Import > pilih file .edl

### 2. FCPXML ({output_name}.fcpxml)
- **Kompatibel:** Final Cut Pro, Adobe Premiere Pro (CC 2022+), DaVinci Resolve 18+
- **Cara buka:**
  - FCP: File > Import > XML
  - Premiere: File > Import > pilih file .fcpxml
  - DaVinci: File > Import > Timeline

### 3. Premiere XML ({output_name}_premiere.xml)
- **Kompatibel:** Adobe Premiere Pro (semua versi)
- **Cara buka:** File > Import > pilih file .xml

### 4. CapCut Guide ({output_name}_timeline.html + capcut_footage/)
- **Kompatibel:** CapCut Desktop v3.0+
- **Cara buka:** Buka file .html untuk lihat timeline guide
- **Method 1:** File > Import Project > pilih ritme_project.edl (CapCut v3.0+)
- **Method 2:** Drag semua file dari /capcut_footage/ ke media panel, susun sesuai guide

## Struktur folder:
- /footage/ = Klip video original dari pipeline
- /capcut_footage/ = Klip video di-rename berurutan (01_intro.mp4, 02_keyword.mp4, dst)
- /narration audio = File audio narasi
- /music file = File musik latar (kalau ada)

## Elemen Finishing (Fase 1) — harus di-replicate manual di editor:
{finishing_notes}

## Tips:
- CapCut Desktop tidak support import JSON. Gunakan EDL (Method 1) atau manual drag-drop
- File EDL dibuka via File > Import Project di CapCut Desktop v3.0+
- File di /capcut_footage/ sudah di-rename berurutan untuk drag-drop mudah
- Jika file tidak ditemukan di editor, pastikan path absolut masih valid
- Musik latar: drag ke audio track di bawah narasi, atur volume ~35% (auto-ducking di RITME sudah hilang karena export ke editor eksternal)
- Jika musik dipakai di video yang dimonetisasi, beri atribusi (lihat music/LICENSES.md di repo RITME)
"""
    (export_dir / "README.md").write_text(readme, encoding="utf-8")

    # Create zip
    with zipfile.ZipFile(str(zip_path), 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in export_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(export_dir)
                zf.write(str(file_path), str(arcname))

    # Cleanup temp dir
    import shutil
    shutil.rmtree(str(export_dir), ignore_errors=True)

    return str(zip_path)


