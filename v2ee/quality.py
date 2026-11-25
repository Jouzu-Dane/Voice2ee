"""Optional (Full version): DNSMOS, Microsoft's speech quality model from the DNS Challenge. It predicts the
1-5 score human listeners give for speech quality (SIG), background noise (BAK) and overall (OVRL), without
needing a clean reference. Runs on onnxruntime; the model file is downloaded by `install.sh --full`."""
import numpy as np
from scipy import signal

from .config import SR

MODEL_URL = "https://github.com/microsoft/DNS-Challenge/raw/master/DNSMOS/DNSMOS/sig_bak_ovr.onnx"
SEG = 144160                                   # 9.01 s at 16 kHz, what the model expects

# polynomial calibration of the raw outputs to MOS (non-personalised), from the DNS Challenge reference script
P_SIG = np.poly1d([-0.08397278, 1.22083953, 0.0052439])
P_BAK = np.poly1d([-0.13166888, 1.60915514, -0.39604546])
P_OVR = np.poly1d([-0.06766283, 1.11546468, 0.04602535])


def model_path():
    from .config import DATA_DIR
    return DATA_DIR / "models" / "sig_bak_ovr.onnx"


def status():
    """(available, reason)."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False, "onnxruntime not installed"
    if not model_path().exists():
        return False, f"model missing ({model_path()})"
    return True, "ready"


class DNSMOS:
    def __init__(self, path=None, session=None):
        if session is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            session = ort.InferenceSession(str(path or model_path()), sess_options=opts,
                                           providers=["CPUExecutionProvider"])
        self.s = session
        self.inp = self.s.get_inputs()[0].name

    def score(self, x, fs=SR):
        x = np.asarray(x, np.float64)
        if x.ndim == 2:
            x = x.mean(axis=1)
        y = signal.resample_poly(x, 1, fs // 16000).astype(np.float32)
        while len(y) < SEG:                    # short clips are repeated, as the reference script does
            y = np.concatenate([y, y])
        hops = max(1, int((len(y) - SEG) / 16000) + 1)
        raw = []
        for h in range(hops):
            seg = y[h * 16000: h * 16000 + SEG]
            if len(seg) < SEG:
                break
            out = self.s.run(None, {self.inp: seg[np.newaxis, :]})[0]
            raw.append(np.asarray(out).reshape(-1)[:3])
        sig, bak, ovr = np.mean(raw, axis=0)
        return {"sig": float(P_SIG(sig)), "bak": float(P_BAK(bak)), "ovrl": float(P_OVR(ovr))}


def load():
    ok, _ = status()
    if not ok:
        return None
    try:
        return DNSMOS()
    except Exception:
        return None
