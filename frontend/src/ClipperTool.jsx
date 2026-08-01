import React, { useState, useEffect, useRef } from "react";
import { X, Upload, Link2, Clapperboard, Loader2, AlertTriangle, Download, Check, Film, Scissors, Info, Zap } from "lucide-react";

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

async function apiPostJSON(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function fmt(s) {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

const ASPECTS = [
  { id: "9:16", label: "9:16 Vertical (Reels/TikTok)", res: "1080×1920" },
  { id: "16:9", label: "16:9 Horizontal (YouTube)", res: "1920×1080" },
  { id: "1:1", label: "1:1 Square (Feed)", res: "1080×1080" },
];

export default function ClipperTool({ onClose }) {
  const [inputType, setInputType] = useState("file");   // file | youtube
  const [file, setFile] = useState(null);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [videoPath, setVideoPath] = useState(null);
  const [sourceName, setSourceName] = useState("");
  const [sourceBusy, setSourceBusy] = useState(false);

  const [numClips, setNumClips] = useState(5);
  const [clips, setClips] = useState([]);
  const [totalDur, setTotalDur] = useState(0);
  const [analyzing, setAnalyzing] = useState(false);
  const [selected, setSelected] = useState({});         // index -> true

  const [aspect, setAspect] = useState("9:16");
  const [rendering, setRendering] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  const prepSource = async () => {
    setError(null); setResults(null); setClips([]); setVideoPath(null);
    setSourceBusy(true);
    try {
      if (inputType === "file") {
        if (!file) { setError("Pilih file video dulu."); return; }
        const fd = new FormData();
        fd.append("video", file);
        const res = await fetch("/api/clipper/upload", { method: "POST", body: fd });
        if (!res.ok) throw new Error(`Upload gagal: HTTP ${res.status}`);
        const data = await res.json();
        setVideoPath(data.video_path);
        setSourceName(data.name || file.name);
      } else {
        if (!youtubeUrl.trim()) { setError("Masukkan link YouTube dulu."); return; }
        const data = await apiPostJSON("/api/clipper/youtube", { youtube_url: youtubeUrl.trim() });
        // poll job sampai selesai
        await new Promise((resolve, reject) => {
          let cancelled = false;
          const tick = async () => {
            if (cancelled) return;
            const job = await (await fetch(`/api/jobs/${data.job_id}`)).json();
            if (job.status === "done") { setVideoPath(job.result.video_path); setSourceName(job.result.name); resolve(); }
            else if (job.status === "error") reject(new Error(job.error || "Download gagal"));
            else setTimeout(tick, 700);
          };
          tick();
        });
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setSourceBusy(false);
    }
  };

  const analyze = async () => {
    if (!videoPath) return;
    setError(null); setResults(null); setAnalyzing(true); setClips([]);
    try {
      const data = await apiPostJSON("/api/clipper/analyze", { video_path: videoPath, num_clips: numClips });
      setClips(data.clips || []);
      setTotalDur(data.total_duration || 0);
      setSelected(Object.fromEntries((data.clips || []).map(c => [c.index, true])));
    } catch (e) {
      setError(String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const toggleClip = (idx) => setSelected(prev => ({ ...prev, [idx]: !prev[idx] }));
  const toggleAll = () => {
    const allOn = clips.length > 0 && clips.every(c => selected[c.index]);
    setSelected(Object.fromEntries(clips.map(c => [c.index, !allOn])));
  };

  const render = async () => {
    const chosen = clips.filter(c => selected[c.index]);
    if (!chosen.length) { setError("Pilih minimal 1 clip."); return; }
    setError(null); setRendering(true); setResults(null);
    try {
      const data = await apiPostJSON("/api/clipper/render", {
        video_path: videoPath, clips: chosen, aspect, output_name: "clipper",
      });
      setResults(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setRendering(false);
    }
  };

  const selectedCount = clips.filter(c => selected[c.index]).length;
  const selDur = clips.filter(c => selected[c.index]).reduce((a, c) => a + c.duration, 0);

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto" style={{ background: "rgba(10,8,5,0.88)", backdropFilter: "blur(4px)" }}>
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        <div className="flex flex-col gap-6" style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, boxShadow: "0 20px 60px rgba(0,0,0,0.6)" }}>
          {/* Header */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <span style={{ fontFamily: F.mono, fontSize: 12, color: C.cyan, letterSpacing: "0.08em" }}>CLIPPER</span>
              <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700, marginTop: 4 }}>1 Video → N Clip</h2>
              <p style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim, marginTop: 4 }}>Potong jadi beberapa clip siap upload (Reels / TikTok / Shorts). Sumber: upload file atau link YouTube.</p>
            </div>
            <button onClick={onClose} className="flex items-center justify-center rounded" style={{ width: 32, height: 32, background: C.panel, border: `1px solid ${C.borderSoft}`, color: C.paperDim, cursor: "pointer" }}>
              <X size={16} />
            </button>
          </div>

          {/* Step 1 — Sumber */}
          <div className="flex flex-col gap-3 rounded p-4" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.amber, letterSpacing: "0.08em" }}>1 / SUMBER VIDEO</span>
            <div className="flex items-center gap-2">
              {[{ id: "file", label: "Upload File", icon: Upload }, { id: "youtube", label: "Link YouTube", icon: Link2 }].map(t => (
                <button key={t.id} onClick={() => setInputType(t.id)} className="flex items-center gap-1.5 px-3.5 py-2 rounded"
                  style={{ background: inputType === t.id ? C.tally : C.panelRaised, border: `1px solid ${inputType === t.id ? C.tally : C.border}`, color: inputType === t.id ? C.paper : C.paperDim, fontFamily: F.body, fontSize: 12, fontWeight: 600, cursor: "pointer" }}>
                  <t.icon size={13} /> {t.label}
                </button>
              ))}
            </div>
            {inputType === "file" ? (
              <div className="flex items-center gap-3">
                <button onClick={() => fileRef.current?.click()} className="flex-1 text-left px-4 py-3 rounded" style={{ background: C.panelRaised, border: `1px dashed ${C.border}`, color: C.paperDim, fontFamily: F.body, fontSize: 12.5, cursor: "pointer" }}>
                  {file ? `📁 ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)` : "Klik untuk pilih file video (mp4/mov/mkv/webm…)"}
                </button>
                <input ref={fileRef} type="file" accept="video/*" style={{ display: "none" }} onChange={e => { if (e.target.files?.[0]) setFile(e.target.files[0]); }} />
                <button onClick={prepSource} disabled={sourceBusy || !file} className="flex items-center gap-2 px-4 py-2 rounded" style={{ background: sourceBusy ? C.panelRaised : C.tally, color: C.paper, fontFamily: F.body, fontWeight: 600, fontSize: 12.5, border: "none", cursor: sourceBusy || !file ? "default" : "pointer", opacity: sourceBusy || !file ? 0.5 : 1 }}>
                  {sourceBusy ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />} Siapkan
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <input value={youtubeUrl} onChange={e => setYoutubeUrl(e.target.value)} placeholder="https://youtube.com/watch?v=…  atau  https://youtu.be/…"
                  style={{ flex: 1, fontFamily: F.body, fontSize: 12.5, color: C.paper, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 6, padding: "10px 12px", outline: "none" }} />
                <button onClick={prepSource} disabled={sourceBusy || !youtubeUrl.trim()} className="flex items-center gap-2 px-4 py-2 rounded" style={{ background: sourceBusy ? C.panelRaised : C.tally, color: C.paper, fontFamily: F.body, fontWeight: 600, fontSize: 12.5, border: "none", cursor: sourceBusy || !youtubeUrl.trim() ? "default" : "pointer", opacity: sourceBusy || !youtubeUrl.trim() ? 0.5 : 1 }}>
                  {sourceBusy ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />} Download
                </button>
              </div>
            )}
            {sourceBusy && inputType === "youtube" && <span style={{ fontFamily: F.body, fontSize: 11.5, color: C.amber }}>Mengunduh video dari YouTube…</span>}
            {videoPath && <span style={{ fontFamily: F.mono, fontSize: 11, color: C.caption }}>✓ {sourceName} — siap di-clip</span>}
          </div>

          {/* Step 2 — Jumlah clip */}
          <div className="flex flex-col gap-3 rounded p-4" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.amber, letterSpacing: "0.08em" }}>2 / JUMLAH CLIP</span>
            <div className="flex items-center gap-4 flex-wrap">
              <div className="flex items-center gap-3">
                <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim }}>{numClips} clip</span>
                <input type="range" min={1} max={12} value={numClips} onChange={e => setNumClips(parseInt(e.target.value))} style={{ width: 180, accentColor: C.tally }} />
              </div>
              <button onClick={analyze} disabled={analyzing || !videoPath} className="flex items-center gap-2 px-4 py-2 rounded" style={{ background: analyzing || !videoPath ? C.panelRaised : C.cyan, color: C.bg, fontFamily: F.body, fontWeight: 700, fontSize: 12.5, border: "none", cursor: analyzing || !videoPath ? "default" : "pointer", opacity: analyzing || !videoPath ? 0.5 : 1 }}>
                {analyzing ? <Loader2 size={14} className="animate-spin" /> : <Scissors size={14} />} {analyzing ? "Menganalisis scene…" : "Analisis Video"}
              </button>
              {totalDur > 0 && <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperFaint }}>total {fmt(totalDur)}</span>}
            </div>
          </div>

          {/* Step 3 — Preview & pilih */}
          {clips.length > 0 && (
            <div className="flex flex-col gap-3 rounded p-4" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
              <div className="flex items-center justify-between">
                <span style={{ fontFamily: F.mono, fontSize: 10, color: C.amber, letterSpacing: "0.08em" }}>3 / PILIH CLIP ({selectedCount}/{clips.length})</span>
                <button onClick={toggleAll} style={{ fontFamily: F.mono, fontSize: 10.5, color: C.cyan, background: "none", border: "none", cursor: "pointer" }}>{selectedCount === clips.length ? "Unselect semua" : "Select semua"}</button>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                {clips.map(c => {
                  const on = !!selected[c.index];
                  return (
                    <button key={c.index} onClick={() => toggleClip(c.index)}
                      className="relative rounded overflow-hidden text-left"
                      style={{ border: `2px solid ${on ? C.tally : C.borderSoft}`, background: C.panelRaised, cursor: "pointer", padding: 0 }}>
                      <div style={{ position: "relative", height: 120, background: "#000" }}>
                        {c.thumbnail_url
                          ? <img src={c.thumbnail_url} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                          : <Film size={20} color={C.paperFaint} style={{ margin: "50px auto", display: "block" }} />}
                        {on && <div className="absolute flex items-center justify-center" style={{ top: 6, right: 6, width: 20, height: 20, borderRadius: "50%", background: C.tally }}><Check size={12} color={C.paper} strokeWidth={3} /></div>}
                      </div>
                      <div className="px-2 py-1.5" style={{ background: C.panelRaised }}>
                        <div style={{ fontFamily: F.mono, fontSize: 10.5, color: C.paper }}>Clip {c.index + 1} · {c.duration.toFixed(1)}s</div>
                        <div style={{ fontFamily: F.mono, fontSize: 9, color: C.paperFaint }}>{fmt(c.start)} – {fmt(c.end)}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Step 4 — Render */}
          {clips.length > 0 && (
            <div className="flex flex-col gap-3 rounded p-4" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
              <span style={{ fontFamily: F.mono, fontSize: 10, color: C.amber, letterSpacing: "0.08em" }}>4 / FORMAT & RENDER</span>
              <div className="flex items-center gap-3 flex-wrap">
                <select value={aspect} onChange={e => setAspect(e.target.value)} style={{ fontFamily: F.mono, fontSize: 11.5, color: C.paper, background: C.panelRaised, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", outline: "none", cursor: "pointer" }}>
                  {ASPECTS.map(a => <option key={a.id} value={a.id}>{a.label} ({a.res})</option>)}
                </select>
                <button onClick={render} disabled={rendering || selectedCount === 0} className="flex items-center gap-2 px-5 py-2.5 rounded" style={{ background: rendering || selectedCount === 0 ? C.panelRaised : C.tally, color: C.paper, fontFamily: F.body, fontWeight: 700, fontSize: 13, border: "none", cursor: rendering || selectedCount === 0 ? "default" : "pointer", opacity: rendering || selectedCount === 0 ? 0.5 : 1 }}>
                  {rendering ? <Loader2 size={14} className="animate-spin" /> : <Clapperboard size={14} />} {rendering ? "Merender…" : `Render ${selectedCount} Clip (${fmt(selDur)})`}
                </button>
              </div>

              {results && (
                <div className="flex flex-col gap-2">
                  <span style={{ fontFamily: F.mono, fontSize: 11, color: C.caption }}>✓ {results.files.length - 1} clip siap — klik untuk unduh, atau ambil semuanya sekaligus:</span>
                  <div className="flex items-center gap-2 flex-wrap">
                    {results.files.map((f, i) => (
                      <a key={i} href={f.url} download style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: F.mono, fontSize: 10.5, color: f.is_zip ? C.amber : C.paperDim, background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 6, padding: "7px 10px", textDecoration: "none" }}>
                        <Download size={12} /> {f.name}
                      </a>
                    ))}
                  </div>
                  <span style={{ fontFamily: F.body, fontSize: 11, color: C.paperFaint }}>Tip: buka clip di editor video biar bisa tambah caption/teks sebelum upload.</span>
                </div>
              )}
            </div>
          )}

          {error && <div className="flex items-center gap-2 px-4 py-3 rounded" style={{ background: "#2A1712", border: `1px solid ${C.tallyDim}` }}><AlertTriangle size={14} color={C.tally} /><span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim }}>{error}</span></div>}

          <div className="flex items-start gap-2 px-3.5 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
            <Info size={14} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
            <span style={{ fontFamily: F.body, fontSize: 11.5, color: C.paperDim, lineHeight: 1.5 }}>
              Clipper memotong otomatis di titik scene change terdekat supaya gak motong di tengah adegan. Audio asli video tetap dipertahankan. Resolusi output 1080×1920 (vertical).
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
