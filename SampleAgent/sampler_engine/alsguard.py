"""`.als` guvenlik kontrolu — Live'i acmadan bozulmayi yakalar.

Neden var: agent bir Live projesine mudahale edince XML kolayca "corrupt"
oluyor ve Live sessizce bos projeye dusuyor. Olculdu 2026-09-02, dort ayri
sinif hata bulundu ve dordu de burada kontrol ediliyor:

  1. Clip uzunlugu <= 0            -> Live clip'i siler, sonra belgeyi reddeder
  1b. Time niteligi != CurrentStart -> clip'ler ayni yere yigilir
  1c. FileRef'te fazladan eleman  -> sample offline gorunur
  1d. Ayni listede ayni clip Id    -> '(Non-unique list ids)'
  1e. Cikti proje klasoru disinda  -> butun sample'lar offline
  2. Kopyalanan altagacta ayni Id  -> "corrupt", hic acilmaz
  3. ReturnTrack sonda degil       -> track sirasi bozuk
  4. TrackGroupId hayali gruba     -> "(Track grouping corrupt)" — olumcul olan
  5. NextPointeeId < en buyuk Id   -> kimlik sayaci geride kalir

Kullanim:
    sorunlar = alsguard.check(root)          # yazmadan once
    if sorunlar: ...                          # bos liste = temiz
"""

import collections
import os

GROUPABLE = ("AudioTrack", "MidiTrack", "GroupTrack")


def _val(el, tag):
    e = el.find(tag)
    return e.get("Value") if e is not None else None


def check_location(template_path, out_path):
    """Cikti sablonun proje klasorunde mi?

    Live sample'lari once GORELI yoldan cozer (RelativePathType=3 = proje
    klasorune gore). Set baska bir klasore yazilirsa goreli yol tutmaz ve Live
    dosyayi mutlak yol diskte VAR olsa bile 'could not be opened' sayar.
    Olculdu 2026-09-02: ayni belge proje disinda 15 sesi offline gosterirken,
    proje klasorune yazilinca sifir hata verdi.
    """
    if os.path.dirname(os.path.abspath(template_path)) != os.path.dirname(os.path.abspath(out_path)):
        return [f"cikti sablonun proje klasorunde degil — sample'lar offline gorunur.\n"
                f"    sablon: {os.path.dirname(os.path.abspath(template_path))}\n"
                f"    cikti : {os.path.dirname(os.path.abspath(out_path))}"]
    return []


def check(root, baseline_dupes=None):
    """Sorun listesi dondurur. Bos liste = yazmaya uygun."""
    problems = []
    tracks_holder = root.find("LiveSet/Tracks")
    if tracks_holder is None:
        return ["LiveSet/Tracks yok"]
    children = list(tracks_holder)

    # 1. clip uzunlugu
    bad = 0
    for clip in root.iter("AudioClip"):
        cs, ce = _val(clip, "CurrentStart"), _val(clip, "CurrentEnd")
        try:
            if cs is not None and ce is not None and float(ce) - float(cs) <= 0:
                bad += 1
        except ValueError:
            bad += 1
    if bad:
        problems.append(f"{bad} clip'in uzunlugu sifir veya negatif "
                        f"(CurrentEnd <= CurrentStart) — Live bunlari siler ve belgeyi reddeder")

    # 1b. Time niteligi ile CurrentStart uyusmali (Live konumu Time'dan okur)
    mismatched = 0
    for clip in root.iter("AudioClip"):
        t, cs = clip.get("Time"), _val(clip, "CurrentStart")
        if t is None or cs is None:
            continue
        try:
            if abs(float(t) - float(cs)) > 1e-6:
                mismatched += 1
        except ValueError:
            mismatched += 1
    if mismatched:
        problems.append(f"{mismatched} clip'te Time niteligi CurrentStart ile uyusmuyor — "
                        f"Live konumu Time'dan okur, clip'ler yanlis yere gider")

    # 1c. FileRef'e fazladan eleman eklenmis mi (sample offline sebebi)
    known = {"RelativePathType", "RelativePath", "Path", "Type", "LivePackName",
             "LivePackId", "OriginalFileSize", "OriginalCrc", "SourceHint", "Data"}
    strange = collections.Counter()
    for fr in root.iter("FileRef"):
        for child in fr:
            if child.tag not in known:
                strange[child.tag] += 1
    if strange:
        problems.append("FileRef'te beklenmeyen eleman: "
                        + ", ".join(f"{k}×{v}" for k, v in strange.items())
                        + " — Live sample'i cozemez (offline gorunur)")

    # 1d. Ayni Events listesinde tekrarlanan clip Id'si
    clash = 0
    for events in root.iter("Events"):
        seen = collections.Counter(c.get("Id") for c in events if c.get("Id") is not None)
        clash += sum(1 for n in seen.values() if n > 1)
    if clash:
        problems.append(f"{clash} Events listesinde clip Id'si tekrarliyor — "
                        f"Live '(Non-unique list ids)' deyip belgeyi reddeder")

    # 1f. Bos warp marker dizisi — Live belgeyi reddeder
    empty_warp = sum(1 for clip in root.iter("AudioClip")
                     for wm in [clip.find("WarpMarkers")]
                     if wm is not None and len(list(wm)) == 0)
    if empty_warp:
        problems.append(f"{empty_warp} clip'te warp marker dizisi bos — "
                        f"Live '(Empty warp marker array.)' deyip belgeyi reddeder")

    # 1g. ClipEnvelope yanlis katmanda mi (Live gormez)
    misplaced = 0
    for clip in root.iter("AudioClip"):
        outer = clip.find("Envelopes")
        if outer is None:
            continue
        for child in outer:
            if child.tag == "ClipEnvelope":
                misplaced += 1
    if misplaced:
        problems.append(f"{misplaced} ClipEnvelope dis katmanda — Live'in kapsayicisi "
                        f"<Envelopes><Envelopes>, disa konan envelope hic gorunmez")

    # 2. tekrarlanan Id — yalnizca cihaz/otomasyon kimligi tasiyan etiketler.
    # Olculdu 2026-09-03: Live'in kendi dosyasinda RemoteableTimeSignature 156
    # kez Id=0, SourceContext ve AudioClip de tekrarli; bunlar normal. Belgeyi
    # gercekten bozanlar track klonlanirken cogalan su ucuydu.
    UNIQUE_TAGS = ("AutomationTarget", "ModulationTarget", "Pointee")
    ids = collections.Counter()
    for el in root.iter():
        v = el.get("Id")
        if v is not None and el.tag in UNIQUE_TAGS:
            ids[(el.tag, v)] += 1
    dupes = sum(1 for n in ids.values() if n > 1)
    if baseline_dupes is not None and dupes > baseline_dupes:
        problems.append(f"tekrarlanan Id sayisi {baseline_dupes} -> {dupes} yukseldi "
                        f"(kopyalanan altagac yeniden numaralanmamis)")

    # 3. ReturnTrack / MainTrack sirasi
    tags = [c.tag for c in children]
    last_normal = max((i for i, t in enumerate(tags) if t in GROUPABLE), default=-1)
    first_return = next((i for i, t in enumerate(tags) if t in ("ReturnTrack", "MainTrack")), None)
    if first_return is not None and last_normal > first_return:
        problems.append("ReturnTrack'ten sonra normal track var — return'lar listenin sonunda olmali")

    # 4. grup bagi
    group_ids = set()
    for c in children:
        if c.tag == "GroupTrack":
            gid = c.get("Id")
            if gid:
                group_ids.add(gid)
    orphan = 0
    for c in children:
        if c.tag not in GROUPABLE:
            continue
        gid = _val(c, "TrackGroupId")
        if gid not in (None, "-1") and gid not in group_ids:
            orphan += 1
    if orphan:
        problems.append(f"{orphan} track var olmayan bir gruba bagli (TrackGroupId) — "
                        f"Live bunu '(Track grouping corrupt)' diye reddeder")

    # 5. kimlik sayaci
    biggest = 0
    for el in root.iter():
        v = el.get("Id")
        if v and v.lstrip("-").isdigit():
            biggest = max(biggest, int(v))
    counter = root.find("LiveSet/NextPointeeId")
    if counter is not None:
        try:
            if int(counter.get("Value")) <= biggest:
                problems.append(f"NextPointeeId ({counter.get('Value')}) en buyuk Id'nin "
                                f"({biggest}) altinda")
        except (TypeError, ValueError):
            problems.append("NextPointeeId okunamadi")
    # Enjekte edilen cihazlar: liste ici Id benzersiz olmali. Live ayni
    # listede tekrar eden Id'yi "(Non-unique list ids)" diye reddediyor.
    dup_devices = 0
    for devs in root.iter("Devices"):
        ids = [d.get("Id") for d in devs if d.get("Id") is not None]
        dup_devices += len(ids) - len(set(ids))
    if dup_devices:
        problems.append(f"{dup_devices} Devices listesinde cihaz Id'si tekrarliyor")

    # Sidechain hedefi var olmayan bir track'e bakmamali. Format Tovbe'den
    # olculdu (2026-09-03): AudioIn/Track.<Id>/PostFxOut.
    track_ids = {tr.get("Id") for tr in root.iter()
                 if tr.tag in ("AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack")}
    dangling = []
    for rt in root.iter("Routable"):
        t = rt.find("Target")
        v = t.get("Value") if t is not None else ""
        if v.startswith("AudioIn/Track."):
            tid = v.split("Track.", 1)[1].split("/", 1)[0]
            if tid not in track_ids:
                dangling.append(v)
    if dangling:
        problems.append(f"{len(dangling)} sidechain hedefi var olmayan track'e bakiyor: "
                        + ", ".join(dangling[:3]))

    return problems


def baseline(root):
    """Sablonun kendi tekrarlanan Id sayisi — kiyas noktasi."""
    ids = collections.Counter()
    for el in root.iter():
        v = el.get("Id")
        if v is not None:
            ids[(el.tag, v)] += 1
    return sum(1 for n in ids.values() if n > 1)
