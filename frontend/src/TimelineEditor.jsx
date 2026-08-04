import React, { useState, useEffect, useRef, useCallback } from "react";
import { Play, Download, Trash2, ArrowUp, ArrowDown, Scissors, Clapperboard, Loader2, AlertTriangle, Info, Film, Captions, Undo2, Redo2, Music2, GripVertical, ZoomIn, Zap, Save, Upload, FileText, Volume2, VolumeX, Library } from "lucide-react";

const C = {
  bg: "#15130F",
  panel: "#1E1B15",
  panelRaised: "#26221A",
  border: "#3A3226",
  borderSoft: "#2A251C",
  tally: "#E8542E",
  tallyDim: "#7A3324",
  amber: "#E8A33D",
  cyan: "#6FE7DD",
  paper: "#F3EEE3",
  paperDim: "#9C9384",
  paperFaint: "#6B6355",
  red: "#E8542E",
  music: "#8B5CF6",
  caption: "#7FB88A",
};

const F = {
  display: "'''Archivo Expanded''', sans-serif",
  body: "'''IBM Plex Sans''', sans-serif",
  mono: "'''IBM Plex Mono''', monospace",
};

function fmt(s) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

async function apiPostJSON(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function PrimaryButton({ children, onClick, icon: Icon, disabled, loading, variant }) {
  const bg = variant === "ghost" ? "transparent" : variant === "outline" ? C.panel : C.tally;
  const border = variant === "outline" ? C.borderSoft : "none";
  return (
    <button onClick={onClick} disabled={disabled || loading} className="flex items-center gap-2 px-4 py-2 rounded"
      style={{ background: disabled ? C.panelRaised : bg, color: disabled ? C.paperFaint : C.paper, fontFamily: F.body, fontWeight: 600, fontSize: 12.5, border: border, cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.6 : 1 }}>
      {loading && <Loader2 size={14} className="animate-spin" />} {children}
    </button>
  );
}

function IconButton({ onClick, icon: Icon, disabled, title, color }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      className="flex items-center justify-center rounded"
      style={{ width: 26, height: 26, background: "none", border: "none", cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.3 : 1, padding: 0 }}>
      <Icon size={14} color={color || C.paperDim} />
    </button>
  );
}

function TimelineEditor({ narration, footageData, picks }) {
  const [segments, setSegments] = useState([]);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [renderPath, setRenderPath] = useState("");  // server path hasil render
  const [playing, setPlaying] = useState(false);
  const [zoom, setZoom] = useState(1.0);           // 3.2 zoom
  const [autoPreview, setAutoPreview] = useState(false); // 3.5 auto preview
  const [dragIdx, setDragIdx] = useState(null);    // 3.2 drag reorder
  const [history, setHistory] = useState([]);      // 3.5 undo/redo
  const [future, setFuture] = useState([]);
  const [finishing, setFinishing] = useState({
    add_music: false,
    music_mood: "calm",
    caption_style: "minimal-white-center",
    transition_style: "hard_cut",
    ken_burns: false,
    aspect_ratio: "9:16",
    watermark_path: "",
    watermark_name: "",
    watermark_pos: "bottom-right",
  });
  const wmRef = useRef(null);

  const onWatermarkFile = async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    try {
      const fd = new FormData();
      fd.append("image", f);
      const res = await fetch("/api/watermark/upload", { method: "POST", body: fd });
      if (!res.ok) throw new Error("Upload watermark gagal");
      const data = await res.json();
      setFinishing(prev => ({ ...prev, watermark_path: data.watermark_path, watermark_name: f.name }));
    } catch (err) { setError(String(err)); }
  };
  const videoRef = useRef(null);
  const cancelRef = useRef(null);
  const firstRunRef = useRef(true);
  const restoredRef = useRef(false);
  const fileInputRef = useRef(null);
  const audioRefs = useRef({});
  // Fase 4: persist project (localStorage autosave + export/import JSON)
  const AUDIO_KEY = "ritme_timeline_project_v1";
  const [selectedIdx, setSelectedIdx] = useState(null);   // shortcut target
  const [playingAudio, setPlayingAudio] = useState(null); // segmen audio lagi play
  const [restoreNotice, setRestoreNotice] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);   // P0.1: modal simpan ke library
  const [saveName, setSaveName] = useState("");
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");
  // P1.1: text/title overlay manual per segmen
  const [titleOverlays, setTitleOverlays] = useState([]);
  // P1.4: sticker/gambar overlay manual per segmen
  const [stickerOverlays, setStickerOverlays] = useState([]);

  const STICKER_POSITIONS = [
    ["0.15,0.2", "Atas Kiri"], ["0.5,0.15", "Atas Tengah"], ["0.85,0.2", "Atas Kanan"],
    ["0.15,0.5", "Tengah Kiri"], ["0.5,0.5", "Tengah"], ["0.85,0.5", "Tengah Kanan"],
    ["0.15,0.85", "Bawah Kiri"], ["0.5,0.85", "Bawah Tengah"], ["0.85,0.85", "Bawah Kanan"],
  ];

  const TITLE_POSITIONS = [
    ["top-left", "Atas Kiri"], ["top-center", "Atas Tengah"], ["top-right", "Atas Kanan"],
    ["center-left", "Tengah Kiri"], ["center", "Tengah"], ["center-right", "Tengah Kanan"],
    ["bottom-left", "Bawah Kiri"], ["bottom-center", "Bawah Tengah"], ["bottom-right", "Bawah Kanan"],
  ];
  const TITLE_COLORS = ["#FFFFFF", "#FFD400", "#FF8A3D", "#6FE7DD", "#7FB88A", "#E8542E", "#E8A33D", "#C084FC"];
  // P1.3: color-grade presets per klip
  const FILTER_PRESETS = [
    ["original", "Asli"], ["warm", "Warm"], ["cool", "Cool"], ["bright", "Bright"],
    ["vivid", "Vivid"], ["bw", "B/W"], ["cinematic", "Cinematic"], ["vintage", "Vintage"],
  ];

  const pxPerSec = 28 * zoom;

  // Restore autosaved project on mount (sebelum narration load timpa)
  useEffect(() => {
    try {
      const raw = localStorage.getItem(AUDIO_KEY);
      if (!raw) return;
      const proj = JSON.parse(raw);
      if (proj?.segments?.length) {
        setSegments(proj.segments);
        setFinishing(f => ({ ...f, ...(proj.finishing || {}) }));
        setTitleOverlays(proj.title_overlays || proj.titleOverlays || []);
        setStickerOverlays(proj.sticker_overlays || proj.stickerOverlays || []);
        setRestoreNotice(true);
        restoredRef.current = true;
      }
    } catch { /* corrupt — abaikan */ }
  }, []);

  useEffect(() => {
    if (!narration?.segments) return;
    if (restoredRef.current) { restoredRef.current = false; return; }
    const segs = narration.segments.map((s, idx) => {
      const cand = footageData?.[String(idx)]?.candidates?.[picks?.[idx] ?? 0];
      return {
        index: idx,
        video_path: cand?.video_path || "",
        narration_text: s.text || "",
        duration: s.duration || 3.0,
        start_trim: 0,
        end_trim: 0,
        keywords: s.keywords || [],
        words: s.words || [],
      };
    });
    setSegments(segs);
    firstRunRef.current = true;
  }, [narration, footageData, picks]);

  // Autosave (debounce 800ms) — skip sebelum ada segmen
  useEffect(() => {
    if (!segments.length) return;
    const t = setTimeout(() => {
      try {
        localStorage.setItem(AUDIO_KEY, JSON.stringify({ segments, finishing, titleOverlays, stickerOverlays, savedAt: Date.now() }));
      } catch { /* quota — abaikan */ }
    }, 800);
    return () => clearTimeout(t);
  }, [segments, finishing, titleOverlays, stickerOverlays]);

  // Keyboard shortcuts: Ctrl+Z/Y undo-redo, Delete hapus, S split, Space play/pause
  useEffect(() => {
    const onKey = (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); redo(); }
      else if (e.key === "Delete" && selectedIdx != null) { e.preventDefault(); removeSegment(selectedIdx); }
      else if (k === "s" && selectedIdx != null) { e.preventDefault(); splitSegment(selectedIdx); }
      else if (e.key === " " && !e.ctrlKey) {
        if (videoRef.current && (videoRef.current.src || videoRef.current.currentSrc)) {
          e.preventDefault();
          if (videoRef.current.paused) videoRef.current.play(); else videoRef.current.pause();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => () => cancelRef.current && cancelRef.current(), []);

  const pushHistory = () => {
    setHistory(h => [...h.slice(-59), segments]);
    setFuture([]);
  };

  const undo = () => {
    if (!history.length) return;
    setFuture(f => [...f, segments]);
    setSegments(history[history.length - 1]);
    setHistory(h => h.slice(0, -1));
  };

  const redo = () => {
    if (!future.length) return;
    setHistory(h => [...h, segments]);
    setSegments(future[future.length - 1]);
    setFuture(f => f.slice(0, -1));
  };

  const updateSegment = (idx, updates) => {
    setSegments(prev => prev.map((s, i) => i === idx ? { ...s, ...updates } : s));
  };

  const moveSegment = (idx, direction) => {
    if ((direction === -1 && idx === 0) || (direction === 1 && idx === segments.length - 1)) return;
    pushHistory();
    const newSegs = [...segments];
    const temp = newSegs[idx];
    newSegs[idx] = newSegs[idx + direction];
    newSegs[idx + direction] = temp;
    setSegments(newSegs);
  };

  const reorderTo = (to) => {
    if (dragIdx === null || dragIdx === to) return;
    pushHistory();
    const arr = [...segments];
    const [item] = arr.splice(dragIdx, 1);
    arr.splice(to, 0, item);
    setSegments(arr);
    setDragIdx(null);
  };

  const removeSegment = (idx) => {
    pushHistory();
    setSegments(prev => prev.filter((_, i) => i !== idx));
  };

  const addTrim = (idx, edge, value) => {
    const seg = segments[idx];
    if (edge === "start") updateSegment(idx, { start_trim: Math.max(0, Math.min(value, seg.duration - 0.5)) });
    if (edge === "end") updateSegment(idx, { end_trim: Math.max(0, Math.min(value, seg.duration - 0.5)) });
  };

  // 3.2 — trim-by-handle: drag kiri/kanan clip di track
  const startTrimDrag = (idx, edge, e) => {
    e.preventDefault();
    e.stopPropagation();
    pushHistory();
    const startX = e.clientX;
    const seg = segments[idx];
    const orig = { start_trim: seg.start_trim, end_trim: seg.end_trim, duration: seg.duration };
    const onMove = (ev) => {
      const dx = (ev.clientX - startX) / pxPerSec;
      if (edge === "start") {
        const maxTrim = Math.max(orig.duration - orig.end_trim - 0.5, 0);
        updateSegment(idx, { start_trim: Math.min(Math.max(orig.start_trim + dx, 0), maxTrim) });
      } else {
        const maxTrim = Math.max(orig.duration - orig.start_trim - 0.5, 0);
        updateSegment(idx, { end_trim: Math.min(Math.max(orig.end_trim - dx, 0), maxTrim) });
      }
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // 3.2 — razor split: bagi 1 segmen jadi 2 (teks dibagi dua)
  const splitSegment = (idx) => {
    const s = segments[idx];
    const words = (s.narration_text || "").trim().split(/\s+/).filter(Boolean);
    const mid = Math.max(1, Math.floor(words.length / 2));
    const textA = words.slice(0, mid).join(" ");
    const textB = words.slice(mid).join(" ");
    const dur = Math.max(s.duration, 1.0);
    pushHistory();
    const newSegs = [...segments];
    const a = { ...s, narration_text: textA || s.narration_text, duration: dur / 2 };
    const b = { ...s, narration_text: textB || s.narration_text, duration: dur - dur / 2 };
    newSegs.splice(idx, 1, a, b);
    setSegments(newSegs);
  };

  // 3.3 — swap footage: ganti video segmen dari kandidat lain
  const swapFootage = (idx, candIdx) => {
    const cands = footageData?.[String(segments[idx].index)]?.candidates || [];
    const c = cands[candIdx];
    if (!c) return;
    pushHistory();
    updateSegment(idx, { video_path: c.video_path });
  };

  // 3.4 — re-transcribe per-segment audio setelah edit
  const [subtitleBusy, setSubtitleBusy] = useState(false);
  const regenerateSubtitles = async () => {
    setSubtitleBusy(true);
    setError(null);
    try {
      const res = await apiPostJSON("/api/timeline/regenerate_subtitles", {
        segments: segments.map(s => ({
          index: s.index,
          text: s.narration_text,
          audio_path: narration?.segment_audio_paths?.[s.index] || "",
          keywords: s.keywords || [],
        })),
      });
      const timed = res.segments || [];
      setSegments(prev => prev.map((s, i) => ({
        ...s,
        duration: timed[i]?.duration || s.duration,
        words: timed[i]?.words || [],
      })));
    } catch (e) { setError(String(e)); }
    finally { setSubtitleBusy(false); }
  };

  // Fase 4: Save/Load project + SRT + audio preview per segmen
  const exportProject = () => {
    const data = {
      segments, finishing, titleOverlays, stickerOverlays, savedAt: Date.now(),
      narrationMeta: { template_name: narration?.template_name || "" },
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ritme_project_${new Date().toISOString().slice(0, 10)}.ritme.json`;
    a.click();
  };

  const importProject = (file) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        if (!data?.segments?.length) throw new Error("tidak ada segmen");
        pushHistory();
        setSegments(data.segments);
        setFinishing(f => ({ ...f, ...(data.finishing || {}) }));
        setTitleOverlays(data.title_overlays || data.titleOverlays || []);
        setStickerOverlays(data.sticker_overlays || data.stickerOverlays || []);
        setRestoreNotice(false);
        setError(null);
      } catch { setError("File project tidak valid (bukan .ritme.json)"); }
    };
    reader.readAsText(file);
  };

  const downloadSrt = async () => {
    setError(null);
    try {
      const res = await fetch("/api/timeline/subtitles", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: segments.map(s => ({
          index: s.index, text: s.narration_text,
          audio_path: narration?.segment_audio_paths?.[s.index] || "",
          keywords: s.keywords || [],
        })) }),
      });
      if (!res.ok) throw new Error("Export SRT gagal");
      const text = await res.text();
      const blob = new Blob([text], { type: "application/x-subrip" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "ritme_subtitles.srt";
      a.click();
    } catch (e) { setError(String(e)); }
  };

  const toggleSegAudio = (idx) => {
    const path = narration?.segment_audio_paths?.[idx];
    if (!path) return;
    const cur = audioRefs.current[idx];
    if (cur && playingAudio === idx) { cur.pause(); cur.currentTime = 0; setPlayingAudio(null); return; }
    if (cur) cur.pause();
    const a = new Audio(path);
    audioRefs.current[idx] = a;
    a.onended = () => setPlayingAudio(null);
    a.play().catch(() => {});
    setPlayingAudio(idx);
  };

  // P0.1: simpan project ke library server-side (projects/<id>/project.json)
  const saveToLibrary = async () => {
    if (!saveName.trim()) return;
    setSaveBusy(true);
    setError(null);
    try {
      const body = {
        name: saveName.trim(),
        segments: segments.map(s => ({
          index: s.index, video_path: s.video_path, narration_text: s.narration_text,
          duration: s.duration, start_trim: s.start_trim, end_trim: s.end_trim,
          keywords: s.keywords || [], audio_path: s.audio_path || "", words: s.words || [],
          filter: s.filter || "original",
        })),
        finishing,
        narration_meta: { template_name: narration?.template_name || "" },
        template_name: narration?.template_name || "",
        watermark_path: finishing.watermark_path || null,
        title_overlays: titleOverlays,
        sticker_overlays: stickerOverlays,
      };
      const res = await fetch("/api/projects", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSaveOpen(false);
      setSaveName("");
      setSaveMsg("Tersimpan ke Library ✅");
      setTimeout(() => setSaveMsg(""), 3000);
    } catch (e) { setError(`Gagal simpan ke library: ${e}`); }
    finally { setSaveBusy(false); }
  };

  // P1.1: text/title overlay manual — CRUD
  const addOverlay = (i) => {
    setTitleOverlays(prev => [...prev, {
      id: `ov-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      segment_index: i, text: "Judul", start_offset: 0, duration: 3,
      position: "top-center", font_size: 48, color: "#FFFFFF", background_pill: false,
    }]);
  };
  const updateOverlay = (id, patch) => {
    setTitleOverlays(prev => prev.map(o => o.id === id ? { ...o, ...patch } : o));
  };
  const removeOverlay = (id) => {
    setTitleOverlays(prev => prev.filter(o => o.id !== id));
  };

  // P1.4: sticker overlay manual — upload + CRUD
  const addSticker = (i) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/webp";
    input.onchange = async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("image", file);
      try {
        const res = await fetch("/api/sticker/upload", { method: "POST", body: fd });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const { sticker_path } = await res.json();
        setStickerOverlays(prev => [...prev, {
          id: `st-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          segment_index: i, image_path: sticker_path, image_name: file.name,
          x: 0.5, y: 0.5, scale: 1.0, rotation: 0.0, start_offset: 0, duration: 3,
        }]);
      } catch (err) { setError(`Gagal upload sticker: ${err}`); }
    };
    input.click();
  };
  const updateSticker = (id, patch) => {
    setStickerOverlays(prev => prev.map(o => o.id === id ? { ...o, ...patch } : o));
  };
  const removeSticker = (id) => {
    setStickerOverlays(prev => prev.filter(o => o.id !== id));
  };

  const exportTimeline = async (preview = false) => {
    setError(null);
    setJob({ progress: 5, message: preview ? "Membuat preview..." : "Merender video..." });
    try {
      const endpoint = preview ? "/api/timeline/preview" : "/api/timeline/export";
      const body = {
        segments: segments.filter(s => s.video_path).map(s => ({
          index: s.index,
          video_path: s.video_path,
          narration_text: s.narration_text,
          duration: s.duration,
          start_trim: s.start_trim,
          end_trim: s.end_trim,
          keywords: s.keywords || [],
          audio_path: narration?.segment_audio_paths?.[s.index] || "",
          words: s.words || [],
          filter: s.filter || "original",
        })),
        narration_audio_path: narration?.audio_path || "",
        output_name: `ritme_${Date.now()}`,
        template_name: narration?.template_name || "",
        add_music: finishing.add_music,
        music_mood: finishing.add_music ? finishing.music_mood : null,
        caption_style: finishing.caption_style,
        transition_style: finishing.transition_style,
        ken_burns: finishing.ken_burns,
        aspect_ratio: finishing.aspect_ratio || "9:16",
        watermark_path: finishing.watermark_path || null,
        watermark_pos: finishing.watermark_pos,
        title_overlays: titleOverlays.map(o => ({
          segment_index: o.segment_index, text: o.text, start_offset: o.start_offset,
          duration: o.duration, position: o.position, font_size: o.font_size,
          color: o.color, background_pill: o.background_pill,
        })),
        sticker_overlays: stickerOverlays.map(o => ({
          segment_index: o.segment_index, image_path: o.image_path, x: o.x, y: o.y,
          scale: o.scale, rotation: o.rotation, start_offset: o.start_offset,
          duration: o.duration,
        })),
      };
      const resp = await fetch(endpoint, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
      });
      if (!resp.ok) throw new Error((preview ? "Preview" : "Export") + " failed");
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      if (preview) {
        setPreviewUrl(url);
        setJob(null);
        if (videoRef.current) { videoRef.current.src = url; setPlaying(true); }
      } else {
        setResult(url);
        setRenderPath(resp.headers.get("X-Render-Path") || "");
        setJob(null);
      }
    } catch (e) { setJob(null); setError(String(e)); }
  };

  // Thumbnail generator dari hasil render (pakai renderPath server)
  const [thumbBusy, setThumbBusy] = useState(false);
  const [thumbUrl, setThumbUrl] = useState(null);
  const [thumbTitle, setThumbTitle] = useState(
    segments.map(s => s.narration_text).join(" ").trim().slice(0, 60)
  );

  const generateThumbnail = async () => {
    if (!renderPath || !thumbTitle.trim()) return;
    setThumbBusy(true); setError(null);
    try {
      const res = await fetch("/api/thumbnail/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_path: renderPath, title: thumbTitle.trim() }),
      });
      if (!res.ok) throw new Error("Thumbnail gagal");
      const data = await res.json();
      setThumbUrl(data.url);
    } catch (e) { setError(String(e)); }
    finally { setThumbBusy(false); }
  };

  // 3.5 — auto-preview: debounce 1.5s setelah edit (kalau toggle nyala)
  const segSignature = JSON.stringify(segments.map(s => ({ v: s.video_path, d: s.duration, st: s.start_trim, et: s.end_trim, t: s.narration_text })));
  useEffect(() => {
    if (firstRunRef.current) { firstRunRef.current = false; return; }
    if (!autoPreview || !segments.length || job) return;
    const t = setTimeout(() => exportTimeline(true), 1500);
    return () => clearTimeout(t);
  }, [segSignature, autoPreview]);

  const downloadVideo = () => {
    if (!result) return;
    const a = document.createElement("a");
    a.href = result;
    a.download = "ritme_timeline.mp4";
    a.click();
  };

  const totalDuration = segments.reduce((a, s) => a + Math.max(s.duration - s.start_trim - s.end_trim, 0.5), 0);

  // Posisi kumulatif tiap segmen (detik) — buat layout track
  const segStarts = [];
  {
    let acc = 0;
    for (const s of segments) {
      segStarts.push(acc);
      acc += Math.max(s.duration - s.start_trim - s.end_trim, 0.5);
    }
  }

  const colors = [C.tally, "#6FE7DD", "#E8A33D", "#7FB88A", "#8B5CF6", "#F472B6", "#FBBF24", "#34D399"];
  const timelineW = Math.max(segments.length * 160, totalDuration * pxPerSec, 420);

  // Ruler ticks tiap 5 detik
  const ticks = [];
  for (let t = 0; t <= totalDuration + 0.001; t += 5) ticks.push(t);

  const musicLabel = finishing.add_music
    ? `Musik: ${finishing.music_mood}`
    : "Musik: mati";

  return (
    <div className="flex flex-col gap-6">
      {/* ===== Header + toolbar (3.5 undo/redo, 3.2 zoom) ===== */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <span style={{ fontFamily: F.mono, fontSize: 12, color: C.cyan, letterSpacing: "0.08em" }}>05 / TIMELINE EDITOR</span>
          <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700, marginTop: 4 }}>Edit Timeline Manual</h2>
          <p style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim, marginTop: 4 }}>{segments.length} segmen · {fmt(totalDuration)} total</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <IconButton onClick={undo} icon={Undo2} disabled={!history.length} title="Undo (Ctrl+Z)" />
          <IconButton onClick={redo} icon={Redo2} disabled={!future.length} title="Redo (Ctrl+Y)" />
          <div className="flex items-center gap-1 px-2 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}`, height: 30 }}>
            <ZoomIn size={13} color={C.paperFaint} />
            <input type="range" min={0.5} max={3} step={0.1} value={zoom} onChange={e => setZoom(parseFloat(e.target.value))}
              style={{ width: 90, accentColor: C.tally }} />
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperDim, width: 34 }}>{zoom.toFixed(1)}x</span>
          </div>
          <label className="flex items-center gap-2 px-2 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}`, height: 30, cursor: "pointer" }}>
            <input type="checkbox" checked={autoPreview} onChange={e => setAutoPreview(e.target.checked)} style={{ accentColor: C.tally }} />
            <Zap size={12} color={autoPreview ? C.amber : C.paperFaint} />
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperDim }}>Auto-preview</span>
          </label>
          <PrimaryButton onClick={regenerateSubtitles} disabled={subtitleBusy || segments.length === 0} loading={subtitleBusy} icon={Captions}>Sinkronkan Subtitle</PrimaryButton>
          <button onClick={() => { setSaveName(prev => prev || "Project " + new Date().toISOString().slice(0, 10)); setSaveOpen(true); }} disabled={segments.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded"
            style={{ background: segments.length === 0 ? C.panelRaised : "rgba(232,163,61,0.12)", border: `1px solid ${segments.length === 0 ? C.borderSoft : C.amber}66`, color: segments.length === 0 ? C.paperFaint : C.amber, fontFamily: F.mono, fontSize: 10, cursor: segments.length === 0 ? "default" : "pointer", opacity: segments.length === 0 ? 0.6 : 1 }}>
            <Library size={12} /> SIMPAN KE LIBRARY
          </button>
          <div className="w-px self-stretch" style={{ background: C.borderSoft, margin: "2px 2px" }} />
          <IconButton onClick={exportProject} icon={Save} disabled={segments.length === 0} title="Simpan project (.ritme.json)" color={C.amber} />
          <IconButton onClick={() => fileInputRef.current?.click()} icon={Upload} title="Muat project (.ritme.json)" color={C.amber} />
          <input ref={fileInputRef} type="file" accept=".ritme.json,.json" style={{ display: "none" }} onChange={e => { if (e.target.files?.[0]) importProject(e.target.files[0]); e.target.value = ""; }} />
          <IconButton onClick={downloadSrt} icon={FileText} disabled={segments.length === 0} title="Download subtitle (.srt)" color={C.caption} />
        </div>
      </div>

      {restoreNotice && (
        <div className="flex items-center gap-3 px-4 py-2.5 rounded" style={{ background: "#241D12", border: `1px solid ${C.amber}55` }}>
          <Info size={14} color={C.amber} />
          <span style={{ fontFamily: F.body, fontSize: 12, color: C.paperDim, flex: 1 }}>Project tersimpan otomatis berhasil dipulihkan (edit terakhir tetap tersimpan di browser ini).</span>
          <button onClick={() => setRestoreNotice(false)} style={{ fontFamily: F.mono, fontSize: 11, color: C.amber, background: "none", border: "none", cursor: "pointer", padding: 0 }}>OK</button>
        </div>
      )}

      {/* ===== Multi-track timeline (3.1) ===== */}
      <div className="flex flex-col gap-3 rounded p-4" style={{ background: C.panel, border: `1px solid ${C.borderSoft}`, overflowX: "auto" }}>
        {/* Ruler */}
        <div className="relative" style={{ height: 18, width: timelineW }}>
          {ticks.map(t => (
            <div key={t} className="absolute flex flex-col" style={{ left: t * pxPerSec }}>
              <div style={{ width: 1, height: 6, background: C.border }} />
              <span style={{ fontFamily: F.mono, fontSize: 9, color: C.paperFaint, marginTop: 1 }}>{Math.floor(t / 60)}:{String(Math.round(t % 60)).padStart(2, "0")}</span>
            </div>
          ))}
        </div>

        {/* Track VIDEO */}
        <div>
          <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>VIDEO</span>
          <div className="relative rounded" style={{ height: 64, width: timelineW, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 4 }}>
            {segments.map((s, i) => {
              const dur = Math.max(s.duration - s.start_trim - s.end_trim, 0.5);
              const cands = footageData?.[String(s.index)]?.candidates || [];
              const curCandIdx = cands.findIndex(c => c.video_path === s.video_path);
              const thumb = cands[curCandIdx >= 0 ? curCandIdx : (picks?.[s.index] ?? 0)]?.thumbnail_url;
              return (
                <div
                  key={`${s.index}-${i}`}
                  draggable={!job}
                  onDragStart={() => setDragIdx(i)}
                  onDragOver={e => e.preventDefault()}
                  onDrop={() => reorderTo(i)}
                  onDragEnd={() => setDragIdx(null)}
                  className="absolute rounded flex items-center"
                  style={{
                    left: segStarts[i] * pxPerSec,
                    width: Math.max(dur * pxPerSec, 56),
                    top: 4, height: 56,
                    background: dragIdx === i ? C.amber + "22" : `${colors[i % colors.length]}26`,
                    border: `1px solid ${dragIdx === i ? C.amber : colors[i % colors.length]}66`,
                    cursor: "grab", overflow: "hidden",
                    boxShadow: dragIdx === i ? `0 0 12px ${C.amber}44` : "none",
                  }}
                >
                  {/* Trim handle kiri */}
                  <div onMouseDown={e => startTrimDrag(i, "start", e)} title="Trim start"
                    style={{ width: 7, height: "100%", cursor: "ew-resize", background: C.paper + "22", flexShrink: 0, borderRight: `1px solid ${C.border}` }} />
                  <div className="flex-1 flex items-center gap-2 px-1.5" style={{ minWidth: 0 }}>
                    {thumb ? <img src={thumb} style={{ width: 44, height: 34, objectFit: "cover", borderRadius: 3, flexShrink: 0 }} /> : <Film size={16} color={C.paperDim} style={{ flexShrink: 0 }} />}
                    <div className="flex flex-col" style={{ minWidth: 0 }}>
                      <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paper, whiteSpace: "nowrap" }}>Seg {i + 1} · {fmt(dur)}</span>
                      {/* 3.3 swap footage dropdown */}
                      {cands.length > 1 ? (
                        <select value={curCandIdx >= 0 ? curCandIdx : 0} onChange={e => swapFootage(i, parseInt(e.target.value))}
                          style={{ fontFamily: F.mono, fontSize: 8.5, color: C.paperDim, background: "transparent", border: "none", outline: "none", maxWidth: 120, cursor: "pointer" }}>
                          {cands.map((c, ci) => <option key={ci} value={ci}>{ci + 1}. {String(c.video_path || "").split(/[\\/]/).pop().slice(0, 18)}</option>)}
                        </select>
                      ) : (
                        <span style={{ fontFamily: F.mono, fontSize: 8.5, color: C.paperFaint, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 110 }}>{String(s.video_path || "no footage").split(/[\\/]/).pop().slice(0, 22)}</span>
                      )}
                    </div>
                  </div>
                  {/* Razor split (3.2) */}
                  <IconButton onClick={() => splitSegment(i)} icon={Scissors} title="Split segmen" color={C.amber} />
                  {/* Trim handle kanan */}
                  <div onMouseDown={e => startTrimDrag(i, "end", e)} title="Trim end"
                    style={{ width: 7, height: "100%", cursor: "ew-resize", background: C.paper + "22", flexShrink: 0, borderLeft: `1px solid ${C.border}` }} />
                </div>
              );
            })}
            {segments.length === 0 && <span style={{ fontFamily: F.body, fontSize: 11, color: C.paperFaint, position: "absolute", top: 24, left: 12 }}>Belum ada segmen — generate script dulu.</span>}
          </div>
        </div>

        {/* Track MUSIK (3.3 swap musik) */}
        <div>
          <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>MUSIK</span>
          <div className="flex items-center gap-3 rounded" style={{ height: 40, width: timelineW, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 4, padding: "0 10px" }}>
            <Music2 size={14} color={finishing.add_music ? C.music : C.paperFaint} />
            <span style={{ fontFamily: F.mono, fontSize: 10.5, color: finishing.add_music ? C.paper : C.paperFaint }}>{musicLabel}</span>
            <div className="flex-1 relative" style={{ height: 26 }}>
              {finishing.add_music && <div style={{ position: "absolute", left: 0, right: 0, top: 8, height: 10, background: `linear-gradient(90deg, ${C.music}33, ${C.music}88)`, borderRadius: 3 }} />}
            </div>
            <label className="flex items-center gap-1.5" style={{ cursor: "pointer" }}>
              <input type="checkbox" checked={finishing.add_music} onChange={e => { pushHistory(); setFinishing({ ...finishing, add_music: e.target.checked }); }} style={{ accentColor: C.tally }} />
              <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>On</span>
            </label>
            <select value={finishing.music_mood} onChange={e => { pushHistory(); setFinishing({ ...finishing, add_music: true, music_mood: e.target.value }); }}
              style={{ fontFamily: F.mono, fontSize: 10.5, color: C.paper, background: C.panelRaised, border: `1px solid ${C.border}`, borderRadius: 3, padding: "3px 6px", outline: "none", cursor: "pointer" }}>
              {["calm", "tense", "sad", "epic", "upbeat"].map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        </div>

        {/* Track CAPTION */}
        <div>
          <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>CAPTION</span>
          <div className="relative rounded" style={{ height: 30, width: timelineW, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 4 }}>
            {segments.map((s, i) => {
              const dur = Math.max(s.duration - s.start_trim - s.end_trim, 0.5);
              return (
                <div key={`cap-${s.index}-${i}`} className="absolute rounded flex items-center px-1.5"
                  style={{ left: segStarts[i] * pxPerSec, width: Math.max(dur * pxPerSec - 3, 30), top: 5, height: 20, background: `${C.caption}26`, border: `1px solid ${C.caption}55`, overflow: "hidden" }}>
                  <span style={{ fontFamily: F.mono, fontSize: 8.5, color: C.caption, whiteSpace: "nowrap" }}>{(s.narration_text || `Seg ${i + 1}`).slice(0, Math.max(2, Math.floor((dur * pxPerSec) / 8)))}</span>
                </div>
              );
            })}
          </div>
        </div>

        {/* Track TEXT (P1.1) */}
        <div>
          <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>TEXT</span>
          <div className="relative rounded" style={{ height: 30, width: timelineW, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 4 }}>
            {titleOverlays.map(o => {
              const left = (segStarts[o.segment_index] || 0) + (o.start_offset || 0);
              return (
                <div key={o.id} className="absolute rounded flex items-center px-1.5" title={`Teks: ${o.text}`}
                  onClick={() => setSelectedIdx(o.segment_index)}
                  style={{ left: left * pxPerSec, width: Math.max((o.duration || 3) * pxPerSec - 3, 40), top: 5, height: 20, background: `${C.amber}26`, border: `1px solid ${C.amber}55`, overflow: "hidden", cursor: "pointer" }}>
                  <span style={{ fontFamily: F.mono, fontSize: 8.5, color: C.amber, whiteSpace: "nowrap" }}>T: {String(o.text || "").slice(0, 10)}</span>
                </div>
              );
            })}
            {titleOverlays.length === 0 && <span style={{ fontFamily: F.body, fontSize: 9.5, color: C.paperFaint, position: "absolute", top: 7, left: 10 }}>Teks manual (judul, lower-third) — klik "+ TEKS" di detail segmen</span>}
          </div>
        </div>

        {/* Track STICKER (P1.4) */}
        <div>
          <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>STICKER</span>
          <div className="relative rounded" style={{ height: 30, width: timelineW, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 4 }}>
            {stickerOverlays.map(o => {
              const left = (segStarts[o.segment_index] || 0) + (o.start_offset || 0);
              return (
                <div key={o.id} className="absolute rounded flex items-center px-1.5" title={`Sticker: ${o.image_name || ""}`}
                  onClick={() => setSelectedIdx(o.segment_index)}
                  style={{ left: left * pxPerSec, width: Math.max((o.duration || 3) * pxPerSec - 3, 40), top: 5, height: 20, background: `${C.caption}26`, border: `1px solid ${C.caption}55`, overflow: "hidden", cursor: "pointer" }}>
                  <span style={{ fontFamily: F.mono, fontSize: 8.5, color: C.caption, whiteSpace: "nowrap" }}>◈ {String(o.image_name || "sticker").slice(0, 8)}</span>
                </div>
              );
            })}
            {stickerOverlays.length === 0 && <span style={{ fontFamily: F.body, fontSize: 9.5, color: C.paperFaint, position: "absolute", top: 7, left: 10 }}>Sticker/gambar overlay — klik "+ STICKER" di detail segmen</span>}
          </div>
        </div>
      </div>

      {/* ===== Detail panel per segmen (list) ===== */}
      <div className="flex flex-col gap-2">
        <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", marginBottom: 4 }}>DETAIL SEGMEN</span>
        {segments.map((s, i) => (
          <div key={`d-${s.index}-${i}`} className="flex flex-col gap-2">
            <div onClick={() => setSelectedIdx(selectedIdx === i ? null : i)}
              className="flex items-center gap-3 px-4 py-3 rounded"
              style={{ background: selectedIdx === i ? "#26221A" : C.panel, border: `1px solid ${selectedIdx === i ? C.amber + "66" : C.borderSoft}`, cursor: "pointer" }}>
              <div className="flex items-center justify-center rounded-full" style={{ width: 26, height: 26, background: C.panelRaised, border: `1px solid ${C.border}`, flexShrink: 0 }}>
                <span style={{ fontFamily: F.mono, fontSize: 11, color: C.cyan, fontWeight: 600 }}>{i + 1}</span>
              </div>
              <GripVertical size={14} color={C.paperFaint} style={{ flexShrink: 0, cursor: "grab" }} />
              <div style={{ width: 50, height: 30, background: C.panelRaised, borderRadius: 3, flexShrink: 0, overflow: "hidden" }}>
                {(() => { const cand = footageData?.[String(s.index)]?.candidates?.[picks?.[s.index] ?? 0]; const thumb = cand?.thumbnail_url; return thumb ? <img src={thumb} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <Film size={16} color={C.paperFaint} style={{ margin: "7px auto", display: "block" }} />; })()}
              </div>
              <div className="flex-1 flex flex-col" style={{ minWidth: 0 }}>
                <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperFaint, marginBottom: 3 }}>Teks narasi (edit, lalu Sinkronkan Subtitle)</span>
                <textarea
                  value={s.narration_text}
                  onChange={e => updateSegment(i, { narration_text: e.target.value })}
                  rows={2}
                  style={{ width: "100%", fontFamily: F.body, fontSize: 11.5, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "4px 6px", outline: "none", resize: "vertical", lineHeight: 1.45 }}
                />
                <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperFaint, marginTop: 3 }}>{s.keywords?.join(", ")}</span>
                <div className="flex items-center gap-1 flex-wrap" style={{ marginTop: 5 }}>
                  <span style={{ fontFamily: F.mono, fontSize: 9, color: C.paperFaint }}>FILTER</span>
                  {FILTER_PRESETS.map(([v, l]) => (
                    <button key={v} onClick={() => updateSegment(i, { filter: v })}
                      style={{ fontFamily: F.mono, fontSize: 9, padding: "1px 7px", borderRadius: 9, cursor: "pointer",
                        background: (s.filter || "original") === v ? C.amber + "33" : C.panelRaised,
                        border: `1px solid ${(s.filter || "original") === v ? C.amber : C.borderSoft}`,
                        color: (s.filter || "original") === v ? C.amber : C.paperDim }}>{l}</button>
                  ))}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ fontFamily: F.mono, fontSize: 9, color: C.paperFaint }}>Start</span>
                  <input type="number" min={0} max={Math.max(s.duration - 0.5, 0)} step={0.1} value={s.start_trim} onChange={e => addTrim(i, "start", parseFloat(e.target.value) || 0)} style={{ width: 42, fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 4px", outline: "none", textAlign: "center" }} />
                  <span style={{ fontFamily: F.mono, fontSize: 9, color: C.paperFaint }}>End</span>
                  <input type="number" min={0} max={Math.max(s.duration - 0.5, 0)} step={0.1} value={s.end_trim} onChange={e => addTrim(i, "end", parseFloat(e.target.value) || 0)} style={{ width: 42, fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 4px", outline: "none", textAlign: "center" }} />
                </div>
              </div>
              <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperFaint, width: 40, textAlign: "right", flexShrink: 0 }}>{fmt(Math.max(s.duration - s.start_trim - s.end_trim, 0.5))}</span>
              <div className="flex items-center gap-1 flex-shrink-0">
                {narration?.segment_audio_paths?.[s.index] && (
                  <IconButton onClick={e => { e.stopPropagation(); toggleSegAudio(i); }} icon={playingAudio === i ? VolumeX : Volume2}
                    title={playingAudio === i ? "Hentikan audio" : "Preview narasi segmen ini"} color={playingAudio === i ? C.cyan : C.paperDim} />
                )}
                <IconButton onClick={e => { e.stopPropagation(); splitSegment(i); }} icon={Scissors} title="Split" color={C.amber} />
                <IconButton onClick={e => { e.stopPropagation(); moveSegment(i, -1); }} icon={ArrowUp} disabled={i === 0} title="Naik" />
                <IconButton onClick={e => { e.stopPropagation(); moveSegment(i, 1); }} icon={ArrowDown} disabled={i === segments.length - 1} title="Turun" />
                {segments.length > 1 && <IconButton onClick={e => { e.stopPropagation(); removeSegment(i); }} icon={Trash2} title="Hapus" color={C.red} />}
              </div>
            </div>
            {/* P1.1: editor teks overlay per segmen */}
            {selectedIdx === i && (
              <div className="flex flex-col gap-2 px-4 py-3 rounded" style={{ background: C.panelRaised, border: `1px dashed ${C.border}` }}>
                <div className="flex items-center justify-between">
                  <span style={{ fontFamily: F.mono, fontSize: 10, color: C.amber, letterSpacing: "0.08em" }}>TEKS OVERLAY (judul · lower-third · callout)</span>
                  <button onClick={() => addOverlay(i)} className="flex items-center gap-1 px-2.5 py-1 rounded"
                    style={{ background: "rgba(232,163,61,0.12)", border: `1px solid ${C.amber}66`, color: C.amber, fontFamily: F.mono, fontSize: 10, cursor: "pointer" }}>
                    + TEKS
                  </button>
                </div>
                {titleOverlays.filter(o => o.segment_index === i).length === 0 && (
                  <span style={{ fontFamily: F.body, fontSize: 11, color: C.paperFaint }}>Belum ada teks di segmen ini — klik "+ TEKS" untuk tambah judul/lower-third.</span>
                )}
                {titleOverlays.filter(o => o.segment_index === i).map(o => (
                  <div key={o.id} className="flex flex-col gap-2 rounded p-2.5" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
                    <div className="flex items-center gap-2">
                      <input value={o.text} onChange={e => updateOverlay(o.id, { text: e.target.value })} placeholder="Teks (mis. EPISODE 3 — RUANG ANGKASA)"
                        style={{ flex: 1, minWidth: 0, fontFamily: F.body, fontSize: 12, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 4, padding: "5px 8px", outline: "none" }} />
                      <IconButton onClick={() => removeOverlay(o.id)} icon={Trash2} title="Hapus teks" color={C.red} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Posisi
                        <select value={o.position} onChange={e => updateOverlay(o.id, { position: e.target.value })}
                          style={{ fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 5px", outline: "none" }}>
                          {TITLE_POSITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </select>
                      </label>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Ukuran
                        <input type="number" min={12} max={160} value={o.font_size} onChange={e => updateOverlay(o.id, { font_size: parseInt(e.target.value) || 48 })}
                          style={{ width: 52, fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 5px", outline: "none", textAlign: "center" }} />
                      </label>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Durasi
                        <input type="number" min={0.5} max={60} step={0.5} value={o.duration} onChange={e => updateOverlay(o.id, { duration: parseFloat(e.target.value) || 3 })}
                          style={{ width: 52, fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 5px", outline: "none", textAlign: "center" }} />s
                      </label>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Mulai
                        <input type="number" min={0} max={Math.max(s.duration - 0.5, 0)} step={0.5} value={o.start_offset} onChange={e => updateOverlay(o.id, { start_offset: parseFloat(e.target.value) || 0 })}
                          style={{ width: 52, fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 5px", outline: "none", textAlign: "center" }} />s
                      </label>
                      <div className="flex items-center gap-1">
                        <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>Warna</span>
                        {TITLE_COLORS.map(c => (
                          <button key={c} title={c} onClick={() => updateOverlay(o.id, { color: c })}
                            style={{ width: 14, height: 14, borderRadius: "50%", background: c, border: o.color === c ? `2px solid ${C.paper}` : `1px solid ${C.border}`, cursor: "pointer", padding: 0 }} />
                        ))}
                      </div>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim, cursor: "pointer" }}>
                        <input type="checkbox" checked={o.background_pill} onChange={e => updateOverlay(o.id, { background_pill: e.target.checked })} style={{ accentColor: C.amber }} />
                        Pill bg
                      </label>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {/* P1.4: editor sticker overlay per segmen */}
            {selectedIdx === i && (
              <div className="flex flex-col gap-2 px-4 py-3 rounded" style={{ background: C.panelRaised, border: `1px dashed ${C.border}` }}>
                <div className="flex items-center justify-between">
                  <span style={{ fontFamily: F.mono, fontSize: 10, color: C.caption, letterSpacing: "0.08em" }}>STICKER / GAMBAR OVERLAY</span>
                  <button onClick={() => addSticker(i)} className="flex items-center gap-1 px-2.5 py-1 rounded"
                    style={{ background: "rgba(127,184,138,0.12)", border: `1px solid ${C.caption}66`, color: C.caption, fontFamily: F.mono, fontSize: 10, cursor: "pointer" }}>
                    + STICKER
                  </button>
                </div>
                {stickerOverlays.filter(o => o.segment_index === i).length === 0 && (
                  <span style={{ fontFamily: F.body, fontSize: 11, color: C.paperFaint }}>Belum ada sticker di segmen ini — klik "+ STICKER" untuk upload gambar (PNG transparan paling bagus).</span>
                )}
                {stickerOverlays.filter(o => o.segment_index === i).map(o => (
                  <div key={o.id} className="flex flex-col gap-2 rounded p-2.5" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
                    <div className="flex items-center gap-2">
                      {o.image_path ? (
                        <img src={`/uploads/${o.image_path.split(/[\\/]/).pop()}`} alt=""
                          onError={e => { e.target.style.display = "none"; }}
                          style={{ width: 36, height: 36, objectFit: "contain", background: C.panelRaised, borderRadius: 3, flexShrink: 0 }} />
                      ) : <span style={{ width: 36, height: 36, background: C.panelRaised, borderRadius: 3, flexShrink: 0 }} />}
                      <span style={{ flex: 1, minWidth: 0, fontFamily: F.body, fontSize: 11.5, color: C.paper, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{o.image_name || "sticker"}</span>
                      <IconButton onClick={() => removeSticker(o.id)} icon={Trash2} title="Hapus sticker" color={C.red} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Posisi
                        <select value={`${o.x},${o.y}`} onChange={e => { const [x, y] = e.target.value.split(","); updateSticker(o.id, { x: parseFloat(x), y: parseFloat(y) }); }}
                          style={{ fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 5px", outline: "none" }}>
                          {STICKER_POSITIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                        </select>
                      </label>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Ukuran
                        <input type="range" min={0.3} max={3} step={0.1} value={o.scale} onChange={e => updateSticker(o.id, { scale: parseFloat(e.target.value) })}
                          style={{ width: 80, accentColor: C.caption }} />
                        <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim, width: 26 }}>{o.scale.toFixed(1)}x</span>
                      </label>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Rotasi
                        <input type="range" min={-180} max={180} step={5} value={o.rotation} onChange={e => updateSticker(o.id, { rotation: parseInt(e.target.value) })}
                          style={{ width: 80, accentColor: C.caption }} />
                        <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim, width: 30 }}>{o.rotation}°</span>
                      </label>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Mulai
                        <input type="number" min={0} max={Math.max(s.duration - 0.5, 0)} step={0.5} value={o.start_offset} onChange={e => updateSticker(o.id, { start_offset: parseFloat(e.target.value) || 0 })}
                          style={{ width: 52, fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 5px", outline: "none", textAlign: "center" }} />s
                      </label>
                      <label className="flex items-center gap-1.5" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                        Durasi
                        <input type="number" min={0.5} max={60} step={0.5} value={o.duration} onChange={e => updateSticker(o.id, { duration: parseFloat(e.target.value) || 3 })}
                          style={{ width: 52, fontFamily: F.mono, fontSize: 10, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 3, padding: "2px 5px", outline: "none", textAlign: "center" }} />s
                      </label>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {error && <div className="flex items-center gap-2 px-4 py-3 rounded" style={{ background: "#2A1712", border: `1px solid ${C.tallyDim}` }}><AlertTriangle size={14} color={C.tally} /><span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim }}>{error}</span></div>}
      {job && <div className="flex items-center gap-3 px-4 py-3 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}><Loader2 size={14} color={C.amber} className="animate-spin" /><span style={{ fontFamily: F.body, fontSize: 12, color: C.paperDim }}>{job.message}</span></div>}

      {/* ===== Finishing options (dipertahankan) ===== */}
      <div className="flex flex-col gap-3 px-4 py-3.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
        <span style={{ fontFamily: F.mono, fontSize: 10, color: C.amber, letterSpacing: "0.08em" }}>FINISHING OPTIONS</span>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
          <label className="flex items-center gap-2" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={finishing.add_music} onChange={e => setFinishing({ ...finishing, add_music: e.target.checked })} style={{ accentColor: C.tally }} />
            <span style={{ fontFamily: F.body, fontSize: 12, color: C.paper }}>Tambah musik</span>
          </label>
          {finishing.add_music && (
            <label className="flex items-center gap-2">
              <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint }}>Mood</span>
              <select value={finishing.music_mood} onChange={e => setFinishing({ ...finishing, music_mood: e.target.value })} style={{ fontFamily: F.mono, fontSize: 11, color: C.paper, background: C.panelRaised, border: `1px solid ${C.border}`, borderRadius: 3, padding: "3px 6px", outline: "none" }}>
                {["calm", "tense", "sad", "epic", "upbeat"].map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
          )}
          <label className="flex items-center gap-2">
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint }}>Gaya caption</span>
            <select value={finishing.caption_style} onChange={e => setFinishing({ ...finishing, caption_style: e.target.value })} style={{ fontFamily: F.mono, fontSize: 11, color: C.paper, background: C.panelRaised, border: `1px solid ${C.border}`, borderRadius: 3, padding: "3px 6px", outline: "none" }}>
              <option value="bold-white-bottom">Bold White Bottom</option>
              <option value="minimal-white-center">Minimal White Center</option>
              <option value="news-style-lower-third">News Lower Third</option>
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint }}>Transisi</span>
            <select value={finishing.transition_style} onChange={e => setFinishing({ ...finishing, transition_style: e.target.value })} style={{ fontFamily: F.mono, fontSize: 11, color: C.paper, background: C.panelRaised, border: `1px solid ${C.border}`, borderRadius: 3, padding: "3px 6px", outline: "none" }}>
              <option value="hard_cut">Hard Cut</option>
              <option value="crossfade">Crossfade</option>
              <option value="dip_to_black">Dip to Black</option>
              <option value="slide">Slide</option>
              <option value="zoom">Zoom</option>
            </select>
          </label>
          <label className="flex items-center gap-2" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={finishing.ken_burns} onChange={e => setFinishing({ ...finishing, ken_burns: e.target.checked })} style={{ accentColor: C.tally }} />
            <span style={{ fontFamily: F.body, fontSize: 12, color: C.paper }}>Ken Burns (zoom pelan)</span>
          </label>
          <div className="flex items-center gap-2">
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint }}>Aspect</span>
            {[["9:16", "TikTok 9:16"], ["16:9", "YouTube 16:9"], ["1:1", "IG 1:1"]].map(([v, l]) => (
              <button key={v} onClick={() => setFinishing({ ...finishing, aspect_ratio: v })}
                className="px-2.5 py-1 rounded"
                style={{ fontFamily: F.mono, fontSize: 10.5, cursor: "pointer",
                  background: finishing.aspect_ratio === v ? C.tally : C.panelRaised,
                  color: finishing.aspect_ratio === v ? "#0B0A07" : C.paperDim,
                  border: `1px solid ${finishing.aspect_ratio === v ? C.tally : C.border}` }}>
                {l}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint }}>Watermark</span>
            {finishing.watermark_name ? (
              <>
                <button onClick={() => wmRef.current?.click()} className="px-2.5 py-1 rounded" style={{ fontFamily: F.mono, fontSize: 11, color: C.cyan, background: C.panelRaised, border: `1px solid ${C.border}`, cursor: "pointer" }}>🖼 {finishing.watermark_name}</button>
                <button onClick={() => setFinishing(f => ({ ...f, watermark_path: "", watermark_name: "" }))} className="px-2 py-1 rounded" style={{ fontFamily: F.mono, fontSize: 11, color: C.red, background: "none", border: "none", cursor: "pointer" }}>✕</button>
              </>
            ) : (
              <button onClick={() => wmRef.current?.click()} className="px-2.5 py-1 rounded" style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim, background: C.panelRaised, border: `1px solid ${C.border}`, cursor: "pointer" }}>+ Logo</button>
            )}
            <input ref={wmRef} type="file" accept="image/*" style={{ display: "none" }} onChange={onWatermarkFile} />
            <select value={finishing.watermark_pos} onChange={e => setFinishing({ ...finishing, watermark_pos: e.target.value })} disabled={!finishing.watermark_path}
              style={{ fontFamily: F.mono, fontSize: 11, color: C.paper, background: C.panelRaised, border: `1px solid ${C.border}`, borderRadius: 3, padding: "3px 6px", outline: "none" }}>
              <option value="bottom-right">Bawah Kanan</option>
              <option value="bottom-left">Bawah Kiri</option>
              <option value="top-right">Atas Kanan</option>
              <option value="top-left">Atas Kiri</option>
              <option value="center">Tengah</option>
            </select>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <PrimaryButton onClick={() => exportTimeline(true)} disabled={job !== null} icon={Play}>Preview</PrimaryButton>
        <PrimaryButton onClick={() => exportTimeline(false)} disabled={job !== null} icon={Clapperboard}>Render Video</PrimaryButton>
        {result && <PrimaryButton onClick={downloadVideo} icon={Download} variant="outline">Download .mp4</PrimaryButton>}
      </div>
      {(previewUrl || result) && <div style={{ borderRadius: 8, overflow: "hidden", background: "#000" }}><video ref={videoRef} src={previewUrl || result} controls style={{ width: "100%", maxHeight: 400, display: "block" }} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} /></div>}
      {result && (
        <div className="flex flex-col gap-3 px-4 py-3.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
          <span style={{ fontFamily: F.mono, fontSize: 10, color: C.amber, letterSpacing: "0.08em" }}>THUMBNAIL YOUTUBE</span>
          <div className="flex items-center gap-3 flex-wrap">
            <input value={thumbTitle} onChange={e => setThumbTitle(e.target.value)} placeholder="Judul thumbnail (teks di gambar)"
              style={{ flex: 1, minWidth: 220, fontFamily: F.body, fontSize: 12.5, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 6, padding: "9px 12px", outline: "none" }} />
            <button onClick={generateThumbnail} disabled={thumbBusy || !thumbTitle.trim()} className="flex items-center gap-2 px-4 py-2 rounded" style={{ background: thumbBusy || !thumbTitle.trim() ? C.panelRaised : C.cyan, color: C.bg, fontFamily: F.body, fontWeight: 700, fontSize: 12.5, border: "none", cursor: thumbBusy || !thumbTitle.trim() ? "default" : "pointer", opacity: thumbBusy || !thumbTitle.trim() ? 0.5 : 1 }}>
              {thumbBusy ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />} Buat Thumbnail
            </button>
            {thumbUrl && <a href={thumbUrl} download style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: F.mono, fontSize: 11, color: C.amber, textDecoration: "none", background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 6, padding: "8px 12px" }}><Download size={12} /> Download .jpg</a>}
          </div>
          {thumbUrl && <img src={thumbUrl} alt="thumbnail" style={{ width: "100%", maxWidth: 480, borderRadius: 8, border: `1px solid ${C.borderSoft}` }} />}
        </div>
      )}
      <div className="flex items-start gap-2 px-3.5 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
        <Info size={14} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
        <span style={{ fontFamily: F.body, fontSize: 11.5, color: C.paperDim, lineHeight: 1.5 }}>
          Drag clip di track Video untuk mengubah urutan · tarik handle kiri/kanan untuk trim · ✂️ untuk split ·
          dropdown di clip untuk ganti footage · track Musik untuk ganti mood · project auto-tersimpan (💾 ekspor .ritme.json) ·
          subtitle .srt siap download · Shortcut: Ctrl+Z/Y undo-redo, Delete hapus segmen terpilih, S split, Spasi play/pause ·
          Auto-preview merender preview kecil otomatis 1.5s setelah edit.
        </span>
      </div>

      {saveMsg && (
        <div className="px-4 py-2.5 rounded" style={{ background: "rgba(127,184,138,0.1)", border: "1px solid rgba(127,184,138,0.4)", color: C.caption, fontFamily: F.body, fontSize: 12, textAlign: "center" }}>
          {saveMsg}
        </div>
      )}

      {saveOpen && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }} onClick={() => !saveBusy && setSaveOpen(false)}>
          <div className="rounded-xl p-5" style={{ width: "100%", maxWidth: 420, background: C.panel, border: `1px solid ${C.border}` }} onClick={e => e.stopPropagation()}>
            <div className="flex items-center gap-2 mb-1">
              <Library size={16} color={C.amber} />
              <span style={{ fontFamily: F.display, fontSize: 15, fontWeight: 700, color: C.paper }}>Simpan ke Library</span>
            </div>
            <div style={{ fontFamily: F.body, fontSize: 12, color: C.paperDim, marginBottom: 12 }}>
              Project tersimpan di server — bisa dibuka lagi dari menu PROJECTS di header, walau ganti browser.
            </div>
            <input autoFocus value={saveName} onChange={e => setSaveName(e.target.value)} placeholder="Nama project (mis. Dokumenter Sains Ep 3)"
              onKeyDown={e => { if (e.key === "Enter") saveToLibrary(); }}
              style={{ width: "100%", boxSizing: "border-box", fontFamily: F.body, fontSize: 13, color: C.paper, background: C.panelRaised, border: `1px solid ${C.border}`, borderRadius: 6, padding: "9px 12px", outline: "none", marginBottom: 14 }} />
            <div className="flex items-center justify-end gap-2">
              <button onClick={() => setSaveOpen(false)} disabled={saveBusy} style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim, background: "none", border: "none", cursor: "pointer", padding: "8px 10px" }}>BATAL</button>
              <button onClick={saveToLibrary} disabled={saveBusy || !saveName.trim()} className="flex items-center gap-2 px-4 py-2 rounded"
                style={{ background: saveBusy || !saveName.trim() ? C.panelRaised : C.amber, color: saveBusy || !saveName.trim() ? C.paperFaint : C.bg, fontFamily: F.mono, fontSize: 11, fontWeight: 700, border: "none", cursor: saveBusy || !saveName.trim() ? "default" : "pointer" }}>
                {saveBusy ? <Loader2 size={13} className="animate-spin" /> : <Library size={13} />} SIMPAN
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
export default TimelineEditor;
