"""Builds realistic test recordings from flite speech synthesis (ffmpeg with --enable-libflite):
room echo, close-mic bass, breath pops, keyboard/mouse clicks and a -65 dBFS noise floor, scaled to a real
PodMic measurement (speech about -37 dBFS, peaks about -12). Usage: python3 corpus.py [output_dir]"""
import subprocess, sys, numpy as np
from pathlib import Path
from scipy import signal
from scipy.io import wavfile
SR = 48000
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / ".speech"
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(42)
LINES = [
 "Last Tuesday, Sam sent six short messages to the station about the strange silver ship.",
 "Honestly, nobody believed his story, but he kept telling it with a smile.",
 "Okay chat, let's see if we can push past the boss this time.",
 "Peter picked a peck of pickled peppers, and that's basically my whole strategy.",
 "Shh, she's sneaking past the sentries. Stay still.",
 "Big push, big push, go go go!",
 "Thanks so much for the follow, seriously, welcome in.",
 "Press space to skip, then pick the purple potion.",
 "So, what do you think? Should we save or spend it?",
 "That was absolutely ridiculous. I cannot believe that worked.",
 "Six silly snakes sat silently, sipping sweet sodas.",
 "Bob bought a big blue boat, but it barely floats.",
 "Alright, quick break, I'll be right back in a sec.",
 "The settings menu is under options, then audio, then voice.",
 "Please subscribe if you enjoyed the stream tonight.",
 "Wait, wait, wait. Did you hear that? Something's behind us.",
 "Tell me what you think, and keep talking like you normally would.",
 "Stop! Stop stop stop, that's a trap!",
 "Speaking softly now, just testing how quiet parts sound.",
 "Peppers, potatoes, parsley and pasta, perfect for a Saturday.",
]
VOICES = ["awb", "rms", "kal16"]
def tts(text, voice):
    t = text.replace("'", "").replace(":", " ")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"flite=text='{t}':voice={voice}",
           "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype="<f4").astype(np.float64)
def db(x): return 20*np.log10(x+1e-12)
def pink(n):
    w = rng.standard_normal(n); b, a = [0.049922035, -0.095993537, 0.050612699, -0.004408786], [1, -2.494956002, 2.017265875, -0.522189400]
    p = signal.lfilter(b, a, w); return p/np.std(p)
def room_ir():
    n = int(0.6*SR); t = np.arange(n)/SR
    ir = rng.standard_normal(n)*np.exp(-6.9*t/0.45)  # RT60 ~0.45 s
    ir = signal.sosfilt(signal.butter(2, 4000, "low", fs=SR, output="sos"), ir)
    ir[:int(0.004*SR)] = 0; ir /= np.sqrt(np.sum(ir**2)); h = np.zeros(n); h[0] = 1.0
    return h + 0.22*ir      # direct + reverb (~ -13 dB): audibly roomy but close-mic'd
def proximity(x):  # dynamic mic up close: +6 dB low shelf ~200 Hz, slight 3 kHz presence
    b, a = signal.iirfilter(2, 250, btype="low", fs=SR, ftype="butter")
    low = signal.lfilter(b, a, x)
    return x + 0.9*low
def pop():
    n = int(0.06*SR); t = np.arange(n)/SR
    return np.sin(2*np.pi*55*t)*np.exp(-t/0.015)*rng.uniform(0.6, 1.0)
def click():
    n = int(0.012*SR); t = np.arange(n)/SR
    c = rng.standard_normal(n)*np.exp(-t/0.002)
    c = signal.sosfilt(signal.butter(2, [800, 7000], "band", fs=SR, output="sos"), c)
    return c/np.abs(c).max()
def take(seed, n_lines=8, secs_target=None):
    r = np.random.default_rng(seed)
    idx = r.choice(len(LINES), n_lines, replace=False)
    parts = []
    for k, i in enumerate(idx):
        v = VOICES[r.integers(len(VOICES))]
        x = tts(LINES[i], v); x /= np.sqrt(np.mean(x[np.abs(x) > 0.01*np.abs(x).max()]**2))
        g = r.uniform(-7, 4)                      # sentence-to-sentence loudness
        if "Stop!" in LINES[i] or "go go" in LINES[i]: g = 7.0   # shouting
        if "softly" in LINES[i]: g = -10.0
        x *= 10**(g/20)
        if r.random() < 0.5:                      # breath pop on a plosive near the start
            p = pop()*3.0*10**(g/20); o = int(r.uniform(0.0, 0.3)*SR); o = min(o, len(x)-len(p)); x[o:o+len(p)] += p
        parts.append(x); parts.append(np.zeros(int(r.uniform(0.35, 0.9)*SR)))
    y = np.concatenate(parts)
    y = proximity(y)
    y = signal.fftconvolve(y, room_ir())[:len(y)]
    return y
def finish(y, seed, clicks=True, speech=True):
    r = np.random.default_rng(seed+100)
    out = y.copy()
    if speech:  # scale: voiced RMS -36.7 dBFS (user's measurement)
        fr = out[: len(out)//2400*2400].reshape(-1, 2400); lv = db(np.sqrt((fr**2).mean(1)))
        v = lv > np.percentile(lv, 95) - 30
        cur = db(np.sqrt((fr[v]**2).mean())); out *= 10**((-32.0 - cur)/20)
    if clicks:  # keyboard/mouse at about quiet-speech level, both in gaps and over speech
        for _ in range(int(len(out)/SR*2.5)):
            c = click()*10**(r.uniform(-44, -32)/20); o = r.integers(0, len(out)-len(c)); out[o:o+len(c)] += c
    out += pink(len(out))*10**(-65/20)
    return out.astype(np.float32)
for i in range(6):
    sp = finish(take(i), i)
    wavfile.write(OUT/f"speech_{i}.wav", SR, sp)
    print(f"speech_{i}.wav  {len(sp)/SR:4.1f}s  peak {db(np.abs(sp).max()):6.1f} dBFS")
wavfile.write(OUT/"noise.wav", SR, finish(np.zeros(4*SR), 99, clicks=False, speech=False))
wavfile.write(OUT/"clicks.wav", SR, finish(np.zeros(6*SR), 98, clicks=True, speech=False))
