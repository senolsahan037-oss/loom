"""Bir yuklemenin dogru hizda olup olmadigi: SURE karsilastirmasiyla.

Erkin Koray kendi resmi yayininin aciklamasinda bootleg baskilar icin
"wrong pitches, wrong speed and tonalities" diyor. Yanlis hizda aktarilmis bir
plaktan alinan chop, projede ne yapilirsa yapilsin tutmaz.

ONCE AKORT SAPMASI DENENDI VE COKTU. librosa.estimate_tuning yarim ton modunda
olcer, yani +-50 cent'te basa sarar: dosyayi bilerek tam bir yarim ton tize
kaydirdim, olcum orijinalle ayni degeri verdi (-49 ve -50 cent). Yarim tonun
kati olan her hiz hatasi bu yontemle gorunmez.

Isleyen olcut SURE. Ayni parcanin yuklemeleri toplanir, medyan sure referans
alinir, sapma cent cinsinden raporlanir: oran = medyan / sure, cent =
1200*log2(oran). Gercek olcum (Erkin Koray - Yagmur, 18 yukleme): coğu +-8
cent icinde kumelendi, "Orjinal Plak Kaydi 1971" diye gecen yukleme +32 cent
HIZLI cikti, "enstrumental" +57.

Bu bir suclama degil: farkli bir mix, canli kayit ya da kisaltilmis versiyon da
sapma gosterir. O yuzden kumeden uzak olan "farkli", sonra bakilmali.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
import statistics

# Bu kadar sapma normal: kirpma, sessizlik, fade farklari.
TOLERANCE_CENTS = 12.0
# Bunun ustu incelenmeli.
SUSPECT_CENTS = 25.0
# Kumeye girmesi icin en az bu kadar yukleme lazim; 3 kayitta medyan medyan degildir.
MIN_UPLOADS = 5


@dataclass
class SpeedCheck:
    title: str
    video_id: str | None
    duration_s: float
    ratio: float
    cents: float
    verdict: str

    def as_dict(self) -> dict:
        return asdict(self)


def compare(uploads: list[dict]) -> tuple[list[SpeedCheck], dict]:
    """uploads: [{'title','id','duration'}]. Medyan sureye gore sapmalar."""
    usable = [u for u in uploads if (u.get("duration") or 0) > 30]
    if len(usable) < MIN_UPLOADS:
        return [], {"ok": False, "reason": f"yetersiz_yukleme({len(usable)}<{MIN_UPLOADS})"}

    reference = statistics.median(u["duration"] for u in usable)
    rows = []
    for u in usable:
        duration = float(u["duration"])
        ratio = reference / duration
        cents = 1200.0 * math.log2(ratio)
        if abs(cents) <= TOLERANCE_CENTS:
            verdict = "kume"
        elif abs(cents) < SUSPECT_CENTS:
            verdict = "kiyida"
        else:
            verdict = "hizli" if cents > 0 else "yavas"
        rows.append(SpeedCheck(
            title=str(u.get("title") or ""), video_id=u.get("id"),
            duration_s=duration, ratio=round(ratio, 4),
            cents=round(cents, 0), verdict=verdict,
        ))
    rows.sort(key=lambda r: abs(r.cents))
    summary = {
        "ok": True,
        "reference_duration_s": reference,
        "uploads": len(rows),
        "in_cluster": sum(1 for r in rows if r.verdict == "kume"),
    }
    return rows, summary


def best(uploads: list[dict]) -> SpeedCheck | None:
    """Kumeye en yakin yukleme -- chop icin alinacak olan."""
    rows, summary = compare(uploads)
    return rows[0] if summary.get("ok") and rows else None
