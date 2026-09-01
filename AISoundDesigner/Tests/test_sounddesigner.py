#!/usr/bin/env python3
"""AISoundDesigner'in headless dogrulanmasi. Live gerekmiyor."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AISoundDesigner"))

from sounddesigner import source_evidence as se  # noqa: E402

checks = []
failures = []


def check(label, condition, detail=""):
    if condition:
        checks.append(label)
    else:
        failures.append("%s  %s" % (label, detail))


check("bounce ciktilari kimlik sayilmaz", se.is_bounce("Bounce KICK [2025-07-14 235240]-2.wav"))
check("freeze dosyalari kimlik sayilmaz", se.is_bounce("Freeze bass 01.wav"))
check("kutuphane sample'i bounce sayilmaz", not se.is_bounce("Kick Golden Era 46.aif"))
check("reverb impulse'lari ses kaynagi sayilmaz",
      se.is_non_source("Hybrid_Early_Reflections_Ableton Studio Backwards L.aif"))
check("normal sample kaynak sayilir", not se.is_non_source("Zero Hour Bass A0.aif"))

rows = se.load_tracks()
# Bkz. Presetor testi: depoda olculmus veri yok, fixture ile de gecmeli.
check("kanit verisi yuklendi", len(rows) >= se.MIN_ROLE_SAMPLE * 5, len(rows))
check("hangi kaynagin kullanildigi bildiriliyor",
      se.data_source() in ("measured", "synthetic_fixture"), se.data_source())
check("ozet de kaynagi tasiyor", se.summary(rows)["data_source"] == se.data_source())

bass = se.palette("bass", rows)
check("bass icin palet var", bass is not None)
if bass:
    check("palet birden fazla projede gorulen sample'lardan olusur",
          all(item.projects >= se.MIN_PROJECTS for item in bass.samples),
          [(i.sample, i.projects) for i in bass.samples[:3]])
    check("palet proje yayilimina gore sirali",
          all(bass.samples[i].projects >= bass.samples[i + 1].projects for i in range(len(bass.samples) - 1)))
    check("paletten bounce elenmis", not any(se.is_bounce(item.sample) for item in bass.samples))
    check("paletten impulse elenmis", not any(se.is_non_source(item.sample) for item in bass.samples))
    check("palet kac track'e dayandigini soyluyor", bass.role_sample >= se.MIN_ROLE_SAMPLE, bass.role_sample)

check("bilinmeyen rol icin palet URETILMEZ", se.palette("__yok_boyle_bir_rol__", rows) is None)
few = [{"role": "tek", "all_samples": ["a.wav"], "instruments": [], "project": "p"}] * (se.MIN_ROLE_SAMPLE - 1)
check("orneklemi kucuk rol sessiz kalir", se.palette("tek", few) is None)
# Ayni sample yeterince cok track'te var ama hepsi TEK projede: bu bir
# aliskanlik degil, o projenin karari -- palet uretilmez.
single = [{"role": "tek", "all_samples": ["a.wav"], "instruments": [], "project": "hep_ayni"}] * se.MIN_ROLE_SAMPLE
check("tek projede tekrarlayan sample palete girmez", se.palette("tek", single) is None)
# Ayni sample iki ayri projede: artik palete girer.
spread = [
    {"role": "tek", "all_samples": ["a.wav"], "instruments": [], "project": "proje_%d" % (index % 2)}
    for index in range(se.MIN_ROLE_SAMPLE)
]
spread_result = se.palette("tek", spread)
check("iki ayri projede gorulen sample palete girer",
      spread_result is not None and [item.sample for item in spread_result.samples] == ["a.wav"],
      spread_result)

summary = se.summary(rows)
check("ozet bounce oranini bildirir", 0 < summary["bounce_share"] < 1, summary["bounce_share"])
check("palet cikmayan roller acikca listelenir", isinstance(summary["roles_without_palette"], list))
check("en sik kimlik sample'lari raporlanir", len(summary["top_identity_samples"]) > 0)

print("%d kontrol gecti:" % len(checks))
for label in checks:
    print("  ok  %s" % label)
if failures:
    print()
    print("BASARISIZ:")
    for failure in failures:
        print("  - %s" % failure)
    sys.exit(1)
print("AISOUNDDESIGNER CALISIYOR")
