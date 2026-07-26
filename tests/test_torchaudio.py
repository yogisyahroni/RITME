import torchaudio

_original = torchaudio.load
def _patched(filepath, *args, **kwargs):
    kwargs['backend'] = 'soundfile'
    return _original(filepath, *args, **kwargs)

torchaudio.load = _patched

print(torchaudio.load('suara_gua.wav'))
