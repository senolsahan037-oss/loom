"""Bir kaydin chop'lanacak kadar saglam olup olmadigi.

DIKKAT -- bu malzemede TIZIN OLMAMASI KUSUR DEGIL. Aranan sey zaten eski
analog transferler; 1974 baskisi bir Misir plagi 7.5 kHz'de biter ve bu onun
imzasidir. "Tepesi bos, demek ki kotu" demek, aranan seyi elemek olurdu.

Kusur olan sey KESIGIN DIKLIGI. Kalibrasyon (ayni kaydin bilerek bozulmus
surumleri, 3 kHz ustundeki en dik 500 Hz'lik dusus):

    orijinal   18.9 dB   rolloff 7515 Hz
    64 kbps    26.8 dB   rolloff 7450 Hz
    32 kbps    45.6 dB   rolloff 5082 Hz

Kodek kestiginde dik bir duvar birakir; analog yumusak iner. Rolloff tek
basina ayirt etmiyor (orijinal ve 64k ayni yerde bitiyor), diklik ayirt ediyor.

Asiri limitleme (loudness war) spektrumda hic iz birakmiyor -- bilerek ezilmis
surum orijinalle ayni spektrumu verdi, fark sadece crest'te cikti (14.2'ye
karsi 16.0). Crest tek basina zayif bir sinyal, cunku yogun bir 1972 mono
45'ligi de dogal olarak 13 civari olabiliyor; o yuzden ELEMEZ, sadece isaretler.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import warnings

import numpy as np
import scipy.signal

if not hasattr(scipy.signal, "hann"):
    scipy.signal.hann = scipy.signal.windows.hann
warnings.filterwarnings("ignore")

import librosa  # noqa: E402

SR = 44100
N_FFT = 4096
WINDOW_SECONDS = 40.0
# Orta bant referansi: spektrum buna gore olculur.
REF_LOW, REF_HIGH = 300.0, 2000.0
ROLLOFF_DB = -45.0
# Kesigi ararken bu frekansin altina bakilmaz; asagidaki dik inisler muzigin
# kendi spektral egimidir, kodek degil.
CLIFF_FLOOR_HZ = 3000.0
# Ve bunun USTUNE de bakilmaz. YouTube sesi Opus'a yeniden kodluyor ve 20 kHz
# civarinda bir tavan birakiyor -- HER videoda var, muzikal olarak anlamsiz.
# Ilk surumde kesik tum bant boyunca arandigi icin 53 kayit bu tavan yuzunden
# "kodek hasarli" diye elendi; hepsinin kesigi 19983 Hz'deydi. Gercek hasar
# asagida olur: olculen kotu ornekler 3219 Hz ve 4630 Hz'de kesiliyordu.
CLIFF_CEILING_HZ = 16000.0
CLIFF_SPAN_HZ = 500.0

# Kalibrasyondan: 32 kbps 45.6, 64 kbps 26.8, orijinal 18.9.
CLIFF_BAD = 35.0        # bunun ustu acikca hasarli
CLIFF_SUSPECT = 25.0    # 64 kbps bandi -- isaretlenir, elenmez
# Rolloff bunun altindaysa malzeme dar. TEK BASINA ELEME SEBEBI DEGIL:
# 1950'lerin bir 78'lik transferi de 5 kHz'de biter ve sahicidir. Dar bant
# ancak DIK BIR DUVARLA birlikte geldiginde kodek hasarina isaret eder --
# yumusak inisle geldiginde sadece eski bir kayittir.
ROLLOFF_NARROW_HZ = 6000.0
# Gercek kusurlar.
MAX_CLIP_RATIO = 0.0005
MIN_PEAK_DBFS = -30.0
MAX_SILENCE_SHARE = 0.35
CREST_TIGHT_DB = 12.0   # bunun altini isaretle, eleme


@dataclass
class Quality:
    path: str
    ok: bool
    error: str | None = None
    rolloff_hz: float | None = None
    cliff_db: float | None = None
    cliff_at_hz: float | None = None
    crest_db: float | None = None
    peak_dbfs: float | None = None
    clip_ratio: float | None = None
    silence_share: float | None = None
    verdict: str | None = None
    flags: tuple = ()

    def as_dict(self) -> dict:
        d = asdict(self)
        d["flags"] = list(self.flags)
        return d


def measure(path: str) -> Quality:
    try:
        total = librosa.get_duration(path=path)
    except Exception as exc:
        return Quality(path=str(path), ok=False, error=f"okunamadi: {exc}")
    offset = min(30.0, total * 0.25) if total > 30 else 0.0
    try:
        y, _ = librosa.load(path, sr=SR, mono=True, offset=offset, duration=WINDOW_SECONDS)
    except Exception as exc:
        return Quality(path=str(path), ok=False, error=f"cozulemedi: {exc}")
    if y.size < SR * 5:
        return Quality(path=str(path), ok=False, error="pencere cok kisa")

    spec = np.abs(librosa.stft(y, n_fft=N_FFT))
    freqs = librosa.fft_frequencies(sr=SR, n_fft=N_FFT)
    energy = np.square(spec).mean(axis=1)
    band = (freqs >= REF_LOW) & (freqs <= REF_HIGH)
    reference = float(energy[band].mean()) or 1e-12
    db = 10.0 * np.log10(np.maximum(energy / reference, 1e-12))

    above = np.flatnonzero((freqs > 1000.0) & (db > ROLLOFF_DB))
    rolloff = float(freqs[above[-1]]) if above.size else 0.0

    step = max(1, int(CLIFF_SPAN_HZ / (freqs[1] - freqs[0])))
    hi = [i for i in np.flatnonzero((freqs > CLIFF_FLOOR_HZ) & (freqs < CLIFF_CEILING_HZ))
          if i + step < len(freqs)]
    if hi:
        cliff, cliff_at = max((float(db[i] - db[i + step]), float(freqs[i])) for i in hi)
    else:
        cliff, cliff_at = 0.0, 0.0

    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(np.square(y))))
    crest = 20.0 * np.log10(peak / max(rms, 1e-12)) if peak > 0 else 0.0
    peak_db = 20.0 * np.log10(max(peak, 1e-12))
    clip_ratio = float(np.mean(np.abs(y) >= 0.999))

    frame = max(1, int(SR * 0.05))
    trimmed = y[: (len(y) // frame) * frame]
    frame_rms = np.sqrt(np.mean(np.square(trimmed.reshape(-1, frame)), axis=1))
    silence = float(np.mean(frame_rms < 1e-6))

    flags, fatal = [], []
    if cliff >= CLIFF_BAD:
        fatal.append(f"kodek_kesigi({cliff:.0f}dB@{cliff_at:.0f}Hz)")
    elif cliff >= CLIFF_SUSPECT:
        flags.append(f"dusuk_bitrate_supheli({cliff:.0f}dB)")
    if rolloff < ROLLOFF_NARROW_HZ:
        if cliff >= CLIFF_SUSPECT:
            fatal.append(f"dar_ve_duvarli({rolloff:.0f}Hz, {cliff:.0f}dB)")
        else:
            flags.append(f"dar_bant({rolloff:.0f}Hz)")
    if clip_ratio > MAX_CLIP_RATIO:
        fatal.append(f"clip({clip_ratio:.4f})")
    if peak_db < MIN_PEAK_DBFS:
        fatal.append(f"sessiz({peak_db:.0f}dBFS)")
    if silence > MAX_SILENCE_SHARE:
        fatal.append(f"bosluk({silence:.2f})")
    if crest < CREST_TIGHT_DB:
        flags.append(f"sikistirilmis(crest {crest:.1f})")

    return Quality(
        path=str(path), ok=True,
        rolloff_hz=round(rolloff, 0), cliff_db=round(cliff, 1),
        cliff_at_hz=round(cliff_at, 0), crest_db=round(crest, 1),
        peak_dbfs=round(peak_db, 1), clip_ratio=round(clip_ratio, 5),
        silence_share=round(silence, 4),
        verdict=("elendi: " + ", ".join(fatal)) if fatal else "gecti",
        flags=tuple(flags),
    )
