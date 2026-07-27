import { create } from "zustand";
import { persist } from "zustand/middleware";

const API_BASE = "http://127.0.0.1:8787";

const useRitmeStore = create(
  persist(
    (set, get) => ({
      // Connection
      backendRunning: false,
      backendPort: null,
      backendError: null,
      
      // Pipeline state
      currentTemplate: null,
      script: null,
      narration: null,
      footageData: null,
      picks: {},
      
      // UI state
      currentStage: 0, // 0=idle, 1=template, 2=script, 3=narration, 4=footage
      
      // Actions
      checkBackend: async () => {
        try {
          const res = await fetch(`${API_BASE}/api/setup/check`);
          if (res.ok) {
            set({ backendRunning: true, backendError: null });
            return true;
          }
        } catch (e) {
          set({ backendRunning: false, backendError: e.message });
        }
        return false;
      },
      
      api: async (method, path, body) => {
        try {
          const opts = { method, headers: { "Content-Type": "application/json" } };
          if (body) opts.body = JSON.stringify(body);
          const res = await fetch(`${API_BASE}${path}`, opts);
          const data = await res.json();
          if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
          return data;
        } catch (e) {
          set({ backendError: e.message });
          throw e;
        }
      },
      
      // Extract template
      extractTemplate: async (videoBlob, name) => {
        const form = new FormData();
        form.append("video", videoBlob);
        form.append("name", name);
        const res = await fetch(`${API_BASE}/api/template/extract`, { method: "POST", body: form });
        const { job_id } = await res.json();
        return get().pollJob(job_id, (result) => {
          set({ currentTemplate: result, currentStage: 1 });
        });
      },
      
      // Poll job
      pollJob: (jobId, onDone) => {
        return new Promise((resolve, reject) => {
          const poll = async () => {
            try {
              const job = await get().api("GET", `/api/jobs/${jobId}`);
              if (job.status === "done") { onDone(job.result); resolve(job.result); }
              else if (job.status === "error") { reject(new Error(job.error)); }
              else setTimeout(poll, 700);
            } catch (e) { reject(e); }
          };
          poll();
        });
      },
      
      // Generate script
      generateScript: async (topic, segments = 8, styleId = null, language = "id") => {
        if (!get().currentTemplate) throw new Error("No template");
        const { job_id } = await get().api("POST", "/api/script/generate", {
          template_name: get().currentTemplate.template_name,
          topic, segments, style_id: styleId, language
        });
        return get().pollJob(job_id, (result) => {
          set({ script: result, currentStage: 2 });
        });
      },
      
      // Generate narration
      generateNarration: async (ttsProvider = "pyttsx3") => {
        if (!get().script) throw new Error("No script");
        const { job_id } = await get().api("POST", "/api/narration/generate", {
          segments: get().script.segments, tts_provider: ttsProvider
        });
        return get().pollJob(job_id, (result) => {
          set({ narration: result, currentStage: 3 });
        });
      },
      
      // Match footage
      matchFootage: async () => {
        if (!get().narration) throw new Error("No narration");
        const { job_id } = await get().api("POST", "/api/footage/match", {
          segments: get().narration.segments
        });
        return get().pollJob(job_id, (result) => {
          const picks = {};
          Object.keys(result).forEach((idx) => { picks[idx] = result[idx].best_index; });
          set({ footageData: result, picks, currentStage: 4 });
        });
      },
      
      // Send footage to timeline
      getTimelineSegments: () => {
        const { narration, footageData, picks } = get();
        if (!narration?.segments) return [];
        return narration.segments.map((s, idx) => {
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
      },
      
      reset: () => set({
        currentTemplate: null, script: null, narration: null,
        footageData: null, picks: {}, currentStage: 0
      }),
    }),
    { name: "ritme-store" }
  )
);

export default useRitmeStore;
