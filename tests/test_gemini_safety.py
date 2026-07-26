import os
from dotenv import load_dotenv
load_dotenv()

from pipeline.stage2_script import _call_gemini
try:
    print("Testing Gemini safety for political topic...")
    res = _call_gemini("You are writing a script for a video about 'KEKOSONGAN DIPUNCAK : SIAPA YANG BERKUASA JIKA PRESIDEN JATUH?'. Respond ONLY with JSON.")
    print(f"Raw output: {res}")
except Exception as e:
    print(f"Error: {e}")
