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


# ---------------------------------------------------------------------------
# EDL (CMX 3600) — most universal format
# ---------------------------------------------------------------------------
def generate_edl(timed_segments: list[dict], footage_map: dict, narration_audio: str,
                  output_name: str = "ritme_project") -> str:
    """
    Generate an EDL file compatible with Premiere, DaVinci Resolve, Avid.
    Each narration segment maps to one video clip on the V1 track.
    The narration audio sits on A1/A2 (stereo).
    """
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

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FCPXML — Final Cut Pro / Premiere / DaVinci
# ---------------------------------------------------------------------------
def generate_fcpxml(timed_segments: list[dict], footage_map: dict, narration_audio: str,
                     output_name: str = "ritme_project") -> str:
    """Generate FCPXML 1.8 compatible with FCP, Premiere Pro, DaVinci Resolve."""

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

    fcpxml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
    <resources>
        <format id="r1" name="FFVideoFormatRateProject" frameDuration="1001/30000s"/>
        <format id="r3" name="FFVideoFormat1080p30" frameDuration="1001/30000s" width="1920" height="1080"/>
    </resources>
    <library>
        <event name="{output_name}">
            <project name="{output_name}">
                <sequence format="r1" duration="{total_frames}/30s" tcStart="0/30s">
                    <spine>
                        <gap name="Gap" offset="0" duration="{total_frames}/30s">
{clips_xml}
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
                           output_name: str = "ritme_project") -> str:
    """Generate FCP7 XML for Adobe Premiere Pro import (File > Import)."""

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
      </audio>
    </media>
  </sequence>
</xmeml>"""

    return xml


# ---------------------------------------------------------------------------
# CapCut JSON Timeline
# ---------------------------------------------------------------------------
def generate_capcut_json(timed_segments: list[dict], footage_map: dict,
                          narration_audio: str, output_name: str = "ritme_project") -> str:
    """
    Generate a JSON timeline that can be imported into CapCut Desktop.
    CapCut supports importing .json project files via File > Import.
    """
    tracks = {
        "video": [],
        "audio": []
    }

    time_offset = 0  # microseconds

    for idx, seg in enumerate(timed_segments):
        clip = footage_map.get(idx)
        if not clip:
            continue

        source_path = clip.get("video_path", "")
        seg_duration = seg.get("duration", 3.0)
        duration_us = int(seg_duration * 1_000_000)

        video_track = {
            "id": f"video_{idx}",
            "type": "video",
            "material_id": f"mat_{idx}",
            "source_path": str(Path(source_path).resolve()) if os.path.exists(source_path) else "",
            "source_in": 0,
            "source_out": duration_us,
            "timeline_in": time_offset,
            "timeline_out": time_offset + duration_us,
            "duration": duration_us,
            "text": seg.get("text", ""),
            "keywords": seg.get("keywords", []),
            "speed": 1.0
        }
        tracks["video"].append(video_track)
        time_offset += duration_us

    # Narration audio track
    if narration_audio and os.path.exists(narration_audio):
        total_us = time_offset
        tracks["audio"].append({
            "id": "narration",
            "type": "audio",
            "material_id": "mat_narration",
            "source_path": str(Path(narration_audio).resolve()),
            "source_in": 0,
            "source_out": total_us,
            "timeline_in": 0,
            "timeline_out": total_us,
            "duration": total_us,
            "volume": 1.0
        })

    project = {
        "format_version": "1.0",
        "project_name": output_name,
        "created_at": datetime.now().isoformat(),
        "generator": "RITME Auto-Edit Pipeline",
        "resolution": {"width": 1920, "height": 1080},
        "fps": 30,
        "tracks": tracks,
        "total_duration_us": time_offset,
        "total_segments": len(timed_segments)
    }

    return json.dumps(project, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main export function — bundles everything into a zip
# ---------------------------------------------------------------------------
def export_project(timed_segments: list[dict], footage_map: dict,
                    narration_audio: str, output_name: str = "ritme_project",
                    formats: list[str] = None) -> str:
    """
    Export project in multiple formats. Returns path to the zip file.

    Supported formats: edl, fcpxml, premiere_xml, capcut_json
    """
    if formats is None:
        formats = ["edl", "fcpxml", "premiere_xml", "capcut_json"]

    export_dir = Path(tempfile.mkdtemp(prefix="ritme_export_"))
    zip_path = Path(f"output/{output_name}_project.zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    # Generate project files
    if "edl" in formats:
        edl_content = generate_edl(timed_segments, footage_map, narration_audio, output_name)
        (export_dir / f"{output_name}.edl").write_text(edl_content, encoding="utf-8")

    if "fcpxml" in formats:
        fcpxml_content = generate_fcpxml(timed_segments, footage_map, narration_audio, output_name)
        (export_dir / f"{output_name}.fcpxml").write_text(fcpxml_content, encoding="utf-8")

    if "premiere_xml" in formats:
        premiere_content = generate_premiere_xml(timed_segments, footage_map, narration_audio, output_name)
        (export_dir / f"{output_name}_premiere.xml").write_text(premiere_content, encoding="utf-8")

    if "capcut_json" in formats:
        capcut_content = generate_capcut_json(timed_segments, footage_map, narration_audio, output_name)
        (export_dir / f"{output_name}_capcut.json").write_text(capcut_content, encoding="utf-8")

    # Copy narration audio
    if narration_audio and os.path.exists(narration_audio):
        import shutil
        audio_dest = export_dir / Path(narration_audio).name
        shutil.copy2(narration_audio, audio_dest)

    # Copy footage clips
    footage_dir = export_dir / "footage"
    footage_dir.mkdir(exist_ok=True)
    import shutil
    copied = set()
    for idx, clip in (footage_map or {}).items():
        src = clip.get("video_path", "")
        if src and os.path.exists(src) and src not in copied:
            shutil.copy2(src, footage_dir / Path(src).name)
            copied.add(src)

    # Write README
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

### 4. CapCut JSON ({output_name}_capcut.json)
- **Kompatibel:** CapCut Desktop
- **Cara buka:** File > Import > pilih file .json

## Struktur folder:
- /footage/ = Klip video yang sudah di-match per segmen
- /narration audio = File audio narasi

## Tips:
- Semua format referensi footage dari folder /footage/
- Jika file tidak ditemukan di editor, pastikan path absolut masih valid
- Untuk CapCut, drag-drop footage secara manual ke timeline setelah import JSON
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
