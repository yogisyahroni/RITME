// P5 — In-browser Preview Player
// Custom controls: play/pause, seek (click+drag), hover-frame popup preview,
// loop toggle, playback speed. Murni frontend — memakai video preview dari
// /api/timeline/preview (low-res, cepat) tanpa perlu export penuh.
import React, { useEffect, useRef, useState, useCallback, forwardRef, useImperativeHandle } from "react";
import { Play, Pause, Repeat, Repeat1, Gauge } from "lucide-react";

const F = { mono: "'JetBrains Mono', monospace", body: "Inter, system-ui, sans-serif" };

function fmt(t) {
  if (!isFinite(t) || t < 0) return "0:00";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export default forwardRef(function PreviewPlayer({ src, autoPlay = false, style }, ref) {
  const videoRef = useRef(null);
  const wrapRef = useRef(null);
  const rafRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  const [loop, setLoop] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [scrubT, setScrubT] = useState(null); // hover time saat scrub
  const [frameUrl, setFrameUrl] = useState(null); // frame popup (data URL)

  // timeupdate -> waktu jalan (throttle via rAF)
  const onTime = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => setTime(v.currentTime));
  }, []);
  useEffect(() => () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); }, []);

  // capture frame saat ini ke dataURL (buat popup scrub preview)
  const captureFrame = useCallback(() => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    const k = 240 / v.videoWidth;
    c.width = 240;
    c.height = Math.round(v.videoHeight * k);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    setFrameUrl(c.toDataURL("image/jpeg", 0.6));
  }, []);

  const seekTo = useCallback((t) => {
    const v = videoRef.current;
    if (!v) return;
    const clamped = Math.max(0, Math.min(t, v.duration || 0));
    v.currentTime = clamped;
    setTime(clamped);
  }, []);

  // hover/drag di scrubber -> seek + capture frame (throttled 80ms)
  const scrubTimer = useRef(null);
  const handleScrub = useCallback((e) => {
    const el = wrapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const t = frac * (dur || 0);
    setScrubT(t);
    if (scrubTimer.current) clearTimeout(scrubTimer.current);
    scrubTimer.current = setTimeout(() => { seekTo(t); captureFrame(); }, 80);
  }, [dur, seekTo, captureFrame]);
  const endScrub = useCallback(() => {
    if (scrubTimer.current) clearTimeout(scrubTimer.current);
    setScrubT(null);
  }, []);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onMeta = () => { setDur(v.duration || 0); if (autoPlay) v.play().catch(() => {}); };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnd = () => { setPlaying(false); if (loop) { v.currentTime = 0; v.play().catch(() => {}); } };
    v.addEventListener("loadedmetadata", onMeta);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("ended", onEnd);
    v.addEventListener("timeupdate", onTime);
    return () => {
      v.removeEventListener("loadedmetadata", onMeta);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("ended", onEnd);
      v.removeEventListener("timeupdate", onTime);
    };
  }, [autoPlay, loop, onTime]);

  const togglePlay = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) v.play().catch(() => {}); else v.pause();
  };
  useImperativeHandle(ref, () => ({ togglePlay, seekTo, getVideo: () => videoRef.current }));
  const skip = (d) => seekTo(time + d);

  const pct = dur > 0 ? (time / dur) * 100 : 0;
  const scrubPct = scrubT != null && dur > 0 ? (scrubT / dur) * 100 : pct;

  return (
    <div style={{ borderRadius: 10, overflow: "hidden", background: "#000", ...style }}>
      <video ref={videoRef} src={src} preload="metadata"
        style={{ width: "100%", maxHeight: 400, display: "block" }} />
      {/* Controls bar */}
      <div style={{ padding: "8px 12px", background: "rgba(10,12,16,0.92)", borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        {/* Scrubber */}
        <div ref={wrapRef} onMouseMove={handleScrub} onMouseLeave={endScrub} onMouseDown={handleScrub}
          onTouchMove={(e) => handleScrub({ clientX: e.touches[0].clientX })} onTouchEnd={endScrub}
          style={{ position: "relative", height: 16, cursor: "pointer", touchAction: "none" }}>
          <div style={{ position: "absolute", left: 0, right: 0, top: 7, height: 3, borderRadius: 2, background: "rgba(255,255,255,0.15)" }} />
          <div style={{ position: "absolute", left: 0, top: 7, height: 3, borderRadius: 2, width: `${scrubPct}%`, background: "#ffd400" }} />
          <div style={{ position: "absolute", left: `calc(${scrubPct}% - 5px)`, top: 4, width: 10, height: 10, borderRadius: "50%", background: "#ffd400" }} />
          {/* Hover frame popup */}
          {scrubT != null && frameUrl && (
            <div style={{ position: "absolute", bottom: 20, left: `calc(${scrubPct}% - 60px)`, width: 120, borderRadius: 6, overflow: "hidden", border: "1px solid rgba(255,255,255,0.2)", boxShadow: "0 8px 24px rgba(0,0,0,0.6)", pointerEvents: "none", background: "#000" }}>
              <img src={frameUrl} alt="frame" style={{ width: 120, display: "block" }} />
              <div style={{ fontFamily: F.mono, fontSize: 9, color: "#fff", textAlign: "center", padding: "2px 0", background: "rgba(0,0,0,0.7)" }}>{fmt(scrubT)}</div>
            </div>
          )}
        </div>
        {/* Buttons */}
        <div className="flex items-center gap-3" style={{ marginTop: 2 }}>
          <button onClick={skip.bind(null, -5)} title="-5s"
            style={{ background: "none", border: "none", color: "#cfd2da", fontSize: 13, cursor: "pointer", padding: 0 }}>⟲5</button>
          <button onClick={togglePlay} title={playing ? "Pause" : "Play"}
            style={{ background: "none", border: "none", color: "#ffd400", cursor: "pointer", padding: 0, display: "flex" }}>
            {playing ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <button onClick={skip.bind(null, 5)} title="+5s"
            style={{ background: "none", border: "none", color: "#cfd2da", fontSize: 13, cursor: "pointer", padding: 0 }}>5⟳</button>
          <span style={{ fontFamily: F.mono, fontSize: 10.5, color: "#aab" }}>{fmt(time)} / {fmt(dur)}</span>
          <div className="flex-1" />
          {/* Speed */}
          <label className="flex items-center gap-1" title="Kecepatan playback">
            <Gauge size={12} color="#889" />
            <select value={speed} onChange={e => { const s = parseFloat(e.target.value); setSpeed(s); if (videoRef.current) videoRef.current.playbackRate = s; }}
              style={{ fontFamily: F.mono, fontSize: 10, color: "#cfd2da", background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.12)", borderRadius: 4, padding: "2px 4px", outline: "none", cursor: "pointer" }}>
              {[0.5, 1, 1.5, 2].map(s => <option key={s} value={s}>{s}x</option>)}
            </select>
          </label>
          {/* Loop */}
          <button onClick={() => setLoop(!loop)} title="Loop"
            style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex", color: loop ? "#ffd400" : "#889" }}>
            {loop ? <Repeat1 size={15} /> : <Repeat size={15} />}
          </button>
        </div>
      </div>
    </div>
  );
});
