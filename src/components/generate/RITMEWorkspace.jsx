import React, { useState, useEffect, useRef } from "react";
import { Play, Download, ChevronRight, Film, FileText, Mic, ScanSearch, Clapperboard, Upload, Loader2, AlertTriangle, Check, Info } from "lucide-react";
import useRitmeStore from "../stores/ritmeStore";

const STAGES = [
  { id: 1, label: "Template", icon: Film },
  { id: 2, label: "Script", icon: FileText },
  { id: 3, label: "Narration", icon: Mic },
  { id: 4, label: "Footage", icon: ScanSearch },
];

function StageBadge({ n, label, icon: Icon, active, done }) {
  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded transition-colors ${active ? "bg-sf-accent/20 border border-sf-accent" : done ? "bg-sf-success/20" : "bg-sf-dark-700"}`}>
      {done ? <Check className="w-4 h-4 text-sf-success" /> : <Icon className={`w-4 h-4 ${active ? "text-sf-accent" : "text-sf-text-muted"}`} />}
      <span className={`text-xs font-medium ${active ? "text-sf-accent" : done ? "text-sf-success" : "text-sf-text-muted"}`}>{label}</span>
    </div>
  );
}

function RITMEWorkspace({ onSendToTimeline }) {
  const store = useRitmeStore();
  const [topic, setTopic] = useState("");
  const [numSegments, setNumSegments] = useState(8);
  const [ttsProvider, setTtsProvider] = useState("pyttsx3");
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const [file, setFile] = useState(null);
  const fileRef = useRef(null);
  const cancelRef = useRef(null);
  const [activeSeg, setActiveSeg] = useState(0);

  useEffect(() => { store.checkBackend(); }, []);
  useEffect(() => () => cancelRef.current && cancelRef.current(), []);

  const runAsync = async (label, fn) => {
    setError(null); setJob({ message: label });
    try { await fn(); setJob(null); } catch (e) { setJob(null); setError(e.message); }
  };

  const extractTemplate = () => {
    if (!file) return;
    runAsync("Extracting template...", () => store.extractTemplate(file, "velorn_style"));
  };

  const generateScript = () => {
    runAsync(`Researching & writing script...`, () => store.generateScript(topic, numSegments, null, "id"));
  };

  const generateNarration = () => {
    runAsync(`Synthesizing narration...`, () => store.generateNarration(ttsProvider));
  };

  const matchFootage = () => {
    runAsync(`Searching + CLIP matching footage...`, () => store.matchFootage());
  };

  const sendToTimeline = () => {
    const segments = store.getTimelineSegments();
    if (onSendToTimeline && segments.length > 0) {
      onSendToTimeline({
        segments,
        narration_audio_path: store.narration?.audio_path || "",
        audio_url: store.narration?.audio_url || "",
      });
    }
  };

  const fmt = (s) => { const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`; };

  const progress = (store) => {
    if (store.currentTemplate) return 1;
    if (store.currentStage >= 1) return 2;
    return 0;
  };

  return (
    <div className="h-full flex flex-col bg-sf-dark-900 overflow-y-auto">
      {/* Header */}
      <div className="p-4 border-b border-sf-dark-700">
        <h2 className="text-lg font-bold text-sf-text-primary">RITME Pipeline</h2>
        <p className="text-xs text-sf-text-muted mt-1">Auto-edit pipeline: template → script → narration → footage → timeline</p>
        <div className={`mt-2 flex items-center gap-2 ${store.backendRunning ? "text-sf-success" : "text-sf-warning"}`}>
          <div className={`w-2 h-2 rounded-full ${store.backendRunning ? "bg-sf-success" : "bg-sf-warning"}`} />
          <span className="text-xs">{store.backendRunning ? "Backend connected" : "Backend not running"}</span>
        </div>
      </div>

      {/* Stage Progress */}
      <div className="p-3 flex gap-2 overflow-x-auto border-b border-sf-dark-700">
        {STAGES.map((s) => (
          <StageBadge key={s.id} n={s.id} label={s.label} icon={s.icon}
            active={store.currentStage === s.id - 1}
            done={store.currentStage >= s.id} />
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 p-4 space-y-4 overflow-y-auto">
        {/* Error */}
        {error && (
          <div className="flex items-center gap-2 px-3 py-2 rounded bg-sf-warning/10 border border-sf-warning/30 text-xs text-sf-text-primary">
            <AlertTriangle className="w-4 h-4 text-sf-warning flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Step 1: Template */}
        <div className="p-3 rounded bg-sf-dark-800 border border-sf-dark-700">
          <h3 className="text-sm font-semibold text-sf-text-primary mb-2">1. Upload Reference Video</h3>
          {!store.currentTemplate ? (
            <div className="space-y-2">
              <label className="flex items-center justify-center gap-2 px-4 py-6 rounded border border-dashed border-sf-dark-600 cursor-pointer hover:border-sf-accent transition-colors">
                <Upload className="w-5 h-5 text-sf-text-muted" />
                <span className="text-xs text-sf-text-muted">{file ? file.name : "Click to select video"}</span>
                <input ref={fileRef} type="file" accept="video/*" className="hidden" onChange={(e) => setFile(e.target.files[0])} />
              </label>
              <button onClick={extractTemplate} disabled={!file || job !== null}
                className="w-full px-3 py-2 rounded bg-sf-accent text-white text-sm font-medium hover:bg-sf-accent/80 transition-colors disabled:opacity-50">
                {job?.message === "Extracting template..." ? "Extracting..." : "Extract Template"}
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-sf-text-primary">
              <Check className="w-4 h-4 text-sf-success" />
              <span>{store.currentTemplate.template_name} ({store.currentTemplate.pacing.shot_count} shots)</span>
            </div>
          )}
        </div>

        {/* Step 2: Script */}
        {store.currentTemplate && (
          <div className="p-3 rounded bg-sf-dark-800 border border-sf-dark-700">
            <h3 className="text-sm font-semibold text-sf-text-primary mb-2">2. Generate Script</h3>
            {!store.script ? (
              <div className="space-y-2">
                <input value={topic} onChange={(e) => setTopic(e.target.value)}
                  placeholder="e.g. Renewable energy in Indonesia"
                  className="w-full px-3 py-2 rounded bg-sf-dark-900 border border-sf-dark-600 text-sm text-sf-text-primary outline-none focus:border-sf-accent" />
                <div className="flex items-center gap-2">
                  <span className="text-xs text-sf-text-muted">Segments:</span>
                  <input type="number" min={3} max={15} value={numSegments} onChange={(e) => setNumSegments(Number(e.target.value))}
                    className="w-16 px-2 py-1 rounded bg-sf-dark-900 border border-sf-dark-600 text-sm text-sf-text-primary outline-none" />
                  <button onClick={generateScript} disabled={!topic || job !== null}
                    className="ml-auto px-3 py-1.5 rounded bg-sf-accent text-white text-xs font-medium hover:bg-sf-accent/80 disabled:opacity-50">
                    {job?.message?.includes("writing script") ? "Writing..." : "Generate"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-sm text-sf-text-primary">
                <Check className="w-4 h-4 text-sf-success" />
                <span>{store.script.segments.length} segments generated</span>
              </div>
            )}
          </div>
        )}

        {/* Step 3: Narration */}
        {store.script && (
          <div className="p-3 rounded bg-sf-dark-800 border border-sf-dark-700">
            <h3 className="text-sm font-semibold text-sf-text-primary mb-2">3. Narration Audio</h3>
            {!store.narration ? (
              <div className="space-y-2">
                <div className="flex gap-2">
                  {["pyttsx3", "elevenlabs"].map((p) => (
                    <button key={p} onClick={() => setTtsProvider(p)}
                      className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${ttsProvider === p ? "bg-sf-accent text-white" : "bg-sf-dark-700 text-sf-text-muted"}`}>{p}</button>
                  ))}
                </div>
                <button onClick={generateNarration} disabled={job !== null}
                  className="w-full px-3 py-2 rounded bg-sf-accent text-white text-sm font-medium hover:bg-sf-accent/80 disabled:opacity-50">
                  {job?.message?.includes("Synthesizing") ? "Synthesizing..." : "Generate Narration"}
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2 text-sm text-sf-text-primary">
                <Check className="w-4 h-4 text-sf-success" />
                <span>Narration ready ({store.narration.segments.length} segments timed)</span>
                {store.narration.audio_url && <audio controls src={store.narration.audio_url} className="h-8 ml-auto" />}
              </div>
            )}
          </div>
        )}

        {/* Step 4: Footage */}
        {store.narration && (
          <div className="p-3 rounded bg-sf-dark-800 border border-sf-dark-700">
            <h3 className="text-sm font-semibold text-sf-text-primary mb-2">4. Match Footage</h3>
            {!store.footageData ? (
              <button onClick={matchFootage} disabled={job !== null}
                className="w-full px-3 py-2 rounded bg-sf-accent text-white text-sm font-medium hover:bg-sf-accent/80 disabled:opacity-50">
                {job?.message?.includes("CLIP") ? "Matching..." : "Search & Match Footage"}
              </button>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Check className="w-4 h-4 text-sf-success" />
                  <span className="text-sm text-sf-text-primary">{Object.keys(store.footageData).length} segments matched</span>
                </div>
                
                {/* Segment selector */}
                <div className="flex gap-1 overflow-x-auto">
                  {Object.keys(store.footageData).map((idx) => (
                    <button key={idx} onClick={() => setActiveSeg(Number(idx))}
                      className={`px-2.5 py-1 rounded text-xs font-medium whitespace-nowrap transition-colors ${activeSeg === Number(idx) ? "bg-sf-accent text-white" : "bg-sf-dark-700 text-sf-text-muted"}`}>
                      Seg {String(Number(idx) + 1).padStart(2, "0")}
                    </button>
                  ))}
                </div>
                
                {/* Candidates grid */}
                <div className="grid grid-cols-2 gap-2">
                  {store.footageData[String(activeSeg)]?.candidates?.slice(0, 4).map((c, ci) => {
                    const isPicked = store.picks[activeSeg] === ci;
                    return (
                      <button key={ci} onClick={() => useRitmeStore.setState({ picks: { ...store.picks, [activeSeg]: ci } })}
                        className={`relative rounded overflow-hidden transition-all ${isPicked ? "ring-2 ring-sf-accent" : "ring-1 ring-sf-dark-600"}`}>
                        <div className="aspect-video bg-sf-dark-900 flex items-center justify-center">
                          {c.thumbnail_url ? <img src={c.thumbnail_url} className="w-full h-full object-cover" /> : <Film className="w-5 h-5 text-sf-text-muted" />}
                        </div>
                        <div className="px-1.5 py-1 flex items-center gap-1">
                          <span className="text-[10px] text-sf-text-muted">{c.source}</span>
                          <div className="flex-1 h-1 rounded bg-sf-dark-600 overflow-hidden">
                            <div className="h-full bg-sf-accent rounded" style={{ width: `${Math.min(c.score * 100, 100)}%` }} />
                          </div>
                          <span className="text-[10px] text-sf-text-muted">{Math.round(c.score * 100)}%</span>
                        </div>
                        {isPicked && <div className="absolute top-1 right-1 w-4 h-4 rounded-full bg-sf-accent flex items-center justify-center"><Check className="w-3 h-3 text-white" /></div>}
                      </button>
                    );
                  })}
                </div>
                
                {/* Send to timeline */}
                <button onClick={sendToTimeline}
                  className="w-full px-3 py-2.5 rounded bg-sf-success text-white text-sm font-semibold hover:bg-sf-success/80 transition-colors flex items-center justify-center gap-2">
                  <ChevronRight className="w-4 h-4" />
                  Send to Timeline
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-sf-dark-700 text-[10px] text-sf-text-muted text-center">
        RITME Pipeline v1.0 • Auto-Edit Video
      </div>
    </div>
  );
}

export default RITMEWorkspace;
