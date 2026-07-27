#!/usr/bin/env python3
"""
Run this after filling in your .env file to check:
  1. Which API keys are set (and which stages will/won't work without them)
  2. Whether your network can actually reach every external service the
     pipeline needs — this is what catches corporate/campus firewalls,
     VPNs, or restrictive cloud sandboxes *before* you burn time debugging
     a failed run halfway through Stage 4.

Usage: python check_setup.py
"""
import os
import sys

# See main.py for why this is needed on Windows (legacy console codepage
# can't print the ✅/❌/⚠️ characters this script uses).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

import requests

CHECK_TIMEOUT = 6

# --- 1. API keys ---
KEY_CHECKS = [
    ("ANTHROPIC_API_KEY", "Stage 2 (script writing) — required if LLM_PROVIDER=anthropic", True),
    ("OPENAI_API_KEY", "Stage 2 (script writing) — required if LLM_PROVIDER=openai", False),
    ("GEMINI_API_KEY", "Stage 2 (script writing) — required if LLM_PROVIDER=gemini", False),
    ("PEXELS_API_KEY", "Stage 4 (footage sourcing) — free key, strongly recommended", True),
    ("PIXABAY_API_KEY", "Stage 4 (footage sourcing) — free key, strongly recommended", True),
    ("YOUTUBE_API_KEY", "Stage 4 (CC-licensed YouTube footage) — optional", False),
    ("ELEVENLABS_API_KEY", "Stage 3 (premium narration) — optional, falls back to free pyttsx3", False),
]

# --- 2. Network reachability ---
# (host, why the pipeline needs it)
DOMAIN_CHECKS = [
    ("huggingface.co", "Downloads the CLIP model (Stage 4) and Whisper model (Stage 1/3)"),
    ("cdn-lfs.hf.co", "Hugging Face's CDN for large model files (new domain, replaces cdn-lfs.huggingface.co)"),
    ("api.anthropic.com", "Stage 2 script generation, if LLM_PROVIDER=anthropic"),
    ("api.openai.com", "Stage 2 script generation, if LLM_PROVIDER=openai"),
    ("generativelanguage.googleapis.com", "Stage 2 script generation, if LLM_PROVIDER=gemini"),
    ("api.pexels.com", "Stage 4 footage sourcing"),
    ("pixabay.com", "Stage 4 footage sourcing"),
    ("commons.wikimedia.org", "Stage 4 footage sourcing (no API key needed)"),
    ("archive.org", "Stage 4 footage sourcing (no API key needed)"),
    ("www.googleapis.com", "Stage 4 YouTube CC search, if YOUTUBE_API_KEY is set"),
    ("www.youtube.com", "Stage 4 downloading matched CC-licensed clips via yt-dlp"),
    ("api.elevenlabs.io", "Stage 3 narration, if TTS_PROVIDER=elevenlabs"),
    ("duckduckgo.com", "Stage 2 free web research"),
]


def check_keys():
    print("=" * 60)
    print("1. API KEYS")
    print("=" * 60)
    any_llm_key = any(os.getenv(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"))

    for key_name, purpose, recommended in KEY_CHECKS:
        value = os.getenv(key_name, "")
        if value:
            status = "✅ set"
        elif key_name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY") and any_llm_key:
            status = "—  (not needed, another LLM key is set)"
        elif recommended:
            status = "❌ MISSING"
        else:
            status = "⚠️  not set (optional)"
        print(f"  {key_name:<22} {status:<40} {purpose}")

    if not any_llm_key:
        print("\n  ⚠️  No LLM key set at all — Stage 2 (script writing) will not run.")


def check_network():
    print("\n" + "=" * 60)
    print("2. NETWORK REACHABILITY")
    print("=" * 60)
    print("  (A failure here usually means a firewall/VPN/proxy is blocking")
    print("   the host — see the README section on network access.)\n")

    results = {}
    for host, purpose in DOMAIN_CHECKS:
        try:
            requests.get(f"https://{host}", timeout=CHECK_TIMEOUT)
            results[host] = True
            print(f"  ✅ {host:<38} reachable")
        except requests.exceptions.RequestException as e:
            results[host] = False
            print(f"  ❌ {host:<38} BLOCKED ({type(e).__name__}) — needed for: {purpose}")

    blocked = [h for h, ok in results.items() if not ok]
    if blocked:
        print(f"\n  {len(blocked)} host(s) unreachable. If huggingface.co or cdn-lfs.hf.co "
              f"are on this list, see 'Network & Hugging Face access' in README.md.")
    else:
        print("\n  All hosts reachable. ✅")


if __name__ == "__main__":
    check_keys()
    check_network()
    print("\nDone. Fix any ❌ above, then re-run this script before `python main.py run ...`.")
