import threading
import pyttsx3
import os

def run():
    try:
        print("Running pyttsx3...")
        engine = pyttsx3.init()
        engine.save_to_file("Testing one two three", "test.wav")
        engine.runAndWait()
        
        print("Importing torch...")
        import torch
        print("Importing faster_whisper...")
        from faster_whisper import WhisperModel
        print("Loading model...")
        m = WhisperModel("tiny", device="cuda", compute_type="float16")
        print("Transcribing...")
        res = m.transcribe("test.wav", word_timestamps=True)
        for seg in res[0]:
            print(seg.text)
            break
        print("Done")
    except Exception as e:
        print(f"Error: {e}")

t = threading.Thread(target=run)
t.start()
t.join()
