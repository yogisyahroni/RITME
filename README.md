# Auto-Editing Video Pipeline

Turns a reference video you like into a reusable **style template**
(shot pacing + narration rhythm), then given a new **topic**, automatically:
researches it, writes a script, generates narration, sources matching
footage from legal sources, and auto-cuts a finished video synced to
the narration.

```
Reference video ──▶ Stage 1: Template Extraction (pacing + narration style)
                                      │
Topic ("EV batteries") ──▶ Stage 2: Research + Script Generation
                                      │
                          Stage 3: Narration Audio + Word Timestamps
                                      │
                          Stage 4: Footage Sourcing + CLIP Matching
                                      │
                          Stage 5: Auto-Cut Assembly ──▶ final_output.mp4
```

## ⚖️ Legal boundaries — read this first

This tool sources footage **only** from:
- **Pexels** and **Pixabay** — free stock video, licensed for reuse
- **Wikimedia Commons** and **Archive.org** — public domain / CC media
- **YouTube, filtered to `videoLicense=creativeCommon`** — i.e. only
  videos the uploader explicitly marked as reusable

It does **not** support, and will not be extended to support:
- Downloading arbitrary non-CC YouTube videos or any other copyrighted
  footage without a license
- Slicing clips into short segments to evade Content ID or other
  copyright-detection systems

If you plan to use screenshots of news articles or journal figures,
treat that as a separate editorial decision outside this pipeline —
use brief, attributed, transformative excerpts, not full reproductions,
and check the outlet's own reuse policy.

## Setup

**Windows note:** Windows' console/file APIs default to a legacy codepage
(not UTF-8) unless told otherwise, which used to make this codebase crash
on Windows the moment it tried to print a "✅" or save a template with a
non-ASCII character. That's fixed in code now — `main.py`, `server.py`,
and `check_setup.py` all force UTF-8 stdout/stderr on startup, and every
JSON file the pipeline writes uses `encoding="utf-8"` explicitly. Nothing
extra to configure. (If you ever hit a similar error in code you add
yourself, the fix is the same: pass `encoding="utf-8"` to `open()` /
`.read_text()` / `.write_text()`, and don't rely on the platform default.)

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and fill in the API keys for the stages you'll use

python check_setup.py           # verifies keys + network access before you run anything
```

You don't need every key on day one — each stage prints a clear error
telling you exactly which key is missing when you first run it.

| Key | Needed for | Free? | Get it at |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Stage 2 script writing | Paid (cheap) | console.anthropic.com |
| `PEXELS_API_KEY` | Stage 4 footage | Free | pexels.com/api |
| `PIXABAY_API_KEY` | Stage 4 footage | Free | pixabay.com/api/docs |
| `YOUTUBE_API_KEY` | Stage 4 CC footage (optional) | Free | console.cloud.google.com |
| `ELEVENLABS_API_KEY` | Stage 3 narration (optional, better quality) | Paid | elevenlabs.io |

If you skip `ELEVENLABS_API_KEY`, narration falls back to `pyttsx3`
(free, offline, robotic — fine for previewing pacing before you commit
to a paid TTS pass).

## Getting each API key, step by step

**Anthropic — Stage 2 script writing (default LLM provider)**
1. Go to `console.anthropic.com` and sign up / log in.
2. Settings → API Keys → Create Key. Copy the key (starts with `sk-ant-...`).
3. Paste it into `.env` as `ANTHROPIC_API_KEY`.
4. Console → Billing → add credits (pay-as-you-go; a single script generation
   costs a fraction of a cent to a few cents).

**Pexels — Stage 4 free stock footage**
1. Go to `pexels.com/api` and sign up (free, no approval wait).
2. Your API key is shown immediately on that page after signup.
3. Paste it into `.env` as `PEXELS_API_KEY`.

**Pixabay — Stage 4 free stock footage**
1. Go to `pixabay.com/api/docs` and create a free account.
2. Once logged in, that same docs page shows "Your API key" at the top.
3. Paste it into `.env` as `PIXABAY_API_KEY`.

**YouTube Data API — optional, Stage 4 Creative-Commons search**
1. Go to `console.cloud.google.com` and create (or reuse) a project.
2. APIs & Services → Library → search "YouTube Data API v3" → Enable.
3. APIs & Services → Credentials → Create Credentials → API key.
4. Paste it into `.env` as `YOUTUBE_API_KEY`.
5. Free tier is 10,000 quota units/day — roughly 100 search calls, plenty
   for normal use.

**ElevenLabs — optional, Stage 3 natural-sounding narration**
1. Go to `elevenlabs.io` and sign up.
2. Profile icon → API Keys → Create.
3. Paste it into `.env` as `ELEVENLABS_API_KEY`.
4. Browse `elevenlabs.io/app/voice-library`, pick a voice, copy its Voice ID
   into `.env` as `ELEVENLABS_VOICE_ID`.

Once your keys are in `.env`, run:
```bash
python check_setup.py
```
It prints a checklist of which keys are set and, separately, tests whether
your network can actually reach every service the pipeline calls — see the
next section if anything shows as blocked.

## Network & Hugging Face access

Two stages need more than a simple REST call: **Stage 1/3** downloads a
Whisper speech-to-text model, and **Stage 4** downloads a CLIP model — both
come from Hugging Face Hub the first time you run them, then get cached
locally (`~/.cache/huggingface/`) so it's only slow once.

**Normal home/office internet or a typical cloud VM:** nothing to configure.
The first run downloads ~150–800MB of model weights automatically; every
run after that is instant since it reads from cache.

**Behind a corporate VPN, campus network, or a locked-down sandbox:**
Hugging Face Hub doesn't serve files from one domain — a download redirects
through several CDN endpoints, and if your firewall blocks even one hop,
you get a confusing SSL/timeout error instead of a clear "blocked" message.
Ask whoever controls the network to allowlist:

- `huggingface.co` (main site + API)
- `hf.co` **and all its subdomains** — if your proxy supports suffix
  matching, this one entry covers every current and future CDN endpoint
- If only exact hostnames are accepted, add these individually:
  `cdn-lfs.hf.co`, `cdn-lfs-us-1.hf.co`, `cdn-lfs-eu-1.hf.co`,
  `cas-bridge.xethub.hf.co`, `cas-server.xethub.hf.co`, `transfer.xethub.hf.co`

Then run `python check_setup.py` — it individually pings every domain the
pipeline touches (Hugging Face, Pexels, Pixabay, your chosen LLM, YouTube,
ElevenLabs, DuckDuckGo) and tells you exactly which ones are still blocked,
instead of you guessing from a stack trace.

**If nothing can be whitelisted (e.g. a locked-down company laptop):**
download the models once on any machine with normal internet, then copy the
cache folder over:
```bash
# on an unrestricted machine:
python3 -c "import open_clip; open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')"
python3 -c "from faster_whisper import WhisperModel; WhisperModel('base')"
# this populates ~/.cache/huggingface/ — copy that whole folder to the
# restricted machine at the same path, and Stage 1/3/4's model loading
# works fully offline from then on (footage/LLM API calls still need
# their own network access, per the table above).
```

**Common errors and fixes:**

| Symptom | Likely cause | Fix |
|---|---|---|
| `SSL: CERTIFICATE_VERIFY_FAILED` | Corporate proxy is doing SSL inspection | Ask IT to exempt `huggingface.co`/`hf.co` from inspection |
| Download hangs, then times out | One specific CDN redirect hop is blocked | Run `check_setup.py`, note which host fails, allowlist that one too |
| `403 Forbidden` even after whitelisting | You're loading a *gated* model that needs login | Doesn't apply here — the CLIP (`ViT-B-32`/openai) and Whisper (`base`) models this pipeline uses are public, no login required |

## Running the web app (RITME UI)

The pipeline has a full web UI — upload a reference video, generate a
script, narration, footage matches, and final render, all from the
browser, backed by the same 5 stages as the CLI below.

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in API keys as needed, see above

python server.py
```

Open `http://localhost:8000`. That's it — the frontend is pre-built
(`frontend/dist/`), so this works with just Python, no Node.js needed.

**If you edit the UI** (`frontend/src/App.jsx`), rebuild with:
```bash
cd frontend && ./build.sh
```
That needs Node.js, only for rebuilding — not for running the app.

**How it's wired:** `server.py` is a single FastAPI process that exposes
each pipeline stage as a REST endpoint and serves the frontend from the
same origin (no CORS setup needed). Slow stages (footage matching,
rendering) run as background jobs the UI polls for progress — render
progress is the real ffmpeg encode percentage (via a moviepy/proglog
hook), not a simulated bar. Stage 4 returns the top 4 CLIP-ranked
candidates per segment instead of auto-picking one, so you can review
and override the match before rendering, same as the Footage Matching
Board design.

## Usage (CLI)

Prefer the terminal, or want to script the pipeline into something else?
Everything above is also available as a CLI.

**1. Extract a template from a video you like:**
```bash
python main.py extract-template --video path/to/reference.mp4 --name my_style
```
This detects shot cuts and (if the video has speech) narration pacing,
and saves `templates/my_style.json`.

**2. Run the full pipeline on a new topic:**
```bash
python main.py run --template my_style --topic "renewable energy in Indonesia" \
  --segments 8 --output-name renewable_energy_id
```
Output lands in `output/renewable_energy_id.mp4`.

**Or run stages separately** (useful for reviewing the script before
burning API credits on narration/footage):
```bash
python main.py generate-script --template my_style --topic "EV batteries"
# review templates/ev-batteries_script.json, edit the text/keywords by hand if needed
```

## How each stage works

- **Stage 1 (`pipeline/stage1_template.py`)** — `PySceneDetect` finds
  shot boundaries via frame-to-frame content differences; `faster-whisper`
  (optional) transcribes any narration to learn words-per-minute and
  sentence length.
- **Stage 2 (`pipeline/stage2_script.py`)** — `duckduckgo-search` (no
  key needed) gathers source snippets; your chosen LLM turns them into
  a script matching the template's pacing, plus visual keywords per
  segment.
- **Stage 3 (`pipeline/stage3_narration.py`)** — synthesizes narration
  audio, then re-transcribes it with Whisper to get precise per-word
  timestamps (more reliable than trusting the TTS engine's own timing).
- **Stage 4 (`pipeline/stage4_footage.py`)** — searches all legal
  sources per segment's keywords, downloads candidates, and uses CLIP
  (a text/image embedding model) to pick the clip whose visual content
  actually matches the keyword — not just whichever API result ranked
  first.
- **Stage 5 (`pipeline/stage5_assembly.py`)** — cuts each segment's
  footage to the narration's timing, re-splits long segments into
  multiple sub-cuts so pacing matches your template's average shot
  length, center-crops to your target aspect ratio, and burns in
  subtitles.

## Notes on this build

- Tested in this environment: Stage 1 scene detection (validated
  against a synthetic 3-shot video — correctly detected 3.0/2.0/4.0-second
  shots), Stage 2/3 alignment logic, Stage 4's search/download/CLIP
  plumbing (validated with a mock model), and Stage 5's full render
  (validated output: correct resolution, duration, audio track, and
  burned-in subtitles), including real (not simulated) ffmpeg encode
  progress via a proglog hook.
- The web app (`server.py` + `frontend/`) was tested with a real headless
  browser end-to-end: real file upload through the actual `<input
  type=file>`, a real click on "Ekstrak Template", a real multipart POST,
  a real background job running Stage 1, real polling, and the UI
  correctly rendering the returned shot/pacing data. Stage 2's
  error-handling path (missing API key) was also verified to show a
  clean error banner with retry, rather than a silent failure.
- Stages 2 (LLM + web research), 3 (ElevenLabs), and 4 (footage APIs,
  CLIP/Whisper model weights) need outbound internet to services not
  available in the sandbox this was built in, so those couldn't be
  exercised live end-to-end here — they're fully coded and unit-tested,
  and you'll do the first live run of those specific stages once your
  API keys and normal internet access are in place. `check_setup.py`
  (or the equivalent `/api/setup/check` used by the web UI) tells you
  exactly which keys are still missing.
- Written for `moviepy>=2.0`'s API. If you have `moviepy<2` installed,
  see the compatibility note at the bottom of `stage5_assembly.py`.

