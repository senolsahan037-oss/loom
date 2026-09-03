"""Simpler-Slicing + Arpeggiator varyasyon uretici — kopyalamaz, dilbilgisinden turetir.

Olculdu 2026-09-03: ~/Desktop/solo altindaki 53 ana set + Backup'lar.
Slicing+Arp birlikte olan 51 kanal, proje x ayar tekillestirilince 10 demet:

  Arpeggiator
    mod            Converge 5 · RandomOnce 2 · Up 1 · Diverge 1 · PinkyUpDown 1
    SyncedRate     8 (8 demet) · 6 (2)          [ham enum indeksi, etiket dogrulanmadi]
    gate           50 (8) · 65 (1) · 200 (1)
    hold           kapali 9 · acik 1
    transpose      steps 0 / dist 12 (7) · 1/10 (1) · cokme yedegi 5/-11 (1)
  Simpler (PlaybackMode 2 = Slicing)
    stil           Region 6 · Transient 3 · Beat 1
    bolge          8 (4) · 39 (4) · 16 (2)      grid 4 (9) · 2 (1)
    esik           100 (hepsi)     voices 5     retrigger acik (hepsi)
  MIDI (Tovbe 32 clip + Felekten Beter 13 clip, istisnasiz)
    TEK NOTA, basili tutulur: tus 34 / 38 · sure 16 / 33,25 vurus · velocity 100
    clip 16 / 32 vurus · araligi 4 / 8 bar

Tek notada arp modunun sesi degismez; mod ancak birden cok tusla anlam
kazanir. Bu yuzden notes>1 secenegi var ama VARSAYILAN DEGIL — kullanicinin
kendi kayitlarinda hic yok, olcum disi oldugu meta'ya yazilir.
"""

import random

# Okurken kullanilan haritalarin tersi — yazilan ham deger, okunanla ayni.
MODE_INDEX = {"Up": 0, "Converge": 6, "Diverge": 7, "PinkyUpDown": 10, "RandomOnce": 17}
STYLE_INDEX = {"Transient": 0, "Beat": 1, "Region": 2}

MODES = ("Converge",) * 5 + ("RandomOnce",) * 2 + ("Up", "Diverge", "PinkyUpDown")
RATES = (8,) * 8 + (6,) * 2
GATES = (50,) * 8 + (65, 200)
HOLDS = (False,) * 9 + (True,)
TRANSPOSES = ((0, 12),) * 7 + ((1, 10), (5, -11))
STYLES = ("Region",) * 6 + ("Transient",) * 3 + ("Beat",)
REGIONS = (8, 8, 39, 39, 16)
GRIDS = (4,) * 9 + (2,)
NOTE_KEYS = (34, 38)
NOTE_LENS = (16.0, 33.25)
SPACINGS_BARS = (4, 8)


def generate(seed=None, notes=1, style=None):
    """Bir ayar demeti dondurur; ayni tohum ayni demeti verir."""
    rng = random.Random(seed)
    steps, dist = rng.choice(TRANSPOSES)
    st = style or rng.choice(STYLES)
    key = rng.choice(NOTE_KEYS)
    keys = [key] + [key + i for i in range(1, max(1, int(notes)))]
    return {
        "seed": seed,
        "arp": {
            "Mode": MODE_INDEX[rng.choice(MODES)],
            "SyncState": True, "SyncedRate": rng.choice(RATES),
            "Gate": rng.choice(GATES), "Hold": rng.choice(HOLDS),
            "TransposeMode": 0, "TransposeKey": 0,
            "TransposeSteps": steps, "TransposeDistance": dist,
            "RepeatCount": 0, "Groove": 0, "Retrigger": 0,
        },
        "slice": {
            "SlicingStyle": STYLE_INDEX[st], "SlicingRegions": rng.choice(REGIONS),
            "SlicingBeatGrid": rng.choice(GRIDS), "SlicingThreshold": 100,
        },
        "midi": {
            "keys": keys, "note_len_beats": rng.choice(NOTE_LENS),
            "velocity": 100, "spacing_bars": rng.choice(SPACINGS_BARS),
        },
        "olcum_disi": notes > 1,
    }


def label(meta):
    """Kanal adina yazilacak kisa etiket: mod, rate, gate, hold, transpoze, dilimleme."""
    mode = {v: k for k, v in MODE_INDEX.items()}
    style = {v: k for k, v in STYLE_INDEX.items()}
    a, s = meta["arp"], meta["slice"]
    parts = [mode.get(a["Mode"], str(a["Mode"])), f"r{a['SyncedRate']}", f"g{a['Gate']}"]
    if a["Hold"]:
        parts.append("hold")
    if a["TransposeSteps"]:
        parts.append(f"t{a['TransposeSteps']}/{a['TransposeDistance']}")
    parts.append(f"{style.get(s['SlicingStyle'])}{s['SlicingRegions']}")
    return " ".join(parts)
