"""Bir eserin chop adaylarini TURUNE GORE ayirmak.

Onceki surum tek bir siralama veriyordu ve pratikte hep introyu one cikariyordu.
Kullanicinin istedigi ayrim: intro, loop, full sample, melodi ve one-shot ayri
ayri listelenmeli -- cunku uretimde bunlarin isi farkli.

Her turun tanimi OLCULEBILIR, etiket uydurulmuyor:

  intro   ilk sesten itibaren, bar hizasinda; puanla yarismaz, hep sunulur.
  loop    2 bar; seviyesi DURGUN (kare kare RMS'in degisimi dusuk) ve atagi
          duzenli -- yani tekrar edince dikis yeri belli olmaz.
  full    8 bar; butun bir bolum. Icinde seviye degisebilir, aranan sey
          tutarlilik degil kapsam.
  melodi  4 bar; harmonik oran yuksek, atak yogunlugu dusuk -- calgi ve akor
          var, davul one cikmiyor.
  one     tek atak: cevresine gore keskin yukselen, kisa (0.15-1.2 sn) ve
          etrafindan sessizlikle ayrilan vurus. Davul/vurmali tek atislari.

Bar hizasi tempo tahminine dayanir ve tempo tahmini uzun kayitlarda
dogrulanmadi; sayfadaki kaydirma dugmeleri bunu elle toparlamak icin var.
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

SR = 22050
BEATS_PER_BAR = 4
VOICE_LOW, VOICE_HIGH = 200.0, 3000.0
MIN_RMS_DBFS = -34.0
INTRO_BARS = 4
SILENCE_DB = -45.0

# Tur basina bar uzunlugu ve kac aday dondurulecegi.
KINDS = {
    "loop": {"bars": 2, "top": 6},
    "melodi": {"bars": 4, "top": 4},
    "full": {"bars": 8, "top": 3},
}
# Esikler PARCAYA GORELI, sabit degil. Sabit esikler kirilgan cikti: atak
# sayimi degisince "melodi" esigi (<=3 atak/sn) hicbir pencerede tutmadi ve
# kategori bosaldi. Ayrica bir 1969 45'liginin atak yogunlugu ile bir 1990
# kaset kaydininki ayni olmuyor -- karsilastirma parcanin KENDI dagilimina
# gore yapilmali.
# Loop: durgunlugu en iyi bu yuzdelik dilime giren pencereler.
LOOP_CV_PERCENTILE = 35
# Melodi: harmonik orani en yuksek ve atagi en seyrek dilim.
MELODY_HARMONIC_PERCENTILE = 65
MELODY_ONSET_PERCENTILE = 40
# One-shot: atak gucunun cevresine orani ve uzunluk siniri.
ONE_MIN_PEAK_RATIO = 3.0
ONE_MIN_S, ONE_MAX_S = 0.15, 1.2
ONE_TOP = 8


@dataclass
class Spot:
    start_s: float
    end_s: float
    kind: str
    bars: int | None
    score: float
    harmonic_ratio: float | None
    onset_rate_hz: float | None
    rms_dbfs: float
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


def _first_sound(mono, sr):
    frame = max(1, int(sr * 0.05))
    trimmed = mono[: (len(mono) // frame) * frame]
    if not trimmed.size:
        return 0.0
    rms = np.sqrt(np.mean(np.square(trimmed.reshape(-1, frame)), axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-12))
    loud = np.flatnonzero(db > SILENCE_DB)
    return float(loud[0] * frame / sr) if loud.size else 0.0


N_FFT, HOP = 1024, 512


class Track:
    """Parcanin tum agir hesabini BIR KEZ yapar, pencereler dilimlenir.

    Onceki surum her pencere icin ayri STFT+HPSS+onset hesapliyordu: 131
    downbeat x 3 tur x 75 ms = 29 saniye, ustune beat tracking icin dalga formu
    uzerinde ayrica HPSS (11 sn). Ayni is yuzlerce kez tekrarlaniyordu.

    Burada STFT bir kez (0.1 sn), HPSS bir kez (4.7 sn), onset zarfi bir kez
    cikariliyor; pencere istatistikleri bu dizilerden dilimle okunuyor.
    """

    def __init__(self, path: str):
        self.y, self.sr = librosa.load(path, sr=SR, mono=True)
        self.duration = len(self.y) / self.sr
        self.spec = np.abs(librosa.stft(self.y, n_fft=N_FFT, hop_length=HOP))
        harm, perc = librosa.decompose.hpss(self.spec)
        self.h_energy = np.square(harm).sum(axis=0)
        self.p_energy = np.square(perc).sum(axis=0)
        self.frame_rms = np.sqrt(np.mean(np.square(self.spec), axis=0))
        self.onset_env = librosa.onset.onset_strength(S=librosa.power_to_db(self.spec ** 2),
                                                      sr=self.sr, hop_length=HOP)
        peaks = librosa.util.peak_pick(self.onset_env, pre_max=3, post_max=3, pre_avg=3,
                                       post_avg=5, delta=0.2, wait=2)
        self.onset_times = librosa.frames_to_time(peaks, sr=self.sr, hop_length=HOP)
        # Beat tracking artik dalga formu HPSS'ine degil, hazir onset zarfina dayaniyor.
        tempo, beats = librosa.beat.beat_track(onset_envelope=self.onset_env, sr=self.sr,
                                               hop_length=HOP, units="time")
        self.tempo = float(np.atleast_1d(tempo)[0])
        self.beats = beats

    def _frames(self, start_s, end_s):
        a = int(start_s * self.sr / HOP)
        b = int(end_s * self.sr / HOP)
        return max(0, a), min(len(self.frame_rms), b)

    def beat_window(self, i: int, beats: int):
        """i. vurustan baslayip `beats` vurus suren pencerenin GERCEK sinirlari.

        Sabit bar suresi (60/tempo*4) kullanmak kayiyordu: olculdu, 2 barlik
        pencerede medyan 93-116 ms, en kotusu 325 ms hata. 120 BPM'de bir vurus
        500 ms; yani loop'un sonu yarim vurustan fazla kayabiliyordu -- "kirik
        kesme"nin sebebi buydu. Izlenen vurus zamanlari kullanilinca hata sifir.
        """
        if i < 0 or i + beats >= len(self.beats):
            return None
        return float(self.beats[i]), float(self.beats[i + beats])

    # Denenip ELENEN iki yol, tekrar denenmesin diye:
    #  * Vurus zamanlariyla kesmek sabit sureye gore dikisi IYILESTIRMEDI
    #    (Necat -%3, Zeki Nasif -%8, digerleri ~0). Yine de daha dogru oldugu
    #    icin korundu: sabit sure 2 barlik pencerede 93-325 ms kayiyordu.
    #  * Onset zarfinin oz-benzerliginden loop periyodu cikarmak tutarsiz:
    #    Rames +%13, Erkin Koray +%5 ama Necat -%6, Zeki Nasif -%2.
    # Isleyen tek sey ADAYI DIKISE GORE SECMEK.

    def seam(self, start_s, end_s, window_s=0.12):
        """Loop basa donunce dikis ne kadar belli olur -- 0 iyi, buyuk kotu.

        Loop'un SONUNDAKI kisa pencere ile BASINDAKI kisa pencerenin spektrumu
        karsilastirilir. Iki taraf birbirine benziyorsa tekrar dikissiz duyulur.
        Olcum, kulakla dogrulanmis bir esik degil; adaylari birbirine gore
        siralamak icin.
        """
        a0, a1 = self._frames(start_s, start_s + window_s)
        b0, b1 = self._frames(max(start_s, end_s - window_s), end_s)
        if a1 - a0 < 2 or b1 - b0 < 2:
            return None
        head = self.spec[:, a0:a1].mean(axis=1)
        tail = self.spec[:, b0:b1].mean(axis=1)
        hn, tn = np.linalg.norm(head), np.linalg.norm(tail)
        if hn <= 0 or tn <= 0:
            return None
        cos = float(np.dot(head, tail) / (hn * tn))
        level = float(abs(20.0 * np.log10(max(float(tn), 1e-9) / max(float(hn), 1e-9))))
        # float() sart: numpy float32 JSON'a yazilamiyor ve parti isi bu yuzden
        # tam sonunda cokmustu.
        return float(round((1.0 - cos) + min(level / 12.0, 1.0), 4))

    def stats(self, start_s, end_s):
        a, b = self._frames(start_s, end_s)
        if b - a < 4:
            return None
        he = float(self.h_energy[a:b].sum())
        pe = float(self.p_energy[a:b].sum())
        harmonic = he / (he + pe) if (he + pe) > 0 else 0.0
        frames = self.frame_rms[a:b]
        cv = float(frames.std() / frames.mean()) if frames.mean() > 0 else 9.9
        seg = self.y[int(start_s * self.sr): int(end_s * self.sr)]
        rms = 20.0 * np.log10(max(float(np.sqrt(np.mean(np.square(seg)))), 1e-12)) \
            if seg.size else -99.0
        n = int(np.sum((self.onset_times >= start_s) & (self.onset_times < end_s)))
        rate = n / max(end_s - start_s, 1e-6)
        return rms, harmonic, cv, rate


def _pick(cands, top, taken):
    out = []
    for s in sorted(cands, key=lambda x: -x.score):
        if all(s.start_s >= t[1] or s.end_s <= t[0] for t in taken):
            out.append(s)
            taken.append((s.start_s, s.end_s))
        if len(out) >= top:
            break
    return out


def _find_on(track, top: int = 6) -> list[Spot]:
    y, sr, duration = track.y, track.sr, track.duration
    if duration < 20 or track.beats.size < BEATS_PER_BAR * 2 or track.tempo <= 0:
        return []
    bar = BEATS_PER_BAR * 60.0 / track.tempo
    # Pencereler artik SABIT SURE ile degil, izlenen vurus zamanlariyla
    # kesiliyor. Downbeat fazi bilinmiyor -- dort fazin dusuk bant enerjisi
    # olculdu ve ayirt etmedi (2413/2781/2626/2673), cunku bu repertuarda
    # batidaki "1'de kick" kalibi yok. O yuzden HER vurustan baslanir ve
    # aday, dikis kalitesine gore siralanir; faz karari kullaniciya kalir
    # (sayfadaki kaydirma dugmeleri).
    beat_starts = list(range(0, max(0, len(track.beats) - 1)))

    out: list[Spot] = []

    intro_at = _first_sound(y, sr)
    got = track.stats(intro_at, intro_at + INTRO_BARS * bar)  # intro sabit kalir
    if got:
        rms, harmonic, cv, rate = got
        out.append(Spot(round(intro_at, 2), round(intro_at + INTRO_BARS * bar, 2), "intro",
                        INTRO_BARS, 1.0, round(harmonic, 3), round(rate, 2), round(rms, 1),
                        f"ilk sesten {INTRO_BARS} bar"))

    # Once tum pencereleri olc, sonra esikleri BU parcanin dagilimindan cikar.
    windows = {}
    for kind, cfg in KINDS.items():
        bars = cfg["bars"]
        rows = []
        want = bars * BEATS_PER_BAR
        for i in beat_starts:
            win = track.beat_window(i, want)
            if win is None:
                continue
            start, end = win
            if end > duration:
                continue
            got = track.stats(start, end)
            if got and got[0] >= MIN_RMS_DBFS:
                rows.append((start, end, got))
        windows[kind] = rows
    def pct(rows, idx, q):
        vals = [r[2][idx] for r in rows]
        return float(np.percentile(vals, q)) if vals else 0.0

    for kind, cfg in KINDS.items():
        bars = cfg["bars"]
        rows = windows[kind]
        cv_cut = pct(rows, 2, LOOP_CV_PERCENTILE)
        h_cut = pct(rows, 1, MELODY_HARMONIC_PERCENTILE)
        r_cut = pct(rows, 3, MELODY_ONSET_PERCENTILE)
        cands = []
        for start, end, got in rows:
            rms, harmonic, cv, rate = got
            if kind == "loop":
                if cv > cv_cut:
                    continue
                sm = track.seam(start, end)
                if sm is None:
                    continue
                # Dikis, durgunluktan AGIR basar: kullanicinin sikayeti tam
                # olarak loop'un kirik kesilmesiydi, seviye durgunlugu degil.
                score = (1.0 - min(cv / max(cv_cut, 1e-6), 1.0)) * 0.4 \
                    + (1.0 - min(sm, 1.0)) * 1.6
                reason = f"dikiş {sm:.2f}, durgunluk {cv:.2f}, atak {rate:.1f}/sn"
            elif kind == "melodi":
                if harmonic < h_cut or rate > r_cut:
                    continue
                score = harmonic + (1.0 - min(rate / max(r_cut, 1e-6), 1.0))
                reason = f"harmonik {harmonic:.2f}, atak {rate:.1f}/sn"
            else:
                score = harmonic * 0.5 + (1.0 - min(cv, 1.5) / 1.5) * 0.5
                reason = f"harmonik {harmonic:.2f}, durgunluk {cv:.2f}"
            cands.append(Spot(round(start, 2), round(end, 2), kind, bars, round(score, 4),
                              round(harmonic, 3), round(rate, 2), round(rms, 1), reason))
        taken = [(s.start_s, s.end_s) for s in out if s.kind == kind]
        out.extend(_pick(cands, cfg["top"], taken))

    out.extend(_one_shots(track))
    out.sort(key=lambda s: (s.kind, s.start_s))
    return out


def find(path: str, top: int = 6) -> list[Spot]:
    return _find_on(Track(path), top)


def _one_shots(track) -> list[Spot]:
    y, sr, duration = track.y, track.sr, track.duration
    env = track.onset_env
    if not np.any(env):
        return []
    times = librosa.times_like(env, sr=sr, hop_length=HOP)
    peaks, _ = scipy.signal.find_peaks(env, height=float(np.percentile(env, 90)),
                                       distance=max(1, int(0.25 * sr / HOP)))
    local = scipy.signal.medfilt(env, kernel_size=31)
    out = []
    for p in peaks:
        base = float(local[p]) if local[p] > 1e-6 else 1e-6
        ratio = float(env[p]) / base
        if ratio < ONE_MIN_PEAK_RATIO:
            continue
        start = float(times[p])
        nxt = times[peaks[peaks > p][0]] if np.any(peaks > p) else duration
        length = min(max(float(nxt) - start, ONE_MIN_S), ONE_MAX_S)
        if start + length > duration:
            continue
        seg = y[int(start * sr): int((start + length) * sr)]
        if seg.size < sr // 40:
            continue
        rms = 20.0 * np.log10(max(float(np.sqrt(np.mean(np.square(seg)))), 1e-12))
        out.append(Spot(round(start, 3), round(start + length, 3), "one", None,
                        round(ratio, 3), None, None, round(rms, 1),
                        f"atak/çevre {ratio:.1f}×"))
    out.sort(key=lambda s: -s.score)
    return out[:ONE_TOP]


def grid(path: str, track=None) -> dict:
    track = track or Track(path)
    if track.tempo <= 0 or track.beats.size < 4:
        return {"ok": False}
    return {"ok": True, "bpm": round(track.tempo, 2),
            "bar_seconds": round(BEATS_PER_BAR * 60.0 / track.tempo, 4),
            "first_beat": round(float(track.beats[0]), 3),
            "duration_s": round(track.duration, 2)}


def find_and_grid(path: str) -> tuple[list[Spot], dict]:
    """Ikisini tek Track uzerinden -- iki kez agir hesap yapmamak icin."""
    track = Track(path)
    if track.duration < 20 or track.beats.size < BEATS_PER_BAR * 2 or track.tempo <= 0:
        return [], {"ok": False}
    return _find_on(track), grid(path, track)
