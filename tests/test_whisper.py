import threading
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

def run():
    try:
        print("Importing torch...")
        import torch
        print("Importing faster_whisper...")
        from faster_whisper import WhisperModel
        print("Loading model...")
        m = WhisperModel("tiny", device="cuda", compute_type="float16")
        print("Transcribing...")
        res = m.transcribe("cache/audio/narration.wav", word_timestamps=True)
        for seg in res[0]:
            print(seg.text)
            break
        print("Done")
    except Exception as e:
        print(f"Error: {e}")

t = threading.Thread(target=run)
t.start()
t.join()
