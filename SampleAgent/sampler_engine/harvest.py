"""Cihaz hasadi — tezgah cihaz YAZMAZ, Live'in yazdigini KOPYALAR.

Iki kaynak var, ikisi de Live tarafindan serilestirilmis gecerli XML:
  * kullanicinin kendi projesi (.als) — cihaz KENDI ayarlariyla gelir
    (orn. Tovbe'nin sidechain'li Gate'i: esik -14,2 dB, 3,5/10/15 ms)
  * Live'in Core Library preset'i (.adv) — hic kullanilmamis Suite cihazlari
    icin tek gecerli kaynak (Spectral Resonator, Spectral Time, Vocoder...)

Hasat edilen altagac engine/fixtures/devices/<ad>.xml olarak saklanir; tezgah
calisirken kullanicinin proje yollarina bagimli olmaz.

Olculdu 2026-09-03: Gate 241 eleman / 35 Id, Transmute 245 / 39, Spectral
289 / 47 — hicbirinde altagac disina bakan referans yok. Yani bench.reindex
tek basina yeterli; Id'ler yeniden numaralanir, baska sey degismez.
"""
import copy
import gzip
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "devices")
CORE = ("/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/"
        "Core Library/Devices/Audio Effects")
CORE_RACKS = ("/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/"
              "Core Library/Racks")
PACKS = os.path.expanduser("~/Music/Ableton/Factory Packs")

# ad -> (kaynak, konum). "als" icin (dosya, track adi, cihaz etiketi, sira);
# "adv" icin dosya yolu. Kaynaklar kullanicinin makinesine ozgu; fixture bir
# kez uretilir ve arac onunla calisir.
SOURCES = {
    # Tovbe VOCAL SYNTH: Gate, sidechain KİCK'ten. Olculen ayar 6 ay degismedi.
    "gate_sidechain": ("als",
        os.path.expanduser("~/Desktop/solo/SampleChopVol1/Tovbe/Tovbe Project/Tovbe.als"),
        "VOCAL SYNTH", "Gate", 0),
    # Hic kullanilmamis Suite cihazlari — Live'in kendi preset'inden.
    "spectral_resonator": ("adv", os.path.join(CORE, "Spectral Resonator", "Reso Mirrors.adv")),
    "spectral_time":      ("adv", os.path.join(CORE, "Spectral Time", "Delay Reso Glitch.adv")),
    "vocoder":            ("adv", os.path.join(CORE, "Vocoder", "Chromatic.adv")),
    "corpus":             ("adv", os.path.join(CORE, "Corpus", "Punchy Snare.adv")),
    "resonators":         ("adv", os.path.join(CORE, "Resonators", "Valhalla.adv")),

    # Yavas "swell" gate: Felekten Beter ROLAND <- SNARE BUSS; attack 13 ms,
    # release 1993 ms, wet 0,65. Olculdu 2026-09-03 — dogramiyor, sisiriyor.
    "gate_swell":         ("als",
        os.path.expanduser("~/Desktop/solo/SampleChopVol1/FelektenBeter/Felekten Beter Vurdu  Project/Felekten Beter Vurdu  809.als"),
        "ROLAND", "Gate", 0),

    # --- Live'in KENDI chop rack'leri (5420 Core Library + Pack rack'i yapisal
    # tarandi, 2026-09-03). Secim olcutu: kullanicinin hic/az kullandigi
    # cihazlari (Beat Repeat 9 set, Spectral 0, Shaper 10/1309, Vocoder 0)
    # Live'in kendi yazdigi ayarlarla tasimalari. Hepsi Audio Effect Rack.
    "live_loop_off_beat":  ("adg", os.path.join(CORE_RACKS, "Audio Effect Racks", "Modulation & Rhythmic", "Loop Off Beat.adg")),
    # Trance Pad Gate ALINMADI: teknigi Max 'Shaper'a dayaniyor, Max nakli
    # Live'i cokertiyor (bkz. feedback_m4l_transplant_crashes_live).
    "pack_skips_a_beat":   ("adg", os.path.join(PACKS, "Chop and Swing", "Effect Racks", "Modulation & Rhythmic", "Skips A Beat.adg")),
    "pack_beat_shaper":    ("adg", os.path.join(PACKS, "Chop and Swing", "Effect Racks", "Modulation & Rhythmic", "Beat Shaper.adg")),
    "pack_distant_stutter":("adg", os.path.join(PACKS, "Beat Tools", "Effect Racks", "Modulation & Rhythmic", "Distant Stutter.adg")),
    "pack_multiband_repeat":("adg", os.path.join(PACKS, "Beat Tools", "Effect Racks", "Modulation & Rhythmic", "Multiband Beat Repeat.adg")),
    "pack_mince_and_hack": ("adg", os.path.join(PACKS, "Glitch and Wash", "Effect Racks", "Modulation & Rhythmic", "Mince and Hack.adg")),
    "pack_rough_diamond":  ("adg", os.path.join(PACKS, "Glitch and Wash", "Effect Racks", "Modulation & Rhythmic", "Rough Diamond.adg")),

    # Kullanicinin Simpler-Slicing + Arpeggiator teknigi. Olculdu 2026-09-03:
    # 53 ana set + backup'larda 51 kanal, 10 farkli ayar demeti. Tovbe 35-B-Slice
    # rack'i Live'in yazdigi tam zincir: Arp > Random > Envelope MIDI > Simpler
    # (Slicing, Region 16) > Gate (kapali) > Utility. Live'in kendi Slicing
    # varsayilanlari (Core Library/Defaults/Slicing) Drum Rack; bu teknik degil.
    "arp_slice_rack":     ("als",
        os.path.expanduser("~/Desktop/solo/SampleChopVol1/Tovbe/Tovbe Project/Tovbe.als"),
        "35-B-Slice", "InstrumentGroupDevice", 0),
    # MIDI clip prototipi: ayni kanalin ilk clip'i — tek nota, 16 vurus, vel 100.
    # 97 eleman, 3 Id; tezgah degerlerini yazar, iskeleti Live'dan alir.
    "midi_clip_proto":    ("als",
        os.path.expanduser("~/Desktop/solo/SampleChopVol1/Tovbe/Tovbe Project/Tovbe.als"),
        "35-B-Slice", "MidiClip", 0),
}


def _load(path):
    with gzip.open(path) as fh:
        return ET.fromstring(fh.read())


def _track_named(root, name):
    for tr in root.iter():
        if tr.tag not in ("AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack"):
            continue
        n = tr.find("Name/EffectiveName")
        if n is not None and n.get("Value") == name:
            return tr
    return None


def harvest(name):
    """Kaynaktan cihaz altagacini kopar; Element dondurur (deepcopy)."""
    kind, *spec = SOURCES[name]
    if kind == "adv":
        root = _load(spec[0])
        return copy.deepcopy(root[0])
    if kind == "adg":
        # .adg = GroupDevicePreset: rack KABUGU (Device) ile dallar (BranchPresets)
        # AYRI tutulur; set icindeki Branches/AudioEffectBranch/DeviceChain
        # yapisi burada yok. Olculdu 2026-09-03 (Loop Off Beat: Device 562 el,
        # BranchPresets 1249 el). Rack'i yeniden kurmak yerine dallardaki
        # cihazlar Live'in ayarlariyla DUZ ZINCIR olarak alinir: teknik
        # (Beat Repeat + Corpus + Gate ayarlari) korunur, makro esleme kaybolur.
        # Sarmalayici <Chain> elemani doner; inject_devices cocuklarini teker
        # teker ekler.
        root = _load(spec[0])
        chain = ET.Element("Chain")
        chain.set("Source", os.path.basename(spec[0]))
        # Sadece ILK dal alinir: paralel dallari (Multiband Beat Repeat'in uc
        # bandi, Beat Shaper'in uc dali) seri zincire dizmek teknigi bozar.
        branches = list(root.iter("AudioEffectBranchPreset")) or list(root.iter("InstrumentBranchPreset"))
        chain.set("Branches", str(len(branches)))
        first = branches[0] if branches else root
        for preset in first.iter("AbletonDevicePreset"):
            dev = preset.find("Device")
            if dev is None or len(dev) == 0:
                continue
            el = dev[0]
            if el.tag.endswith("MixerDevice") or el.tag.startswith("MxDevice"):
                continue   # dal mikseri cihaz degil / Max nakli yasak
            chain.append(copy.deepcopy(el))
        if len(chain) == 0:
            raise SystemExit(f"{spec[0]}: ilk dalda cihaz yok")
        return chain
    path, track, tag, index = spec
    root = _load(path)
    tr = _track_named(root, track)
    if tr is None:
        raise SystemExit(f"{path}: '{track}' adli track yok")
    # cihaz rack icinde olabilir; etiket + sira ile bulunur, sadece gercek
    # cihazlar sayilir (Gate: Threshold tasiyan)
    hits = [d for d in tr.iter(tag) if tag != "Gate" or d.find("Threshold") is not None]
    if len(hits) <= index:
        raise SystemExit(f"{path}: '{track}' icinde {tag} #{index} yok")
    return copy.deepcopy(hits[index])


def fixture_path(name):
    return os.path.join(FIXTURES, name + ".xml")


def build_fixtures(names=None):
    os.makedirs(FIXTURES, exist_ok=True)
    out = []
    for name in (names or SOURCES):
        el = harvest(name)
        ET.ElementTree(el).write(fixture_path(name), encoding="utf-8", xml_declaration=False)
        out.append((name, el.tag, sum(1 for _ in el.iter())))
    return out


def load_fixture(name):
    """Tezgahin kullandigi giris: fixture'dan taze bir kopya."""
    path = fixture_path(name)
    if not os.path.exists(path):
        raise SystemExit(f"cihaz fixture'i yok: {path}\n"
                         f"  python3 -m engine.harvest  ile uretilir")
    return ET.parse(path).getroot()


if __name__ == "__main__":
    for name, tag, n in build_fixtures():
        print(f"  {name:<20} {tag:<12} {n} eleman")
