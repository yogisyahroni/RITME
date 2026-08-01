import React, { useState, useRef, useEffect } from "react";
import { X, Upload, Layers, Loader2, AlertTriangle, Download, Check, Trash2, Play } from "lucide-react";

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
  caption: "#7FB88A",
};

const F = {
  display: "'''Archivo Expanded''', sans-serif",
  body: "'''IBM Plex Sans''', sans-serif",
  mono: "'''IBM Plex Mono''', monospace",
};

async function apiPostJSON(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) {
    let msg = `${path}: HTTP ${res.status}`;
    try { const t = await res.json(); if (t.detail) msg = String(t.detail); } catch {}
    throw new Error(msg);
  }
  return res.json();
}

function fmtDur(segs) {
  const d = (segs || []).reduce((a, s) => a + Math.max((s.duration || 3) - (s.start_trim || 0) - (s.end_trim || 0), 0.5), 0);
  const m = Math.floor(d / 60);
  return `${m}m ${Math.round(d % 60)}s`;
}

export default function BatchRenderTool({ onClose }) {
  const [items, setItems] = useState([]);       // {name, body, file}
  const [jobId, setJobId] = useState(null);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const fileRef = useRef(null);

  const addFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    const added = [];
    for (const f of files) {
      try {
        const text = await f.text();
        const data = JSON.parse(text);
        if (!data?.segments?.length) throw new Error("tidak ada segmen");
        const fin = data.finishing || {};
        added.push({
          name: (data.name || f.name.replace(/\.ritme\.json$/i, "")).slice(0, 60),
          file: f.name,
          body: {
            name: (data.name || f.name.replace(/\.ritme\.json$/i, "")).slice(0, 60),
            segments: data.segments,
            narration_audio_path: data.narration?.audio_path || fin.narration_audio_path || "",
            template_name: data.narrationMeta?.template_name || "",
            add_music: !!fin.add_music,
            music_mood: fin.music_mood || null,
            caption_style: fin.caption_style || "minimal-white-center",
            transition_style: fin.transition_style || "hard_cut",
            ken_burns: !!fin.ken_burns,
            watermark_path: fin.watermark_path || null,
            watermark_pos: fin.watermark_pos || "bottom-right",
          },
        });
      } catch (e) {
        added.push({ name: f.name, file: f.name, error: `File tidak valid (${String(e)})` });
      }
    }
    setItems(prev => [...prev, ...added]);
    setResult(null); setError(null);
  };

  const removeItem = (i) => {
    setItems(prev => prev.filter((_, idx) => idx !== i));
    setResult(null);
  };

  const runBatch = async () => {
    const valid = items.filter(it => !it.error);
    if (!valid.length) { setError("Tidak ada project valid untuk dirender."); return; }
    setError(null); setResult(null); setJob({ progress: 1, message: "Memulai batch…" });
    try {
      const data = await apiPostJSON("/api/batch/render", { items: valid.map(it => it.body) });
      setJobId(data.job_id);
    } catch (e) { setJob(null); setError(String(e)); }
  };

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer = null;
    const tick = async () => {
      if (cancelled) return;
      try {
        const j = await (await fetch(`/api/jobs/${jobId}`)).json();
        if (cancelled) return;
        setJob({ progress: j.progress, message: j.message });
        if (j.status === "done") { setJob(null); setResult(j.result); }
        else if (j.status === "error") { setJob(null); setError(j.error || "Batch gagal"); }
        else timer = setTimeout(tick, 900);
      } catch (e) { if (!cancelled) { setJob(null); setError(String(e)); } }
    };
    tick();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [jobId]);

  const okCount = result ? result.items.filter(i => i.status === "ok").length : 0;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto" style={{ background: "rgba(10,8,5,0.88)", backdropFilter: "blur(4px)" }}>
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6">
        <div className="flex flex-col gap-5" style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, boxShadow: "0 20px 60px rgba(0,0,0,0.6)" }}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <span style={{ fontFamily: F.mono, fontSize: 12, color: C.cyan, letterSpacing: "0.08em" }}>BATCH RENDER</span>
              <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700, marginTop: 4 }}>Render Banyak Project Sekaligus</h2>
              <p style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim, marginTop: 4 }}>Upload file project (.ritme.json) dari Save Project, render semua berurutan. Maksimal 10 project per batch.</p>
            </div>
            <button onClick={onClose} className="flex items-center justify-center rounded" style={{ width: 32, height: 32, background: C.panel, border: `1px solid ${C.borderSoft}`, color: C.paperDim, cursor: "pointer" }}>
              <X size={16} />
            </button>
          </div>

          <div className="flex items-center gap-3">
            <button onClick={() => fileRef.current?.click()} className="flex items-center gap-2 px-4 py-2.5 rounded" style={{ background: C.tally, color: C.paper, fontFamily: F.body, fontWeight: 600, fontSize: 12.5, border: "none", cursor: "pointer" }}>
              <Upload size={14} /> Tambah Project (.ritme.json)
            </button>
            <input ref={fileRef} type="file" accept=".ritme.json,.json" multiple style={{ display: "none" }}
              onChange={e => { addFiles(e.target.files); e.target.value = ""; }} />
            {items.length > 0 && <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperFaint }}>{items.length} project</span>}
          </div>

          {items.length > 0 && (
            <div className="flex flex-col gap-2">
              {items.map((it, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${it.error ? C.tallyDim : C.borderSoft}` }}>
                  <Layers size={14} color={it.error ? C.red : C.cyan} style={{ flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: F.body, fontSize: 12.5, color: it.error ? C.red : C.paper, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {it.name} {it.error && <span style={{ color: C.red }}>— {it.error}</span>}
                    </div>
                    {!it.error && <div style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint }}>{it.file} · {it.body.segments.length} segmen · {fmtDur(it.body.segments)}</div>}
                  </div>
                  <button onClick={() => removeItem(i)} className="flex items-center justify-center rounded" style={{ width: 26, height: 26, background: "none", border: "none", color: C.paperFaint, cursor: "pointer" }}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          )}

          {error && <div className="flex items-center gap-2 px-4 py-3 rounded" style={{ background: "#2A1712", border: `1px solid ${C.tallyDim}` }}><AlertTriangle size={14} color={C.tally} /><span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim }}>{error}</span></div>}

          {job && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2" style={{ fontFamily: F.mono, fontSize: 11.5, color: C.amber }}>
                <Loader2 size={14} className="animate-spin" /> {job.message}
              </div>
              <div style={{ height: 6, borderRadius: 3, background: C.panelRaised, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${job.progress || 0}%`, background: C.tally, transition: "width 0.4s" }} />
              </div>
            </div>
          )}

          {!job && items.filter(it => !it.error).length > 0 && (
            <button onClick={runBatch} className="flex items-center justify-center gap-2 px-5 py-3 rounded" style={{ background: C.cyan, color: C.bg, fontFamily: F.body, fontWeight: 700, fontSize: 13.5, border: "none", cursor: "pointer" }}>
              <Play size={14} /> Render {items.filter(it => !it.error).length} Project
            </button>
          )}

          {result && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-2" style={{ fontFamily: F.mono, fontSize: 11.5, color: C.caption }}>
                <Check size={14} /> Batch selesai — {okCount}/{result.items.length} berhasil
              </div>
              {result.items.map((r, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${r.status === "ok" ? C.caption : C.tallyDim}` }}>
                  <span style={{ fontFamily: F.mono, fontSize: 10.5, color: r.status === "ok" ? C.caption : C.red, width: 64, flexShrink: 0 }}>{r.status.toUpperCase()}</span>
                  <span style={{ fontFamily: F.body, fontSize: 12.5, color: C.paper, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.name}</span>
                  {r.status === "ok"
                    ? <a href={r.url} download style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: F.mono, fontSize: 11, color: C.amber, textDecoration: "none", background: C.panelRaised, border: `1px solid ${C.borderSoft}`, borderRadius: 6, padding: "6px 10px" }}><Download size={12} /> {r.name}.mp4</a>
                    : <span style={{ fontFamily: F.mono, fontSize: 10, color: C.red, maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
