"""
Stage 2 — Research + Script Generation

Given a topic, this stage:
  1. Runs a free web search (DuckDuckGo, no API key needed) to gather
     source material.
  2. Sends the topic + sources to an LLM (Anthropic/OpenAI/Gemini —
     you choose in .env) asking it to write a narration script whose
     pacing matches the template extracted in Stage 1.

Output: a list of script "segments", each with narration text and a
handful of visual keywords Stage 4 will use to source matching footage.
"""
import json
import re
from pathlib import Path

from config import LLM_PROVIDER, ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENAI_MODEL_NAME, GEMINI_API_KEY, require

SCRIPT_STYLES_DIR = Path(__file__).resolve().parent / "script_styles"


def list_script_styles() -> list[dict]:
    """Returns [{"style_id", "display_name", "description"}, ...] for every
    preset in pipeline/script_styles/ — used by the web UI's style picker."""
    styles = []
    for p in sorted(SCRIPT_STYLES_DIR.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        styles.append({
            "style_id": data["style_id"],
            "display_name": data["display_name"],
            "description": data["description"],
        })
    return styles


def load_script_style(style_id: str) -> dict:
    path = SCRIPT_STYLES_DIR / f"{style_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Script style '{style_id}' not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def web_research(topic: str, max_results: int = 5) -> list[dict]:
    """
    Free web research via DuckDuckGo (no API key required).
    Returns a list of {"title", "snippet", "url"}.
    Requires: pip install duckduckgo-search
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        raise RuntimeError(
            "duckduckgo-search not installed. Run: pip install duckduckgo-search"
        )

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(topic, max_results=max_results):
            results.append({
                "title": r.get("title", ""),
                "snippet": r.get("body", ""),
                "url": r.get("href", ""),
            })
    return results


def _build_prompt(topic: str, sources: list[dict], template: dict, target_segments: int) -> str:
    source_block = "\n".join(
        f"- {s['title']}: {s['snippet']} (source: {s['url']})" for s in sources
    ) or "(no external sources found — rely on general knowledge, but flag this)"

    pacing = template["pacing"]
    narration = template.get("narration")
    narration_hint = (
        f"Target narration pace: ~{narration['words_per_minute']} words/minute, "
        f"average sentence length ~{narration['avg_words_per_sentence']} words."
        if narration else
        "No narration pacing detected in the reference template — use a natural, "
        "conversational documentary pace (~150 words/minute)."
    )

    return f"""You are writing a narration script for a short documentary-style video about:
"{topic}"

Reference material gathered from the web:
{source_block}

Style constraints (extracted from a reference video template):
- The reference video has {pacing['shot_count']} shots, averaging {pacing['avg_shot_duration']}s per shot
  (min {pacing['min_shot_duration']}s, max {pacing['max_shot_duration']}s).
- {narration_hint}

Write the script as exactly {target_segments} segments. Each segment should be
narratable in roughly {pacing['avg_shot_duration']}–{pacing['avg_shot_duration']*2:.1f} seconds.

For EACH segment provide:
1. "text": the narration sentence(s), factual and grounded in the sources above.
   Do not fabricate quotes or statistics that aren't in the sources.
2. "keywords": 2-4 concrete, visual, searchable keywords/phrases describing what
   footage should appear on screen during this segment (e.g. "solar panel field",
   "stock market chart falling", "Jakarta traffic jam"). Prefer generic, filmable
   scenes over anything that would require footage of a specific named private
   individual.

Respond ONLY with valid JSON in this exact shape, no other text:
{{
  "music_mood": "<one of: upbeat, calm, tense, epic, sad — the background-music mood that fits this script>",
  "segments": [
    {{"text": "...", "keywords": ["...", "..."]}},
    ...
  ]
}}
"""


def _distribute_segments(target_segments: int, acts: list[dict]) -> list[int]:
    """
    Splits target_segments across acts proportionally, guaranteeing the
    per-act counts always sum to EXACTLY target_segments.

    Naively rounding each act's share independently (max(1, round(target *
    proportion))) can under- or over-count the total — e.g. target=6 across
    5 acts with proportions [.17,.23,.23,.23,.14] gives raw shares
    [1.02, 1.38, 1.38, 1.38, 0.84], every one of which rounds DOWN to 1
    individually (none reaches x.5) even though they sum to 6. That
    silently asked the LLM for 5 segments when the person asked for 6.

    This uses the largest-remainder method instead: take the floor of each
    share, then hand out the leftover one-by-one to whichever act lost the
    most to flooring (highest fractional remainder first) — same idea as
    apportioning parliament seats fairly.
    """
    n = len(acts)
    if target_segments <= 0:
        return [0] * n

    raw = [target_segments * a["proportion"] for a in acts]
    ensure_min = target_segments >= n  # only guarantee every act appears if that's even possible

    floors = [int(r) for r in raw]
    if ensure_min:
        floors = [max(1, f) for f in floors]

    remainder = target_segments - sum(floors)
    frac_order = sorted(range(n), key=lambda i: raw[i] - int(raw[i]), reverse=True)

    i = 0
    while remainder > 0:
        floors[frac_order[i % n]] += 1
        remainder -= 1
        i += 1
    while remainder < 0:
        floor_bound = 1 if ensure_min else 0
        candidates = [j for j in range(n) if floors[j] > floor_bound]
        if not candidates:
            break
        j = min(candidates, key=lambda j: raw[j] - int(raw[j]))
        floors[j] -= 1
        remainder += 1

    return floors


def _build_styled_chunk_prompt(topic: str, sources: list[dict], template: dict, style: dict, act: dict, n_seg: int, previous_context: list[dict], language: str, custom_script: str | None = None, total_segments: int = 8) -> str:
    source_block = "\n".join(
        f"- {s['title']}: {s['snippet']} (source: {s['url']})" for s in sources
    ) or "(no external sources found — rely on general knowledge, but flag this)"

    pacing_instruction = "ATURAN MUTLAK: Tulis narasi berupa SATU PARAGRAF PANJANG (sekitar 5 sampai 7 kalimat yang padat dan mendalam) untuk SETIAP segmennya. PENTING: Jawab LANGSUNG dengan JSON, JANGAN MENGHITUNG KATA ATAU MENULIS PEMIKIRAN DI LUAR JSON!"
    
    context_str = ""
    if previous_context:
        prev_lines = "\n".join([f"- {s.get('text', '')}" for s in previous_context])
        context_str = f"\nSebagai konteks, berikut adalah narasi dari babak sebelumnya:\n{prev_lines}\nLANJUTKAN narasi tersebut agar mengalir secara natural.\n"

    user_script_directive = ""
    if custom_script:
        user_script_directive = f"\nATURAN MUTLAK: Pengguna memberikan NASKAH KUSTOM:\n\"\"\"\n{custom_script}\n\"\"\"\nAnda HARUS mengikuti isi, pesan, dan struktur dari naskah tersebut. JIKA naskah tersebut menggunakan bahasa yang BERBEDA dari {language}, Anda HARUS menerjemahkannya/menulis ulangnya ke dalam bahasa {language}. Jangan biarkan dalam bahasa aslinya jika tidak sesuai kode {language}!\n"
    elif len(topic) > 100:
        user_script_directive = f"\nATURAN MUTLAK: Pengguna memberikan instruksi/naskah spesifik pada topik. Anda HARUS mengikuti narasi dan struktur yang diberikan pengguna (jangan mengarang narasi baru jika sudah ada).\n"

    return f"""Anda menulis naskah narasi untuk video esai bergaya "{style['display_name']}" tentang topik: "{topic}"

Nada/tone: {style['tone']}
{style.get('title_guidance', '')}

Materi riset dari web:
{source_block}
{context_str}
Tugas Anda SAAT INI adalah menulis naskah HANYA untuk Babak "{act['name']}".
Fungsi narasi babak ini: {act['narrative_function']}
Arahan visual babak ini: {act['visual_direction']}

BAHASA / LANGUAGE KODE: {language}
ATURAN BAHASA: Naskah/narasi (field "text") HARUS 100% ditulis menggunakan bahasa sesuai kode di atas (misal 'id' = Indonesia, 'en-US' = English). Jangan pernah mencampur bahasa!
{user_script_directive}

TOTAL SEGMEN UNTUK BABAK INI HARUS PERSIS {n_seg} segmen. Jangan kurang atau lebih.

{pacing_instruction}

Untuk TIAP segmen berikan:
1. "act": "{act['id']}"
2. "text": kalimat narasi — faktual, berdasar sumber riset di atas.
3. "keywords": 2-4 keyword visual konkret buat sourcing footage.

Jawab HANYA dengan JSON valid, format persis:
{{
  "music_mood": "<salah satu dari: upbeat, calm, tense, epic, sad — mood musik latar yang cocok untuk naskah ini>",
  "segments": [
    {{"act": "{act['id']}", "text": "...", "keywords": ["...", "..."]}},
    ...
  ]
}}
"""


def _build_chunk_prompt(topic: str, sources: list[dict], template: dict, n_seg: int, previous_context: list[dict], chunk_idx: int, total_chunks: int, language: str, custom_script: str | None = None) -> str:
    source_block = "\n".join(
        f"- {s['title']}: {s['snippet']} (source: {s['url']})" for s in sources
    ) or "(no external sources found — rely on general knowledge, but flag this)"

    pacing = template["pacing"]
    narration = template.get("narration")
    narration_hint = (
        f"Target narration pace: ~{narration['words_per_minute']} words/minute, "
        f"average sentence length ~{narration['avg_words_per_sentence']} words."
        if narration else
        "No narration pacing detected in the reference template — use a natural, "
        "conversational documentary pace (~150 words/minute)."
    )

    context_str = ""
    if previous_context:
        prev_lines = "\n".join([f"- {s.get('text', '')}" for s in previous_context])
        context_str = f"\nSebagai konteks, berikut adalah narasi dari bagian sebelumnya:\n{prev_lines}\nLANJUTKAN narasi tersebut agar mengalir secara natural.\n"

    user_script_directive = ""
    if custom_script:
        user_script_directive = f"\nCRITICAL RULE: The user has provided a CUSTOM SCRIPT:\n\"\"\"\n{custom_script}\n\"\"\"\nYou MUST adhere to its content, message, and structure. IF the custom script is in a different language than {language}, you MUST translate/rewrite it entirely into {language}!\n"
    elif len(topic) > 100:
        user_script_directive = f"\nCRITICAL RULE: The user has provided detailed instructions/script in the topic. You MUST adhere to their content and structure.\n"

    return f"""You are writing a narration script for a short documentary-style video about:
"{topic}"

Reference material gathered from the web:
{source_block}

Style constraints (extracted from a reference video template):
- The reference video has {pacing['shot_count']} shots, averaging {pacing['avg_shot_duration']}s per shot
  (min {pacing['min_shot_duration']}s, max {pacing['max_shot_duration']}s).
- {narration_hint}

LANGUAGE CODE: {language}
LANGUAGE RULE: The narration text MUST be written 100% in the language corresponding to the code above (e.g., 'id' = Indonesian, 'en-US' = English). Do not mix languages!
{user_script_directive}
{context_str}
This is part {chunk_idx} of {total_chunks}. Write EXACTLY {n_seg} segments for this part.
STRICT RULE: Write ONE LONG PARAGRAPH (about 5 to 7 dense and in-depth sentences) for EACH segment! IMPORTANT: Reply DIRECTLY with JSON, DO NOT COUNT WORDS OR WRITE THOUGHTS OUTSIDE JSON!

For EACH segment provide:
1. "text": the narration sentence(s), factual and grounded in the sources above.
2. "keywords": 2-4 concrete, visual, searchable keywords/phrases describing what
   footage should appear on screen.

Respond ONLY with valid JSON in this exact shape, no other text:
{{
  "music_mood": "<one of: upbeat, calm, tense, epic, sad — the background-music mood that fits this script>",
  "segments": [
    {{"text": "...", "keywords": ["...", "..."]}},
    ...
  ]
}}
"""


def _chunk_custom_script(script_text: str, max_chars: int = 2500) -> list[str]:
    import re
    # Remove lines containing Visual/Audio directions, JUDUL, or BAB
    script_text = re.sub(r"(?im)^(JUDUL|BAB|Visual/Audio|Visual|Audio|Durasi).*$", "", script_text)
    # Remove Narration prefixes but keep the text
    script_text = re.sub(r"(?im)^(Narasi Pembuka|Pernyataan Tesis|Narasi):\s*", "", script_text)
    
    # Split by double newline (paragraphs) or single newline if no double
    if '\n\n' in script_text:
        paragraphs = [p.strip() for p in script_text.split('\n\n') if p.strip()]
    else:
        paragraphs = [p.strip() for p in script_text.split('\n') if p.strip()]
        
    chunks = []
    current_chunk = ""
    for p in paragraphs:
        if len(current_chunk) + len(p) > max_chars and current_chunk:
            chunks.append(current_chunk)
            current_chunk = p
        else:
            current_chunk += "\n\n" + p if current_chunk else p
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

def _build_exact_script_prompt(script_chunk: str, language: str) -> str:
    return f"""Anda adalah asisten AI. Pengguna telah menyediakan sebuah naskah video secara utuh.
Tugas Anda HANYA memecah naskah ini menjadi segmen-segmen (shots) untuk video, DAN menerjemahkannya jika diperlukan.
TIDAK BOLEH meringkas atau memotong makna naskah. Teks narasi di setiap segmen harus mencakup keseluruhan makna dari naskah asli tanpa ada yang terlewat.

NASKAH ASLI:
\"\"\"
{script_chunk}
\"\"\"

ATURAN BAHASA: Target bahasa adalah {language}. JIKA naskah asli berbeda bahasa dengan {language}, Anda WAJIB MENERJEMAHKAN SELURUH TEKS ke dalam bahasa {language}. Jika bahasanya sudah sama, pertahankan teks aslinya.

Untuk TIAP segmen berikan:
1. "act": isi dengan "custom"
2. "text": kalimat narasi asli dari naskah di atas (bagilah per 1-2 kalimat untuk setiap segmen).
3. "keywords": 2-4 keyword visual konkret (dalam bahasa Inggris, untuk pencarian footage/video stock) yang menggambarkan apa yang diucapkan di segmen tersebut. (misal "solar panel field", "stock market chart falling").

Jawab HANYA dengan JSON valid, format persis:
{{
  "music_mood": "<salah satu dari: upbeat, calm, tense, epic, sad — mood musik latar yang cocok untuk naskah ini>",
  "segments": [
    {{"act": "custom", "text": "...", "keywords": ["...", "..."]}},
    ...
  ]
}}
"""


def generate_script(topic: str, template: dict, target_segments: int = 8,
                     research_results: list[dict] | None = None,
                     style_id: str | None = None, language: str = "id", custom_script: str | None = None) -> dict:
    """
    Full Stage 2 entry point. Returns a dict: {"topic":..., "segments": [...]}
    and also saves it to templates/<topic-slug>_script.json for inspection.
    """
    sources = research_results if research_results is not None else web_research(topic)

    provider = LLM_PROVIDER.lower()
    def call_provider(prompt):
        if provider == "anthropic": return _call_anthropic(prompt)
        elif provider == "openai": return _call_openai(prompt)
        elif provider == "gemini": return _call_gemini(prompt)
        raise RuntimeError(f"Unknown LLM_PROVIDER: {provider}")

    segments = []
    previous_context = []
    music_mood = None

    if custom_script:
        # Bypass template pacing completely, chunk the script text and extract segments exactly
        chunks = _chunk_custom_script(custom_script)
        for i, text_chunk in enumerate(chunks):
            print(f"[stage2] Generating script chunk {i+1}/{len(chunks)} from custom script...")
            prompt = _build_exact_script_prompt(text_chunk, language)
            text = call_provider(prompt)
            new_segs, meta = _parse_json_response(text)
            if music_mood is None and isinstance(meta.get("music_mood"), str) and meta["music_mood"]:
                music_mood = meta["music_mood"]
            
            for s in new_segs:
                if "act" not in s:
                    s["act"] = "custom"
            segments.extend(new_segs)
    else:
        if style_id:
            style = load_script_style(style_id)
            counts = _distribute_segments(target_segments, style["acts"])
            
            for act, n_seg in zip(style["acts"], counts):
                if n_seg <= 0: continue
                rem = n_seg
                while rem > 0:
                    c = min(2, rem)
                    print(f"[stage2] Generating script chunk for Act: {act['name']} ({c} segments)...")
                    prompt = _build_styled_chunk_prompt(topic, sources, template, style, act, c, previous_context, language, custom_script, target_segments)
                    text = call_provider(prompt)
                    new_segs, meta = _parse_json_response(text)
                    if music_mood is None and isinstance(meta.get("music_mood"), str) and meta["music_mood"]:
                        music_mood = meta["music_mood"]
                    
                    for s in new_segs:
                        s["act"] = act["id"]
                    segments.extend(new_segs)
                    
                    if new_segs:
                        previous_context = new_segs[-2:]
                    rem -= c
        else:
            chunks = []
            rem = target_segments
            while rem > 0:
                c = min(2, rem)
                chunks.append(c)
                rem -= c
                
            for i, c in enumerate(chunks):
                chunk_idx = i + 1
                print(f"[stage2] Generating script chunk {chunk_idx}/{len(chunks)} ({c} segments)...")
                prompt = _build_chunk_prompt(topic, sources, template, c, previous_context, chunk_idx, len(chunks), language, custom_script)
                text = call_provider(prompt)
                new_segs, meta = _parse_json_response(text)
                if music_mood is None and isinstance(meta.get("music_mood"), str) and meta["music_mood"]:
                    music_mood = meta["music_mood"]
                segments.extend(new_segs)
                
                if new_segs:
                    previous_context = new_segs[-2:]

    if not custom_script and len(segments) != target_segments:
        print(f"[stage2] WARNING: asked for {target_segments} segments, "
              f"LLM returned {len(segments)}.")

    # Fase 1.2: fall back to a keyword heuristic when the LLM didn't say.
    if music_mood is None:
        try:
            from pipeline.stage_music import guess_music_mood
            music_mood = guess_music_mood(" ".join(str(s.get("text", "")) for s in segments))
        except Exception:
            music_mood = "calm"
        print(f"[stage2] LLM didn't provide music_mood — heuristic picked '{music_mood}'.")

    for s in segments:
        s["music_mood"] = music_mood

    result = {"topic": topic, "sources": sources, "segments": segments,
              "style_id": style_id, "music_mood": music_mood}

    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    out_path = Path("templates") / f"{slug}_script.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[stage2] Script saved to {out_path} ({len(segments)} segments)")

    return result


def _parse_json_response(text: str) -> tuple[list[dict], dict]:
    """
    Parse LLM output into (segments, meta). `meta` carries script-level
    fields the LLM may add — today that's `music_mood` (Fase 1.2: the
    background-music mood fitting this script).
    """
    if text is None:
        raise RuntimeError("Gagal mendapatkan respons dari AI (kemungkinan terkena Safety Filter atau jaringan terputus). Silakan coba lagi.")
    # Strip <think> blocks if present (from models like DeepSeek R1)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    
    # LLMs sometimes wrap JSON in markdown fences — strip those first
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    
    # Sometimes there's extra text before { or after }, try to extract just the JSON
    json_match = re.search(r"(\{.*\})", cleaned, flags=re.DOTALL)
    if json_match:
        cleaned = json_match.group(1)
        
    try:
        data = json.loads(cleaned)
        segments = data.get("segments", [])
        if not isinstance(segments, list):
            raise ValueError("'segments' key missing or not a list")
        meta = {k: v for k, v in data.items() if k != "segments"}
        return segments, meta
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        if "User Safety" in text or "safety" in text.lower():
            raise RuntimeError(
                f"Topik ini ditolak oleh filter keamanan (Safety Filter) dari AI. "
                f"Sistem mendeteksi topik ini terlalu sensitif/politis. "
                f"Silakan gunakan topik yang lebih netral atau ganti model AI."
            )
        raise RuntimeError(
            f"Could not parse LLM output as the expected JSON shape: {e}\n"
            f"Raw output:\n{text}"
        )


def _parse_json_segments(text: str) -> list[dict]:
    """Backward-compatible wrapper: segments only."""
    segments, _meta = _parse_json_response(text)
    return segments


def _call_anthropic(prompt: str) -> str:
    require(ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY",
            "Get one at https://console.anthropic.com/settings/keys")
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("Run: pip install anthropic")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _call_openai(prompt: str) -> str:
    import os
    import time
    from openai import OpenAI
    # Use environment variables specifically configured for the proxy
    base_url = os.environ.get("OPENAI_BASE_URL", os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1"))
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model_name = os.environ.get("OPENAI_MODEL_NAME", "gpt-4-turbo")

    client = OpenAI(
        base_url=base_url,
        api_key=api_key
    )

    max_retries = 3
    last_error = None
    
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "Anda adalah seorang asisten AI yang membalas MURNI dalam format JSON tanpa markdown, backticks, atau teks tambahan apapun."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=3000
            )
            
            if not response.choices:
                raise ValueError("API mengembalikan respon kosong (choices = []).")
                
            return response.choices[0].message.content
            
        except Exception as e:
            last_error = e
            print(f"[stage2] Percobaan {attempt + 1}/{max_retries} gagal: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)  # Tunggu 2 detik sebelum mencoba lagi
            
    # Jika semua percobaan gagal, tampilkan pesan error yang ramah pengguna
    print(f"[stage2] Error final dari API: {last_error}")
    raise RuntimeError("API gratisan sedang tidak stabil atau server penuh setelah beberapa kali percobaan. Silakan klik 'Generate ulang'.")


def _call_gemini(prompt: str) -> str:
    require(GEMINI_API_KEY, "GEMINI_API_KEY",
            "Get one at https://aistudio.google.com/apikey")
    try:
        import google.generativeai as genai
    except ImportError:
        raise RuntimeError("Run: pip install google-generativeai")

    import time
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.0-flash")
    
    max_retries = 3
    last_error = None
    
    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                prompt,
                safety_settings={
                    'HARM_CATEGORY_HARASSMENT': 'BLOCK_NONE',
                    'HARM_CATEGORY_HATE_SPEECH': 'BLOCK_NONE',
                    'HARM_CATEGORY_SEXUALLY_EXPLICIT': 'BLOCK_NONE',
                    'HARM_CATEGORY_DANGEROUS_CONTENT': 'BLOCK_NONE'
                }
            )
            
            # Cek jika terkena safety filter atau kosong
            if not response.parts:
                raise ValueError(f"Respon kosong atau diblokir oleh Safety Filter. (Finish reason: {response.candidates[0].finish_reason if response.candidates else 'Unknown'})")
                
            text = response.text
            if not text:
                raise ValueError("AI mengembalikan string kosong.")
                
            return text
            
        except Exception as e:
            last_error = e
            print(f"[stage2] Gemini attempt {attempt + 1}/{max_retries} failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                
    raise RuntimeError(f"Gagal memanggil Gemini API setelah {max_retries} percobaan. Error: {last_error}")
