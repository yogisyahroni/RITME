import React, { useState, useEffect, useCallback } from "react";
import { FolderOpen, Plus, Trash2, Copy, Loader2, Play, Clock, Film, Type, RefreshCw, AlertTriangle, LayoutGrid } from "lucide-react";

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

const AUDIO_KEY = "ritme_timeline_project_v1";

function fmtDur(sec) {
  if (!sec || sec <= 0) return "—";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
  if (m > 0) return `${m}:${String(ss).padStart(2, "0")}`;
  return `${ss}s`;
}

function fmtDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString("id-ID", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

export default function ProjectsPage({ onClose }) {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null);      // id project yang lagi diproses
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/projects");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setProjects(data.projects || []);
    } catch (e) { setError(String(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const openProject = async (p) => {
    setBusy(p.id);
    setError(null);
    try {
      const res = await fetch(`/api/projects/${p.id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const proj = await res.json();
      localStorage.setItem(AUDIO_KEY, JSON.stringify({
        segments: proj.segments || [],
        finishing: proj.finishing || {},
        savedAt: Date.now(),
      }));
      window.location.hash = "#/studio";
    } catch (e) { setError(`Gagal buka project: ${e}`); }
    finally { setBusy(null); }
  };

  const duplicateProject = async (p) => {
    setBusy(p.id);
    setError(null);
    try {
      const res = await fetch(`/api/projects/${p.id}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const proj = await res.json();
      const body = {
        name: `${proj.name || "project"} (copy)`,
        segments: proj.segments || [],
        finishing: proj.finishing || {},
        narration_meta: proj.narration_meta || {},
        template_name: proj.template_name || "",
      };
      const r2 = await fetch("/api/projects", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r2.ok) throw new Error(`HTTP ${r2.status}`);
      await refresh();
    } catch (e) { setError(`Gagal duplikat: ${e}`); }
    finally { setBusy(null); }
  };

  const deleteProject = async (p) => {
    if (!window.confirm(`Hapus project "${p.name}"? (tidak bisa dibatalkan)`)) return;
    setBusy(p.id);
    setError(null);
    try {
      const res = await fetch(`/api/projects/${p.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await refresh();
    } catch (e) { setError(`Gagal hapus: ${e}`); }
    finally { setBusy(null); }
  };

  const newProject = () => {
    try { localStorage.removeItem(AUDIO_KEY); } catch { /* ignore */ }
    window.location.hash = "#/studio";
  };

  return (
    <div className="px-4 sm:px-6 py-5" style={{ maxWidth: 1280, margin: "0 auto" }}>
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <FolderOpen size={20} color={C.amber} />
          <div>
            <h2 style={{ fontFamily: F.display, fontSize: 18, fontWeight: 700, color: C.paper, margin: 0 }}>PROJECT LIBRARY</h2>
            <span style={{ fontFamily: F.mono, fontSize: 10, color: C.paperFaint, letterSpacing: "0.06em" }}>
              {loading ? "MEMUAT..." : `${projects.length} PROJECT TERSIMPAN`}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={refresh} title="Refresh"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded"
            style={{ background: C.panelRaised, border: `1px solid ${C.border}`, color: C.paperDim, fontFamily: F.mono, fontSize: 10 }}>
            <RefreshCw size={12} /> REFRESH
          </button>
          <button onClick={newProject}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded"
            style={{ background: C.amber, border: "none", color: "#15130F", fontFamily: F.mono, fontSize: 10, fontWeight: 700 }}>
            <Plus size={12} /> PROJECT BARU
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded"
          style={{ background: "rgba(232,84,46,0.08)", border: `1px solid ${C.tallyDim}`, color: C.tally }}>
          <AlertTriangle size={13} /> <span style={{ fontSize: 12 }}>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16" style={{ color: C.paperFaint }}>
          <Loader2 size={20} className="animate-spin" />
        </div>
      ) : projects.length === 0 ? (
        <div className="rounded-xl py-16 text-center"
          style={{ background: C.panel, border: `1px dashed ${C.border}` }}>
          <LayoutGrid size={28} color={C.paperFaint} style={{ margin: "0 auto 12px" }} />
          <div style={{ color: C.paperDim, fontSize: 14, marginBottom: 4 }}>Belum ada project tersimpan</div>
          <div style={{ color: C.paperFaint, fontSize: 12, marginBottom: 16 }}>
            Bikin project di Studio, terus klik "Simpan ke Library" di timeline editor.
          </div>
          <button onClick={newProject}
            className="px-4 py-2 rounded"
            style={{ background: C.amber, border: "none", color: "#15130F", fontFamily: F.mono, fontSize: 11, fontWeight: 700 }}>
            BUKA STUDIO
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {projects.map(p => (
            <div key={p.id} className="rounded-xl overflow-hidden"
              style={{ background: C.panel, border: `1px solid ${C.border}` }}>
              {/* Thumbnail */}
              <div onClick={() => openProject(p)} style={{ cursor: "pointer", position: "relative", aspectRatio: "16/9", background: "#0E0C09", display: "flex", alignItems: "center", justifyContent: "center" }}>
                {p.thumb_url ? (
                  <img src={p.thumb_url} alt={p.name} loading="lazy"
                    style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                ) : (
                  <Film size={26} color={C.paperFaint} />
                )}
                <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.25)", opacity: 0, transition: "opacity .15s", display: "flex", alignItems: "center", justifyContent: "center" }}
                  className="hover:opacity-100" >
                  <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full"
                    style={{ background: "rgba(0,0,0,0.72)", color: C.paper, fontFamily: F.mono, fontSize: 10 }}>
                    <Play size={11} /> BUKA
                  </span>
                </div>
              </div>
              {/* Body */}
              <div className="p-3">
                <div style={{ color: C.paper, fontSize: 13, fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", marginBottom: 6 }}>{p.name}</div>
                <div className="flex flex-wrap gap-2 mb-3">
                  <span className="flex items-center gap-1" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                    <Clock size={10} /> {fmtDur(p.duration)}
                  </span>
                  <span className="flex items-center gap-1" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                    <Film size={10} /> {p.scene_count} clip
                  </span>
                  <span className="flex items-center gap-1" style={{ fontFamily: F.mono, fontSize: 9.5, color: C.paperDim }}>
                    <Type size={10} /> {p.wpm || 0} wpm
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span style={{ fontFamily: F.mono, fontSize: 9, color: C.paperFaint }}>
                    {p.segments_count} segmen · {fmtDate(p.saved_at)}
                  </span>
                  <div className="flex items-center gap-1">
                    <button title="Duplikat" onClick={() => duplicateProject(p)} disabled={busy === p.id}
                      className="p-1.5 rounded"
                      style={{ background: C.panelRaised, border: `1px solid ${C.border}`, color: C.paperDim, cursor: "pointer" }}>
                      <Copy size={11} />
                    </button>
                    <button title="Hapus" onClick={() => deleteProject(p)} disabled={busy === p.id}
                      className="p-1.5 rounded"
                      style={{ background: C.panelRaised, border: `1px solid ${C.border}`, color: C.tally, cursor: "pointer" }}>
                      <Trash2 size={11} />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
