import React, { useState, useEffect, useRef } from "react";
import { X, Upload, Link2, Clapperboard, Loader2, AlertTriangle, Download, Check, Film, Scissors, Info, Zap, ArrowLeft, Play } from "lucide-react";

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

export default function ClipperTool({ onClose, variant = "modal" }) {
  const isPage = variant === "page";
  const [inputType, setInputType] = useState("file");   // file | youtube
  const [file, setFile] = useState(null);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [videoPath, setVideoPath] = useState(null);
  const [sourceName, setSourceName] = useState("");
  const [sourceBusy, setSourceBusy] = useState(false);

  const [numClips, setNumClips] = useState(5);
  const [clips, setClips] = useState([]);
  const [totalDur, setTotalDur] = useState(0);
  const [videoUrl, setVideoUrl] = useState("");
  const [previewClip, setPreviewClip] = useState(null);   // index clip yang di-preview
  const [analyzing, setAnalyzing] = useState(false);
  const [selected, setSelected] = useState({});         // index -> true

  const [aspect, setAspect] = useState("9:16");
  const [autoCaption, setAutoCaption] = useState(false);
  const [captionStyle, setCaptionStyle] = useState("bold-white-bottom");
  const [rendering, setRendering] = useState(false);
  const [previewWords, setPreviewWords] = useState(null);   // words utk preview clip
  const [capsLoading, setCapsLoading] = useState(false);
  const [capsError, setCapsError] = useState("");
  const [activeWord, setActiveWord] = useState(-1);          // index kata aktif di preview
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  // Preview caption: fetch word timestamps saat previewClip berubah (selalu, biar preview nunjukin caption kayak hasil render)
  useEffect(() => {
    setActiveWord(-1);
    const c = clips[previewClip];
    if (previewClip === null || !c) {
      setPreviewWords(null);
      setCapsLoading(false);
      return;
    }
    if (!videoPath) return;
    let cancelled = false;
    setCapsLoading(true);
    setCapsError("");
    apiPostJSON("/api/clipper/preview_captions", {
      video_path: videoPath, start: c.start, end: c.end,
    }).then(d => {
      if (cancelled) return;
      setPreviewWords(d.words || []);
    }).catch(e => {
      if (cancelled) return;
      setCapsError(String(e.message || e));
      setPreviewWords(null);
    }).finally(() => { if (!cancelled) setCapsLoading(false); });
    return () => { cancelled = true; };
  }, [previewClip, autoCaption, videoPath]);

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
    setError(null); setResults(null); setAnalyzing(true); setClips([]); setPreviewClip(null);
    try {
      const data = await apiPostJSON("/api/clipper/analyze", { video_path: videoPath, num_clips: numClips });
      setClips(data.clips || []);
      setTotalDur(data.total_duration || 0);
      setVideoUrl(data.video_url || "");
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
        captions: autoCaption, caption_style: captionStyle,
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
    <div className={isPage ? "min-h-[calc(100vh-57px)]" : "fixed inset-0 z-50 overflow-y-auto"} style={isPage ? undefined : { background: "rgba(10,8,5,0.88)", backdropFilter: "blur(4px)" }}>
      <div className={isPage ? "max-w-6xl mx-auto px-4 sm:px-6 py-6" : "max-w-5xl mx-auto px-4 sm:px-6 py-6"}>
        <div className="flex flex-col gap-6" style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, boxShadow: "0 20px 60px rgba(0,0,0,0.6)" }}>
          {/* Header */}
          <div className="flex items-start justify-between gap-4">
            <div>
              <span style={{ fontFamily: F.mono, fontSize: 12, color: C.cyan, letterSpacing: "0.08em" }}>CLIPPER</span>
              <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700, marginTop: 4 }}>1 Video → N Clip</h2>
              <p style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim, marginTop: 4 }}>Potong jadi beberapa clip siap upload (Reels / TikTok / Shorts). Sumber: upload file atau link YouTube.</p>
            </div>
            {isPage ? (
              <button onClick={onClose} className="flex items-center gap-1.5 px-3 py-1.5 rounded" style={{ background: C.panelRaised, border: `1px solid ${C.borderSoft}`, color: C.paperDim, cursor: "pointer" }}>
                <ArrowLeft size={14} />
                <span style={{ fontFamily: F.mono, fontSize: 10 }}>Kembali ke Studio</span>
              </button>
            ) : (
              <button onClick={onClose} className="flex items-center justify-center rounded" style={{ width: 32, height: 32, background: C.panel, border: `1px solid ${C.borderSoft}`, color: C.paperDim, cursor: "pointer" }}>
                <X size={16} />
              </button>
            )}
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

              {/* Preview player — klik ▶ di card buat play clip dari posisi start */}
              {previewClip !== null && clips[previewClip] && (
                <div className="flex flex-col gap-2 rounded overflow-hidden" style={{ background: "#000", border: `1px solid ${C.tally}` }}>
                  <div className="flex items-center justify-between px-3 py-2" style={{ background: "rgba(232,84,46,0.12)" }}>
                    <span style={{ fontFamily: F.mono, fontSize: 11, color: C.tally, letterSpacing: "0.06em" }}>
                      ▶ PREVIEW CLIP {previewClip + 1} — {clips[previewClip].duration.toFixed(1)}s ({fmt(clips[previewClip].start)} – {fmt(clips[previewClip].end)}) · {ASPECTS.find(a => a.id === aspect)?.res || aspect}
                    </span>
                    <span className="flex items-center gap-2">
                      <span style={{ fontFamily: F.mono, fontSize: 9.5, color: autoCaption ? C.caption : C.paperFaint, background: autoCaption ? "rgba(127,184,138,0.15)" : "rgba(255,255,255,0.05)", border: `1px solid ${autoCaption ? C.caption : C.border}`, borderRadius: 10, padding: "2px 8px" }}>
                        CAPTION {autoCaption ? "AKTIF" : "PREVIEW"}
                      </span>
                      <button onClick={() => setPreviewClip(null)} style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim, background: "none", border: "none", cursor: "pointer" }}>✕ Tutup</button>
                    </span>
                  </div>
                  {videoUrl && (
                    <div className="flex items-center justify-center" style={{
                      background: "radial-gradient(circle, #1E1B15 0%, #15130F 70%)",
                      padding: "16px 0", minHeight: 460,
                    }}>
                      <div style={{ height: "min(520px, 70vh)", aspectRatio: aspect === "1:1" ? "1 / 1" : aspect === "16:9" ? "16 / 9" : "9 / 16", maxWidth: "100%", boxShadow: "0 8px 32px rgba(0,0,0,0.6)", borderRadius: 6, overflow: "hidden", position: "relative" }}>
                        <video
                          key={`${previewClip}-${aspect}`}
                          src={videoUrl}
                          controls
                          autoPlay
                          playsInline
                          style={{ width: "100%", height: "100%", objectFit: "cover", background: "#000", display: "block" }}
                          onLoadedMetadata={(e) => {
                            try { e.currentTarget.currentTime = clips[previewClip].start + 0.05; e.currentTarget.play().catch(() => {}); } catch (_) {}
                          }}
                          onTimeUpdate={(e) => {
                            if (!previewWords || !previewWords.length) return;
                            const rel = e.currentTarget.currentTime - clips[previewClip].start;
                            let idx = -1;
                            for (let i = 0; i < previewWords.length; i++) {
                              if (rel >= previewWords[i].start && rel <= (previewWords[i].end || previewWords[i].start + 0.1)) { idx = i; break; }
                            }
                            if (idx !== activeWord) setActiveWord(idx);
                          }}
                        />
                        {/* Live caption overlay (selalu tampil di preview; toggle AutoCaption cuma kontrol burn di render) */}
                        <div style={{ position: "absolute", left: 0, right: 0, bottom: 48, padding: "0 10px", textAlign: "center", pointerEvents: "none" }}>
                            {capsLoading && (
                              <div className="flex items-center justify-center gap-2" style={{ fontFamily: F.body, fontSize: 12, color: C.paperDim, background: "rgba(0,0,0,0.55)", borderRadius: 20, padding: "6px 14px", display: "inline-flex" }}>
                                <Loader2 size={13} className="animate-spin" /> Transkripsi clip…
                              </div>
                            )}
                            {capsError && (
                              <div style={{ fontFamily: F.body, fontSize: 11, color: C.red, background: "rgba(0,0,0,0.6)", borderRadius: 8, padding: "6px 10px", display: "inline-block" }}>
                                ⚠ {capsError}
                              </div>
                            )}
                            {!capsLoading && !capsError && previewWords && previewWords.length > 0 && (
                              <div style={{ fontFamily: F.body, fontWeight: 800, fontSize: 17, lineHeight: 1.5, textShadow: "0 2px 4px rgba(0,0,0,0.9), 0 0 2px #000, 0 0 2px #000" }}>
                                {previewWords.map((w, i) => {
                                  // window: 6 kata sebelum & sesudah kata aktif
                                  const vis = activeWord < 0 || (i >= activeWord - 6 && i <= activeWord + 6);
                                  if (!vis) return null;
                                  return (
                                    <span key={i} style={{
                                      color: i === activeWord ? "#ffd400" : i < activeWord ? "#ffffff" : "rgba(255,255,255,0.55)",
                                    }}>{w.word}{" "}</span>
                                  );
                                })}
                              </div>
                            )}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                {clips.map(c => {
                  const on = !!selected[c.index];
                  return (
                    <button key={c.index} onClick={() => toggleClip(c.index)}
                      className="relative rounded overflow-hidden text-left"
                      style={{ border: `2px solid ${on ? C.tally : C.borderSoft}`, background: C.panelRaised, cursor: "pointer", padding: 0 }}>
                      <div style={{ position: "relative", aspectRatio: "9 / 16", background: "#000", overflow: "hidden" }}>
                        {c.thumbnail_url
                          ? <img src={c.thumbnail_url} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                          : <Film size={20} color={C.paperFaint} style={{ margin: "50px auto", display: "block" }} />}
                        {/* tombol play preview */}
                        <div
                          onClick={(e) => { e.stopPropagation(); setPreviewClip(c.index); }}
                          className="absolute inset-0 flex items-center justify-center"
                          style={{ background: "rgba(0,0,0,0.35)", cursor: "pointer", border: "none" }}>
                          <div style={{ width: 38, height: 38, borderRadius: "50%", background: "rgba(255,255,255,0.92)", display: "flex", alignItems: "center", justifyContent: "center", boxShadow: "0 2px 10px rgba(0,0,0,0.5)" }}>
                            <Play size={17} color="#000" style={{ marginLeft: 2 }} />
                          </div>
                        </div>
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

                {/* AutoCaption toggle */}
                <label className="flex items-center gap-2 px-3 py-2 rounded" style={{ background: autoCaption ? "rgba(127,184,138,0.12)" : C.panelRaised, border: `1px solid ${autoCaption ? C.caption : C.border}`, cursor: "pointer" }}>
                  <input type="checkbox" checked={autoCaption} onChange={e => setAutoCaption(e.target.checked)} style={{ accentColor: C.caption }} />
                  <span style={{ fontFamily: F.body, fontSize: 12, color: autoCaption ? C.caption : C.paperDim, fontWeight: 600 }}>AutoCaption</span>
                  {autoCaption && (
                    <select value={captionStyle} onChange={e => setCaptionStyle(e.target.value)} onClick={e => e.stopPropagation()}
                      style={{ fontFamily: F.mono, fontSize: 10.5, color: C.paper, background: C.panel, border: `1px solid ${C.borderSoft}`, borderRadius: 4, padding: "3px 6px", outline: "none", cursor: "pointer" }}>
                      <option value="bold-white-bottom">Bold Putih</option>
                      <option value="minimal-white-center">Minimal Tengah</option>
                      <option value="news-style-lower-third">News Style</option>
                    </select>
                  )}
                </label>

                <button onClick={render} disabled={rendering || selectedCount === 0} className="flex items-center gap-2 px-5 py-2.5 rounded" style={{ background: rendering || selectedCount === 0 ? C.panelRaised : C.tally, color: C.paper, fontFamily: F.body, fontWeight: 700, fontSize: 13, border: "none", cursor: rendering || selectedCount === 0 ? "default" : "pointer", opacity: rendering || selectedCount === 0 ? 0.5 : 1 }}>
                  {rendering ? <Loader2 size={14} className="animate-spin" /> : <Clapperboard size={14} />} {rendering ? "Merender…" : `Render ${selectedCount} Clip (${fmt(selDur)})`}
                </button>
              </div>
              {autoCaption && (
                <p style={{ fontFamily: F.body, fontSize: 11, color: C.paperFaint, lineHeight: 1.5 }}>
                  ✨ AutoCaption: tiap clip di-transcribe (Whisper) & caption karaoke di-burn langsung ke video — kayak video TikTok/Reels viral.
                </p>
              )}

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
