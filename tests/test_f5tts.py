import os
import torch
import soundfile as sf
from huggingface_hub import hf_hub_download
from f5_tts.infer.utils_infer import infer_process, load_model, load_vocoder
from f5_tts.model import DiT

def test_f5():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    repo_id = "PapaRazi/Ijazah_Palsu_V2"
    print(f"Downloading model from {repo_id}...")
    ckpt_path = hf_hub_download(repo_id=repo_id, filename="model_last_v2_rev1.safetensors")
    vocab_file = hf_hub_download(repo_id=repo_id, filename="vocab.txt")

    print(f"Ckpt downloaded to: {ckpt_path}")
    
    # Standard F5-TTS Base configuration
    model_cfg = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)
    
    print("Loading model...")
    model = load_model(DiT, model_cfg, ckpt_path, vocab_file=vocab_file, device=device)
    print("Loading vocoder...")
    vocoder = load_vocoder(is_local=False) # downloads vocos

    ref_audio = "suara_gua.wav"
    ref_text = "Tiga, dua, satu. Action!" # Just some random short string, f5tts works better if it matches the ref audio text, but let's try with dummy text
    gen_text = "Halo, ini adalah tes sintesis suara F5-TTS menggunakan model berbahasa Indonesia."
    
    if not os.path.exists(ref_audio):
        print(f"{ref_audio} not found, generating dummy audio to test pipeline.")
        import numpy as np
        sf.write(ref_audio, np.zeros(16000), 16000)
    
    print("Synthesizing...")
    # infer_process signature:
    # (ref_audio, ref_text, gen_text, model_obj, vocoder, mel_spec_type='vocos', show_info=<built-in function print>, progress=<module 'tqdm'>, target_rms=0.1, cross_fade_duration=0.15, nfe_step=32, cfg_strength=2.0, sway_sampling_coef=-1.0, speed=1.0, fix_duration=None, device='cuda')
    audio_out, sr, spect = infer_process(
        ref_audio, 
        ref_text, 
        gen_text, 
        model, 
        vocoder,
        device=device
    )
    
    sf.write("test_f5_out.wav", audio_out, sr)
    print("Success! Saved test_f5_out.wav")

if __name__ == "__main__":
    test_f5()
