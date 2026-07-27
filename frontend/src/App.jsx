import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Film, FileText, Mic, ScanSearch, Clapperboard,
  Check, Lock, RefreshCw, Download, Info, ChevronRight, Play,
  Upload, AlertTriangle, X, Plus, Loader2, Scissors, Trash2
} from "lucide-react";

/* ============================================================
   DESIGN TOKENS
   ============================================================ */
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
  display: "'Archivo Expanded', sans-serif",
  body: "'IBM Plex Sans', sans-serif",
  mono: "'IBM Plex Mono', monospace",
};

const FONT_IMPORT = `@import url('https://fonts.googleapis.com/css2?family=Archivo+Expanded:wght@600;700;800&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');`;

function useStickyState(defaultValue, key) {
  const [value, setValue] = useState(() => {
    try {
      const stickyValue = window.localStorage.getItem(key);
      return stickyValue !== null ? JSON.parse(stickyValue) : defaultValue;
    } catch {
      return defaultValue;
    }
  });
  useEffect(() => {
    try {
      if (value === null) {
        window.localStorage.removeItem(key);
      } else {
        window.localStorage.setItem(key, JSON.stringify(value));
      }
    } catch {}
  }, [key, value]);
  return [value, setValue];
}

const SOURCE_STYLES = {
  PEXELS: C.cyan,
  PIXABAY: C.amber,
  WIKIMEDIA: C.tally,
  "ARCHIVE.ORG": C.paperDim,
  YOUTUBE_CC: "#7FB88A",
};

// Display labels for script style acts (pipeline/script_styles/*.json).
// Keyed by act id so the Script Desk can show a readable section header
// instead of the raw id.
const ACT_LABELS = {
  hook_thesis: "Hook & Thesis",
  genesis_pattern: "Genesis & Pattern",
  paradox_catalyst: "Paradox & Catalyst",
  invisible_mechanism: "Invisible Mechanism",
  projection_open_loop: "Projection & Open Loop",
};

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1).padStart(4, "0");
  return `${m}:${sec}`;
}

/* ============================================================
   API HELPERS — same-origin, FastAPI serves both API + frontend
   ============================================================ */
async function apiPostJSON(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

async function apiPostForm(path, formData) {
  const res = await fetch(path, { method: "POST", body: formData });
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

/** Polls GET /api/jobs/{id} until status is "done" or "error". */
function pollJob(jobId, { onUpdate, onDone, onError, intervalMs = 700 }) {
  let cancelled = false;
  const tick = async () => {
    if (cancelled) return;
    try {
      const job = await apiGet(`/api/jobs/${jobId}`);
      if (cancelled) return;
      onUpdate && onUpdate(job);
      if (job.status === "done") {
        onDone && onDone(job.result);
      } else if (job.status === "error") {
        onError && onError(job.error || "Unknown error");
      } else {
        setTimeout(tick, intervalMs);
      }
    } catch (e) {
      if (!cancelled) onError && onError(String(e));
    }
  };
  tick();
  return () => { cancelled = true; };
}

/* ============================================================
   SHARED UI BITS
   ============================================================ */
function Eyebrow({ n, children }) {
  return (
    <div className="flex items-baseline gap-2 mb-1">
      <span style={{ fontFamily: F.mono, fontSize: 12, color: C.tally, letterSpacing: "0.08em" }}>
        {String(n).padStart(2, "0")} /
      </span>
      <span style={{ fontFamily: F.mono, fontSize: 12, color: C.paperDim, letterSpacing: "0.12em" }}>
        {children}
      </span>
    </div>
  );
}

function StatCell({ label, value, unit }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span style={{ fontFamily: F.mono, fontSize: 20, color: C.cyan, fontWeight: 600, lineHeight: 1 }}>
        {value}
        {unit && <span style={{ fontSize: 12, color: C.paperDim, marginLeft: 2 }}>{unit}</span>}
      </span>
      <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>
        {label}
      </span>
    </div>
  );
}

function PrimaryButton({ children, onClick, icon: Icon, disabled, loading }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      className="flex items-center gap-2 px-5 py-2.5 rounded"
      style={{
        background: disabled || loading ? C.panelRaised : C.tally,
        color: disabled || loading ? C.paperFaint : C.paper,
        fontFamily: F.body, fontWeight: 600, fontSize: 13.5,
        border: "none", cursor: disabled || loading ? "default" : "pointer",
        opacity: disabled ? 0.6 : 1,
      }}
    >
      {loading && <Loader2 size={15} className="animate-spin" />}
      {children}
      {!loading && Icon && <Icon size={15} />}
    </button>
  );
}

function ProgressBar({ progress, message }) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
      <Loader2 size={15} color={C.amber} className="animate-spin" style={{ flexShrink: 0 }} />
      <div className="flex-1 flex flex-col gap-1.5">
        <span style={{ fontFamily: F.body, fontSize: 12, color: C.paperDim }}>{message || "Memproses…"}</span>
        <div className="rounded-full overflow-hidden" style={{ height: 4, background: C.border }}>
          <div style={{ width: `${Math.max(progress, 4)}%`, height: "100%", background: C.amber, transition: "width 200ms ease" }} />
        </div>
      </div>
      <span style={{ fontFamily: F.mono, fontSize: 11, color: C.amber, width: 32, textAlign: "right" }}>{Math.round(progress)}%</span>
    </div>
  );
}

function ErrorBanner({ error, onRetry }) {
  return (
    <div className="flex items-start gap-2.5 px-4 py-3 rounded" style={{ background: "#2A1712", border: `1px solid ${C.tallyDim}` }}>
      <AlertTriangle size={15} color={C.tally} style={{ marginTop: 1, flexShrink: 0 }} />
      <div className="flex-1 flex flex-col gap-1.5">
        <span style={{ fontFamily: F.body, fontSize: 12.5, color: C.paper, fontWeight: 600 }}>Gagal</span>
        <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim, wordBreak: "break-word" }}>{error}</span>
      </div>
      {onRetry && (
        <button onClick={onRetry} className="flex-shrink-0" style={{ fontFamily: F.body, fontSize: 11.5, color: C.cyan, background: "none", border: "none", cursor: "pointer" }}>
          Coba lagi
        </button>
      )}
    </div>
  );
}

/* ============================================================
   SIGNAL-CHAIN STAGE NAV
   ============================================================ */
const STAGES = [
  { id: 1, label: "TEMPLATE", icon: Film },
  { id: 2, label: "SKRIP", icon: FileText },
  { id: 3, label: "NARASI", icon: Mic },
  { id: 4, label: "FOOTAGE", icon: ScanSearch },
  { id: 5, label: "RENDER", icon: Clapperboard },
];

function StageNav({ active, setActive, maxUnlocked }) {
  return (
    <div className="w-full overflow-x-auto" style={{ borderBottom: `1px solid ${C.border}` }}>
      <div className="flex items-stretch min-w-max relative px-4 sm:px-6" style={{ height: 64 }}>
        <div className="absolute left-0 right-0" style={{ top: 32, height: 1, background: C.border, marginLeft: 24, marginRight: 24 }} />
        {STAGES.map((s) => {
          const isActive = s.id === active;
          const isDone = s.id < maxUnlocked;
          const isLocked = s.id > maxUnlocked;
          const Icon = s.icon;
          return (
            <button
              key={s.id}
              onClick={() => !isLocked && setActive(s.id)}
              disabled={isLocked}
              className="flex flex-col items-center justify-center gap-1.5 relative px-4 sm:px-6"
              style={{ cursor: isLocked ? "default" : "pointer", background: "none", border: "none" }}
            >
              <div
                className="flex items-center justify-center rounded-full relative"
                style={{
                  width: 30, height: 30,
                  background: isActive ? C.tally : isDone ? C.panelRaised : C.panel,
                  border: `1.5px solid ${isActive ? C.tally : isDone ? C.cyan : C.border}`,
                  boxShadow: isActive ? `0 0 0 3px ${C.tallyDim}` : "none",
                  zIndex: 1,
                }}
              >
                {isDone ? <Check size={14} color={C.cyan} strokeWidth={2.5} />
                  : isLocked ? <Lock size={12} color={C.paperFaint} />
                  : <Icon size={14} color={isActive ? C.paper : C.paperDim} />}
              </div>
              <span style={{ fontFamily: F.mono, fontSize: 10, letterSpacing: "0.06em", color: isActive ? C.paper : isLocked ? C.paperFaint : C.paperDim, fontWeight: isActive ? 600 : 400 }}>
                {String(s.id).padStart(2, "0")} {s.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/* ============================================================
   STAGE 1 — TEMPLATE STUDIO
   ============================================================ */
function StageTemplate({ template, setTemplate, onNext }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState("my_style");
  const [job, setJob] = useState(null); // {progress, message}
  const [error, setError] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [savedTemplates, setSavedTemplates] = useState([]);
  const cancelRef = useRef(null);

  useEffect(() => () => cancelRef.current && cancelRef.current(), []);
  useEffect(() => { apiGet("/api/templates").then(setSavedTemplates).catch(() => setSavedTemplates([])); }, []);

  const runExtract = async () => {
    if (!file) return;
    setError(null);
    setJob({ progress: 5, message: "Mengunggah video…" });
    try {
      const form = new FormData();
      form.append("video", file);
      form.append("name", name || "my_style");
      form.append("analyze_speech", "true");
      const { job_id } = await apiPostForm("/api/template/extract", form);
      cancelRef.current = pollJob(job_id, {
        onUpdate: (j) => setJob({ progress: j.progress, message: j.message }),
        onDone: (result) => { setJob(null); setTemplate(result); },
        onError: (err) => { setJob(null); setError(err); },
      });
    } catch (e) {
      setJob(null);
      setError(String(e));
    }
  };

  const sprocket = {
    backgroundImage: `radial-gradient(circle, ${C.bg} 2.2px, transparent 2.6px)`,
    backgroundSize: "18px 100%", backgroundPosition: "9px center", backgroundRepeat: "repeat-x",
    height: 10, background: C.panelRaised,
  };

  let cumulative = [];
  if (template) {
    let acc = 0;
    template.shots.forEach((s) => { cumulative.push(acc); acc += s.duration; });
    cumulative.push(acc);
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Eyebrow n={1}>TEMPLATE STUDIO</Eyebrow>
        <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700 }}>Pelajari Ritme Video Referensi</h2>
        <p style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim, marginTop: 4, maxWidth: 520 }}>
          Upload video referensi — tiap potongan dipetakan jadi data pacing beneran (scene detection + Whisper), dipakai lagi buat nentuin ritme video baru.
        </p>
      </div>

      {!template && (
        <div className="flex flex-col gap-5">
          {savedTemplates.length > 0 && (
            <div>
              <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>PILIH TEMPLATE YANG SUDAH ADA</span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-2">
                {savedTemplates.map(t => (
                  <button 
                    key={t.template_name} 
                    onClick={() => setTemplate(t)}
                    className="flex items-center gap-2 px-3 py-3 rounded text-left"
                    style={{ background: C.panelRaised, border: `1px solid ${C.borderSoft}`, color: C.paper, cursor: "pointer" }}
                  >
                    <Film size={15} color={C.cyan} />
                    <span style={{ fontFamily: F.mono, fontSize: 12.5 }}>{t.template_name}</span>
                    <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperDim, marginLeft: "auto" }}>
                      {t.pacing?.shot_count || 0} shots
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div style={{ borderTop: `1px dashed ${C.borderSoft}`, paddingTop: 16 }}>
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", display: "block", marginBottom: 8 }}>
              {savedTemplates.length > 0 ? "ATAU BUAT TEMPLATE BARU" : "BUAT TEMPLATE BARU"}
            </span>
            <div className="flex flex-col gap-3">
              <label
                className="flex flex-col items-center justify-center gap-2 px-4 py-8 rounded cursor-pointer"
                style={{ background: C.panel, border: `1.5px dashed ${file ? C.cyan : C.borderSoft}` }}
              >
                <Upload size={20} color={file ? C.cyan : C.paperDim} />
                <span style={{ fontFamily: F.body, fontSize: 12.5, color: file ? C.paper : C.paperDim }}>
                  {file ? file.name : "Pilih file video referensi"}
                </span>
                <input type="file" accept="video/*" className="hidden" onChange={(e) => setFile(e.target.files[0] || null)} />
              </label>

              <div className="flex items-center gap-2">
                <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", flexShrink: 0 }}>NAMA TEMPLATE</span>
                <input
                  value={name} onChange={(e) => setName(e.target.value)}
                  className="flex-1 px-3 py-2 rounded"
                  style={{ fontFamily: F.mono, fontSize: 12.5, color: C.paper, background: C.panel, border: `1px solid ${C.borderSoft}`, outline: "none" }}
                />
              </div>

              {error && <ErrorBanner error={error} onRetry={runExtract} />}
              {job && <ProgressBar progress={job.progress} message={job.message} />}

              {!job && (
                <PrimaryButton onClick={runExtract} disabled={!file} icon={ScanSearch}>Ekstrak Template</PrimaryButton>
              )}
            </div>
          </div>
        </div>
      )}

      {template && (
        <>
          <div className="flex items-center justify-between px-4 py-3 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
            <div className="flex items-center gap-2">
              <Film size={15} color={C.paperDim} />
              <span style={{ fontFamily: F.mono, fontSize: 12.5, color: C.paper }}>{template.template_name}</span>
            </div>
            <button onClick={() => setTemplate(null)} style={{ fontFamily: F.body, fontSize: 12, color: C.cyan, background: "none", border: "none", cursor: "pointer" }}>
              Ganti video
            </button>
          </div>

          <div>
            <div style={sprocket} className="rounded-t" />
            <div className="flex" style={{ height: 88, gap: 2 }}>
              {template.shots.map((shot, i) => (
                <div
                  key={i} onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)}
                  className="flex items-center justify-center relative"
                  style={{ flexGrow: shot.duration, flexBasis: 0, background: hovered === i ? C.panelRaised : C.panel, border: `1px solid ${hovered === i ? C.cyan : C.borderSoft}`, cursor: "pointer", transition: "background 120ms ease" }}
                >
                  <span style={{ fontFamily: F.mono, fontSize: 12, color: hovered === i ? C.cyan : C.paperDim }}>{shot.duration.toFixed(1)}s</span>
                </div>
              ))}
            </div>
            <div style={sprocket} className="rounded-b" />
            <div className="flex justify-between mt-1.5 px-0.5">
              {cumulative.map((t, i) => (
                <span key={i} style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint }}>{fmtTime(t)}</span>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 px-4 py-4 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
            <StatCell label="AVG SHOT" value={template.pacing.avg_shot_duration.toFixed(1)} unit="s" />
            <StatCell label="MEDIAN" value={template.pacing.median_shot_duration.toFixed(1)} unit="s" />
            <StatCell label="MIN / MAX" value={`${template.pacing.min_shot_duration}–${template.pacing.max_shot_duration}`} unit="s" />
            <StatCell label="TOTAL SHOT" value={template.pacing.shot_count} />
            <StatCell label="NARASI WPM" value={template.narration ? template.narration.words_per_minute : "—"} />
          </div>

          <div className="flex justify-end">
            <PrimaryButton onClick={onNext} icon={ChevronRight}>Lanjut ke Skrip</PrimaryButton>
          </div>
        </>
      )}
    </div>
  );
}

/* ============================================================
   STAGE 2 — SCRIPT DESK
   ============================================================ */
function StageScript({ template, script, setScript, onNext }) {
  const [mode, setMode] = useState("ai");
  const [topic, setTopic] = useState("");
  const [customScript, setCustomScript] = useState("");
  const [numSegments, setNumSegments] = useState(6);
  const [styles, setStyles] = useState([]);
  const [styleId, setStyleId] = useState(null); // null = default flat style
  const [language, setLanguage] = useState("id");
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const cancelRef = useRef(null);

  const downloadScriptText = () => {
    if (!script) return;
    let text = `TOPIK: ${(topic || "Naskah Kustom").toUpperCase()}\n\n`;
    
    let currentAct = null;
    script.segments.forEach((seg, i) => {
      if (seg.act && seg.act !== currentAct) {
        currentAct = seg.act;
        const actLabel = ACT_LABELS[seg.act] || seg.act;
        text += `\nBABAK: ${actLabel}\n`;
      }
      text += `${seg.text}\n\n`;
    });
    
    const blob = new Blob([text], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `naskah_${(topic || "kustom").replace(/\s+/g, "_").toLowerCase()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  useEffect(() => () => cancelRef.current && cancelRef.current(), []);
  useEffect(() => { apiGet("/api/script/styles").then(setStyles).catch(() => setStyles([])); }, []);

  const runGenerate = async () => {
    if (!topic.trim() && !customScript.trim()) return;
    setError(null);
    setJob({ progress: 5, message: "Memulai…" });
    try {
      const { job_id } = await apiPostJSON("/api/script/generate", {
        template_name: template.template_name, topic, segments: Number(numSegments), style_id: styleId, language, custom_script: customScript || null,
      });
      cancelRef.current = pollJob(job_id, {
        onUpdate: (j) => setJob({ progress: j.progress, message: j.message }),
        onDone: (result) => { setJob(null); setScript(result); },
        onError: (err) => { setJob(null); setError(err); },
      });
    } catch (e) {
      setJob(null);
      setError(String(e));
    }
  };

  const updateSegmentText = (i, text) => {
    const next = { ...script, segments: script.segments.map((s, idx) => idx === i ? { ...s, text } : s) };
    setScript(next);
  };

  const removeKeyword = (segIdx, kwIdx) => {
    const next = {
      ...script,
      segments: script.segments.map((s, idx) => idx === segIdx
        ? { ...s, keywords: s.keywords.filter((_, ki) => ki !== kwIdx) }
        : s),
    };
    setScript(next);
  };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Eyebrow n={2}>SCRIPT DESK</Eyebrow>
        <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700 }}>Riset & Tulis Naskah</h2>
      </div>

      {!script && (
        <div className="flex flex-col gap-3">
          <div className="flex gap-2 mb-2">
            <button
              onClick={() => { setMode("ai"); setCustomScript(""); }}
              className="px-4 py-2.5 rounded font-semibold text-sm cursor-pointer"
              style={{ background: mode === "ai" ? C.tally : C.panel, color: mode === "ai" ? C.paper : C.paperDim, border: `1px solid ${mode === "ai" ? C.tally : C.borderSoft}` }}
            >
              AI Generate (Otomatis)
            </button>
            <button
              onClick={() => { setMode("custom"); }}
              className="px-4 py-2.5 rounded font-semibold text-sm cursor-pointer"
              style={{ background: mode === "custom" ? C.tally : C.panel, color: mode === "custom" ? C.paper : C.paperDim, border: `1px solid ${mode === "custom" ? C.tally : C.borderSoft}` }}
            >
              Naskah Buatan Sendiri (Custom)
            </button>
          </div>

          <div>
            <label style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>
              {mode === "ai" ? "TOPIK" : "JUDUL PROYEK / VIDEO"}
            </label>
            <input
              value={topic} onChange={(e) => setTopic(e.target.value)}
              placeholder={mode === "ai" ? "mis. Energi Terbarukan di Indonesia" : "mis. Investigasi Mangkok Merah"}
              className="w-full mt-1.5 px-4 py-3 rounded"
              style={{ fontFamily: F.body, fontSize: 14.5, color: C.paper, background: C.panel, border: `1px solid ${C.borderSoft}`, outline: "none" }}
            />
          </div>
          
          {mode === "custom" && (
            <div>
              <label style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>TEKS NASKAH</label>
              <textarea
                value={customScript} onChange={(e) => setCustomScript(e.target.value)} placeholder="Paste teks naskah lo di sini..."
                rows={6}
                className="w-full mt-1.5 px-4 py-3 rounded resize-y"
                style={{ fontFamily: F.body, fontSize: 13.5, color: C.paper, background: C.panel, border: `1px solid ${C.borderSoft}`, outline: "none", minHeight: "120px" }}
              />
            </div>
          )}

          <div className="flex items-center gap-2 mt-1">
            {mode === "ai" && (
              <>
                <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>JUMLAH SEGMEN</span>
                <div className="flex items-center gap-2">
                  <input
                    type="number" min={3} max={300} value={numSegments} onChange={(e) => setNumSegments(e.target.value)}
                    className="px-3 py-1.5 rounded" style={{ width: 64, fontFamily: F.mono, fontSize: 12.5, color: C.paper, background: C.panel, border: `1px solid ${C.borderSoft}`, outline: "none" }}
                  />
                  <span style={{ fontFamily: F.body, fontSize: 11, color: C.paperDim, fontStyle: "italic" }}>
                    (1 segmen = 100 kata)
                  </span>
                </div>
              </>
            )}
            
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", marginLeft: mode === "ai" ? 16 : 0 }}>BAHASA</span>
            <select
              value={language} onChange={(e) => setLanguage(e.target.value)}
              className="px-3 py-1.5 rounded" style={{ fontFamily: F.mono, fontSize: 12.5, color: C.paper, background: C.panel, border: `1px solid ${C.borderSoft}`, outline: "none" }}
            >
              <option value="id">Indonesia</option>
              <option value="en-US">English</option>
            </select>
          </div>

          {mode === "ai" && styles.length > 0 && (
            <div className="mt-1">
              <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>GAYA NASKAH</span>
              <div className="flex flex-wrap gap-2 mt-1.5">
                <button onClick={() => setStyleId(null)} className="px-3.5 py-2 rounded text-left"
                  style={{ fontFamily: F.body, fontSize: 12, cursor: "pointer", background: styleId === null ? C.tally : C.panel, color: styleId === null ? C.paper : C.paperDim, border: `1px solid ${styleId === null ? C.tally : C.borderSoft}` }}>
                  Default
                </button>
                {styles.map((s) => (
                  <button key={s.style_id} onClick={() => setStyleId(s.style_id)} className="px-3.5 py-2 rounded text-left"
                    style={{ maxWidth: 220, fontFamily: F.body, fontSize: 12, cursor: "pointer", background: styleId === s.style_id ? C.tally : C.panel, color: styleId === s.style_id ? C.paper : C.paperDim, border: `1px solid ${styleId === s.style_id ? C.tally : C.borderSoft}` }}>
                    {s.display_name}
                  </button>
                ))}
              </div>
              {styleId && (
                <p style={{ fontFamily: F.body, fontSize: 11.5, color: C.paperFaint, marginTop: 6, lineHeight: 1.5 }}>
                  {styles.find((s) => s.style_id === styleId)?.description}
                </p>
              )}
            </div>
          )}

          {error && <ErrorBanner error={error} onRetry={runGenerate} />}
          {job && <ProgressBar progress={job.progress} message={job.message} />}
          {!job && (
            <PrimaryButton 
              onClick={runGenerate} 
              disabled={mode === "ai" ? !topic.trim() : (!topic.trim() || !customScript.trim())} 
              icon={FileText}
            >
              Generate Skrip
            </PrimaryButton>
          )}
        </div>
      )}

      {script && (
        <>
          <div className="flex flex-col lg:flex-row gap-5">
            <div className="lg:w-64 flex-shrink-0">
              <div style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", marginBottom: 8 }}>
                SUMBER RISET · {script.sources?.length || 0}
              </div>
              <div className="flex flex-col gap-2">
                {(script.sources || []).map((s, i) => (
                  <div key={i} className="px-3 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
                    <div style={{ fontFamily: F.body, fontSize: 12.5, color: C.paper, marginBottom: 3 }}>{s.title}</div>
                    <div style={{ fontFamily: F.mono, fontSize: 10.5, color: C.cyan }}>{s.url ? new URL(s.url).hostname : s.domain}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex-1 flex flex-col gap-3">
              {script.segments.map((seg, i) => {
                const prevAct = i > 0 ? script.segments[i - 1].act : null;
                const showActHeader = seg.act && seg.act !== prevAct;
                const actMeta = showActHeader ? ACT_LABELS[seg.act] : null;
                return (
                  <React.Fragment key={i}>
                    {showActHeader && (
                      <div className="flex items-center gap-2 mt-2 first:mt-0">
                        <span style={{ fontFamily: F.mono, fontSize: 10.5, color: C.amber, letterSpacing: "0.1em", fontWeight: 600 }}>
                          BABAK · {actMeta || seg.act}
                        </span>
                        <div className="flex-1" style={{ height: 1, background: C.borderSoft }} />
                      </div>
                    )}
                    <div className="px-4 py-3.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
                      <div className="flex items-start justify-between gap-3 mb-2">
                        <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperFaint }}>SEGMEN {String(i + 1).padStart(2, "0")}</span>
                      </div>
                      <textarea
                        value={seg.text} onChange={(e) => updateSegmentText(i, e.target.value)} rows={2}
                        className="w-full resize-none"
                        style={{ fontFamily: F.body, fontSize: 13.5, color: C.paper, lineHeight: 1.5, marginBottom: 10, background: "transparent", border: "none", outline: "none" }}
                      />
                      <div className="flex flex-wrap gap-1.5">
                        {seg.keywords.map((k, ki) => (
                          <span key={ki} className="flex items-center gap-1 px-2 py-1 rounded" style={{ fontFamily: F.mono, fontSize: 10.5, color: C.paperDim, background: C.panelRaised, border: `1px solid ${C.border}` }}>
                            {k}
                            <X size={10} style={{ cursor: "pointer" }} onClick={() => removeKeyword(i, ki)} />
                          </span>
                        ))}
                      </div>
                    </div>
                  </React.Fragment>
                );
              })}
            </div>
          </div>

          <div className="flex justify-between items-center mt-5">
            <div className="flex gap-4">
              <button onClick={() => setScript(null)} className="flex items-center gap-1.5" style={{ fontFamily: F.body, fontSize: 12.5, color: C.paperDim, background: "none", border: "none", cursor: "pointer" }}>
                <RefreshCw size={13} /> Generate ulang
              </button>
              <button onClick={downloadScriptText} className="flex items-center gap-1.5" style={{ fontFamily: F.body, fontSize: 12.5, color: C.cyan, background: "none", border: "none", cursor: "pointer" }}>
                <Download size={13} /> Download Teks (.txt)
              </button>
            </div>
            <PrimaryButton onClick={onNext} icon={ChevronRight}>Lanjut ke Narasi</PrimaryButton>
          </div>
        </>
      )}
    </div>
  );
}

/* ============================================================
   STAGE 3 — NARRATION BOOTH
   ============================================================ */
async function extractWaveform(audioUrl, barCount = 72) {
  try {
    const resp = await fetch(audioUrl);
    const buf = await resp.arrayBuffer();
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    const audioBuffer = await ctx.decodeAudioData(buf);
    const raw = audioBuffer.getChannelData(0);
    const blockSize = Math.max(1, Math.floor(raw.length / barCount));
    const bars = [];
    for (let i = 0; i < barCount; i++) {
      let sum = 0;
      const start = i * blockSize;
      for (let j = 0; j < blockSize && start + j < raw.length; j++) sum += Math.abs(raw[start + j]);
      bars.push(sum / blockSize);
    }
    const max = Math.max(...bars, 0.0001);
    ctx.close();
    return bars.map((b) => 6 + (b / max) * 58);
  } catch (e) {
    return Array.from({ length: barCount }, () => 20); // flat fallback if decode fails
  }
}

function StageNarration({ script, narration, setNarration, onNext }) {
  const [ttsProvider, setTtsProvider] = useState("pyttsx3");
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [waveform, setWaveform] = useState(null);
  const [audioFile, setAudioFile] = useState(null);
  const cancelRef = useRef(null);

  useEffect(() => () => cancelRef.current && cancelRef.current(), []);

  useEffect(() => {
    if (narration?.audio_url) {
      extractWaveform(narration.audio_url).then(setWaveform);
    }
  }, [narration]);

  const runGenerate = async () => {
    setError(null);
    setJob({ progress: 5, message: "Memulai…" });
    try {
      let job_id;
      if (ttsProvider === "upload") {
        if (!audioFile) {
          throw new Error("Silakan pilih file audio terlebih dahulu.");
        }
        const fd = new FormData();
        fd.append("audio", audioFile);
        fd.append("segments", JSON.stringify(script.segments));
        
        const res = await fetch("/api/narration/upload", {
          method: "POST",
          body: fd,
        });
        if (!res.ok) {
          const t = await res.text();
          throw new Error(t);
        }
        const data = await res.json();
        job_id = data.job_id;
      } else {
        const data = await apiPostJSON("/api/narration/generate", {
          segments: script.segments, tts_provider: ttsProvider,
        });
        job_id = data.job_id;
      }
      
      cancelRef.current = pollJob(job_id, {
        onUpdate: (j) => setJob({ progress: j.progress, message: j.message }),
        onDone: (result) => { setJob(null); setNarration(result); },
        onError: (err) => { setJob(null); setError(err); },
      });
    } catch (e) {
      setJob(null);
      setError(String(e));
    }
  };

  let bounds = [];
  let total = 0;
  if (narration) {
    narration.segments.forEach((s) => { bounds.push(s.start); });
    total = narration.segments.length ? narration.segments[narration.segments.length - 1].end : 0;
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Eyebrow n={3}>NARRATION BOOTH</Eyebrow>
        <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700 }}>Generate Suara & Timing</h2>
      </div>

      {!narration && (
        <div className="flex flex-col gap-3">
          <div className="flex gap-2">
            {[{ id: "pyttsx3", label: "pyttsx3 · gratis" }, { id: "f5tts", label: "F5-TTS · local" }, { id: "omnivoice", label: "OmniVoice · local" }, { id: "elevenlabs", label: "ElevenLabs · premium" }, { id: "upload", label: "Upload Sendiri" }].map((opt) => (
              <button key={opt.id} onClick={() => setTtsProvider(opt.id)} className="px-4 py-2 rounded"
                style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600, cursor: "pointer", background: ttsProvider === opt.id ? C.tally : C.panel, color: ttsProvider === opt.id ? C.paper : C.paperDim, border: `1px solid ${ttsProvider === opt.id ? C.tally : C.borderSoft}` }}>
                {opt.label}
              </button>
            ))}
          </div>
          {ttsProvider === "upload" && (
            <div className="flex flex-col gap-2 p-4 rounded" style={{ background: "rgba(255,255,255,0.02)", border: `1px dashed ${C.borderSoft}` }}>
              <label style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim }}>Pilih file rekaman suara Anda (.wav, .mp3, .m4a)</label>
              <input type="file" accept="audio/*" onChange={(e) => setAudioFile(e.target.files[0])} style={{ color: C.paper }} />
            </div>
          )}
          {error && <ErrorBanner error={error} onRetry={runGenerate} />}
          {job && <ProgressBar progress={job.progress} message={job.message} />}
          {!job && <PrimaryButton onClick={runGenerate} icon={Mic}>{ttsProvider === "upload" ? "Selaraskan Audio" : "Generate Suara"}</PrimaryButton>}
        </div>
      )}

      {narration && (
        <>
          <audio controls src={narration.audio_url} style={{ width: "100%", height: 34 }} />

          <div className="px-4 py-5 rounded relative" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
            <div className="flex items-end gap-[2px]" style={{ height: 64 }}>
              {(waveform || Array.from({ length: 72 }, () => 20)).map((h, i) => (
                <div key={i} style={{ width: 3, height: `${h}px`, background: C.cyan, opacity: 0.55, borderRadius: 1 }} />
              ))}
            </div>
            <div className="relative mt-2" style={{ height: 16 }}>
              {bounds.map((b, i) => (
                <span key={i} className="absolute" style={{ left: `${total ? (b / total) * 100 : 0}%`, fontFamily: F.mono, fontSize: 9.5, color: C.paperFaint, transform: "translateX(-2px)" }}>
                  {fmtTime(b)}
                </span>
              ))}
            </div>
          </div>

          <div className="flex flex-col gap-2">
            {narration.segments.map((seg, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
                <span style={{ fontFamily: F.mono, fontSize: 11, color: C.cyan, width: 96, flexShrink: 0 }}>{fmtTime(seg.start)}–{fmtTime(seg.end)}</span>
                <span style={{ fontFamily: F.body, fontSize: 12.5, color: C.paperDim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0, flex: 1 }}>{seg.text}</span>
              </div>
            ))}
          </div>

          <div className="flex items-center justify-between mt-4">
            <button onClick={() => setNarration(null)} className="flex items-center gap-1.5 px-3 py-1.5 rounded hover:bg-white/5 transition-colors" style={{ fontFamily: F.body, fontSize: 12, color: C.paperDim, border: `1px solid ${C.borderSoft}` }}>
              <RefreshCw size={14} /> Generate ulang
            </button>
            <PrimaryButton onClick={onNext} icon={ChevronRight}>Lanjut ke Footage</PrimaryButton>
          </div>
        </>
      )}
    </div>
  );
}

/* ============================================================
   STAGE 4 — FOOTAGE MATCHING BOARD
   ============================================================ */
function StageFootage({ narration, footageData, setFootageData, picks, setPicks, onNext }) {
  const [activeSeg, setActiveSeg] = useState(0);
  const [exportJob, setExportJob] = useState(null);

  const downloadProject = async () => {
    try {
      setExportJob({ progress: 10, message: 'Mempersiapkan project...' });
      
      const footageMap = {};
      Object.entries(picks).forEach(([segIdx, candIdx]) => {
        const cand = footageData[segIdx]?.candidates?.[candIdx];
        if (cand) footageMap[segIdx] = cand;
      });

      const res = await fetch('/api/export/project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          timed_segments: narration.segments,
          footage_map: footageMap,
          narration_audio_path: narration.audio_path || '',
          output_name: 'ritme_project',
          formats: ['edl', 'fcpxml', 'premiere_xml', 'capcut_json']
        })
      });

      if (!res.ok) throw new Error('Export gagal');

      setExportJob({ progress: 90, message: 'Mengunduh...' });
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'ritme_project.zip';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setExportJob(null);
    } catch (e) {
      alert('Export gagal: ' + e.message);
      setExportJob(null);
    }
  };
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const cancelRef = useRef(null);

  useEffect(() => () => cancelRef.current && cancelRef.current(), []);

  const runMatch = async () => {
    setError(null);
    setJob({ progress: 2, message: "Memulai…" });
    try {
      const { job_id } = await apiPostJSON("/api/footage/match", { segments: narration.segments });
      cancelRef.current = pollJob(job_id, {
        onUpdate: (j) => setJob({ progress: j.progress, message: j.message }),
        onDone: (result) => {
          setJob(null);
          setFootageData(result);
          const defaultPicks = {};
          Object.entries(result).forEach(([idx, data]) => { defaultPicks[idx] = data.best_index; });
          setPicks(defaultPicks);
        },
        onError: (err) => { setJob(null); setError(err); },
      });
    } catch (e) {
      setJob(null);
      setError(String(e));
    }
  };

  const segData = footageData ? footageData[String(activeSeg)] : null;
  const candidates = segData?.candidates || [];

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Eyebrow n={4}>FOOTAGE MATCHING BOARD</Eyebrow>
        <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700 }}>Cocokkan Footage per Segmen</h2>
      </div>

      <div className="flex items-start gap-2 px-3.5 py-2.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
        <Info size={14} color={C.amber} style={{ marginTop: 1, flexShrink: 0 }} />
        <span style={{ fontFamily: F.body, fontSize: 11.5, color: C.paperDim, lineHeight: 1.5 }}>
          Sumber legal only — Pexels, Pixabay, Wikimedia Commons, Archive.org, dan YouTube berlisensi Creative Commons.
        </span>
      </div>

      {!footageData && (
        <div className="flex flex-col gap-3">
          {error && <ErrorBanner error={error} onRetry={runMatch} />}
          {job && <ProgressBar progress={job.progress} message={job.message} />}
          {!job && <PrimaryButton onClick={runMatch} icon={ScanSearch}>Cari Footage</PrimaryButton>}
        </div>
      )}

      {footageData && (
        <>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {narration.segments.map((s, i) => (
              <button key={i} onClick={() => setActiveSeg(i)} className="px-3.5 py-1.5 rounded flex-shrink-0"
                style={{ fontFamily: F.mono, fontSize: 11.5, cursor: "pointer", background: activeSeg === i ? C.tally : C.panel, color: activeSeg === i ? C.paper : C.paperDim, border: `1px solid ${activeSeg === i ? C.tally : C.borderSoft}` }}>
                SEG {String(i + 1).padStart(2, "0")}
              </button>
            ))}
          </div>

          {candidates.length === 0 ? (
            <div className="px-4 py-6 rounded text-center" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
              <span style={{ fontFamily: F.body, fontSize: 12.5, color: C.paperDim }}>Nggak ada footage yang ketemu buat segmen ini — coba ubah keyword di Stage 2.</span>
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {candidates.map((c, ci) => {
                const isPicked = picks[activeSeg] === ci;
                return (
                  <button key={ci} onClick={() => setPicks({ ...picks, [activeSeg]: ci })} className="flex flex-col rounded overflow-hidden text-left"
                    style={{ background: C.panel, cursor: "pointer", border: `1.5px solid ${isPicked ? C.tally : C.borderSoft}` }}>
                    <div className="flex items-center justify-center relative" style={{ height: 80, background: C.panelRaised, backgroundImage: c.thumbnail_url ? `url(${c.thumbnail_url})` : "none", backgroundSize: "cover", backgroundPosition: "center" }}>
                      {!c.thumbnail_url && <Film size={20} color={C.paperFaint} />}
                      {ci === 0 && (
                        <span className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded" style={{ fontFamily: F.mono, fontSize: 8.5, background: C.tally, color: C.paper }}>MATCH TERBAIK</span>
                      )}
                      {isPicked && (
                        <div className="absolute top-1.5 right-1.5 rounded-full flex items-center justify-center" style={{ width: 18, height: 18, background: C.cyan }}>
                          <Check size={11} color={C.bg} strokeWidth={3} />
                        </div>
                      )}
                    </div>
                    <div className="px-2.5 py-2 flex flex-col gap-1.5">
                      <span style={{ fontFamily: F.mono, fontSize: 9.5, color: SOURCE_STYLES[c.source] || C.paperDim, letterSpacing: "0.05em" }}>{c.source}</span>
                      <div className="flex items-center gap-1.5">
                        <div className="flex-1 rounded-full overflow-hidden" style={{ height: 3, background: C.border }}>
                          <div style={{ width: `${c.score * 100}%`, height: "100%", background: C.cyan }} />
                        </div>
                        <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperDim }}>{Math.round(c.score * 100)}%</span>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {exportJob && <ProgressBar progress={exportJob.progress} message={exportJob.message} />}
          <div className="flex justify-end gap-3">
            {!exportJob && (
              <PrimaryButton onClick={downloadProject} icon={Download}>Download Project (Edit)</PrimaryButton>
            )}
            <PrimaryButton onClick={onNext} icon={ChevronRight}>Lanjut ke Render</PrimaryButton>
          </div>
        </>
      )}
    </div>
  );
}

/* ============================================================
   STAGE 5 — ASSEMBLY & RENDER
   ============================================================ */
function StageRender({ template, narration, footageData, picks }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const cancelRef = useRef(null);

  useEffect(() => () => cancelRef.current && cancelRef.current(), []);

  const avgShot = template.pacing.avg_shot_duration;
  const subcuts = [];
  narration.segments.forEach((seg, idx) => {
    const nCuts = Math.max(1, Math.round(seg.duration / avgShot));
    const dur = seg.duration / nCuts;
    for (let i = 0; i < nCuts; i++) subcuts.push({ segIdx: idx, duration: dur });
  });
  const totalDur = subcuts.reduce((a, c) => a + c.duration, 0);

  const startRender = async () => {
    setError(null);
    setJob({ progress: 2, message: "Memulai…" });
    try {
      const footageMap = {};
      Object.entries(picks).forEach(([segIdx, candIdx]) => {
        const cand = footageData[segIdx]?.candidates?.[candIdx];
        if (cand) footageMap[segIdx] = cand;
      });
      const { job_id } = await apiPostJSON("/api/render", {
        template_name: template.template_name,
        timed_segments: narration.segments,
        footage_map: footageMap,
        narration_audio_path: narration.audio_path,
        output_name: `ritme_${Date.now()}`,
      });
      cancelRef.current = pollJob(job_id, {
        onUpdate: (j) => setJob({ progress: j.progress, message: j.message }),
        onDone: (r) => { setJob(null); setResult(r); },
        onError: (err) => { setJob(null); setError(err); },
      });
    } catch (e) {
      setJob(null);
      setError(String(e));
    }
  };

  const firstThumb = footageData?.[Object.keys(footageData)[0]]?.candidates?.[0]?.thumbnail_url;
  const previewText = narration.segments[0]?.text || "";

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Eyebrow n={5}>ASSEMBLY & RENDER</Eyebrow>
        <h2 style={{ fontFamily: F.display, fontSize: 22, color: C.paper, fontWeight: 700 }}>Susun Timeline & Render</h2>
      </div>

      <div className="flex flex-col lg:flex-row gap-5">
        <div className="flex-1 flex flex-col gap-3">
          <div style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>
            TIMELINE · {subcuts.length} SUB-CUT · {totalDur.toFixed(1)}s
          </div>
          <div className="flex" style={{ height: 46, gap: 1 }}>
            {subcuts.map((c, i) => (
              <div key={i} title={`${c.duration.toFixed(1)}s`} style={{ flexGrow: c.duration, flexBasis: 0, background: c.segIdx % 2 === 0 ? C.panelRaised : C.panel, border: `1px solid ${C.borderSoft}`, borderTopColor: C.cyan, borderTopWidth: 2 }} />
            ))}
          </div>

          <div className="mt-2" style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>OUTPUT</div>

          {error && <ErrorBanner error={error} onRetry={startRender} />}

          {!result && (
            <div className="flex items-center gap-3 px-4 py-3 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}` }}>
              {!job ? (
                <PrimaryButton onClick={startRender} icon={Play}>Render Video Final</PrimaryButton>
              ) : (
                <div className="flex-1 flex items-center gap-2">
                  <Loader2 size={14} color={C.amber} className="animate-spin" />
                  <span style={{ fontFamily: F.mono, fontSize: 11, color: C.paperDim, flexShrink: 0 }}>{job.message}</span>
                  <div className="flex-1 rounded-full overflow-hidden" style={{ height: 5, background: C.border }}>
                    <div style={{ width: `${job.progress}%`, height: "100%", background: C.tally, transition: "width 200ms linear" }} />
                  </div>
                  <span style={{ fontFamily: F.mono, fontSize: 11, color: C.amber, width: 34 }}>{Math.round(job.progress)}%</span>
                </div>
              )}
            </div>
          )}

          {result && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between px-4 py-3 rounded" style={{ background: C.panel, border: `1px solid ${C.cyan}` }}>
                <div className="flex items-center gap-2.5">
                  <div className="rounded-full flex items-center justify-center" style={{ width: 22, height: 22, background: C.cyan }}>
                    <Check size={13} color={C.bg} strokeWidth={3} />
                  </div>
                  <span style={{ fontFamily: F.mono, fontSize: 12, color: C.paper }}>{result.output_path.split("/").pop()}</span>
                </div>
                <a href={result.output_url} download className="flex items-center gap-1.5" style={{ fontFamily: F.body, fontSize: 12, fontWeight: 600, color: C.bg, background: C.cyan, border: "none", padding: "6px 12px", borderRadius: 4, cursor: "pointer", textDecoration: "none" }}>
                  <Download size={13} /> Unduh
                </a>
              </div>
              <video controls src={result.output_url} style={{ width: "100%", maxHeight: 400, background: "#000", borderRadius: 4 }} />
            </div>
          )}
        </div>

        {!result && (
          <div className="flex-shrink-0 flex flex-col items-center gap-2">
            <div style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em", alignSelf: "flex-start" }}>PREVIEW</div>
            <div className="relative flex items-end justify-center overflow-hidden" style={{ width: 160, height: 284, background: firstThumb ? `#222 url(${firstThumb}) center/cover` : "#8B2E1A", border: `1px solid ${C.border}`, borderRadius: 2 }}>
              <p className="text-center px-3 pb-4" style={{ fontFamily: F.body, fontWeight: 700, fontSize: 13, color: "#fff", textShadow: "0 0 3px #000, 0 0 3px #000, 1px 1px 0 #000, -1px -1px 0 #000" }}>
                {previewText}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ============================================================
   FOOTAGE EXTRACTOR TOOL (Standalone)
   ============================================================ */
function FootageExtractorTool({ onClose }) {
  const [file, setFile] = useState(null);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [topic, setTopic] = useState("");
  const [inputType, setInputType] = useState("file");
  
  const [jobId, setJobId] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  
  const handleFile = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const startExtract = async () => {
    if (inputType === "file" && !file) return;
    if (inputType === "youtube" && !youtubeUrl) return;
    setError(null);
    setJobId(null);
    setJob(null);
    setResult(null);
    
    try {
      let res;
      if (inputType === "file") {
        const fd = new FormData();
        fd.append("video", file);
        if (topic) fd.append("topic", topic);
        res = await apiPostForm("/api/footage/extract", fd);
      } else {
        res = await apiPostJSON("/api/footage/extract_youtube", { youtube_url: youtubeUrl, topic: topic });
      }
      setJobId(res.job_id);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    if (!jobId) return;
    return pollJob(jobId, {
      onUpdate: setJob,
      onDone: (res) => { setResult(res); setJobId(null); },
      onError: (err) => { setError(err); setJobId(null); }
    });
  }, [jobId]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: "rgba(0,0,0,0.5)" }}>
      {/* Sidebar Panel */}
      <div className="w-full max-w-md h-full flex flex-col shadow-2xl" style={{ background: C.bg, borderLeft: `1px solid ${C.border}`, animation: "slideInRight 0.3s ease-out forwards" }}>
        <style>{`@keyframes slideInRight { from { transform: translateX(100%); } to { transform: translateX(0); } }`}</style>
        
        <div className="flex items-center justify-between px-5 py-4" style={{ background: C.panel, borderBottom: `1px solid ${C.border}` }}>
          <div className="flex items-center gap-3">
            <Scissors size={18} color={C.tally} />
            <span style={{ fontFamily: F.display, fontSize: 16, fontWeight: 700, color: C.paper }}>Ekstrak Footage</span>
          </div>
          <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: C.paperDim }}><X size={20} /></button>
        </div>
        
        <div className="p-5 flex flex-col gap-5 overflow-y-auto flex-1">
          <p style={{ fontFamily: F.body, fontSize: 13, color: C.paperDim, lineHeight: 1.5 }}>
            Potong video panjang secara sekuensial. Hasil potongan akan bisa dicari otomatis oleh Tahap 4 berdasarkan "Kata Kunci Footage".
          </p>

          <div className="flex gap-2 p-1 rounded" style={{ background: C.panel }}>
            <button onClick={() => setInputType("file")} className="flex-1 py-1.5 rounded" style={{ border: "none", cursor: "pointer", background: inputType === "file" ? C.borderSoft : "transparent", color: inputType === "file" ? C.paper : C.paperDim, fontSize: 12, fontWeight: 600 }}>File Lokal</button>
            <button onClick={() => setInputType("youtube")} className="flex-1 py-1.5 rounded" style={{ border: "none", cursor: "pointer", background: inputType === "youtube" ? C.borderSoft : "transparent", color: inputType === "youtube" ? C.paper : C.paperDim, fontSize: 12, fontWeight: 600 }}>Link YouTube</button>
          </div>
          
          <div className="flex flex-col gap-1.5">
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>VIDEO SUMBER</span>
            {inputType === "file" ? (
              <div className="flex items-center gap-3 relative">
                <input type="file" accept="video/mp4,video/webm" onChange={handleFile} className="absolute inset-0 opacity-0 cursor-pointer" />
                <div className="flex-1 px-3 py-2 rounded flex items-center justify-between" style={{ background: C.panel, border: `1px dashed ${C.borderSoft}` }}>
                  <span style={{ fontFamily: F.mono, fontSize: 11, color: file ? C.cyan : C.paperDim, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {file ? file.name : "Pilih file video (.mp4)..."}
                  </span>
                  <Upload size={14} color={file ? C.cyan : C.paperDim} />
                </div>
              </div>
            ) : (
              <input type="text" placeholder="https://www.youtube.com/watch?v=..." value={youtubeUrl} onChange={(e) => setYoutubeUrl(e.target.value)} className="w-full px-3 py-2 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}`, color: C.paper, fontSize: 13 }} />
            )}
          </div>
          
          <div className="flex flex-col gap-1.5">
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>TOPIK VIDEO (Opsional)</span>
            <input type="text" placeholder="Misal: Ekonomi IKN" value={topic} onChange={(e) => setTopic(e.target.value)} className="w-full px-3 py-2 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}`, color: C.paper, fontSize: 13 }} />
          </div>
          
          {error && <ErrorBanner error={error} />}
          
          {jobId && job && (
             <ProgressBar progress={job.progress} message={job.message} />
          )}
          
          {result && (
             <div className="p-3 rounded" style={{ background: C.panelRaised, border: `1px solid ${C.cyan}` }}>
               <span style={{ fontFamily: F.mono, fontSize: 11, color: C.cyan, lineHeight: 1.6 }}>
                 Selesai! <strong>{result.count} klip</strong> diekstrak ke folder:<br />
                 <span style={{ color: C.paper }}>{result.output_dir}</span>
               </span>
             </div>
          )}
        </div>
        
        <div className="p-5 flex justify-end gap-3" style={{ borderTop: `1px solid ${C.border}`, background: C.panel }}>
           <button onClick={onClose} style={{ background: "transparent", color: C.paper, border: "none", fontFamily: F.body, fontWeight: 600, fontSize: 13, cursor: "pointer" }}>Tutup</button>
           <PrimaryButton onClick={startExtract} disabled={(inputType === "file" && !file) || (inputType === "youtube" && !youtubeUrl) || jobId} loading={!!jobId} icon={Scissors}>Mulai Ekstrak</PrimaryButton>
        </div>
      </div>
    </div>
  );
}

/* ============================================================
   ROOT
   ============================================================ */
export default function Ritme() {
  const [active, setActive] = useStickyState(1, "ritme_active");
  const [maxUnlocked, setMaxUnlocked] = useStickyState(1, "ritme_maxUnlocked");
  const [showExtractor, setShowExtractor] = useState(false);

  const [template, setTemplate] = useStickyState(null, "ritme_template");
  const [script, setScript] = useStickyState(null, "ritme_script");
  const [narration, setNarration] = useStickyState(null, "ritme_narration");
  const [footageData, setFootageData] = useStickyState(null, "ritme_footageData");
  const [picks, setPicks] = useStickyState({}, "ritme_picks");

  const handleReset = () => {
    if (window.confirm("Reset seluruh progress dan mulai project baru?")) {
      setActive(1);
      setMaxUnlocked(1);
      setTemplate(null);
      setScript(null);
      setNarration(null);
      setFootageData(null);
      setPicks({});
      window.localStorage.clear();
    }
  };

  const goNext = () => {
    const next = Math.min(active + 1, 5);
    setActive(next);
    setMaxUnlocked((m) => Math.max(m, next));
  };

  return (
    <div style={{ background: C.bg, minHeight: "100vh", fontFamily: F.body }}>
      <style>{FONT_IMPORT}</style>
      <style>{`.animate-spin { animation: ritme-spin 1s linear infinite; } @keyframes ritme-spin { to { transform: rotate(360deg); } }`}</style>

      <div className="flex items-center justify-between px-4 sm:px-6 py-4" style={{ borderBottom: `1px solid ${C.border}` }}>
        <div className="flex items-baseline gap-2.5">
          <span style={{ fontFamily: F.display, fontSize: 19, fontWeight: 800, color: C.paper, letterSpacing: "0.02em" }}>RITME</span>
          <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.08em" }}>AUTO-EDIT PIPELINE</span>
        </div>
        <div className="flex items-center gap-4">
          <button onClick={handleReset} className="flex items-center gap-1.5 px-3 py-1.5 rounded" style={{ background: "transparent", border: "none", color: C.red, cursor: "pointer", opacity: 0.8 }} onMouseEnter={e => e.currentTarget.style.opacity = 1} onMouseLeave={e => e.currentTarget.style.opacity = 0.8}>
            <Trash2 size={12} />
            <span style={{ fontFamily: F.mono, fontSize: 10 }}>Reset Project</span>
          </button>
          <button onClick={() => setShowExtractor(true)} className="flex items-center gap-1.5 px-3 py-1.5 rounded" style={{ background: C.panel, border: `1px solid ${C.borderSoft}`, color: C.paperDim, cursor: "pointer" }}>
            <Scissors size={12} />
            <span style={{ fontFamily: F.mono, fontSize: 10 }}>Footage Extractor</span>
          </button>
          <div className="flex items-center gap-1.5">
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.tally }} />
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperDim }}>REC</span>
          </div>
        </div>
      </div>
      
      {showExtractor && <FootageExtractorTool onClose={() => setShowExtractor(false)} />}

      <StageNav active={active} setActive={setActive} maxUnlocked={maxUnlocked} />

      <div className="px-4 sm:px-6 py-6 max-w-3xl mx-auto">
        {active === 1 && <StageTemplate template={template} setTemplate={setTemplate} onNext={goNext} />}
        {active === 2 && template && <StageScript template={template} script={script} setScript={setScript} onNext={goNext} />}
        {active === 3 && script && <StageNarration script={script} narration={narration} setNarration={setNarration} onNext={goNext} />}
        {active === 4 && narration && <StageFootage narration={narration} footageData={footageData} setFootageData={setFootageData} picks={picks} setPicks={setPicks} onNext={goNext} />}
        {active === 5 && narration && footageData && <StageRender template={template} narration={narration} footageData={footageData} picks={picks} />}
      </div>

      <div className="px-4 sm:px-6 py-4 text-center" style={{ borderTop: `1px solid ${C.border}` }}>
        <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.04em" }}>
          Footage legal only — tidak ada ripped content, tidak ada slicing buat ngakalin Content ID.
        </span>
      </div>
    </div>
  );
}

