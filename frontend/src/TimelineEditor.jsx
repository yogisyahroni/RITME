import React, { useState, useEffect, useRef, useCallback } from "react";
import { Play, Download, ChevronRight, Trash2, ArrowUp, ArrowDown, Scissors, Clapperboard, Check, Loader2, AlertTriangle, Info, Film } from "lucide-react";

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

function TimelineEditor({ narration, footageData, picks }) {
  const [segments, setSegments] = useState([]);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [finishing, setFinishing] = useState({
    add_music: false,
    music_mood: "calm",
    caption_style: "minimal-white-center",
    transition_style: "hard_cut",
    ken_burns: false,
  });
  const videoRef = useRef(null);
  const cancelRef = useRef(null);

  useEffect(() => {
    if (!narration?.segments) return;
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
      };
    });
    setSegments(segs);
  }, [narration, footageData, picks]);

  useEffect(() => () => cancelRef.current && cancelRef.current(), []);

  const updateSegment = (idx, updates) => {
    setSegments(prev => prev.map((s, i) => i === idx ? { ...s, ...updates } : s));
  };

  const moveSegment = (idx, direction) => {
    if ((direction === -1 && idx === 0) || (direction === 1 && idx === segments.length - 1)) return;
    const newSegs = [...segments];
    const temp = newSegs[idx];
    newSegs[idx] = newSegs[idx + direction];
    newSegs[idx + direction] = temp;
    setSegments(newSegs);
  };

  const removeSegment = (idx) => {
    setSegments(prev => prev.filter((_, i) => i !== idx));
  };

  const addTrim = (idx, edge, value) => {
    const seg = segments[idx];
    if (edge === "start") updateSegment(idx, { start_trim: Math.max(0, Math.min(value, seg.duration - 0.5)) });
    if (edge === "end") updateSegment(idx, { end_trim: Math.max(0, Math.min(value, seg.duration - 0.5)) });
  };

  const exportTimeline = async (preview = false) => {
    setError(null);
    setJob({ progress: 5, message: preview ? "Membuat preview..." : "Merender video..." });
    try {
      const endpoint = preview ? "/api/timeline/preview" : "/api/timeline/export";
      const body = {
        segments: segments.filter(s => s.video_path),
        narration_audio_path: narration?.audio_path || "",
        output_name: `ritme_${Date.now()}`,
        template_name: narration?.template_name || "",
        add_music: finishing.add_music,
        music_mood: finishing.add_music ? finishing.music_mood : null,
        caption_style: finishing.caption_style,
        transition_style: finishing.transition_style,
        ken_burns: finishing.ken_burns,
      };
      if (preview) {
        const blob = await fetch(endpoint, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
        }).then(r => { if (!r.ok) throw new Error("Preview failed"); return r.blob(); });
        const url = URL.createObjectURL(blob);
        setPreviewUrl(url);
        setJob(null);
        if (videoRef.current) { videoRef.current.src = url; setPlaying(true); }
      } else {
        const blob = await fetch(endpoint, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
        }).then(r => { if (!r.ok) throw new Error("Export failed"); return r.blob(); });
        const url = URL.createObjectURL(blob);
        setResult(url);
        setJob(null);
      }
    } catch (e) { setJob(null); setError(String(e)); }
  };

  const downloadVideo = () => {
    if (!result) return;
    const a = document.createElement("a");
    a.href = result;
    a.download = "ritme_timeline.mp4";
    a.click();
  };

  const totalDuration = segments.reduce((a, s) => a + Math.max(s.duration - s.start_trim - s.end_trim, 0.5), 0);

  const colors = [C.tally, "#6FE7DD", "#E8A33D", "#7FB88A", "#8B5CF6", "#F472B6", "#FBBF24", "#34D399"];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <span style={{ fontFamily: F.mono, fontSize: 12, color: C.cyan, letterSpacing: "0.08em" }}>05 / TIMELINE EDITOR</span>
        <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700, marginTop: 4 }}>Edit Timeline Manual</h2>
        <p style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim, marginTop: 4 }}>{segments.length} segmen · {fmt(totalDuration)} total</p>
      </div>
      <div className="flex flex-col gap-1">
        <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", marginBottom: 4 }}>TIMELINE</span>
        <div className="flex" style={{ height: 36, gap: 2, borderRadius: 4, overflow: "hidden" }}>
          {segments.map((s, i) => {
            const dur = Math.max(s.duration - s.start_trim - s.end_trim, 0.5);
            const w = totalDuration > 0 ? (dur / totalDuration) * 100 : 100 / segments.length;
            return <div key={i} style={{ width: `${w}%`, background: colors[i % colors.length], display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden", minWidth: 30 }}>
              <span style={{ fontFamily: F.mono, fontSize: 9, color: "#fff", fontWeight: 600, textShadow: "0 1px 2px rgba(0,0,0,0.5)" }}>{fmt(dur)}</span>
            </div>;
          })}
        </div>
      </div>
      <div className="flex flex-col gap-2">
        <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", marginBottom: 4 }}>CLIPS</span>
        {segments.map((s, i) => (
          <div key={i} className="flex items-center gap-3 px-4 py-3 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
            <div className="flex items-center justify-center rounded-full" style={{ width: 26, height: 26, background: C.panelRaised, border: `1px solid ${C.border}`, flexShrink: 0 }}>
              <span style={{ fontFamily: F.mono, fontSize: 11, color: C.cyan, fontWeight: 600 }}>{i + 1}</span>
            </div>
            <div style={{ width: 50, height: 30, background: C.panelRaised, borderRadius: 3, flexShrink: 0, overflow: "hidden" }}>
              {(() => { const cand = footageData?.[String(s.index)]?.candidates?.[picks?.[s.index] ?? 0]; const thumb = cand?.thumbnail_url; return thumb ? <img src={thumb} style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : <Film size={16} color={C.paperFaint} style={{ margin: "7px auto", display: "block" }} />; })()}
            </div>
            <div className="flex-1 flex flex-col" style={{ minWidth: 0 }}>
              <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paper, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>Seg {i + 1}: {s.narration_text.slice(0, 60)}</span>
              <span style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperFaint }}>{s.keywords?.join(", ")}</span>
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
              <button onClick={() => moveSegment(i, -1)} disabled={i === 0} style={{ background: "none", border: "none", cursor: i === 0 ? "default" : "pointer", opacity: i === 0 ? 0.3 : 1, padding: 2 }}><ArrowUp size={13} color={C.paperDim} /></button>
              <button onClick={() => moveSegment(i, 1)} disabled={i === segments.length - 1} style={{ background: "none", border: "none", cursor: i === segments.length - 1 ? "default" : "pointer", opacity: i === segments.length - 1 ? 0.3 : 1, padding: 2 }}><ArrowDown size={13} color={C.paperDim} /></button>
              {segments.length > 1 && <button onClick={() => removeSegment(i)} style={{ background: "none", border: "none", cursor: "pointer", padding: 2 }}><Trash2 size={13} color={C.red} /></button>}
            </div>
          </div>
        ))}
      </div>
      {error && <div className="flex items-center gap-2 px-4 py-3 rounded" style={{ background: "#2A1712", border: `1px solid ${C.tallyDim}` }}><AlertTriangle size={14} color={C.tally} /><span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim }}>{error}</span></div>}
      {job && <div className="flex items-center gap-3 px-4 py-3 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}><Loader2 size={14} color={C.amber} className="animate-spin" /><span style={{ fontFamily: F.body, fontSize: 12, color: C.paperDim }}>{job.message}</span></div>}
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
            </select>
          </label>
          <label className="flex items-center gap-2" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={finishing.ken_burns} onChange={e => setFinishing({ ...finishing, ken_burns: e.target.checked })} style={{ accentColor: C.tally }} />
            <span style={{ fontFamily: F.body, fontSize: 12, color: C.paper }}>Ken Burns (zoom pelan)</span>
          </label>
        </div>
      </div>
      <div className="flex items-center gap-3 flex-wrap">
        <PrimaryButton onClick={() => exportTimeline(true)} disabled={job !== null} icon={Play}>Preview</PrimaryButton>
        <PrimaryButton onClick={() => exportTimeline(false)} disabled={job !== null} icon={Clapperboard}>Render Video</PrimaryButton>
        {result && <PrimaryButton onClick={downloadVideo} icon={Download} variant="outline">Download .mp4</PrimaryButton>}
      </div>
      {(previewUrl || result) && <div style={{ borderRadius: 8, overflow: "hidden", background: "#000" }}><video ref={videoRef} src={previewUrl || result} controls style={{ width: "100%", maxHeight: 400, display: "block" }} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} /></div>}
      <div className="flex items-start gap-2 px-3.5 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
        <Info size={14} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
        <span style={{ fontFamily: F.body, fontSize: 11.5, color: C.paperDim, lineHeight: 1.5 }}>Trim in/out dalam detik. Setiap segmen bisa diatur ulang (urutan) atau dihapus. Klik Preview untuk render cepat resolusi kecil, Render untuk full HD.</span>
      </div>
    </div>
  );
}
export default TimelineEditor;
