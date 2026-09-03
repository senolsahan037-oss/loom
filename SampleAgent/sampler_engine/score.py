"""Aday puanlama.

Uc mod icin ayri olculer. Her puan, olculen buyuklukleri normalize edip
agirliklandirir; bilesenler ciktiya yazilir ki puan kara kutu olmasin.
Olculemeyen bir buyuklukte aday atilir, tahmin edilmez.
"""

from . import compat  # noqa: F401

import librosa
import numpy as np

ANALYSIS_SR = 22050


def _db(x):
    return 20 * np.log10(max(float(x), 1e-12))


def _norm(values):
    """0..1'e olcekle. Hepsi ayniysa hepsi 0.5."""
    arr = np.asarray(values, dtype=float)
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-9:
        return np.full(len(arr), 0.5)
    return (arr - lo) / (hi - lo)


def frame_rms(y, frame=1024, hop=512):
    n = max(0, (len(y) - frame) // hop)
    return np.array([np.sqrt(np.mean(y[i * hop:i * hop + frame] ** 2)) for i in range(n)])


# ---------------------------------------------------------------- warp

def warp_candidates(y, sr, beat_times, bars, beats_per_bar=4):
    """Grid'e oturan N barlik pencereler.

    Puan bilesenleri:
      seviye   - pencerenin RMS'i (sessiz pencere ise yaramaz)
      duzluk   - RMS'in pencere icinde ne kadar sabit oldugu (dongu esitligi)
      armoni   - chroma'nin ne kadar sabit kaldigi (tek armonide kalan dongu doner)
      vurus    - pencere basinin en yakin vurusa uzakligi (grid'e oturma)
    """
    step = max(1, round(float(bars) * beats_per_bar))   # kesirli bar (0.5, 0.375) desteklenir
    if len(beat_times) < step + 1:
        return []

    chroma = librosa.feature.chroma_stft(y=y, sr=sr, hop_length=512)
    hop_t = 512 / sr
    out = []
    for i in range(0, len(beat_times) - step, step):
        start, end = float(beat_times[i]), float(beat_times[i + step])
        a, b = int(start * sr), int(end * sr)
        if b - a < sr // 2:
            continue
        block = y[a:b]
        rms = frame_rms(block)
        if rms.size < 4 or rms.max() <= 0:
            continue
        ca, cb = int(start / hop_t), int(end / hop_t)
        ch = chroma[:, ca:cb]
        if ch.shape[1] < 4:
            continue
        out.append({
            "start": start, "end": end,
            "_level": _db(np.sqrt(np.mean(block ** 2))),
            "_flat": -float(np.std(rms) / (np.mean(rms) + 1e-9)),
            "_harm": -float(np.mean(np.std(ch, axis=1))),
            "_grid": -abs(float(np.median(np.diff(beat_times[i:i + step + 1]))) * step - (end - start)),
        })
    return out


# ------------------------------------------------------------- one-shot

def oneshot_candidates(y, sr, onset_times, max_len=0.7, pre=0.03):
    """Tek atislar.

    Puan bilesenleri:
      atak     - onset gucu (ne kadar keskin giriyor)
      yalitim  - atagin hemen oncesinin sessizligi (temiz kesim)
      sonum    - -30 dB'ye dusme suresi (kisa = temiz one-shot)
      seviye   - tepe seviyesi
    """
    out = []
    for i, t in enumerate(onset_times):
        start = float(t)
        nxt = float(onset_times[i + 1]) if i + 1 < len(onset_times) else start + max_len
        end = min(nxt, start + max_len, len(y) / sr)
        a, b = int(start * sr), int(end * sr)
        if b - a < int(0.02 * sr):
            continue
        block = y[a:b]
        peak = float(np.max(np.abs(block)))
        if peak <= 0:
            continue
        pa = max(0, a - int(pre * sr))
        before = y[pa:a]
        quiet = _db(np.sqrt(np.mean(before ** 2))) if before.size else -90.0

        env = frame_rms(block, frame=256, hop=128)
        decay = float(len(block) / sr)
        if env.size:
            floor = env.max() * 10 ** (-30 / 20)
            below = np.where(env < floor)[0]
            if below.size:
                decay = float(below[0] * 128 / sr)
        # Ust sinira dayanip hic sonumlenmeyen sey tek atis degil, bir parcadir.
        if decay >= (end - start) * 0.98 and (end - start) >= max_len * 0.98:
            continue
        out.append({
            "start": start, "end": end,
            "_attack": _db(peak) - quiet,
            "_isolation": -quiet,
            "_decay": -decay,
            "_level": _db(peak),
        })
    return out


# ---------------------------------------------------------- drum sampler

BANDS = {"kick": (20, 120), "snare": (150, 1200), "hat": (4000, 11000)}
SLOTS = {"kick": "C1", "snare": "D1", "hat": "F#1"}


def band_shares(block, sr):
    if block.size < 128:
        return None
    spec = np.abs(np.fft.rfft(block * np.hanning(len(block))))
    freqs = np.fft.rfftfreq(len(block), 1 / sr)
    total = spec.sum() + 1e-12
    out = {}
    for name, (lo, hi) in BANDS.items():
        m = (freqs >= lo) & (freqs < hi)
        out[name] = float(spec[m].sum() / total)
    return out


def reference_shares(y, sr):
    """Kaydin kendi bant dagilimi — siniflandirmanin karsilastirma tabani."""
    return band_shares(y, sr)


def classify_drum(y, sr, start, end, reference=None):
    """kick / snare / hat ve VURGU orani.

    Ham bant payi bant genisligine yanlidir (snare bandi en genis oldugu icin
    her sey snare cikiyordu). Bunun yerine adayin bant payi, KAYDIN KENDI
    ortalama payina bolunur: "bu vurus, plagin genelinden ne kadar daha
    bas/tiz agirlikli" sorusu. Olculdu 2026-09-02.
    """
    a, b = int(start * sr), int(end * sr)
    shares = band_shares(y[a:b], sr)
    if shares is None:
        return None, 0.0
    ref = reference if reference is not None else reference_shares(y, sr)
    if ref is None:
        return None, 0.0
    ratios = {k: shares[k] / max(ref[k], 1e-9) for k in BANDS}
    best = max(ratios, key=ratios.get)
    return best, ratios[best]


def fragment_candidates(y, sr, onset_times, beats, bpm, max_count=64):
    """Transientten baslayan SABIT uzunlukta kucuk parcalar.

    Kiyma makinesinin gercek boyu icin gerekli: olculdu 2026-09-03, kullanicinin
    envelope'lu 469 ham clip'inin 469'u bir bardan cok kisa — 0.06 bar (ceyrek
    vurus) ve 0.16 bar. Vurus izgarasindan bu boy cikmaz, transientten kesilir.
    """
    length_s = beats * 60.0 / bpm
    out = []
    for t in onset_times[:max_count]:
        start = float(t)
        end = start + length_s
        if end > len(y) / sr:
            break
        block = y[int(start * sr):int(end * sr)]
        if block.size < 64:
            continue
        peak = float(np.max(np.abs(block)))
        if peak <= 0:
            continue
        out.append({"start": start, "end": end,
                    "_level": _db(peak),
                    "_attack": _db(peak) - _db(np.sqrt(np.mean(block[:max(1, len(block)//8)] ** 2))),
                    "_isolation": 0.0, "_decay": 0.0})
    return out


def rank(candidates, weights):
    """Bilesenleri normalize edip agirlikli toplar; 0-100 puan dondurur."""
    if not candidates:
        return []
    keys = list(weights)
    cols = {k: _norm([c[k] for c in candidates]) for k in keys}
    total_w = sum(weights.values())
    for i, c in enumerate(candidates):
        c["components"] = {k.lstrip("_"): round(float(cols[k][i]), 3) for k in keys}
        c["score"] = round(100 * sum(weights[k] * cols[k][i] for k in keys) / total_w, 1)
    return sorted(candidates, key=lambda c: -c["score"])
